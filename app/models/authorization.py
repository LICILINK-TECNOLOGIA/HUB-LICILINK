from sqlalchemy.dialects.postgresql import UUID
from .base import BaseModel
from ..extensions import db

class Role(BaseModel):
    __tablename__ = 'roles'

    name = db.Column(db.String(50), unique=True, nullable=False) # Ex: 'admin', 'user', 'owner'
    description = db.Column(db.String(255))

    # Relacionamentos
    role_permissions = db.relationship('RolePermission', back_populates='role', cascade="all, delete-orphan")

class Permission(BaseModel):
    __tablename__ = 'permissions'

    name = db.Column(db.String(50), unique=True, nullable=False) # Ex: 'manage_users', 'view_billing'
    description = db.Column(db.String(255))

    # Relacionamentos
    role_permissions = db.relationship('RolePermission', back_populates='permission', cascade="all, delete-orphan")

class RolePermission(BaseModel):
    __tablename__ = 'role_permissions'
    __table_args__ = (
        db.UniqueConstraint('role_id', 'permission_id', name='uq_role_permission_role_perm'),
    )

    role_id = db.Column(UUID(as_uuid=True), db.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False, index=True)
    permission_id = db.Column(UUID(as_uuid=True), db.ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False, index=True)

    # Relacionamentos
    role = db.relationship('Role', back_populates='role_permissions')
    permission = db.relationship('Permission', back_populates='role_permissions')
