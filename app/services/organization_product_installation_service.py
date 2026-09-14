import ipaddress
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Organization, OrganizationProduct, OrganizationProductInstallation, Product
from .audit_service import AuditService
from .bootstrap_service import STRUCTURAL_PRODUCTS

# Issue #68: mesma fonte canônica única já usada por
# `AccessService._resolve_canonical_product`/`ProductLaunchCodeService`
# - nunca redeclarar código/URL de produto em outro lugar. Indexado por
# `code` para resolução O(1) tanto do produto quanto do
# `url_config_key` correspondente.
_CANONICAL_PRODUCTS_BY_CODE = {spec["code"]: spec for spec in STRUCTURAL_PRODUCTS}

# Teto de tamanho já existente na coluna (`String(255)`) - validado aqui
# também, antes de qualquer parsing, nunca só confiado ao banco.
_MAX_URL_LENGTH = 255

# Esquemas aceitos pela URL de uma instalação - qualquer outro
# (`javascript:`, `data:`, `file:`, `ftp:`, etc.) é rejeitado
# nominalmente. `http` só é aceito sob a regra de ambiente/host de
# desenvolvimento (ver `_is_dev_http_host_allowed`).
_ALLOWED_SCHEMES = frozenset({"https", "http"})

# Hosts de desenvolvimento reconhecidos para tolerar `http` fora de
# produção (Issue #68) - `localhost`/loopback exatos, mais qualquer host
# terminado em `.local` (sufixo sintético já usado por
# `tests/conftest.py` para `L_KALENDER_URL`/`L_GEDO_URL`). Nunca
# qualquer host HTTP arbitrário, mesmo fora de produção.
_DEV_HTTP_EXACT_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEV_HTTP_HOST_SUFFIX = ".local"


class OrganizationProductInstallationError(ValueError):
    """Erro de domínio esperado e seguro (URL/produto/organização
    inválidos, instalação inexistente, transição de estado repetida,
    etc.) - a mensagem já é curada para ser exibida diretamente ao
    operador, nunca contém a URL/`product_code` bruto recebido nem
    detalhe de banco/driver. Continua sendo um `ValueError` (mesma
    compatibilidade já usada pelos demais services), mas nunca é a
    mesma classe usada para uma falha inesperada - ver
    `OrganizationProductInstallationOperationError`. Mesmo padrão já
    estabelecido por `OrganizationError`/`OrganizationOperationError`,
    `ProductAccessError`/`ProductAccessOperationError` e
    `InstallationCredentialError`/`InstallationCredentialOperationError`."""


class OrganizationProductInstallationOperationError(ValueError):
    """Falha inesperada ao processar a operação (banco, driver,
    AuditLog, colisão de `organization_product_id` que escapou do lock,
    ou qualquer exceção não prevista) - deliberadamente NÃO é subclasse
    de `OrganizationProductInstallationError` (são classes irmãs). A
    mensagem pública é sempre genérica; a causa técnica real é
    preservada em `__cause__` via `raise ... from exc`, nunca exposta
    ao usuário e nunca contém a URL/`product_code` envolvidos."""


class OrganizationProductInstallationService:
    @staticmethod
    def _has_control_characters(value):
        """Rejeita C0 (`0x00-0x1F`), DEL (`0x7F`) e C1 (`0x80-0x9F`) -
        Achado M1-2 da revisão técnica: a versão anterior só cobria C0+DEL,
        deixando passar despercebido um controle C1 (ex.: `\\x85`, NEL).
        Nunca rejeita caracteres imprimíveis comuns de path/host (a faixa
        C1 termina em `0x9F`; `0xA0` em diante já são caracteres visíveis
        do Unicode/Latin-1, fora desta checagem)."""
        return any(
            ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F
            for ch in value
        )

    @staticmethod
    def _looks_like_ip_literal(hostname):
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_dev_http_host_allowed(hostname):
        """Issue #68: `http` só é tolerado fora de produção para hosts de
        desenvolvimento reconhecidos - nunca para qualquer host HTTP
        arbitrário, mesmo fora de produção. `hostname` já chega
        normalizado em minúsculas (`urlsplit(...).hostname`)."""
        if hostname in _DEV_HTTP_EXACT_HOSTS:
            return True
        return hostname.endswith(_DEV_HTTP_HOST_SUFFIX)

    @staticmethod
    def _resolve_canonical_host(product_code):
        """Resolve host/porta/esquema da URL canônica configurada para
        `product_code` (`current_app.config[url_config_key]`,
        `url_config_key` vindo de `STRUCTURAL_PRODUCTS` - nunca
        duplicado em outro dicionário). A configuração canônica também é
        entrada administrada por humanos (variável de ambiente) e pode
        estar ausente ou malformada - tratada aqui como falha fechada
        (Issue #68, "Validação da própria configuração canônica"),
        nunca como fallback silencioso: nunca cai em `Product.url`,
        nunca aceita qualquer host, nunca interpola o valor configurado
        na mensagem. Lida SEMPRE via `current_app.config` (a aplicação
        ativa), nunca importando `Config`/`DevelopmentConfig` etc.
        diretamente - isso ignoraria qualquer override de teste/ambiente
        aplicado via `app.config.update(...)`."""
        spec = _CANONICAL_PRODUCTS_BY_CODE[product_code]
        # Achado M1-1 da revisão técnica: `spec["url_config_key"]` cru
        # levantava `KeyError` (nunca capturado como
        # `OrganizationProductInstallationError`) para um produto
        # presente em `STRUCTURAL_PRODUCTS` sem essa chave mapeada -
        # inatingível com o catálogo real hoje (kalender/gedo/hunt
        # sempre têm a chave), mas um produto estrutural futuro mal
        # configurado cairia fechado com o TIPO errado de exceção. A
        # própria chave também é validada explicitamente (existe, é
        # `str`, não vazia) antes de ser usada para ler
        # `current_app.config` - nunca um acesso direto por índice.
        url_config_key = spec.get("url_config_key")
        if not isinstance(url_config_key, str) or url_config_key == "":
            raise OrganizationProductInstallationError(
                "Configuração de URL do produto inválida."
            )

        canonical_url = current_app.config.get(url_config_key)

        if not isinstance(canonical_url, str) or canonical_url.strip() == "":
            raise OrganizationProductInstallationError(
                "Configuração de URL do produto inválida."
            )

        try:
            parts = urlsplit(canonical_url)
            hostname = parts.hostname
            port = parts.port
        except ValueError:
            raise OrganizationProductInstallationError(
                "Configuração de URL do produto inválida."
            )

        if (
            parts.scheme not in _ALLOWED_SCHEMES
            or not hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise OrganizationProductInstallationError(
                "Configuração de URL do produto inválida."
            )

        return hostname, port, parts.scheme

    @staticmethod
    def validate_installation_url(url, product_code):
        """Contrato mínimo e definitivo de uma URL de instalação (Issue
        #68) - reutilizado tanto pela escrita administrativa
        (`configure_installation`) quanto pelo uso
        (`ProductLaunchCodeService.issue_launch_code`). `product_code` é
        obrigatório e resolvido exclusivamente via `STRUCTURAL_PRODUCTS`
        - a API pública nunca aceita `allowed_hosts`/`url_config_key`
        vindos do chamador; a resolução do host canônico é sempre
        interna. Devolve a URL exatamente como recebida quando válida -
        nunca uma forma canônica reescrita (nenhuma normalização,
        mesmo internamente comparações usam cópias em minúsculas, nunca
        alterando o valor devolvido). Nenhuma mensagem de erro
        interpola `url`/`product_code` brutos."""
        if product_code not in _CANONICAL_PRODUCTS_BY_CODE:
            raise OrganizationProductInstallationError("Produto inválido.")

        if (
            not isinstance(url, str)
            or url == ""
            or len(url) > _MAX_URL_LENGTH
            or url != url.strip()
            or OrganizationProductInstallationService._has_control_characters(url)
            or "\\" in url
        ):
            raise OrganizationProductInstallationError("URL de instalação inválida.")

        try:
            parts = urlsplit(url)
            hostname = parts.hostname
            port = parts.port
        except ValueError:
            raise OrganizationProductInstallationError("URL de instalação inválida.")

        if (
            parts.scheme not in _ALLOWED_SCHEMES
            or not hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment != ""
            or parts.query != ""
            or not hostname.isascii()
            # Achado M1-3 da revisão técnica: espaço literal (`0x20`,
            # nunca capturado pela checagem de caracteres de controle -
            # espaço não é um controle) dentro do hostname já analisado
            # por `urlsplit` - nunca "corrigido" removendo o espaço,
            # sempre rejeitado. Mesma regra em qualquer ambiente
            # (produção ou não) - é uma checagem estrutural, não de
            # política de host por produto.
            or any(ch.isspace() for ch in hostname)
        ):
            raise OrganizationProductInstallationError("URL de instalação inválida.")

        is_production = bool(current_app.config.get("IS_PRODUCTION"))

        if parts.scheme == "http":
            if is_production or not OrganizationProductInstallationService._is_dev_http_host_allowed(hostname):
                raise OrganizationProductInstallationError("URL de instalação inválida.")

        if is_production:
            canonical_hostname, canonical_port, canonical_scheme = (
                OrganizationProductInstallationService._resolve_canonical_host(product_code)
            )
            candidate_host = hostname.lower()
            canonical_host = canonical_hostname.lower()

            if OrganizationProductInstallationService._looks_like_ip_literal(canonical_host):
                host_allowed = candidate_host == canonical_host
            else:
                host_allowed = candidate_host == canonical_host or candidate_host.endswith(
                    "." + canonical_host
                )

            if not host_allowed:
                raise OrganizationProductInstallationError(
                    "Host de instalação não autorizado para este produto."
                )

            effective_candidate_port = port if port is not None else (443 if parts.scheme == "https" else 80)
            effective_canonical_port = (
                canonical_port if canonical_port is not None else (443 if canonical_scheme == "https" else 80)
            )
            if effective_candidate_port != effective_canonical_port:
                raise OrganizationProductInstallationError(
                    "Host de instalação não autorizado para este produto."
                )

        return url

    @staticmethod
    def _resolve_organization_product(organization_id, product_code):
        """Resolve organização, produto canônico já persistido e o
        `OrganizationProduct` (assinatura) correspondente - nunca cria
        nenhum implicitamente. Mesmo padrão de resolução já usado por
        `AccessService._resolve_canonical_product`/`_apply_product_status`,
        reimplementado aqui (nunca importado de `AccessService`, que é
        uma preocupação de negócio diferente - status de assinatura, não
        instalação)."""
        organization = Organization.query.filter_by(id=organization_id).first()
        if organization is None:
            raise OrganizationProductInstallationError("Organização não encontrada.")

        if product_code not in _CANONICAL_PRODUCTS_BY_CODE:
            raise OrganizationProductInstallationError("Produto inválido.")

        product = Product.query.filter_by(code=product_code).first()
        if product is None:
            raise OrganizationProductInstallationError("Produto inválido.")

        org_product = OrganizationProduct.query.filter_by(
            organization_id=organization.id, product_id=product.id,
        ).first()
        if org_product is None:
            raise OrganizationProductInstallationError(
                "Este produto ainda não foi concedido a esta organização."
            )

        return organization, product, org_product

    @staticmethod
    def configure_installation(organization_id, product_code, url, actor_user_id):
        """Cria ou atualiza (upsert) a instalação de `product_code` para
        `organization_id` - uma única operação administrativa, nunca
        duas rotas/telas separadas para criar e editar. Permitida mesmo
        com `OrganizationProduct.status` `inactive`/`suspended`
        (preparação operacional) - nunca cria o `OrganizationProduct`
        implicitamente, exige que ele já exista. `product_code` usado na
        validação é sempre `product.code` (já resolvido do
        relacionamento persistido), nunca o parâmetro recebido sem
        revalidação. Reenviar exatamente a mesma URL já configurada é
        rejeitado como erro de domínio (mesmo padrão de "nenhuma
        transição para o mesmo estado", já usado por
        `OrganizationService.change_member_status`), sem auditoria
        artificial. Criação sempre começa com `is_active=False`
        (decisão de segurança registrada na Issue #68 - ativação é uma
        ação administrativa separada, ver `activate_installation`).
        Atualização de URL em uma instalação já existente NUNCA altera
        `is_active`, em nenhuma direção (preserva o estado exatamente
        como estava)."""
        try:
            organization, product, org_product = (
                OrganizationProductInstallationService._resolve_organization_product(
                    organization_id, product_code
                )
            )
            validated_url = OrganizationProductInstallationService.validate_installation_url(
                url, product.code
            )

            # Lock na linha do OrganizationProduct (mesmo padrão de
            # `InstallationCredentialService._lock_installation_row`) -
            # serializa criações concorrentes da mesma instalação; a
            # constraint única em `organization_product_id` permanece
            # como defesa adicional (ver `except IntegrityError`
            # abaixo), nunca o único controle.
            locked_org_product = OrganizationProduct.query.filter_by(
                id=org_product.id
            ).with_for_update().first()
            if locked_org_product is None:
                raise OrganizationProductInstallationError(
                    "Este produto ainda não foi concedido a esta organização."
                )

            installation = OrganizationProductInstallation.query.filter_by(
                organization_product_id=locked_org_product.id
            ).first()

            if installation is None:
                # Decisão de segurança (revisão técnica pós-implementação,
                # registrada na Issue #68): uma instalação recém-criada
                # começa explicitamente INATIVA - configurar a URL e
                # ativar o lançamento são ações administrativas
                # distintas; ativação exige `activate_installation`
                # depois. Não altera o default estrutural da coluna
                # (`is_active` continua `default=True` no model, para
                # qualquer outro caminho de inserção) - só este service
                # define o valor explicitamente.
                installation = OrganizationProductInstallation(
                    organization_product_id=locked_org_product.id,
                    url=validated_url,
                    is_active=False,
                )
                db.session.add(installation)
                db.session.flush()
                action = "organization_product_installation.created"
            else:
                if installation.url == validated_url:
                    raise OrganizationProductInstallationError(
                        "A URL informada já é a URL configurada para esta instalação."
                    )
                installation.url = validated_url
                action = "organization_product_installation.url_updated"

            AuditService.log_action(
                action,
                user_id=actor_user_id,
                organization_id=organization.id,
                resource_type="organization_product_installation",
                resource_id=installation.id,
                details={"product_code": product.code, "url": validated_url},
                commit=False,
            )

            db.session.commit()
        except OrganizationProductInstallationError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise OrganizationProductInstallationOperationError(
                "Não foi possível salvar a instalação. Nenhuma alteração foi salva."
            ) from exc
        except Exception as exc:
            db.session.rollback()
            raise OrganizationProductInstallationOperationError(
                "Não foi possível salvar a instalação. Nenhuma alteração foi salva."
            ) from exc

        return installation

    @staticmethod
    def _set_installation_active_state(organization_id, product_code, actor_user_id, new_is_active, action):
        """Núcleo transacional compartilhado por `activate_installation`/
        `deactivate_installation` - troca pura de `is_active`, nunca
        toca `url`/`public_id`/credenciais/códigos. Repetir a mesma
        transição para o estado já atual é rejeitada explicitamente
        como erro de domínio (mesmo padrão de
        `InstallationCredentialService.revoke_credential`), nunca um
        no-op silencioso."""
        try:
            organization, product, org_product = (
                OrganizationProductInstallationService._resolve_organization_product(
                    organization_id, product_code
                )
            )

            installation = OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).first()
            if installation is None:
                raise OrganizationProductInstallationError(
                    "Esta organização ainda não possui uma instalação configurada para este produto."
                )

            if installation.is_active == new_is_active:
                state_label = "ativa" if new_is_active else "inativa"
                raise OrganizationProductInstallationError(
                    f"Esta instalação já está {state_label}."
                )

            installation.is_active = new_is_active

            AuditService.log_action(
                action,
                user_id=actor_user_id,
                organization_id=organization.id,
                resource_type="organization_product_installation",
                resource_id=installation.id,
                details={"product_code": product.code},
                commit=False,
            )

            db.session.commit()
        except OrganizationProductInstallationError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise OrganizationProductInstallationOperationError(
                "Não foi possível alterar o estado da instalação. Nenhuma alteração foi salva."
            ) from exc

        return installation

    @staticmethod
    def activate_installation(organization_id, product_code, actor_user_id):
        return OrganizationProductInstallationService._set_installation_active_state(
            organization_id, product_code, actor_user_id,
            new_is_active=True,
            action="organization_product_installation.activated",
        )

    @staticmethod
    def deactivate_installation(organization_id, product_code, actor_user_id):
        return OrganizationProductInstallationService._set_installation_active_state(
            organization_id, product_code, actor_user_id,
            new_is_active=False,
            action="organization_product_installation.deactivated",
        )

    @staticmethod
    def list_installations_by_organization(organization_id):
        """Consulta somente leitura, usada pela tela administrativa
        (`org_details`) para exibir o estado de instalação de cada
        produto - nunca cria/altera nenhum registro. Duas queries
        (vínculos da organização + instalações correspondentes),
        combinadas em memória - mesmo padrão já usado por
        `AccessService.list_organization_products_for_admin`. Devolve um
        dicionário indexado por `product_id` (a chave que a tela já usa
        via `item.product.id`), nunca por `product_code` (evita uma
        conversão extra no template)."""
        org_products = OrganizationProduct.query.filter_by(
            organization_id=organization_id
        ).all()
        if not org_products:
            return {}

        org_product_ids = [op.id for op in org_products]
        installations = OrganizationProductInstallation.query.filter(
            OrganizationProductInstallation.organization_product_id.in_(org_product_ids)
        ).all()
        installations_by_org_product_id = {
            installation.organization_product_id: installation for installation in installations
        }

        result = {}
        for org_product in org_products:
            installation = installations_by_org_product_id.get(org_product.id)
            if installation is not None:
                result[org_product.product_id] = installation
        return result
