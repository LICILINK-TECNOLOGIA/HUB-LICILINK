"""add is_internal_admin

Revision ID: 9f09532a8f30
Revises: 93b105619ed7
Create Date: 2026-08-17 23:08:43.264871

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f09532a8f30'
down_revision = '93b105619ed7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('is_internal_admin', sa.Boolean(), nullable=False, server_default='false'))

def downgrade():
    op.drop_column('users', 'is_internal_admin')
