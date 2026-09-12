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
from .installation_credential_service import InstallationCredentialService
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

# Issue #67: tetos de tamanho para os valores apresentados no consumo -
# aplicados ANTES de qualquer hash/consulta, tanto no service (defesa
# própria, para qualquer chamador direto) quanto na rota HTTP (que
# valida a estrutura da requisição antes mesmo de chamar o service).
# Nenhum é um tamanho exato: `installation_secret`/`presented_code` têm
# hoje 43 caracteres (`secrets.token_urlsafe(32)`), mas um teto
# generoso (256) evita acoplar o contrato a esse tamanho específico,
# permitindo aumentar a entropia no futuro sem quebrar compatibilidade.
# `installation_public_id` tem folga sobre os 36 de um UUID canônico -
# o FORMATO exato é responsabilidade de `authenticate_installation`
# (nunca lança para um valor malformado), não deste teto.
_MAX_INSTALLATION_PUBLIC_ID_LENGTH = 64
_MAX_INSTALLATION_SECRET_LENGTH = 256
_MAX_PRESENTED_CODE_LENGTH = 256

# Issue #67: contrato mínimo de autorização devolvido no consumo -
# `_CONTRACT_VERSION` é incrementado só em mudança incompatível de
# formato; `_CONTRACT_ISSUER` é uma constante estável e documentada
# (nunca derivada de configuração/ambiente), identificando o HUB como
# emissor da autorização perante o produto consumidor.
_CONTRACT_VERSION = 1
_CONTRACT_ISSUER = "hub.licilink"


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


class ProductLaunchCodeAuthorizationRevoked(ProductLaunchCodeError):
    """Issue #67, Caminho C: o código foi reivindicado atomicamente
    (`consumed_at` já commitado - ver `consume_launch_code`), mas a
    revalidação da cadeia de autorização (usuário/organização/vínculo/
    assinatura/instalação), feita IMEDIATAMENTE após a reivindicação,
    falhou. É deliberadamente uma SUBCLASSE de `ProductLaunchCodeError`
    (nunca uma classe irmã nova) - isso permite que a camada HTTP trate
    as duas com o mesmo `except ProductLaunchCodeError`, sempre `401`,
    sem nenhum caso especial na rota; ao mesmo tempo, dentro do próprio
    `consume_launch_code`, um `except` específico para esta subclasse,
    posicionado ANTES do `except ProductLaunchCodeError` genérico,
    nunca chama `db.session.rollback()` - o `commit()` da queima já
    aconteceu antes desta exceção ser levantada, então não há nada
    pendente para reverter. Nunca cai em `ProductLaunchCodeOperationError`
    - não é uma falha operacional, é uma decisão de autorização (negar),
    só que tomada depois que o código já havia sido consumido."""


@dataclass(frozen=True)
class ProductLaunchAuthorization:
    """Resultado de `consume_launch_code` - contrato mínimo de
    identidade/autorização exigido pela arquitetura #62 (seção "Contrato
    mínimo"), devolvido ao produto após o consumo bem-sucedido do
    código. Todos os campos já são tipos nativos de JSON (UUIDs já
    convertidos para `str` pelo service, nunca `uuid.UUID` cru) -
    `dataclasses.asdict()` deste objeto é diretamente serializável.

    Nenhum campo carrega código, hash ou segredo - por isso nenhum
    campo usa `field(repr=False)` (diferente de `ProductLaunchCodeIssuance.code`);
    isso é uma confirmação explícita, não uma omissão.

    `role` é estritamente informativo - o HUB nunca deriva nem concede
    nenhuma permissão Django a partir dele; a decisão sobre o que fazer
    com esse valor pertence inteiramente ao repositório do GEDO (fora do
    escopo desta Issue).

    Deliberadamente NÃO inclui `organization_product_status`,
    `issued_at` nem `expires_at` (decisão de minimização da Issue #67):
    a decisão de autorização já é definitiva quando esta dataclass é
    construída (o código já foi consumido); `active`/`trial` concedem o
    mesmo acesso nesta versão; a janela de sessão do GEDO é calculada a
    partir do momento do consumo, não da emissão original do código."""
    contract_version: int
    issuer: str
    authorization_id: str
    sub: str
    email: str
    name: str
    organization_id: str
    product_code: str
    installation_public_id: str
    role: str


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
    def _validate_bounded_string(value, max_length, error_message):
        """Valida `value` como `str` não vazia e dentro do teto de
        tamanho (Issue #67) - ANTES de qualquer hash/consulta. Rejeita
        `None`, booleano, número, lista, dict ou qualquer outro tipo
        (não apenas não-string). NUNCA aplica `.strip()`: uma string
        composta só de espaços passa por esta validação (é uma `str`
        não vazia dentro do teto) e é rejeitada mais adiante, de forma
        natural, por nunca corresponder a um hash real - nunca por uma
        normalização silenciosa aqui. A mensagem nunca inclui o valor
        bruto recebido nem seu tamanho real."""
        if not isinstance(value, str) or value == '' or len(value) > max_length:
            raise ProductLaunchCodeError(error_message)
        return value

    @staticmethod
    def consume_launch_code(installation_public_id, installation_secret, presented_code):
        """Autentica a instalação apresentada e consome atomicamente um
        código de lançamento (Issue #65/#66) em nome dela, devolvendo o
        contrato mínimo de autorização (`ProductLaunchAuthorization`) da
        arquitetura #62.

        Recebe SOMENTE valores apresentados pelo chamador - nunca uma
        instalação "já confiável": a autenticação
        (`InstallationCredentialService.authenticate_installation`,
        Issue #64, reutilizada sem nenhuma alteração) é sempre executada
        internamente, como o primeiro passo de fato (depois só da
        validação de tipo/tamanho, mais barata). Não existe caminho para
        um chamador contornar a credencial passando um objeto de
        instalação já resolvido.

        Ordem obrigatória: 1) validar tipo/tamanho dos três valores
        apresentados; 2) autenticar a instalação; 3) só então calcular o
        SHA-256 do código apresentado; 4) consumir atomicamente o código
        vinculado exatamente a esta instalação já autenticada.

        Três caminhos de rejeição, todos externamente indistinguíveis
        (mesma `ProductLaunchCodeError`, mesma resposta HTTP `401` na
        camada de rota):

        - Caminho A (credencial inválida): `authenticate_installation`
          retorna `None` - nenhuma busca ou consumo de código ocorre,
          nenhuma mutação, nenhuma auditoria.
        - Caminho B (código não elegível): a atualização condicional
          (`UPDATE ... WHERE code_hash = ... AND
          organization_product_installation_id = ... AND consumed_at IS
          NULL AND expires_at > now`) afeta zero linhas - cobre código
          inexistente, expirado, já consumido, ou emitido para outra
          instalação, todos com a MESMA rejeição, sem nenhuma nova
          mutação nem auditoria.
        - Caminho C (autorização revogada após a reivindicação): o
          código foi reivindicado (`rowcount == 1`), mas a revalidação
          subsequente (usuário/organização/vínculo/assinatura/
          instalação) falha. `consumed_at` é COMMITADO mesmo assim -
          nunca revertido, nunca deixado pendente para uma nova
          tentativa - e `ProductLaunchCodeAuthorizationRevoked` é
          levantada sem `rollback()` (ver a exceção). Nenhuma auditoria
          de sucesso, nenhuma identidade devolvida.

        Duas requisições concorrentes com o mesmo código: a atualização
        condicional do Caminho B/reivindicação é uma única instrução
        atômica, serializada pelo próprio SGBD sobre a mesma linha -
        garantidamente, no máximo uma das duas obtém `rowcount == 1`
        (nunca `> 1`, `code_hash` é `unique`); a outra observa
        `rowcount == 0` e cai no Caminho B, com a mesma resposta
        genérica de qualquer outro código inválido.

        Auditoria (`organization_product_installation.launch_code_consumed`)
        e o consumo compartilham a MESMA transação - exatamente um
        evento por consumo vencedor E autorizado; nenhum evento em
        qualquer rejeição (A, B ou C). Falha ao gravar a auditoria ou no
        commit final reverte o consumo inteiro (`rollback()`,
        `ProductLaunchCodeOperationError`) - `consumed_at` volta a
        `NULL`, nenhuma auditoria parcial sobrevive."""
        try:
            installation_public_id = ProductLaunchCodeService._validate_bounded_string(
                installation_public_id, _MAX_INSTALLATION_PUBLIC_ID_LENGTH,
                "Credencial de instalação inválida."
            )
            installation_secret = ProductLaunchCodeService._validate_bounded_string(
                installation_secret, _MAX_INSTALLATION_SECRET_LENGTH,
                "Credencial de instalação inválida."
            )
            presented_code = ProductLaunchCodeService._validate_bounded_string(
                presented_code, _MAX_PRESENTED_CODE_LENGTH,
                "Código de lançamento inválido."
            )

            installation = InstallationCredentialService.authenticate_installation(
                installation_public_id, installation_secret
            )
            if installation is None:
                raise ProductLaunchCodeError("Credencial de instalação inválida.")

            now = datetime.now(timezone.utc)
            code_hash = ProductLaunchCodeService._hash_code(presented_code)

            # Caminho B / reivindicação: única instrução atômica -
            # "verificar e marcar" é logicamente uma única operação,
            # então não há passo de bloqueio explícito separado (ao
            # contrário de `InstallationCredentialService.issue_credential`,
            # que protege um invariante que abrange múltiplos passos).
            # `synchronize_session=False`: bulk update em SQL puro, sem
            # tocar o identity map da sessão - a leitura seguinte
            # (poucas linhas abaixo) é sempre uma consulta nova de
            # verdade, nunca um objeto reaproveitado.
            rowcount = db.session.query(ProductLaunchCode).filter(
                ProductLaunchCode.code_hash == code_hash,
                ProductLaunchCode.organization_product_installation_id == installation.id,
                ProductLaunchCode.consumed_at.is_(None),
                ProductLaunchCode.expires_at > now,
            ).update({'consumed_at': now}, synchronize_session=False)

            if rowcount == 0:
                raise ProductLaunchCodeError("Código de lançamento inválido.")

            # A partir daqui, esta linha pertence exclusivamente a esta
            # requisição, dentro da transação atual - `code_hash` é
            # `unique`, então esta consulta nunca traz outra linha.
            launch_code = ProductLaunchCode.query.filter_by(code_hash=code_hash).first()

            # Revalidação completa, cada checagem curto-circuitando a
            # próxima para nunca desreferenciar um valor ausente - só a
            # partir de dados já resolvidos no banco (nunca de qualquer
            # dado apresentado pelo chamador além da credencial/código já
            # validados acima).
            authorization_revoked = False

            user = User.query.get(launch_code.user_id)
            if user is None or not user.is_active or user.email_verified_at is None:
                authorization_revoked = True

            org_product = None
            organization = None
            membership = None
            product = None

            if not authorization_revoked:
                org_product = OrganizationProduct.query.get(installation.organization_product_id)
                if org_product is None or org_product.status not in ('active', 'trial'):
                    authorization_revoked = True

            if not authorization_revoked:
                organization = Organization.query.get(org_product.organization_id)
                if organization is None or not organization.is_active:
                    authorization_revoked = True

            if not authorization_revoked:
                membership = OrganizationService.get_active_membership(user.id, organization.id)
                if membership is None:
                    authorization_revoked = True

            # Redundante com a autenticação já feita acima (que já exige
            # instalação ativa), mas revalidado explicitamente aqui - o
            # estado pode, em teoria, mudar dentro da mesma requisição,
            # mesmo padrão de paranoia já usado em #66.
            if not authorization_revoked and not installation.is_active:
                authorization_revoked = True

            if not authorization_revoked:
                product = Product.query.get(org_product.product_id)
                if product is None:
                    authorization_revoked = True

            if authorization_revoked:
                # Caminho C: commit AGORA (só a queima de `consumed_at`
                # feita acima - nenhum `AuditLog` foi adicionado à sessão
                # neste caminho), depois levanta a exceção dedicada, que
                # nunca aciona rollback (ver `ProductLaunchCodeAuthorizationRevoked`).
                db.session.commit()
                raise ProductLaunchCodeAuthorizationRevoked(
                    "Não foi possível concluir a autorização. Nenhuma identidade foi liberada."
                )

            authorization = ProductLaunchAuthorization(
                contract_version=_CONTRACT_VERSION,
                issuer=_CONTRACT_ISSUER,
                authorization_id=str(launch_code.id),
                sub=str(user.id),
                email=user.email,
                name=user.name,
                organization_id=str(organization.id),
                product_code=product.code,
                installation_public_id=str(installation.public_id),
                role=membership.role.name,
            )

            AuditService.log_action(
                'organization_product_installation.launch_code_consumed',
                user_id=user.id,
                organization_id=organization.id,
                resource_type='organization_product_installation',
                resource_id=installation.id,
                details={'launch_code_id': str(launch_code.id)},
                commit=False,
            )

            db.session.commit()
        except ProductLaunchCodeAuthorizationRevoked:
            # O commit da queima (Caminho C) já aconteceu antes desta
            # exceção ser levantada, alguns parágrafos acima - nunca
            # `rollback()` aqui, que desfaria apenas a transação ATUAL
            # (já vazia/nova neste ponto), nunca a anterior já commitada.
            raise
        except ProductLaunchCodeError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise ProductLaunchCodeOperationError(
                "Não foi possível consumir o código de lançamento. Nenhuma alteração foi salva."
            ) from exc

        return authorization

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
