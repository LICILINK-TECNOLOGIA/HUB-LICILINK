import uuid

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
    # Issue #63: no máximo uma instalação por OrganizationProduct nesta
    # primeira versão (uselist=False = um-para-um). cascade="all,
    # delete-orphan" só tem efeito em exclusão física real da linha pai
    # (via ORM) - nunca em revogação lógica, que só muda `status` e nunca
    # toca este relacionamento.
    installation = db.relationship(
        'OrganizationProductInstallation',
        back_populates='organization_product',
        uselist=False,
        cascade="all, delete-orphan",
    )


class OrganizationProductInstallation(BaseModel):
    """Issue #63: instalação de um produto (ex. GEDO) vinculada a uma
    organização específica - fundação estrutural para autenticação
    federada (Issue #62). Puramente estrutural: nenhum comportamento do
    HUB depende deste model ainda (launcher, grant/revoke e
    BootstrapService continuam usando exclusivamente `Product.url`)."""
    __tablename__ = 'organization_product_installations'

    # FK única para OrganizationProduct.id (não FKs separadas para
    # Organization/Product) - OrganizationProduct já é a chave natural do
    # par organização/produto (uq_org_prod_org_prod) e já carrega o
    # estado da assinatura; duplicar organization_id/product_id aqui
    # criaria uma segunda fonte de verdade para "quais pares existem".
    # unique=True garante no máximo uma instalação por par nesta primeira
    # versão.
    organization_product_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey('organization_products.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
        index=True,
    )

    # Identificador público opaco, deliberadamente separado da PK interna
    # (id) - a identidade externa (futuro endpoint servidor-servidor)
    # nunca deve se acoplar à identidade interna de implementação. Gerado
    # aleatoriamente (uuid4), nunca derivado de organização/produto/URL/
    # e-mail. Não é segredo: pode aparecer em log/auditoria; conhecer este
    # valor sozinho não concede acesso a nada (nenhuma credencial é
    # gerada nesta Issue).
    public_id = db.Column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
        default=uuid.uuid4,
    )

    url = db.Column(db.String(255), nullable=False)

    # Mesmo padrão de default duplo (Python + banco) já usado por
    # OrganizationMember.status (Issue #60) - garante o valor correto
    # mesmo para linhas inseridas fora do ORM.
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default='true',
    )

    # Relacionamentos
    organization_product = db.relationship('OrganizationProduct', back_populates='installation')
