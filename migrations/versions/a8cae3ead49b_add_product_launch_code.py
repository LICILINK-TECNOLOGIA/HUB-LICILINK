"""add product_launch_code

Revision ID: a8cae3ead49b
Revises: 6139b6b03a30
Create Date: 2026-09-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8cae3ead49b'
down_revision = '6139b6b03a30'
branch_labels = None
depends_on = None


def upgrade():
    # Issue #65: tabela nova, isolada - nenhuma tabela existente é
    # modificada, nenhum backfill, nenhum código de lançamento criado
    # por esta migration.
    op.create_table('product_launch_codes',
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('organization_product_installation_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_product_installation_id'], ['organization_product_installations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('product_launch_codes', schema=None) as batch_op:
        # Índice único: no máximo um código por hash - nomes bem abaixo
        # do limite de 63 bytes do PostgreSQL (tabela/colunas curtas
        # nesta Issue), então a convenção automática do SQLAlchemy
        # (`ix_<tabela>_<coluna>`) é usada sem necessidade de nome
        # manual abreviado, ao contrário da Issue #64.
        batch_op.create_index(
            batch_op.f('ix_product_launch_codes_code_hash'),
            ['code_hash'], unique=True,
        )
        batch_op.create_index(
            batch_op.f('ix_product_launch_codes_expires_at'),
            ['expires_at'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_product_launch_codes_organization_product_installation_id'),
            ['organization_product_installation_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_product_launch_codes_user_id'),
            ['user_id'], unique=False,
        )


def downgrade():
    with op.batch_alter_table('product_launch_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_product_launch_codes_user_id'))
        batch_op.drop_index(batch_op.f('ix_product_launch_codes_organization_product_installation_id'))
        batch_op.drop_index(batch_op.f('ix_product_launch_codes_expires_at'))
        batch_op.drop_index(batch_op.f('ix_product_launch_codes_code_hash'))

    op.drop_table('product_launch_codes')
