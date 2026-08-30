"""add status to organization_member

Revision ID: 0a4802def021
Revises: fadff4c0a642
Create Date: 2026-08-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0a4802def021'
down_revision = 'fadff4c0a642'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Adiciona a coluna permitindo NULL temporariamente, para permitir o
    #    backfill explícito antes de torná-la obrigatória.
    with op.batch_alter_table('organization_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=True))

    # 2. Backfill explícito: todo vínculo existente antes desta migration é
    #    considerado ativo (nenhum mecanismo de suspensão/desligamento
    #    controlado existia até aqui).
    op.execute("UPDATE organization_members SET status = 'active' WHERE status IS NULL")

    # 3. Torna a coluna obrigatória, define o padrão em nível de banco
    #    (defesa em profundidade além do default já aplicado pelo model) e
    #    adiciona a checagem de valores válidos - nunca string livre.
    with op.batch_alter_table('organization_members', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.String(length=20),
            nullable=False,
            server_default='active',
        )
        batch_op.create_check_constraint(
            'ck_org_member_status_valid',
            "status IN ('active', 'suspended', 'removed')",
        )


def downgrade():
    with op.batch_alter_table('organization_members', schema=None) as batch_op:
        batch_op.drop_constraint('ck_org_member_status_valid', type_='check')
        batch_op.drop_column('status')
