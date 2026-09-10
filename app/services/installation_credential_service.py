import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask import current_app

from ..extensions import db
from ..models import OrganizationProductInstallation, OrganizationProductInstallationCredential
from .audit_service import AuditService

# Entropia mínima do segredo (bytes lidos do CSPRNG antes da codificação
# URL-safe) - 32 bytes = 256 bits, equivalente ao já usado para
# `SECRET_KEY` de desenvolvimento (`secrets.token_hex(32)` em
# `app/config.py`), nunca menos.
_SECRET_ENTROPY_BYTES = 32


class InstallationCredentialError(ValueError):
    """Erro de domínio esperado e seguro (instalação inexistente,
    credencial já ativa, nenhuma credencial para rotacionar/revogar,
    etc.) - a mensagem já é curada para ser exibida diretamente ao
    operador, nunca contém detalhe de banco/driver nem o segredo. Continua
    sendo um `ValueError` (mesma compatibilidade já usada pelos demais
    services), mas nunca é a mesma classe usada para uma falha inesperada
    - ver `InstallationCredentialOperationError`. Mesmo padrão já
    estabelecido por `OrganizationError`/`OrganizationOperationError` e
    `ProductAccessError`/`ProductAccessOperationError`."""


class InstallationCredentialOperationError(ValueError):
    """Falha inesperada ao processar a operação (banco, driver, AuditLog,
    ou qualquer exceção não prevista, incluindo `IntegrityError` real de
    uma corrida que escapou do lock) - deliberadamente NÃO é subclasse de
    `InstallationCredentialError` (são classes irmãs). A mensagem pública
    é sempre genérica; a causa técnica real é preservada em `__cause__`
    via `raise ... from exc`, nunca exposta ao usuário e nunca contém o
    segredo/hash envolvido."""


# Issue #51 (CWE-208), mesmo princípio já usado em
# `AuthService._DUMMY_PASSWORD_HASH`: digest fixo, calculado uma única vez
# na importação deste módulo (nunca por chamada), usado para executar a
# mesma operação de comparação mesmo quando não há nenhuma credencial
# real candidata (public_id inexistente, instalação inativa, ou sem
# nenhuma credencial aceita) - elimina a assimetria de tempo entre esses
# casos e "segredo incorreto para uma credencial real existente". Gerado
# a partir de bytes aleatórios sintéticos, nunca uma credencial real; o
# resultado da comparação contra ele é sempre descartado.
_DUMMY_SECRET_HASH = hashlib.sha256(secrets.token_bytes(_SECRET_ENTROPY_BYTES)).hexdigest()


class InstallationCredentialService:
    @staticmethod
    def _normalize_installation_id(installation_id):
        """Normaliza `installation_id` para `uuid.UUID` antes de
        qualquer lock/consulta/mutação (Achado M1) - aceita um
        `uuid.UUID` já pronto ou uma string contendo um UUID válido,
        mesmo padrão já estabelecido por
        `AuthService.verify_email`/`resend_code`
        (`uuid.UUID(str(pending_id))`). Um valor inválido é sempre um
        erro de domínio (`InstallationCredentialError`), nunca uma
        falha operacional - a mensagem nunca inclui o valor bruto
        recebido (poderia ser qualquer objeto arbitrário). Captura
        `Exception` genérica (nunca `BaseException`): além de
        `ValueError`/`AttributeError`/`TypeError` da conversão UUID em
        si, cobre também qualquer exceção levantada por `str(value)`
        (ex.: um objeto cujo `__str__` está quebrado) - sem isso, tal
        entrada escaparia deste método e seria capturada pelo `except
        Exception` genérico do chamador, reclassificando incorretamente
        um erro de domínio como falha operacional (Achado B1)."""
        try:
            return uuid.UUID(str(installation_id))
        except Exception:
            raise InstallationCredentialError("Identificador de instalação inválido.")

    @staticmethod
    def _normalize_public_id_for_auth(public_id):
        """Normaliza `public_id` para autenticação (Achado A2) - mesma
        conversão de `_normalize_installation_id`, mas aqui uma entrada
        inválida (`None`, string vazia/não-UUID, int, list, dict ou
        qualquer outro objeto) NUNCA pode propagar exceção: retorna
        `None`, preservando o contrato de `authenticate_installation`
        de nunca lançar e nunca revelar se o valor recebido
        corresponde a um `public_id` existente. Captura `Exception`
        genérica (nunca `BaseException`): além de
        `ValueError`/`AttributeError`/`TypeError` da conversão UUID em
        si, cobre também qualquer exceção levantada por `str(public_id)`
        (ex.: um objeto cujo `__str__` está quebrado) - sem isso, tal
        entrada quebraria o contrato de `authenticate_installation`
        nunca propagar exceção (Achado B1)."""
        try:
            return uuid.UUID(str(public_id))
        except Exception:
            return None

    @staticmethod
    def _valid_presented_secret(presented_secret):
        """Só uma `str` não vazia é um candidato real de segredo
        (Achado A2). Deliberadamente NUNCA aplica `.strip()`: espaço em
        branco faz parte do valor apresentado e deve apenas causar
        falha de autenticação, nunca ser normalizado silenciosamente.
        `None`, `bytes`, `int`, `list`, `dict` e qualquer outro tipo
        são sempre rejeitados - não há decisão explícita e testada de
        aceitar `bytes` nesta versão."""
        return isinstance(presented_secret, str) and presented_secret != ""

    @staticmethod
    def _lock_installation_row(installation_id):
        """Bloqueia (`SELECT ... FOR UPDATE`) a linha de
        `OrganizationProductInstallation` correspondente, para serializar
        qualquer emissão/rotação/revogação concorrente de credenciais da
        MESMA instalação - mesmo padrão e mesma justificativa de
        `OrganizationService._lock_organization_row`. No PostgreSQL,
        bloqueia de verdade a segunda transação concorrente até a
        primeira commitar ou reverter. No SQLite (testes), não bloqueia
        de fato - os testes verificam que o lock É ADQUIRIDO (via
        spy/mock) e que as invariantes finais se sustentam, nunca que o
        bloqueio real de concorrência do PostgreSQL foi reproduzido.

        A constraint de banco (índice único parcial em
        `OrganizationProductInstallationCredential`) é defesa adicional,
        não o único controle de domínio - o lock é a proteção primária
        contra a corrida; a constraint garante a invariante mesmo se,
        por algum motivo, o lock não fosse adquirido.
        """
        return OrganizationProductInstallation.query.filter_by(id=installation_id).with_for_update().first()

    @staticmethod
    def _generate_secret():
        """CSPRNG, nunca `random`/`uuid.uuid4()`. `token_urlsafe` produz
        uma string sem caracteres que exijam escaping em header HTTP
        (`Authorization: Bearer <segredo>`). Independente de `public_id`,
        organização, produto, URL ou qualquer dado previsível - nenhuma
        derivação, só aleatoriedade pura."""
        return secrets.token_urlsafe(_SECRET_ENTROPY_BYTES)

    @staticmethod
    def _hash_secret(secret):
        """SHA-256 (hash rápido), não scrypt/PBKDF2: o segredo já tem
        256 bits de entropia própria (CSPRNG), então um hash lento não
        adiciona margem de segurança real contra força bruta - só custo
        de CPU a cada autenticação, potencialmente frequente (a cada
        lançamento de produto), diferente do código de e-mail (baixa
        entropia, poucas chamadas por usuário)."""
        return hashlib.sha256(secret.encode('utf-8')).hexdigest()

    @staticmethod
    def _accepted_credentials_query(installation_id, now):
        """Credenciais 'temporalmente aceitas' agora: `revoked_at IS
        NULL` (atual) OU `revoked_at > now` (anterior, ainda dentro da
        janela curta de rotação). Nunca inclui uma credencial com
        `revoked_at` no passado."""
        return OrganizationProductInstallationCredential.query.filter(
            OrganizationProductInstallationCredential.organization_product_installation_id == installation_id,
            db.or_(
                OrganizationProductInstallationCredential.revoked_at.is_(None),
                OrganizationProductInstallationCredential.revoked_at > now,
            ),
        )

    @staticmethod
    def issue_credential(installation_id, actor_user_id):
        """Emite a primeira credencial de uma instalação sem nenhuma
        credencial aceita no momento. Retorna o segredo em claro - a
        ÚNICA vez que ele existe fora da memória volátil desta chamada;
        o banco recebe somente o hash, na mesma transação do AuditLog."""
        try:
            installation_id = InstallationCredentialService._normalize_installation_id(installation_id)

            installation = InstallationCredentialService._lock_installation_row(installation_id)
            if installation is None:
                raise InstallationCredentialError("Instalação não encontrada.")

            now = datetime.now(timezone.utc)
            existing = InstallationCredentialService._accepted_credentials_query(installation.id, now).first()
            if existing is not None:
                raise InstallationCredentialError(
                    "Esta instalação já possui uma credencial aceita. Use a rotação para substituí-la."
                )

            secret = InstallationCredentialService._generate_secret()
            credential = OrganizationProductInstallationCredential(
                organization_product_installation_id=installation.id,
                secret_hash=InstallationCredentialService._hash_secret(secret),
            )
            db.session.add(credential)
            db.session.flush()

            AuditService.log_action(
                'organization_product_installation.credential_issued',
                user_id=actor_user_id,
                resource_type='organization_product_installation',
                resource_id=installation.id,
                details={'credential_id': str(credential.id)},
                commit=False,
            )

            db.session.commit()
        except InstallationCredentialError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise InstallationCredentialOperationError(
                "Não foi possível emitir a credencial. Nenhuma alteração foi salva."
            ) from exc

        return secret

    @staticmethod
    def rotate_credential(installation_id, actor_user_id):
        """Substitui a credencial atual por uma nova, mantendo a antiga
        aceita só durante uma janela curta
        (`Config.INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`).
        Encerra IMEDIATAMENTE qualquer janela de uma rotação anterior
        ainda aberta antes de abrir a nova - nunca mais de duas
        credenciais aceitas ao mesmo tempo (a nova atual + no máximo uma
        anterior em janela). Retorna o novo segredo em claro - a ÚNICA
        vez que ele existe fora desta chamada."""
        try:
            installation_id = InstallationCredentialService._normalize_installation_id(installation_id)

            installation = InstallationCredentialService._lock_installation_row(installation_id)
            if installation is None:
                raise InstallationCredentialError("Instalação não encontrada.")

            now = datetime.now(timezone.utc)
            current_credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id, revoked_at=None,
            ).first()
            if current_credential is None:
                raise InstallationCredentialError(
                    "Esta instalação não possui credencial ativa para rotacionar. Emita uma credencial primeiro."
                )

            # 1. Encerra imediatamente qualquer janela de uma rotação
            #    ANTERIOR a esta que ainda esteja aberta - nunca deixa
            #    acumular uma terceira credencial aceita. Nunca toca
            #    `current_credential` aqui (ainda não entrou em janela).
            db.session.query(OrganizationProductInstallationCredential).filter(
                OrganizationProductInstallationCredential.organization_product_installation_id == installation.id,
                OrganizationProductInstallationCredential.id != current_credential.id,
                OrganizationProductInstallationCredential.revoked_at.isnot(None),
                OrganizationProductInstallationCredential.revoked_at > now,
            ).update({'revoked_at': now}, synchronize_session=False)

            # 2. Agenda a revogação (curta, futura) da credencial atual.
            #    flush() explícito ANTES do passo 3: garante que esta
            #    UPDATE seja enviada ao banco antes do INSERT da nova
            #    credencial abaixo - sem isso, a ordem de flush do
            #    SQLAlchemy não é garantida, e o índice único parcial
            #    (`revoked_at IS NULL`) poderia ver as duas linhas
            #    simultaneamente dentro da mesma transação.
            grace_seconds = current_app.config.get('INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS', 300)
            current_credential.revoked_at = now + timedelta(seconds=grace_seconds)
            db.session.flush()

            # 3. Nova credencial atual.
            secret = InstallationCredentialService._generate_secret()
            new_credential = OrganizationProductInstallationCredential(
                organization_product_installation_id=installation.id,
                secret_hash=InstallationCredentialService._hash_secret(secret),
            )
            db.session.add(new_credential)
            db.session.flush()

            AuditService.log_action(
                'organization_product_installation.credential_rotated',
                user_id=actor_user_id,
                resource_type='organization_product_installation',
                resource_id=installation.id,
                details={
                    'previous_credential_id': str(current_credential.id),
                    'new_credential_id': str(new_credential.id),
                },
                commit=False,
            )

            db.session.commit()
        except InstallationCredentialError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise InstallationCredentialOperationError(
                "Não foi possível rotacionar a credencial. Nenhuma alteração foi salva."
            ) from exc

        return secret

    @staticmethod
    def revoke_credential(installation_id, actor_user_id):
        """Revoga IMEDIATAMENTE todas as credenciais ainda aceitas da
        instalação (a atual e qualquer uma em janela de rotação) - nunca
        exclui nenhuma linha, só marca `revoked_at = agora`. Repetir
        sobre uma instalação sem nenhuma credencial aceita é rejeitado
        como erro de domínio explícito (nunca um no-op silencioso, mesmo
        padrão de `OrganizationService.change_member_status` ao rejeitar
        transição para o mesmo estado). Nunca altera a instalação, a URL,
        `OrganizationProduct.status` ou qualquer dado de plano/quota."""
        try:
            installation_id = InstallationCredentialService._normalize_installation_id(installation_id)

            installation = InstallationCredentialService._lock_installation_row(installation_id)
            if installation is None:
                raise InstallationCredentialError("Instalação não encontrada.")

            now = datetime.now(timezone.utc)
            accepted = InstallationCredentialService._accepted_credentials_query(installation.id, now).all()
            if not accepted:
                raise InstallationCredentialError(
                    "Esta instalação não possui nenhuma credencial aceita para revogar."
                )

            revoked_ids = []
            for credential in accepted:
                credential.revoked_at = now
                revoked_ids.append(str(credential.id))
            db.session.flush()

            AuditService.log_action(
                'organization_product_installation.credential_revoked',
                user_id=actor_user_id,
                resource_type='organization_product_installation',
                resource_id=installation.id,
                details={'revoked_credential_ids': revoked_ids},
                commit=False,
            )

            db.session.commit()
        except InstallationCredentialError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise InstallationCredentialOperationError(
                "Não foi possível revogar a credencial. Nenhuma alteração foi salva."
            ) from exc

    @staticmethod
    def authenticate_installation(public_id, presented_secret):
        """Autentica uma instalação por `public_id` + segredo apresentado.

        Retorna a `OrganizationProductInstallation` autenticada em caso de
        sucesso, ou `None` em qualquer falha - a causa exata (`public_id`
        de formato inválido, `public_id` inexistente, instalação
        inativa, segredo de formato/tipo inválido, nenhuma credencial
        aceita, ou segredo incorreto) NUNCA é distinguível pelo
        chamador, nunca propaga exceção (Achado A2 - este é o contrato
        desta função, agora reforçado por normalização explícita e
        segura de `public_id` e `presented_secret`, nunca por deixar um
        `TypeError`/`AttributeError` escapar) e nunca é distinguível
        pelo tempo de resposta de forma óbvia/evitável (mesma mitigação
        de enumeração de `AuthService.authenticate`, Issue #51/CWE-208 -
        não é uma garantia matematicamente constante, só a ausência de
        uma distinção de timing óbvia entre "entrada inválida" e
        "segredo errado para um candidato existente"): quando não há
        nenhum candidato real, ainda assim executa uma comparação de
        tempo constante contra um hash fixo (`_DUMMY_SECRET_HASH`), com
        valores do mesmo tipo/tamanho de uma comparação real.

        `public_id` aceita um `uuid.UUID` já pronto ou uma string
        contendo um UUID válido; qualquer outro valor (`None`, string
        vazia/inválida, int, list, dict, outro objeto) é tratado como
        "nenhum candidato" - nunca lança, nunca revela se o valor
        corresponde a uma instalação existente. `presented_secret` só é
        tratado como candidato real quando é uma `str` não vazia;
        NUNCA sofre `.strip()` - espaço em branco faz parte do valor
        apresentado e deve simplesmente falhar a autenticação, nunca
        ser normalizado. Qualquer outro tipo (`None`, `bytes`, int,
        list, dict, ...) também é tratado como "nenhum candidato".

        Quando há mais de um candidato aceito (durante uma janela de
        rotação), compara contra TODOS, sem interromper no primeiro
        acerto - evita introduzir uma diferença de timing observável
        entre qual dos candidatos (o atual ou o anterior) combinou.

        Nunca cria `AuditLog`, nunca altera estado, nunca consulta ou
        autentica uma instalação diferente da identificada por
        `public_id`, e nunca inclui o `public_id`/segredo/hash
        recebidos em nenhum valor de retorno, exceção ou log.
        `public_id` sozinho nunca autentica - só identifica; sem uma
        credencial aceita cujo hash combine, a autenticação falha mesmo
        com o `public_id` correto. Não verifica usuário, vínculo,
        organização ou `OrganizationProduct.status` - isso pertence ao
        futuro endpoint de consumo do código descartável."""
        now = datetime.now(timezone.utc)

        valid_secret = InstallationCredentialService._valid_presented_secret(presented_secret)
        # O hash real só é calculável quando `presented_secret` é uma
        # `str` - para qualquer outro caso, usa o hash fixo desde já
        # (equivalente, em tipo/tamanho, ao que seria comparado no
        # caminho "sem candidato" logo abaixo).
        presented_hash = (
            InstallationCredentialService._hash_secret(presented_secret)
            if valid_secret
            else _DUMMY_SECRET_HASH
        )

        normalized_public_id = InstallationCredentialService._normalize_public_id_for_auth(public_id)
        if normalized_public_id is None:
            installation = None
        else:
            installation = OrganizationProductInstallation.query.filter_by(
                public_id=normalized_public_id
            ).first()

        if installation is None or not installation.is_active or not valid_secret:
            candidates = []
        else:
            candidates = InstallationCredentialService._accepted_credentials_query(installation.id, now).all()

        if not candidates:
            hmac.compare_digest(presented_hash, _DUMMY_SECRET_HASH)
            return None

        matched = False
        for credential in candidates:
            if hmac.compare_digest(presented_hash, credential.secret_hash):
                matched = True

        if not matched:
            return None

        return installation
