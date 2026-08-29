from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import UUID
from werkzeug.security import generate_password_hash, check_password_hash
from .base import BaseModel
from ..extensions import db

# Comprimento mínimo/máximo exigido para a senha definitiva de um usuário
# (registro e qualquer redefinição futura). O mínimo é deliberadamente igual
# ao exigido pelo CLI administrativo (Issue #11, `MIN_ADMIN_PASSWORD_LENGTH`
# em app/cli.py) para evitar duas políticas arbitrariamente diferentes — não
# há MFA obrigatório implementado no HUB hoje que justificasse um mínimo
# menor para usuários comuns. O máximo existe apenas para evitar entrada
# excessiva (ex.: DoS via string extremamente longa), não por limitação do
# algoritmo de hash.
MIN_USER_PASSWORD_LENGTH = 12
MAX_USER_PASSWORD_LENGTH = 128


def validate_password_strength(password):
    """Valida a força mínima/máxima de uma senha de usuário antes de
    qualquer hash ou persistência.

    A senha é validada exatamente como foi informada: nenhuma normalização
    (strip, lower, Unicode) ou truncamento é aplicada em nenhum momento —
    apenas checagens de tipo e comprimento. Não exige combinações artificiais
    de maiúsculas/números/símbolos. Nunca inclui o valor da senha na
    mensagem de erro.
    """
    if not isinstance(password, str):
        raise ValueError("Senha inválida: deve ser uma string de texto.")
    if password.strip() == "":
        raise ValueError("Senha inválida: não pode ser vazia ou conter apenas espaços.")
    if len(password) < MIN_USER_PASSWORD_LENGTH:
        raise ValueError(
            f"Senha inválida: deve conter ao menos {MIN_USER_PASSWORD_LENGTH} caracteres."
        )
    if len(password) > MAX_USER_PASSWORD_LENGTH:
        raise ValueError(
            f"Senha inválida: deve conter no máximo {MAX_USER_PASSWORD_LENGTH} caracteres."
        )

class Organization(BaseModel):
    __tablename__ = 'organizations'

    legal_name = db.Column(db.String(255), nullable=False)
    trade_name = db.Column(db.String(255), nullable=True)
    cnpj = db.Column(db.String(14), unique=True, nullable=True) # Somente numeros
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relacionamentos
    members = db.relationship('OrganizationMember', back_populates='organization', cascade="all, delete-orphan")

class User(UserMixin, BaseModel):
    __tablename__ = 'users'

    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_internal_admin = db.Column(db.Boolean, default=False, nullable=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)

    # Relacionamentos
    memberships = db.relationship('OrganizationMember', back_populates='user', cascade="all, delete-orphan")

    @staticmethod
    def hash_password(raw_password):
        """Único ponto de geração de hash aceito para a senha definitiva do
        usuário — valida a força internamente antes de hashear, então
        nenhum chamador (`set_password`, o fluxo de registro, ou qualquer
        código futuro) consegue gerar um hash a partir de uma senha inválida,
        mesmo esquecendo de chamar `validate_password_strength` antes.
        Reutilizável tanto a partir de uma instância de `User`
        (`set_password`) quanto antes de o usuário existir (fluxo de
        registro/verificação de e-mail)."""
        validate_password_strength(raw_password)
        return generate_password_hash(raw_password)

    def set_password(self, raw_password):
        """Único mecanismo aceito para definir/alterar a senha do usuário.

        Delega toda a validação para `hash_password`."""
        self.password_hash = self.hash_password(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

class OrganizationMember(BaseModel):
    __tablename__ = 'organization_members'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'organization_id', name='uq_org_member_user_org'),
    )

    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    organization_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    role_id = db.Column(UUID(as_uuid=True), db.ForeignKey('roles.id', ondelete='RESTRICT'), nullable=False)

    # Relacionamentos
    user = db.relationship('User', back_populates='memberships')
    organization = db.relationship('Organization', back_populates='members')
    role = db.relationship('Role')

class PendingEmailVerification(BaseModel):
    __tablename__ = 'pending_email_verifications'

    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    verification_code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    max_attempts = db.Column(db.Integer, default=5, nullable=False)
    last_sent_at = db.Column(db.DateTime, nullable=False)
    resend_count = db.Column(db.Integer, default=0, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)

