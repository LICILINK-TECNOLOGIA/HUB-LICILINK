from sqlalchemy.dialects.postgresql import UUID
from .base import BaseModel
from ..extensions import db

class Product(BaseModel):
    __tablename__ = 'products'

    # Issue #27: código canônico persistido - sempre sem o prefixo comercial
    # 'L-' (que pertence somente a Product.name e às variáveis L_*_URL).
    # Único catálogo válido, definido em STRUCTURAL_PRODUCTS
    # (app/services/bootstrap_service.py): 'kalender', 'gedo', 'hunt'.
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    url = db.Column(db.String(255))

    # Relacionamentos
    product_permissions = db.relationship('ProductPermission', back_populates='product', cascade="all, delete-orphan")
    organization_products = db.relationship('OrganizationProduct', back_populates='product', cascade="all, delete-orphan")

class ProductPermission(BaseModel):
    __tablename__ = 'product_permissions'
    __table_args__ = (
        db.UniqueConstraint('product_id', 'permission_id', name='uq_prod_perm_prod_perm'),
    )

    product_id = db.Column(UUID(as_uuid=True), db.ForeignKey('products.id', ondelete='CASCADE'), nullable=False, index=True)
    permission_id = db.Column(UUID(as_uuid=True), db.ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False, index=True)

    # Relacionamentos
    product = db.relationship('Product', back_populates='product_permissions')
    permission = db.relationship('Permission')

class OrganizationProduct(BaseModel):
    __tablename__ = 'organization_products'
    __table_args__ = (
        db.UniqueConstraint('organization_id', 'product_id', name='uq_org_prod_org_prod'),
    )

    organization_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    product_id = db.Column(UUID(as_uuid=True), db.ForeignKey('products.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Status pode ser 'active', 'trial', 'inactive', 'suspended'
    status = db.Column(db.String(50), default='inactive', nullable=False)

    # Relacionamentos
    organization = db.relationship('Organization')
    product = db.relationship('Product', back_populates='organization_products')
