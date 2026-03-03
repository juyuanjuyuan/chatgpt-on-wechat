from datetime import datetime
from pydantic import BaseModel
from typing import Optional, Any


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
