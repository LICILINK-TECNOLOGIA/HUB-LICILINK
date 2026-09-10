"""add organization_product_installation_credential

Revision ID: 6139b6b03a30
Revises: 523d5040b82b
Create Date: 2026-09-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6139b6b03a30'
down_revision = '523d5040b82b'
branch_labels = None
depends_on = None


def upgrade():
    # Issue #64: tabela nova, isolada - nenhuma tabela existente é
    # modificada, nenhum backfill, nenhuma credencial/instalação/produto
    # criada por esta migration.
    op.create_table('organization_product_installation_credentials',
    sa.Column('organization_product_installation_id', sa.UUID(), nullable=False),
    sa.Column('secret_hash', sa.String(length=64), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_product_installation_id'], ['organization_product_installations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('organization_product_installation_credentials', schema=None) as batch_op:
        # Índice regular (não único), nomeado explicitamente e mais curto
        # que a convenção padrão `ix_<tabela>_<coluna>` produziria (85
        # caracteres, excederia o limite de 63 bytes de identificador do
        # PostgreSQL, dado o tamanho dos nomes de tabela/coluna aqui).
        batch_op.create_index(
            'ix_org_prod_installation_credentials_installation_id',
            ['organization_product_installation_id'], unique=False,
        )
        # Índice único PARCIAL: no máximo uma linha com `revoked_at IS
        # NULL` por instalação - suportado tanto por PostgreSQL quanto
        # por SQLite (usado nos testes), diferente de lock de linha.
        batch_op.create_index(
            'uq_org_prod_installation_credential_active',
            ['organization_product_installation_id'], unique=True,
            sqlite_where=sa.text('revoked_at IS NULL'),
            postgresql_where=sa.text('revoked_at IS NULL'),
        )


def downgrade():
    with op.batch_alter_table('organization_product_installation_credentials', schema=None) as batch_op:
        batch_op.drop_index('uq_org_prod_installation_credential_active')
        batch_op.drop_index('ix_org_prod_installation_credentials_installation_id')

    op.drop_table('organization_product_installation_credentials')
