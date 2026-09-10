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
    # Issue #64: histórico completo de credenciais já emitidas para esta
    # instalação - relação um-para-muitos de verdade (nunca uselist=False
    # aqui, ao contrário de `installation` acima), porque uma instalação
    # tem várias credenciais ao longo do tempo (emissão inicial + cada
    # rotação), mesmo que no máximo uma esteja "atual"
    # (`revoked_at IS NULL`, garantido pelo índice único parcial em
    # `OrganizationProductInstallationCredential.__table_args__`) e no
    # máximo uma outra em janela curta de rotação a qualquer momento -
    # ambos controlados pelo service, nunca pela cascade do relacionamento
    # em si. cascade="all, delete-orphan" só age em exclusão física real
    # da instalação (via ORM) - nunca em revogação/rotação, que apenas
    # marcam `revoked_at`, sem jamais remover nenhuma linha da coleção.
    credentials = db.relationship(
        'OrganizationProductInstallationCredential',
        back_populates='organization_product_installation',
        cascade="all, delete-orphan",
    )


class OrganizationProductInstallationCredential(BaseModel):
    """Issue #64: credencial simétrica de uma OrganizationProductInstallation
    - fundação para a autenticação servidor-servidor da federação (Issue
    #62). Cada linha é uma credencial real já emitida (nunca sobrescrita);
    `revoked_at` marca a transição de estado (nunca uma exclusão):
    `NULL` = credencial atual, sem revogação agendada; um instante FUTURO
    = credencial anterior ainda aceita durante a janela curta de rotação
    (`Config.INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`); um instante
    já passado = credencial revogada/expirada, rejeitada pela
    autenticação. Nunca armazena o segredo em claro - só `secret_hash`
    (SHA-256 do segredo aleatório de alta entropia, nunca hash lento tipo
    scrypt/PBKDF2, desproporcional para um segredo já CSPRNG de 256 bits).
    Sem nenhuma relação com plano comercial ou quota de armazenamento."""
    __tablename__ = 'organization_product_installation_credentials'
    __table_args__ = (
        # Índice regular (não único) só para performance de busca por
        # instalação - nomeado explicitamente (em vez de `index=True` na
        # coluna, que geraria o nome padrão `ix_<tabela>_<coluna>` e
        # excederia os 63 bytes de identificador do PostgreSQL, dado o
        # tamanho dos nomes de tabela/coluna envolvidos).
        db.Index(
            'ix_org_prod_installation_credentials_installation_id',
            'organization_product_installation_id',
        ),
        # Índice único PARCIAL: garante no banco, para PostgreSQL E
        # SQLite (usado nos testes), que no máximo UMA linha por
        # instalação pode ter `revoked_at IS NULL` ao mesmo tempo - a
        # credencial "atual" é sempre única de verdade, nunca depende
        # somente da disciplina do service para ser garantida.
        db.Index(
            'uq_org_prod_installation_credential_active',
            'organization_product_installation_id',
            unique=True,
            sqlite_where=db.text('revoked_at IS NULL'),
            postgresql_where=db.text('revoked_at IS NULL'),
        ),
    )

    organization_product_installation_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey('organization_product_installations.id', ondelete='CASCADE'),
        nullable=False,
    )

    # SHA-256 hexdigest do segredo aleatório de alta entropia - tamanho
    # fixo de 64 caracteres, sem truncamento.
    secret_hash = db.Column(db.String(64), nullable=False)

    # NULL = atual (sem revogação agendada); futuro = anterior, ainda
    # aceita durante a janela de rotação; passado = revogada/expirada.
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relacionamentos
    organization_product_installation = db.relationship(
        'OrganizationProductInstallation', back_populates='credentials'
    )
