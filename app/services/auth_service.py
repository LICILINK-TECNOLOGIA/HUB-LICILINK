import werkzeug.security
import secrets
from datetime import datetime, timezone, timedelta
from flask import current_app
from ..extensions import db
from ..models import User, PendingEmailVerification
from .organization_service import OrganizationService
from .audit_service import AuditService
from .email_service import EmailService

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
            raise ValueError("E-mail já está em uso ou é inválido.")
            
        # 3. Gerar código e hash
        code = str(secrets.randbelow(900000) + 100000)
        code_hash = werkzeug.security.generate_password_hash(code)
        
        # 4. Hash da senha
        password_hash = werkzeug.security.generate_password_hash(password)
        
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
        
        db.session.commit()
        
        # 7. Enviar e-mail
        EmailService.send_verification_code(email, code)
        
        AuditService.log_action('user.registration.started', resource_type='pending_registration', resource_id=str(pending.id))
        
        return pending

    @staticmethod
    def verify_email(pending_id, code):
        pending = PendingEmailVerification.query.get(pending_id)
        if not pending:
            raise ValueError("Registro pendente não encontrado.")
            
        if pending.verified_at:
            raise ValueError("E-mail já verificado.")
            
        if datetime.utcnow() > pending.expires_at:
            raise ValueError("O código expirou. Solicite um novo código.")
            
        max_attempts = current_app.config.get('VERIFICATION_MAX_ATTEMPTS', 5)
        if pending.attempts >= max_attempts:
            raise ValueError("Número máximo de tentativas atingido. Solicite um novo código.")
            
        if not werkzeug.security.check_password_hash(pending.verification_code_hash, code):
            pending.attempts += 1
            db.session.commit()
            AuditService.log_action('user.email_verification.failed', resource_type='pending_registration', resource_id=str(pending.id))
            raise ValueError("Código inválido.")
            
        # Sucesso - Mesma transação
        try:
            user = User(
                name=pending.name,
                email=pending.email,
                password_hash=pending.password_hash,
                email_verified_at=datetime.utcnow()
            )
            db.session.add(user)
            
            pending.verified_at = datetime.utcnow()
            
            db.session.commit()
            
            AuditService.log_action('user.email_verified', user_id=user.id)
            return user
        except Exception as e:
            db.session.rollback()
            raise ValueError("Erro ao criar usuário.")

    @staticmethod
    def resend_code(pending_id):
        pending = PendingEmailVerification.query.get(pending_id)
        if not pending:
            raise ValueError("Registro pendente não encontrado.")
            
        if pending.verified_at:
            raise ValueError("E-mail já verificado.")
            
        cooldown = current_app.config.get('VERIFICATION_RESEND_COOLDOWN', 60)
        if pending.last_sent_at and datetime.utcnow() < pending.last_sent_at + timedelta(seconds=cooldown):
            raise ValueError(f"Aguarde antes de solicitar um novo código.")
            
        max_resends = current_app.config.get('VERIFICATION_MAX_RESENDS', 5)
        if pending.resend_count >= max_resends:
            raise ValueError("Limite de reenvios atingido.")
            
        code = str(secrets.randbelow(900000) + 100000)
        code_hash = werkzeug.security.generate_password_hash(code)
        
        ttl = current_app.config.get('VERIFICATION_CODE_TTL', 600)
        expires_at = datetime.utcnow() + timedelta(seconds=ttl)
        
        pending.verification_code_hash = code_hash
        pending.expires_at = expires_at
        pending.attempts = 0
        pending.last_sent_at = datetime.utcnow()
        pending.resend_count += 1
        
        db.session.commit()
        
        EmailService.send_verification_code(pending.email, code)
        AuditService.log_action('user.email_verification.resent', resource_type='pending_registration', resource_id=str(pending.id))

    @staticmethod
    def authenticate(email, password):
        email = email.lower().strip()
        user = User.query.filter_by(email=email).first()
        if user and werkzeug.security.check_password_hash(user.password_hash, password):
            if user.email_verified_at is None:
                raise ValueError("E-mail não verificado.")
            AuditService.log_action('user_login', user_id=user.id)
            return user
        return None
