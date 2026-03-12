"""add last_active_at to conversations

Revision ID: 0003_conversations_last_active
Revises: 0002_prompt_examples
Create Date: 2026-03-06
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_conversations_last_active'
down_revision = '0002_prompt_examples'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column(
        'last_active_at',
        sa.DateTime(timezone=True),
        server_default=sa.text('now()'),
        nullable=False,
    ))


def downgrade() -> None:
    op.drop_column('conversations', 'last_active_at')
