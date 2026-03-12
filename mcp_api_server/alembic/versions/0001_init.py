"""init schema

Revision ID: 0001_init
Revises: 
Create Date: 2026-03-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    candidate_status = sa.Enum('pending_photo','pending_review','reviewing','passed','rejected','blacklisted','underage_terminated','need_more_photo', name='candidatestatus')
    user_role = sa.Enum('admin','readonly', name='userrole')

    op.create_table('candidates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('external_id', sa.String(128), nullable=False),
        sa.Column('nickname', sa.String(128)),
        sa.Column('city', sa.String(64)),
        sa.Column('live_experience', sa.String(256)),
        sa.Column('platform', sa.String(128)),
        sa.Column('status', candidate_status, nullable=False),
        sa.Column('refusal_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('external_id')
    )
    op.create_index('ix_candidates_external_id', 'candidates', ['external_id'])

    op.create_table('conversations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_id', sa.Integer(), sa.ForeignKey('candidates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(64), nullable=False),
        sa.Column('session_key', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('session_key')
    )
    op.create_index('ix_conversations_candidate_id', 'conversations', ['candidate_id'])
    op.create_index('ix_conversations_session_key', 'conversations', ['session_key'])

    op.create_table('media_assets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_id', sa.Integer(), sa.ForeignKey('candidates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_path', sa.String(512), nullable=False),
        sa.Column('original_filename', sa.String(256), nullable=False),
        sa.Column('mime_type', sa.String(128), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('file_path')
    )

    op.create_table('messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_id', sa.Integer(), sa.ForeignKey('candidates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sender', sa.String(16), nullable=False),
        sa.Column('message_type', sa.String(32), nullable=False),
        sa.Column('content', sa.Text()),
        sa.Column('media_asset_id', sa.Integer(), sa.ForeignKey('media_assets.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('ix_messages_message_type', 'messages', ['message_type'])
    op.create_index('ix_messages_created_at', 'messages', ['created_at'])
    op.create_index('idx_messages_conversation_created', 'messages', ['conversation_id', 'created_at'])

    op.create_table('events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_id', sa.Integer(), sa.ForeignKey('candidates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE')),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_events_candidate_created', 'events', ['candidate_id', 'created_at'])

    op.create_table('metrics_daily',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('metric_date', sa.Date(), nullable=False),
        sa.Column('new_candidates', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('photo_candidates', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversion_rate', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_response_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('metric_date', name='uq_metrics_daily_date')
    )

    op.create_table('prompts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('version', sa.String(32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('published_by', sa.String(64), nullable=False),
        sa.Column('effective_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table('users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('role', user_role, nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('username')
    )


def downgrade() -> None:
    op.drop_table('users')
    op.drop_table('prompts')
    op.drop_table('metrics_daily')
    op.drop_table('events')
    op.drop_table('messages')
    op.drop_table('media_assets')
    op.drop_table('conversations')
    op.drop_table('candidates')
