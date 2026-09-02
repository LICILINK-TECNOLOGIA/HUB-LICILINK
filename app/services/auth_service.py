import werkzeug.security
import secrets
from datetime import datetime, timezone, timedelta
from flask import current_app
from ..extensions import db
from ..models import User, PendingEmailVerification
from .organization_service import OrganizationService
from .audit_service import AuditService
from .email import EmailService


class AuthError(ValueError):
    """Erro de domínio esperado e seguro (e-mail já em uso, registro
    pendente não encontrado, código inválido/expirado, cooldown de
    reenvio, etc.) - a mensagem já é curada para ser exibida diretamente
    ao operador, nunca contém detalhe de banco/driver. Continua sendo um
    `ValueError` (compatibilidade com `pytest.raises(ValueError)` já
    usado pelos chamadores existentes), mas nunca é a mesma classe usada
    para uma falha inesperada - ver `AuthOperationError`."""


class AuthOperationError(ValueError):
    """Falha inesperada ao processar a operação (banco, driver, AuditLog,
    envio de e-mail, ou qualquer exceção não prevista) - deliberadamente
    NÃO é subclasse de `AuthError` (são classes irmãs), para que uma
    rota consiga capturar uma sem capturar a outra. A mensagem pública
    desta exceção é sempre genérica/pré-definida; a causa técnica real é
    preservada em `__cause__` via `raise ... from exc`, nunca exposta ao
    usuário - só para quem inspecionar/logar a exceção no servidor (ver
    `app/blueprints/auth.py`). Mesmo padrão já estabelecido por
    `OrganizationError`/`OrganizationOperationError` em
    `organization_service.py` e `ProductAccessError`/
    `ProductAccessOperationError` em `access_service.py` - nunca
    reaproveitadas diretamente, cada domínio tem seu próprio par."""


class AuthService:
    @staticmethod
    def start_registration(name, email, password):
        # 1. Normalizar e-mail
        email = email.lower().strip()
        
        # 2. Verificar se já existe usuário
        if User.query.filter_by(email=email).first():
            # Não revelar a existência diretamente se quisermos evitar enumeração,
            # mas no admin podemos ou levantar erro genérico.
            # O plano pede para verificar se existe User.
            raise AuthError("E-mail já está em uso ou é inválido.")
            
        # 3. Gerar código e hash (hash de uso único do código de verificação
        # por e-mail - propósito diferente do hash de senha, permanece
        # independente do mecanismo de senha do usuário)
        code = str(secrets.randbelow(900000) + 100000)
        code_hash = werkzeug.security.generate_password_hash(code)

        # 4. Gerar hash da senha definitiva do usuário pelo mesmo mecanismo
        # centralizado usado por User.set_password(). User.hash_password()
        # já valida a força da senha internamente - não há como contornar
        # essa validação chamando-a diretamente. Ainda não existe um User
        # neste ponto (só é criado após a verificação do e-mail), por isso o
        # hash é calculado aqui e guardado, já hasheado, em
        # PendingEmailVerification - a senha em texto puro nunca chega a ser
        # persistida, nem mesmo temporariamente.
        password_hash = User.hash_password(password)
        
        # 5. Calcular expiração
        ttl = current_app.config.get('VERIFICATION_CODE_TTL', 600)
        
        # Precisamos de datetime ingênuo ou aware? O BaseModel atual usa naive utcnow()
        # Vamos usar datetime.utcnow() para ser compatível com as datas dos outros models
        expires_at = datetime.utcnow() + timedelta(seconds=ttl)
        
        # 6. Atualizar ou criar pendência
        pending = PendingEmailVerification.query.filter_by(email=email).first()
        if not pending:
            pending = PendingEmailVerification(email=email)
            db.session.add(pending)
            
        pending.name = name
        pending.password_hash = password_hash
        pending.verification_code_hash = code_hash
        pending.expires_at = expires_at
        pending.attempts = 0
        pending.last_sent_at = datetime.utcnow()
        pending.resend_count = 0
        pending.verified_at = None

        # Issue #45: pending e AuditLog na MESMA transação - flush() para
        # obter pending.id quando é um registro novo (necessário para o
        # AuditLog), AuditLog com commit=False, um único commit final.
        # Antes, eram dois commits separados; se o segundo (AuditLog)
        # falhasse, o pending já estava persistido mas o chamador via uma
        # falha genérica sem saber que o registro tinha sido criado.
        try:
            db.session.flush()

            AuditService.log_action(
                'user.registration.started',
                resource_type='pending_registration',
                resource_id=pending.id,
                commit=False,
            )

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise AuthOperationError(
                "Não foi possível processar o registro. Tente novamente."
            ) from exc

        # 7. Enviar e-mail - só depois do commit acima já confirmado. Uma
        # falha aqui NÃO reverte (nem tenta reverter) a transação já
        # commitada: pending e AuditLog permanecem persistidos - o
        # registro pendente continua válido e pode ser reenviado via
        # resend_code (ver limite de atomicidade banco/e-mail da Issue
        # #45 - envio de e-mail é um efeito externo, fora da transação
        # SQL).
        try:
            EmailService().send_verification_email(to=email, name=name, code=code)
        except Exception as exc:
            raise AuthOperationError(
                "Não foi possível enviar o código de confirmação. Tente novamente."
            ) from exc

        return pending

    @staticmethod
    def verify_email(pending_id, code):
        import uuid
        pending_uuid = uuid.UUID(str(pending_id))
        pending = PendingEmailVerification.query.get(pending_uuid)
        if not pending:
            raise AuthError("Registro pendente não encontrado.")

        if pending.verified_at:
            raise AuthError("E-mail já verificado.")

        if datetime.utcnow() > pending.expires_at:
            raise AuthError("O código expirou. Solicite um novo código.")

        max_attempts = current_app.config.get('VERIFICATION_MAX_ATTEMPTS', 5)
        if pending.attempts >= max_attempts:
            raise AuthError("Número máximo de tentativas atingido. Solicite um novo código.")

        if not werkzeug.security.check_password_hash(pending.verification_code_hash, code):
            # Issue #45: incremento de `attempts` e AuditLog na MESMA
            # transação (commit=False + commit único). O AuthError de
            # domínio ("Código inválido.") só é levantado DEPOIS do
            # commit ter sucesso, fora do bloco que trata falha
            # operacional - nunca é capturado/reembalado como
            # AuthOperationError.
            try:
                pending.attempts += 1

                AuditService.log_action(
                    'user.email_verification.failed',
                    resource_type='pending_registration',
                    resource_id=pending.id,
                    commit=False,
                )

                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                raise AuthOperationError(
                    "Não foi possível registrar a tentativa de verificação. Tente novamente."
                ) from exc

            raise AuthError("Código inválido.")

        # Sucesso - User, pending.verified_at e AuditLog na MESMA
        # transação (flush() para obter user.id, AuditLog com
        # commit=False, um único commit final). Antes, o AuditLog era um
        # segundo commit separado; uma falha isolada nele, depois do
        # commit principal já ter sucesso, fazia este método relatar
        # falha mesmo com a conta já criada e verificada (Issue #45).
        try:
            # pending.password_hash já foi calculado por User.hash_password()
            # em start_registration - copiado diretamente aqui (sem chamar
            # set_password() novamente, o que geraria um hash-do-hash) pois a
            # senha em texto puro nunca foi persistida e não está mais
            # disponível neste ponto do fluxo.
            user = User(
                name=pending.name,
                email=pending.email,
                password_hash=pending.password_hash,
                email_verified_at=datetime.utcnow()
            )
            db.session.add(user)

            pending.verified_at = datetime.utcnow()

            db.session.flush()

            AuditService.log_action(
                'user.email_verified',
                user_id=user.id,
                commit=False,
            )

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise AuthOperationError("Erro ao criar usuário.") from exc

        return user

    @staticmethod
    def resend_code(pending_id):
        import uuid
        pending_uuid = uuid.UUID(str(pending_id))
        pending = PendingEmailVerification.query.get(pending_uuid)
        if not pending:
            raise AuthError("Registro pendente não encontrado.")

        if pending.verified_at:
            raise AuthError("E-mail já verificado.")

        cooldown = current_app.config.get('VERIFICATION_RESEND_COOLDOWN', 60)
        if pending.last_sent_at and datetime.utcnow() < pending.last_sent_at + timedelta(seconds=cooldown):
            raise AuthError("Aguarde antes de solicitar um novo código.")

        max_resends = current_app.config.get('VERIFICATION_MAX_RESENDS', 5)
        if pending.resend_count >= max_resends:
            raise AuthError("Limite de reenvios atingido.")

        code = str(secrets.randbelow(900000) + 100000)
        code_hash = werkzeug.security.generate_password_hash(code)

        ttl = current_app.config.get('VERIFICATION_CODE_TTL', 600)
        expires_at = datetime.utcnow() + timedelta(seconds=ttl)

        pending.verification_code_hash = code_hash
        pending.expires_at = expires_at
        pending.attempts = 0
        pending.last_sent_at = datetime.utcnow()
        pending.resend_count += 1

        # Issue #45: mutação do pending e AuditLog na MESMA transação
        # (commit=False + commit único) - antes, eram dois commits
        # separados, sem nenhum try/except ao redor de nenhum dos dois.
        try:
            AuditService.log_action(
                'user.email_verification.resent',
                resource_type='pending_registration',
                resource_id=pending.id,
                commit=False,
            )

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise AuthOperationError(
                "Não foi possível reenviar o código de confirmação. Tente novamente."
            ) from exc

        # Envio só depois do commit acima já confirmado. Uma falha aqui
        # NÃO reverte (nem tenta reverter) a transação já commitada: o
        # novo hash e o AuditLog já estão persistidos, o código anterior
        # já não funciona mais, mesmo que o e-mail não tenha sido
        # entregue (mesmo limite de atomicidade banco/e-mail documentado
        # na Issue #45).
        try:
            EmailService().send_verification_email(to=pending.email, name=pending.name, code=code)
        except Exception as exc:
            raise AuthOperationError(
                "Não foi possível reenviar o código de confirmação. Tente novamente."
            ) from exc

    @staticmethod
    def authenticate(email, password):
        email = email.lower().strip()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if user.email_verified_at is None:
                raise ValueError("E-mail não verificado.")
            AuditService.log_action('user_login', user_id=user.id)
            return user
        return None
