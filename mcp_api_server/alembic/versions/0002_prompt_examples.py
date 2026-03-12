"""add prompt_examples table

Revision ID: 0002_prompt_examples
Revises: 0001_init
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_prompt_examples'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('prompt_examples',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('context_summary', sa.Text(), nullable=False),
        sa.Column('correct_response', sa.Text(), nullable=False),
        sa.Column('source', sa.String(32), nullable=False, server_default='manual'),
        sa.Column('is_reviewed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_by', sa.String(64), nullable=False, server_default='admin'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_prompt_examples_is_reviewed', 'prompt_examples', ['is_reviewed'])


def downgrade() -> None:
    op.drop_index('ix_prompt_examples_is_reviewed')
    op.drop_table('prompt_examples')
