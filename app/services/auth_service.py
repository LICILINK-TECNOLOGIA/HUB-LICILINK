import werkzeug.security
from ..extensions import db
from ..models import User
from .organization_service import OrganizationService
from .audit_service import AuditService

class AuthService:
    @staticmethod
    def register_user(name, email, password):
        # 1. Checar se usuário já existe
        if User.query.filter_by(email=email).first():
            raise ValueError("Email já cadastrado.")
            
        # 2. Criar Usuário
        password_hash = werkzeug.security.generate_password_hash(password)
        user = User(name=name, email=email, password_hash=password_hash)
        db.session.add(user)
        db.session.commit() # Comita para garantir o user.id
        
        AuditService.log_action('user_registered', user_id=user.id)
        return user

    @staticmethod
    def authenticate(email, password):
        user = User.query.filter_by(email=email).first()
        if user and werkzeug.security.check_password_hash(user.password_hash, password):
            AuditService.log_action('user_login', user_id=user.id)
            return user
        return None
