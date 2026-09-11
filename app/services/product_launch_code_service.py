import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from flask import current_app

from ..extensions import db
from ..models import (
    Organization,
    OrganizationMember,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    ProductLaunchCode,
    User,
)
from .audit_service import AuditService
from .bootstrap_service import STRUCTURAL_PRODUCTS
from .organization_service import OrganizationService

# Issue #65/#66: entropia do código opaco de lançamento - mesma constante
# de propósito (32 bytes = 256 bits) já usada para a credencial de
# instalação (Issue #64), mas deliberadamente uma constante PRÓPRIA deste
# módulo, nunca importada/derivada de `InstallationCredentialService` -
# código de lançamento e credencial de instalação são segredos
# independentes, gerados e girados em momentos diferentes por motivos
# diferentes.
_SECRET_ENTROPY_BYTES = 32

# Issue #27/#30: mesma fonte canônica única já usada por `AccessService`
# - nunca redeclarar códigos de produto aqui além desta derivação direta
# de `STRUCTURAL_PRODUCTS`.
_CANONICAL_PRODUCT_CODES = {spec["code"] for spec in STRUCTURAL_PRODUCTS}

# Limites aceitos para `PRODUCT_LAUNCH_CODE_TTL_SECONDS` (Issue #66) -
# validados aqui, no momento do uso, nunca em `app/config.py`: o mínimo
# (1s) exclui qualquer valor não positivo; o máximo (300s = 5 minutos) é
# uma ordem de grandeza acima do padrão (60s) e da faixa útil real
# (30-60s) - grande o bastante para nunca ser atingido por engano em uma
# configuração razoável, pequeno o bastante para que mesmo um erro grosseiro
# de configuração não crie uma janela de uso indevido de horas.
_MIN_TTL_SECONDS = 1
_MAX_TTL_SECONDS = 300


class ProductLaunchCodeError(ValueError):
    """Erro de domínio esperado e seguro (usuário/organização inexistente
    ou inativa, e-mail não verificado, vínculo ausente/inativo, produto
    não canônico, assinatura fora de `active`/`trial`, instalação
    inexistente/inativa/sem URL configurada, TTL configurado fora da
    faixa aceita, identificador malformado) - a mensagem já é curada para
    ser exibida diretamente ao usuário, nunca contém detalhe de banco/
    driver, nem o valor bruto recebido, nem o código/hash. Continua sendo
    um `ValueError` (mesma compatibilidade já usada pelos demais
    services), mas nunca é a mesma classe usada para uma falha inesperada
    - ver `ProductLaunchCodeOperationError`. Mesmo padrão já estabelecido
    por `OrganizationError`/`OrganizationOperationError`,
    `ProductAccessError`/`ProductAccessOperationError` e
    `InstallationCredentialError`/`InstallationCredentialOperationError`."""


class ProductLaunchCodeOperationError(ValueError):
    """Falha inesperada ao processar a emissão (banco, driver, AuditLog,
    colisão de `code_hash` extremamente improvável, ou qualquer exceção
    não prevista) - deliberadamente NÃO é subclasse de
    `ProductLaunchCodeError` (são classes irmãs). A mensagem pública é
    sempre genérica; a causa técnica real é preservada em `__cause__` via
    `raise ... from exc`, nunca exposta ao usuário e nunca contém o
    código/hash envolvido. Uma colisão de `code_hash` (violação da
    constraint única) sempre cai aqui - nunca uma segunda tentativa
    silenciosa de gerar outro código."""


@dataclass(frozen=True)
class ProductLaunchCodeIssuance:
    """Resultado de `issue_launch_code` - só os valores que a futura rota
    precisa para montar a resposta/redirect ao produto.

    `code` é o valor em claro, presente SOMENTE neste retorno - nunca
    persistido, nunca reconstruível a partir do banco (só `code_hash` é
    armazenado). `field(repr=False)` remove `code` do `repr()`/`str()`
    padrão gerado pelo `@dataclass` (que, por padrão, listaria TODOS os
    campos) - um `logger.info("...", result)` ingênuo em uma futura rota
    nunca imprime o segredo por acidente; `result.code` continua
    acessível normalmente para quem precisa do valor real. Nenhum
    `__repr__` customizado foi escrito - `field(repr=False)` já é
    suportado nativamente pelo `@dataclass` e não afeta `__eq__`
    (continua comparando todos os campos, `repr` ou não) nem a
    imutabilidade (`frozen=True`).

    `destination_url` é a URL EXATA da `OrganizationProductInstallation`
    já resolvida e validada pela própria cadeia de revalidação (nunca
    `Product.url`, nunca uma segunda consulta) - existe para que a
    futura rota não precise reconsultar/re-resolver a instalação só para
    descobrir para onde redirecionar, o que duplicaria a cadeia inteira
    de `issue_launch_code` e arriscaria (por divergência futura entre as
    duas implementações) redirecionar para uma instalação diferente
    daquela à qual o código foi realmente vinculado no banco. Não é
    segredo (mesmo status de `OrganizationProductInstallation.url` e
    `public_id`, Issue #63) - por isso não usa `repr=False`."""
    code: str = field(repr=False)
    destination_url: str
    expires_at: datetime


class ProductLaunchCodeService:
    @staticmethod
    def _normalize_uuid(value, error_message):
        """Normaliza `value` para `uuid.UUID` - aceita um `uuid.UUID` já
        pronto ou uma string contendo um UUID válido, mesmo padrão já
        estabelecido por `AuthService.verify_email`/`resend_code` e por
        `InstallationCredentialService._normalize_installation_id`
        (Issue #64, Achados M1/B1). Captura `Exception` genérica (nunca
        `BaseException`): cobre tanto `ValueError`/`TypeError` da
        conversão UUID em si quanto qualquer exceção levantada por
        `str(value)` (ex.: um objeto cujo `__str__` está quebrado). Um
        valor inválido é sempre `ProductLaunchCodeError`, nunca
        `ProductLaunchCodeOperationError` - a mensagem nunca inclui o
        valor bruto recebido (poderia ser qualquer objeto arbitrário)."""
        try:
            return uuid.UUID(str(value))
        except Exception:
            raise ProductLaunchCodeError(error_message)

    @staticmethod
    def _resolve_canonical_product(product_code):
        """Resolve um `Product` persistido a partir de um código do
        catálogo canônico (`STRUCTURAL_PRODUCTS`) - nunca aceita um
        `product_id` vindo do chamador. Rejeita, com a MESMA mensagem
        genérica (nunca interpola `product_code` bruto, diferente de
        `AccessService._resolve_canonical_product`, que é uma operação
        administrativa já auditada separadamente): tipo inesperado
        (`None`, não-string, string vazia); código fora do catálogo
        canônico; código canônico válido mas sem `Product` correspondente
        ainda persistido (bootstrap estrutural nunca executado) - do
        ponto de vista de quem chama a emissão, os três casos são
        igualmente "produto inválido", sem necessidade de distinguir a
        causa técnica exata."""
        if not isinstance(product_code, str) or product_code == '':
            raise ProductLaunchCodeError("Produto inválido.")
        if product_code not in _CANONICAL_PRODUCT_CODES:
            raise ProductLaunchCodeError("Produto inválido.")
        product = Product.query.filter_by(code=product_code).first()
        if product is None:
            raise ProductLaunchCodeError("Produto inválido.")
        return product

    @staticmethod
    def _resolve_ttl_seconds():
        """Lê `PRODUCT_LAUNCH_CODE_TTL_SECONDS` no momento do uso (mesmo
        padrão de `InstallationCredentialService.rotate_credential` para
        `INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`) e valida a
        faixa aceita (Issue #66) - nunca em `app/config.py`. Um valor não
        numérico no ambiente já falha na importação de `app/config.py`
        (`int(os.getenv(...))`, mesmo comportamento de todas as configs
        semelhantes), então nunca chega aqui. Aqui, rejeita explicitamente:
        tipo diferente de `int` (inclui `bool` - `isinstance(True, int)`
        é `True` em Python, então a checagem usa `type(...) is int`,
        nunca `isinstance`, para nunca aceitar um booleano como inteiro
        válido); zero; negativo; acima do limite superior. Nunca corrige
        silenciosamente (clamp) nem usa um valor de fallback - configuração
        inválida é sempre um erro de domínio explícito, e a mensagem nunca
        expõe o valor configurado."""
        ttl = current_app.config.get('PRODUCT_LAUNCH_CODE_TTL_SECONDS', 60)
        if type(ttl) is not int or ttl < _MIN_TTL_SECONDS or ttl > _MAX_TTL_SECONDS:
            raise ProductLaunchCodeError("Configuração de validade do código inválida.")
        return ttl

    @staticmethod
    def _generate_code():
        """CSPRNG, nunca `random`/`uuid.uuid4()` - mesmo raciocínio de
        `InstallationCredentialService._generate_secret`, mas com
        constante de entropia própria deste módulo."""
        return secrets.token_urlsafe(_SECRET_ENTROPY_BYTES)

    @staticmethod
    def _hash_code(code):
        return hashlib.sha256(code.encode('utf-8')).hexdigest()

    @staticmethod
    def issue_launch_code(user_id, organization_id, product_code):
        """Revalida integralmente a cadeia de autorização
        `User -> OrganizationMember -> Organization -> OrganizationProduct
        -> OrganizationProductInstallation` e, somente se todos os passos
        forem satisfeitos, emite um novo código de lançamento (Issue #65)
        para a instalação resolvida.

        `organization_id` e `product_code` compõem a assinatura, mas
        NUNCA são tratados como confiáveis por si só - toda a cadeia é
        consultada e comparada no banco a cada chamada, nunca cacheada
        (ver Issue #66, revisão de especificação: a mesma chamada, para o
        mesmo usuário/instalação, deve falhar imediatamente se a
        organização for desativada entre duas emissões).

        Nenhum código/hash é gerado antes de TODA a cadeia de
        revalidação (usuário existente/ativo/verificado; organização
        existente/ativa; vínculo pertencente exatamente a este par
        usuário/organização e `active`; produto canônico; assinatura
        `active`/`trial`; instalação pertencente exatamente a este
        `OrganizationProduct` e ativa; URL da instalação não vazia; TTL
        configurado válido) ser confirmada - qualquer rejeição interrompe
        a operação sem criar `ProductLaunchCode`, sem `AuditLog`, sem
        gerar nenhum material sensível.

        Múltiplos códigos pendentes para o mesmo usuário/instalação são
        permitidos e nunca invalidados por uma nova emissão (mesma
        decisão já registrada na Issue #65 para o schema). Nenhum lock
        pessimista é usado - não existe invariante de unicidade que uma
        corrida concorrente possa violar aqui (diferente de
        `InstallationCredentialService.issue_credential`, que protege
        "no máximo uma credencial ativa").

        Retorna um `ProductLaunchCodeIssuance` (código em claro,
        `destination_url` e `expires_at`) - o código em claro só existe
        neste retorno; o banco recebe somente o hash, na mesma transação
        do `AuditLog`. `destination_url` é exatamente `installation.url`
        já resolvida e validada acima (nunca `Product.url`, nunca uma
        segunda consulta) - a futura rota usa este valor diretamente
        para o redirect, sem precisar reconsultar/re-resolver a cadeia."""
        try:
            normalized_user_id = ProductLaunchCodeService._normalize_uuid(
                user_id, "Identificador de usuário inválido."
            )
            normalized_organization_id = ProductLaunchCodeService._normalize_uuid(
                organization_id, "Identificador de organização inválido."
            )

            user = User.query.get(normalized_user_id)
            if user is None:
                raise ProductLaunchCodeError("Usuário não encontrado.")
            if not user.is_active:
                raise ProductLaunchCodeError("Usuário inativo.")
            if user.email_verified_at is None:
                raise ProductLaunchCodeError("E-mail do usuário não verificado.")

            organization = Organization.query.get(normalized_organization_id)
            if organization is None:
                raise ProductLaunchCodeError("Organização não encontrada.")
            if not organization.is_active:
                raise ProductLaunchCodeError("Organização inativa.")

            membership = OrganizationService.get_active_membership(
                normalized_user_id, normalized_organization_id
            )
            if membership is None:
                raise ProductLaunchCodeError(
                    "Usuário não possui vínculo ativo com esta organização."
                )

            product = ProductLaunchCodeService._resolve_canonical_product(product_code)

            org_product = OrganizationProduct.query.filter_by(
                organization_id=organization.id, product_id=product.id,
            ).first()
            if org_product is None or org_product.status not in ('active', 'trial'):
                raise ProductLaunchCodeError(
                    "Esta organização não possui assinatura ativa para este produto."
                )

            installation = OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id,
            ).first()
            if installation is None:
                raise ProductLaunchCodeError(
                    "Nenhuma instalação encontrada para este produto."
                )
            if not installation.is_active:
                raise ProductLaunchCodeError("Instalação inativa.")
            if installation.url is None or installation.url.strip() == '':
                raise ProductLaunchCodeError("Instalação sem URL configurada.")
            # Capturado em variável local ANTES do commit (que expira
            # todos os objetos da sessão por padrão) - evita uma consulta
            # extra de refresh só para reler `installation.url` depois do
            # `commit()` abaixo, sem nunca reconsultar/re-resolver a
            # instalação. Nunca modificado (nenhum `.strip()` aqui - essa
            # normalização já ocorreu só para a checagem de vazio acima).
            destination_url = installation.url

            ttl_seconds = ProductLaunchCodeService._resolve_ttl_seconds()

            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=ttl_seconds)

            code = ProductLaunchCodeService._generate_code()
            launch_code = ProductLaunchCode(
                code_hash=ProductLaunchCodeService._hash_code(code),
                user_id=user.id,
                organization_product_installation_id=installation.id,
                expires_at=expires_at,
            )
            db.session.add(launch_code)
            db.session.flush()

            AuditService.log_action(
                'organization_product_installation.launch_code_issued',
                user_id=user.id,
                organization_id=organization.id,
                resource_type='organization_product_installation',
                resource_id=installation.id,
                details={'launch_code_id': str(launch_code.id)},
                commit=False,
            )

            db.session.commit()
        except ProductLaunchCodeError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise ProductLaunchCodeOperationError(
                "Não foi possível emitir o código de lançamento. Nenhuma alteração foi salva."
            ) from exc

        return ProductLaunchCodeIssuance(
            code=code, destination_url=destination_url, expires_at=expires_at
        )
