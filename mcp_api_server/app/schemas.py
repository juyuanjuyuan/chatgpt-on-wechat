from datetime import datetime
from pydantic import BaseModel
from typing import Any, Optional


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str


class CandidateUpsert(BaseModel):
    external_id: str
    nickname: Optional[str] = None
    city: Optional[str] = None
    live_experience: Optional[str] = None
    platform: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class MessageCreate(BaseModel):
    external_id: str
    session_key: str
    channel: str
    sender: str
    message_type: str
    content: Optional[str] = None


class EventCreate(BaseModel):
    external_id: str
    session_key: Optional[str] = None
    event_type: str
    payload: dict[str, Any] = {}


class ReviewUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


class PromptPublish(BaseModel):
    version: str
    content: str
    published_by: str


class PromptRollback(BaseModel):
    version: str


class PromptOut(BaseModel):
    id: int
    version: str
    is_active: bool
    content: str
    published_by: str
    created_at: datetime

    class Config:
        from_attributes = True


class PromptExampleCreate(BaseModel):
    context_summary: str
    correct_response: str
    source: str = "manual"


class PromptExampleUpdate(BaseModel):
    context_summary: Optional[str] = None
    correct_response: Optional[str] = None


class PromptExampleReview(BaseModel):
    is_reviewed: bool


class FollowupMessageOut(BaseModel):
    sender: str
    content: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PendingFollowupConversation(BaseModel):
    conversation_id: int
    session_key: str
    channel: str
    candidate_id: int
    external_id: str
    nickname: Optional[str] = None
    last_active_at: datetime
    followup_count: int
    recent_messages: list[FollowupMessageOut]

    class Config:
        from_attributes = True


class PendingProfileExtractionConversation(BaseModel):
    conversation_id: int
    session_key: str
    channel: str
    candidate_id: int
    external_id: str
    nickname: Optional[str] = None
    status: str
    status_label: str
    last_active_at: datetime
    recent_messages: list[FollowupMessageOut]

    class Config:
        from_attributes = True


class CandidateProfileExtractionOut(BaseModel):
    conversation_id: int
    candidate_id: int
    external_id: str
    updated: bool
    nickname: Optional[str] = None
    city: Optional[str] = None
    status: str
    status_label: str
    confidence: Optional[str] = None
    reason: Optional[str] = None
