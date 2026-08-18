from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import UUID
from .base import BaseModel
from ..extensions import db

class Organization(BaseModel):
    __tablename__ = 'organizations'

    name = db.Column(db.String(255), nullable=False)
    cnpj = db.Column(db.String(20), unique=True, nullable=True) # Alguns podem nao ter cnpj

    # Relacionamentos
    members = db.relationship('OrganizationMember', back_populates='organization', cascade="all, delete-orphan")

class User(UserMixin, BaseModel):
    __tablename__ = 'users'

    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_internal_admin = db.Column(db.Boolean, default=False, nullable=False)

    # Relacionamentos
    memberships = db.relationship('OrganizationMember', back_populates='user', cascade="all, delete-orphan")

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
