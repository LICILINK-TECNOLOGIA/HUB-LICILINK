from sqlalchemy.dialects.postgresql import UUID, JSONB
from .base import BaseModel
from ..extensions import db

class AuditLog(BaseModel):
    __tablename__ = 'audit_logs'

    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    organization_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True, index=True)
    
    action = db.Column(db.String(100), nullable=False, index=True) # Ex: 'user_login', 'role_changed', 'product_access_granted'
    resource_type = db.Column(db.String(100)) # Ex: 'OrganizationMember', 'Product'
    resource_id = db.Column(UUID(as_uuid=True)) # ID do recurso afetado
    
    details = db.Column(JSONB, default=dict) # Detalhes da alteração, ex: {"old_role": "user", "new_role": "admin"}
    ip_address = db.Column(db.String(50))

    # Relacionamentos
    user = db.relationship('User')
    organization = db.relationship('Organization')
