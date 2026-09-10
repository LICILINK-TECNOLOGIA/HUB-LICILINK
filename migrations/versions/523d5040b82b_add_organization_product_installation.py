"""add organization_product_installation

Revision ID: 523d5040b82b
Revises: 0a4802def021
Create Date: 2026-09-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '523d5040b82b'
down_revision = '0a4802def021'
branch_labels = None
depends_on = None


def upgrade():
    # Issue #63: tabela nova, isolada - nenhuma tabela existente é
    # modificada ou recriada, nenhum backfill é necessário (nenhuma
    # instalação é criada por esta migration).
    op.create_table('organization_product_installations',
    sa.Column('organization_product_id', sa.UUID(), nullable=False),
    sa.Column('public_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_product_id'], ['organization_products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('organization_product_installations', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_organization_product_installations_organization_product_id'),
            ['organization_product_id'], unique=True,
        )
        batch_op.create_index(
            batch_op.f('ix_organization_product_installations_public_id'),
            ['public_id'], unique=True,
        )


def downgrade():
    with op.batch_alter_table('organization_product_installations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organization_product_installations_public_id'))
        batch_op.drop_index(batch_op.f('ix_organization_product_installations_organization_product_id'))

    op.drop_table('organization_product_installations')
