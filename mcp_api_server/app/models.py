import enum
from sqlalchemy import Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class CandidateStatus(str, enum.Enum):
    pending_photo = "pending_photo"
    pending_review = "pending_review"
    reviewing = "reviewing"
    passed = "passed"
    rejected = "rejected"
    blacklisted = "blacklisted"
    underage_terminated = "underage_terminated"
    need_more_photo = "need_more_photo"


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True)
    external_id = Column(String(128), nullable=False, unique=True, index=True)
    nickname = Column(String(128))
    city = Column(String(64))
    live_experience = Column(String(256))
    platform = Column(String(128))
    status = Column(Enum(CandidateStatus), nullable=False, default=CandidateStatus.pending_photo)
    refusal_count = Column(Integer, nullable=False, default=0)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(64), nullable=False)
    session_key = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_active_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String(16), nullable=False)
    message_type = Column(String(32), nullable=False, index=True)
    content = Column(Text)
    media_asset_id = Column(Integer, ForeignKey("media_assets.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path = Column(String(512), nullable=False, unique=True)
    original_filename = Column(String(256), nullable=False)
    mime_type = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(JSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class MetricDaily(Base):
    __tablename__ = "metrics_daily"
    __table_args__ = (UniqueConstraint("metric_date", name="uq_metrics_daily_date"),)

    id = Column(Integer, primary_key=True)
    metric_date = Column(Date, nullable=False)
    new_candidates = Column(Integer, nullable=False, default=0)
    photo_candidates = Column(Integer, nullable=False, default=0)
    conversion_rate = Column(Integer, nullable=False, default=0)
    avg_response_seconds = Column(Integer, nullable=False, default=0)


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, default="beibei")
    version = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    published_by = Column(String(64), nullable=False)
    effective_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PromptExample(Base):
    __tablename__ = "prompt_examples"

    id = Column(Integer, primary_key=True)
    context_summary = Column(Text, nullable=False)
    correct_response = Column(Text, nullable=False)
    source = Column(String(32), nullable=False, default="manual")
    is_reviewed = Column(Boolean, nullable=False, default=False, index=True)
    created_by = Column(String(64), nullable=False, default="admin")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class UserRole(str, enum.Enum):
    admin = "admin"
    readonly = "readonly"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.readonly)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

Index("idx_messages_conversation_created", Message.conversation_id, Message.created_at)
Index("idx_events_candidate_created", Event.candidate_id, Event.created_at)
