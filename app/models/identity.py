from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import UUID
from werkzeug.security import generate_password_hash, check_password_hash
from .base import BaseModel
from ..extensions import db

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

    def set_password(self, raw_password):
        """Único mecanismo aceito para definir/alterar a senha do usuário."""
        self.password_hash = generate_password_hash(raw_password)

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

