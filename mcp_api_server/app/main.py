import csv
import io
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .auth import create_token, get_current_user, hash_password, require_admin, verify_password
from .database import get_db
from .models import Candidate, CandidateStatus, Conversation, Event, MediaAsset, Message, Prompt, PromptExample, User, UserRole
from .profile_extraction import (
    PROFILE_EXTRACTION_EVENT_TYPE,
    candidate_status_label,
    get_profile_extraction_channels,
    is_profile_extraction_candidate_status,
    run_profile_extraction,
)
from .schemas import (
    CandidateProfileExtractionOut, CandidateUpsert, EventCreate, FollowupMessageOut,
    LoginRequest, MessageCreate, PendingFollowupConversation,
    PendingProfileExtractionConversation, PromptExampleCreate, PromptExampleReview,
    PromptExampleUpdate, PromptPublish, PromptRollback, ReviewUpdate, TokenResponse,
)

app = FastAPI(title="CowAgent MCP API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./data/media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_TOKEN = os.getenv("MEDIA_TOKEN", "media-secret")


def resolve_prompt_path(filename: str) -> Path:
    current = Path(__file__).resolve()
    candidates = [
        current.parents[2] / "prompts" / filename,  # repo layout
        current.parents[1] / "prompts" / filename,  # container layout (/app/app -> /app/prompts)
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load_default_recruiter_prompt() -> str:
    prompt_path = resolve_prompt_path("recruiter_v1.md")
    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load recruiter_v1.md: {exc}") from exc


def build_current_prompt_content(version: str, prompt_content: str, examples: list[PromptExample]) -> str:
    # Keep v1 aligned with the repository prompt file so local edits become
    # the active system prompt without requiring a separate publish step.
    if version == "v1":
        content = load_default_recruiter_prompt()
    else:
        content = (prompt_content or "").strip()
    if examples:
        parts = ["\n\n## 补充回复规范\n\n当遇到类似以下场景时，请参考对应的回复方式：\n"]
        for i, ex in enumerate(examples, 1):
            parts.append(f"### 场景 {i}\n用户问: \"{ex.context_summary}\"\n你应该回复: \"{ex.correct_response}\"\n")
        content += "\n".join(parts)
    return content


def ensure_candidate(db: Session, external_id: str) -> Candidate:
    c = db.query(Candidate).filter(Candidate.external_id == external_id).first()
    if not c:
        c = Candidate(external_id=external_id)
        db.add(c)
        db.flush()
    return c


def ensure_conversation(db: Session, candidate_id: int, session_key: str, channel: str) -> Conversation:
    conv = db.query(Conversation).filter(Conversation.session_key == session_key).first()
    if not conv:
        conv = Conversation(candidate_id=candidate_id, session_key=session_key, channel=channel)
        db.add(conv)
        db.flush()
    return conv


@app.get('/health')
def health():
    return {"status": "ok"}


@app.post('/auth/login', response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=create_token(user))


@app.post('/candidates/upsert')
def upsert_candidate(payload: CandidateUpsert, db: Session = Depends(get_db)):
    c = ensure_candidate(db, payload.external_id)
    for field in ["nickname", "city", "live_experience", "platform", "notes"]:
        v = getattr(payload, field)
        if v is not None:
            setattr(c, field, v)
    if payload.status:
        c.status = CandidateStatus(payload.status)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "external_id": c.external_id, "status": c.status.value}


@app.get('/candidates')
def list_candidates(
    status: Optional[str] = None,
    city: Optional[str] = None,
    has_photo: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    query = db.query(Candidate)
    if status:
        query = query.filter(Candidate.status == CandidateStatus(status))
    if city:
        query = query.filter(Candidate.city == city)
    if q:
        query = query.filter(Candidate.nickname.ilike(f"%{q}%"))
    if has_photo is not None:
        sub = db.query(MediaAsset.candidate_id).distinct().subquery()
        query = query.filter(Candidate.id.in_(sub) if has_photo else ~Candidate.id.in_(sub))
    total = query.count()
    rows = query.order_by(Candidate.updated_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "external_id": c.external_id,
                "nickname": c.nickname,
                "city": c.city,
                "status": c.status.value,
                "status_label": candidate_status_label(c.status),
                "refusal_count": c.refusal_count,
                "created_at": c.created_at,
            }
            for c in rows
        ],
    }


@app.get('/candidates/{candidate_id}')
def get_candidate_detail(candidate_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    convs = db.query(Conversation).filter(Conversation.candidate_id == candidate_id).all()
    conv_ids = [x.id for x in convs]
    last_active_at = max((conv.last_active_at for conv in convs if conv.last_active_at), default=None)
    msgs = db.query(Message).filter(Message.conversation_id.in_(conv_ids)).order_by(Message.created_at.asc()).all() if conv_ids else []
    media = db.query(MediaAsset).filter(MediaAsset.candidate_id == candidate_id).order_by(MediaAsset.created_at.desc()).all()
    return {
        "candidate": {
            "id": c.id,
            "external_id": c.external_id,
            "nickname": c.nickname,
            "city": c.city,
            "live_experience": c.live_experience,
            "platform": c.platform,
            "status": c.status.value,
            "status_label": candidate_status_label(c.status),
            "notes": c.notes,
            "refusal_count": c.refusal_count,
            "last_active_at": last_active_at,
        },
        "messages": [
            {
                "id": m.id, "sender": m.sender, "message_type": m.message_type, "content": m.content, "created_at": m.created_at,
                **({"media_asset_id": m.media_asset_id, "preview_url": f"/media/{m.media_asset_id}?token={MEDIA_TOKEN}"} if m.media_asset_id else {}),
            }
            for m in msgs
        ],
        "photos": [
            {
                "id": p.id,
                "filename": p.original_filename,
                "mime_type": p.mime_type,
                "created_at": p.created_at,
                "preview_url": f"/media/{p.id}?token={MEDIA_TOKEN}",
            }
            for p in media
        ],
    }


@app.patch('/candidates/{candidate_id}/status')
def update_status(candidate_id: int, payload: ReviewUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    c.status = CandidateStatus(payload.status)
    if payload.notes is not None:
        c.notes = payload.notes
    db.add(Event(candidate_id=c.id, event_type="review_status_changed", payload={"status": payload.status}))
    db.commit()
    return {"ok": True}


@app.post('/messages')
def append_message(payload: MessageCreate, db: Session = Depends(get_db)):
    c = ensure_candidate(db, payload.external_id)
    conv = ensure_conversation(db, c.id, payload.session_key, payload.channel)
    m = Message(
        candidate_id=c.id,
        conversation_id=conv.id,
        sender=payload.sender,
        message_type=payload.message_type,
        content=payload.content,
    )
    db.add(m)
    conv.last_active_at = func.now()
    db.commit()
    return {"id": m.id, "candidate_id": c.id, "conversation_id": conv.id}


@app.get('/conversations/history')
def conversation_history(
    session_key: str = Query(..., description="Session key to look up"),
    limit: int = Query(40, ge=1, le=200, description="Max messages to return (most recent)"),
    db: Session = Depends(get_db),
):
    conv = db.query(Conversation).filter(Conversation.session_key == session_key).first()
    if not conv:
        return {"messages": []}
    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    msgs.reverse()
    return {
        "messages": [
            {"sender": m.sender, "content": m.content}
            for m in msgs
            if m.content
        ]
    }


@app.get('/conversations')
def list_conversations(page: int = 1, page_size: int = 20, db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = db.query(Conversation)
    total = q.count()
    rows = q.order_by(Conversation.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [{"id": r.id, "session_key": r.session_key, "channel": r.channel, "candidate_id": r.candidate_id} for r in rows]}


@app.get('/conversations/pending-followup')
def pending_followup_conversations(
    min_idle_hours: float = Query(2, description="Minimum hours since last activity"),
    max_followups: int = Query(3, description="Max auto-followup attempts per conversation"),
    limit: int = Query(50, description="Max conversations to return"),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_idle_hours)

    followup_counts = (
        db.query(Event.conversation_id, func.count(Event.id).label("cnt"))
        .filter(Event.event_type == "auto_followup")
        .group_by(Event.conversation_id)
        .subquery()
    )

    last_msg = (
        db.query(
            Message.conversation_id,
            Message.sender,
            func.max(Message.created_at).label("last_at"),
        )
        .group_by(Message.conversation_id, Message.sender)
        .subquery()
    )

    last_sender_sub = (
        db.query(
            Message.conversation_id,
            Message.sender.label("last_sender"),
        )
        .distinct(Message.conversation_id)
        .order_by(Message.conversation_id, Message.created_at.desc())
        .subquery()
    )

    rows = (
        db.query(Conversation, Candidate, followup_counts.c.cnt)
        .join(Candidate, Candidate.id == Conversation.candidate_id)
        .outerjoin(followup_counts, followup_counts.c.conversation_id == Conversation.id)
        .outerjoin(last_sender_sub, last_sender_sub.c.conversation_id == Conversation.id)
        .filter(
            Candidate.status == CandidateStatus.pending_photo,
            Conversation.last_active_at < cutoff,
            last_sender_sub.c.last_sender == "assistant",
        )
        .filter(
            (followup_counts.c.cnt == None) | (followup_counts.c.cnt < max_followups)  # noqa: E711
        )
        .order_by(Conversation.last_active_at.asc())
        .limit(limit)
        .all()
    )

    result = []
    for conv, cand, fu_count in rows:
        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(20)
            .all()
        )
        msgs.reverse()
        result.append(PendingFollowupConversation(
            conversation_id=conv.id,
            session_key=conv.session_key,
            channel=conv.channel,
            candidate_id=cand.id,
            external_id=cand.external_id,
            nickname=cand.nickname,
            last_active_at=conv.last_active_at,
            followup_count=fu_count or 0,
            recent_messages=[
                FollowupMessageOut(sender=m.sender, content=m.content, created_at=m.created_at)
                for m in msgs
            ],
        ))
    return result


@app.get('/conversations/pending-profile-extraction', response_model=list[PendingProfileExtractionConversation])
def pending_profile_extraction_conversations(
    idle_minutes: int = Query(20, ge=1, le=1440, description="Minimum idle minutes before extraction"),
    limit: int = Query(50, ge=1, le=200, description="Max conversations to return"),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)
    extraction_events = (
        db.query(Event.conversation_id, func.max(Event.created_at).label("last_extracted_at"))
        .filter(Event.event_type == PROFILE_EXTRACTION_EVENT_TYPE)
        .group_by(Event.conversation_id)
        .subquery()
    )

    query = (
        db.query(Conversation, Candidate, extraction_events.c.last_extracted_at)
        .join(Candidate, Candidate.id == Conversation.candidate_id)
        .outerjoin(extraction_events, extraction_events.c.conversation_id == Conversation.id)
        .filter(
            Conversation.last_active_at < cutoff,
            Candidate.status == CandidateStatus.pending_photo,
        )
        .filter(
            (extraction_events.c.last_extracted_at == None) | (extraction_events.c.last_extracted_at < Conversation.last_active_at)  # noqa: E711
        )
        .order_by(Conversation.last_active_at.asc())
    )

    channels = get_profile_extraction_channels()
    if channels:
        query = query.filter(Conversation.channel.in_(channels))

    rows = query.limit(limit).all()
    result = []
    for conv, cand, _ in rows:
        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(30)
            .all()
        )
        msgs.reverse()
        result.append(PendingProfileExtractionConversation(
            conversation_id=conv.id,
            session_key=conv.session_key,
            channel=conv.channel,
            candidate_id=cand.id,
            external_id=cand.external_id,
            nickname=cand.nickname,
            status=getattr(cand.status, "value", cand.status),
            status_label=candidate_status_label(cand.status),
            last_active_at=conv.last_active_at,
            recent_messages=[
                FollowupMessageOut(sender=m.sender, content=m.content, created_at=m.created_at)
                for m in msgs
            ],
        ))
    return result


@app.post('/conversations/{conversation_id}/extract-profile', response_model=CandidateProfileExtractionOut)
def extract_candidate_profile(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    cand = db.query(Candidate).filter(Candidate.id == conv.candidate_id).first()
    if not cand:
        raise HTTPException(404, "Candidate not found")
    if not is_profile_extraction_candidate_status(cand.status):
        return CandidateProfileExtractionOut(
            conversation_id=conv.id,
            candidate_id=cand.id,
            external_id=cand.external_id,
            updated=False,
            status=getattr(cand.status, "value", cand.status),
            status_label=candidate_status_label(cand.status),
            reason="status not eligible for idle profile extraction",
        )

    messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
    if not messages:
        return CandidateProfileExtractionOut(
            conversation_id=conv.id,
            candidate_id=cand.id,
            external_id=cand.external_id,
            updated=False,
            status=getattr(cand.status, "value", cand.status),
            status_label=candidate_status_label(cand.status),
            reason="conversation has no messages",
        )

    try:
        extracted = run_profile_extraction(cand, conv, messages)
    except Exception as exc:
        return CandidateProfileExtractionOut(
            conversation_id=conv.id,
            candidate_id=cand.id,
            external_id=cand.external_id,
            updated=False,
            status=getattr(cand.status, "value", cand.status),
            status_label=candidate_status_label(cand.status),
            reason=str(exc),
        )
    if extracted["nickname"]:
        cand.nickname = extracted["nickname"]
    if extracted["city"]:
        cand.city = extracted["city"]
    if extracted["status"]:
        cand.status = extracted["status"]

    result_status = cand.status
    event_payload = {
        "nickname": extracted["nickname"],
        "city": extracted["city"],
        "status": getattr(result_status, "value", result_status),
        "status_label": candidate_status_label(result_status),
        "confidence": extracted["confidence"],
        "reasoning": extracted["reasoning"],
        "raw": extracted["raw"],
        "model": extracted["model"],
    }
    db.add(Event(candidate_id=cand.id, conversation_id=conv.id, event_type=PROFILE_EXTRACTION_EVENT_TYPE, payload=event_payload))
    db.commit()
    return CandidateProfileExtractionOut(
        conversation_id=conv.id,
        candidate_id=cand.id,
        external_id=cand.external_id,
        nickname=cand.nickname,
        city=cand.city,
        updated=True,
        status=getattr(result_status, "value", result_status),
        status_label=candidate_status_label(result_status),
        confidence=extracted["confidence"],
        reason=extracted["reasoning"],
    )


@app.post('/events')
def create_event(payload: EventCreate, db: Session = Depends(get_db)):
    c = ensure_candidate(db, payload.external_id)
    conv_id = None
    if payload.session_key:
        conv = db.query(Conversation).filter(Conversation.session_key == payload.session_key).first()
        conv_id = conv.id if conv else None
    e = Event(candidate_id=c.id, conversation_id=conv_id, event_type=payload.event_type, payload=payload.payload)
    if payload.event_type.startswith("photo_refused"):
        c.refusal_count += 1
    db.add(e)
    db.commit()
    return {"id": e.id}


@app.post('/media/upload')
async def upload_photo(
    external_id: str = Form(...),
    session_key: str = Form(...),
    channel: str = Form("wecom"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    c = ensure_candidate(db, external_id)
    conv = ensure_conversation(db, c.id, session_key, channel)
    suffix = Path(file.filename).suffix or ".jpg"
    path = MEDIA_DIR / f"{secrets.token_hex(16)}{suffix}"
    data = await file.read()
    path.write_bytes(data)
    media = MediaAsset(
        candidate_id=c.id,
        conversation_id=conv.id,
        file_path=str(path),
        original_filename=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        file_size=len(data),
    )
    db.add(media)
    db.flush()
    db.add(Message(candidate_id=c.id, conversation_id=conv.id, sender="user", message_type="image", content=file.filename, media_asset_id=media.id))
    conv.last_active_at = func.now()
    c.status = CandidateStatus.pending_review
    db.add(Event(candidate_id=c.id, conversation_id=conv.id, event_type="photo_received", payload={"media_asset_id": media.id}))
    db.commit()
    return {"asset_id": media.id, "url": f"/media/{media.id}?token={MEDIA_TOKEN}"}


@app.get('/media/{asset_id}')
def read_media(asset_id: int, token: str, db: Session = Depends(get_db)):
    if token != MEDIA_TOKEN:
        raise HTTPException(403, "invalid token")
    asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "Not found")
    return FileResponse(asset.file_path, media_type=asset.mime_type)


@app.get('/metrics/overview')
def metrics_overview(db: Session = Depends(get_db), user=Depends(get_current_user)):
    today = date.today()
    new_candidates = db.query(func.count(Candidate.id)).filter(func.date(Candidate.created_at) == today).scalar() or 0
    photo_candidates_today = (
        db.query(func.count(func.distinct(MediaAsset.candidate_id))).filter(func.date(MediaAsset.created_at) == today).scalar() or 0
    )
    # 发照转化率：历史口径 = 已发照人数 / 历史进入流程总人数；按候选人状态统计（未发送照片 vs 已发送照片）
    total_candidates = db.query(func.count(Candidate.id)).scalar() or 0
    status_photo_sent = (
        CandidateStatus.pending_review,
        CandidateStatus.reviewing,
        CandidateStatus.passed,
        CandidateStatus.rejected,
        CandidateStatus.need_more_photo,
    )
    total_photo_candidates = (
        db.query(func.count(Candidate.id)).filter(Candidate.status.in_(status_photo_sent)).scalar() or 0
    )
    photo_conversion_rate = (
        round((total_photo_candidates / total_candidates * 100), 2) if total_candidates else 0
    )
    return {
        "today_new_candidates": new_candidates,
        "today_photo_candidates": photo_candidates_today,
        "today_photo_conversion_rate": round((photo_candidates_today / new_candidates * 100), 2) if new_candidates else 0,
        "photo_conversion_rate": photo_conversion_rate,
    }


@app.get('/metrics/funnel')
def metrics_funnel(db: Session = Depends(get_db), user=Depends(get_current_user)):
    consult = db.query(func.count(Candidate.id)).scalar() or 0
    photo = db.query(func.count(func.distinct(MediaAsset.candidate_id))).scalar() or 0
    review = db.query(func.count(Candidate.id)).filter(Candidate.status.in_([CandidateStatus.reviewing, CandidateStatus.passed, CandidateStatus.rejected])).scalar() or 0
    passed = db.query(func.count(Candidate.id)).filter(Candidate.status == CandidateStatus.passed).scalar() or 0
    return {"consult": consult, "photo": photo, "review": review, "passed": passed}


@app.get('/prompts/current')
def get_current_prompt(db: Session = Depends(get_db)):
    p = db.query(Prompt).filter(Prompt.is_active == True).order_by(Prompt.created_at.desc()).first()
    examples = db.query(PromptExample).filter(PromptExample.is_reviewed == True).order_by(PromptExample.id.asc()).all()
    if not p:
        return {"version": "v1", "content": build_current_prompt_content("v1", "", examples)}
    content = build_current_prompt_content(p.version, p.content, examples)
    return {"version": p.version, "content": content}


@app.get('/prompts')
def list_prompts(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(Prompt).order_by(Prompt.created_at.desc()).all()
    return [{"id": p.id, "version": p.version, "is_active": p.is_active, "published_by": p.published_by, "created_at": p.created_at} for p in rows]


@app.post('/prompts/publish')
def publish_prompt(payload: PromptPublish, db: Session = Depends(get_db), user=Depends(require_admin)):
    db.query(Prompt).filter(Prompt.is_active == True).update({"is_active": False})
    p = Prompt(version=payload.version, content=payload.content, published_by=payload.published_by, is_active=True)
    db.add(p)
    db.commit()
    return {"ok": True}


@app.post('/prompts/rollback')
def rollback_prompt(payload: PromptRollback, db: Session = Depends(get_db), user=Depends(require_admin)):
    db.query(Prompt).filter(Prompt.is_active == True).update({"is_active": False})
    p = db.query(Prompt).filter(Prompt.version == payload.version).order_by(Prompt.created_at.desc()).first()
    if not p:
        raise HTTPException(404, "Version not found")
    p.is_active = True
    db.commit()
    return {"ok": True}


@app.get('/prompt-examples')
def list_prompt_examples(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(PromptExample).order_by(PromptExample.id.asc()).all()
    return [
        {
            "id": e.id,
            "context_summary": e.context_summary,
            "correct_response": e.correct_response,
            "source": e.source,
            "is_reviewed": e.is_reviewed,
            "created_by": e.created_by,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@app.post('/prompt-examples')
def create_prompt_example(payload: PromptExampleCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    e = PromptExample(
        context_summary=payload.context_summary,
        correct_response=payload.correct_response,
        source=payload.source,
        created_by=user.username,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"id": e.id, "ok": True}


@app.put('/prompt-examples/{example_id}')
def update_prompt_example(example_id: int, payload: PromptExampleUpdate, db: Session = Depends(get_db), user=Depends(require_admin)):
    e = db.query(PromptExample).filter(PromptExample.id == example_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    if payload.context_summary is not None:
        e.context_summary = payload.context_summary
    if payload.correct_response is not None:
        e.correct_response = payload.correct_response
    db.commit()
    return {"ok": True}


@app.delete('/prompt-examples/{example_id}')
def delete_prompt_example(example_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    e = db.query(PromptExample).filter(PromptExample.id == example_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    db.delete(e)
    db.commit()
    return {"ok": True}


@app.patch('/prompt-examples/{example_id}/review')
def review_prompt_example(example_id: int, payload: PromptExampleReview, db: Session = Depends(get_db), user=Depends(require_admin)):
    e = db.query(PromptExample).filter(PromptExample.id == example_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    e.is_reviewed = payload.is_reviewed
    db.commit()
    return {"ok": True}


@app.patch('/prompt-examples/review-all')
def review_all_prompt_examples(payload: PromptExampleReview, db: Session = Depends(get_db), user=Depends(require_admin)):
    db.query(PromptExample).update({"is_reviewed": payload.is_reviewed})
    db.commit()
    return {"ok": True}


@app.get('/export/candidates.csv')
def export_candidates(db: Session = Depends(get_db), user=Depends(get_current_user)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "external_id", "nickname", "city", "status", "created_at"])
    for c in db.query(Candidate).order_by(Candidate.id.asc()).all():
        writer.writerow([c.id, c.external_id, c.nickname, c.city, c.status.value, c.created_at.isoformat()])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=candidates.csv"})


@app.get('/export/funnel.csv')
def export_funnel(db: Session = Depends(get_db), user=Depends(get_current_user)):
    funnel = metrics_funnel(db, user)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["stage", "count"])
    for k in ["consult", "photo", "review", "passed"]:
        writer.writerow([k, funnel[k]])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=funnel.csv"})


@app.get('/export/messages_summary.csv')
def export_messages_summary(db: Session = Depends(get_db), user=Depends(get_current_user)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["candidate_id", "total_messages", "last_message_at"])
    rows = db.query(Message.candidate_id, func.count(Message.id), func.max(Message.created_at)).group_by(Message.candidate_id).all()
    for cid, cnt, last_at in rows:
        writer.writerow([cid, cnt, last_at.isoformat() if last_at else ""])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=messages_summary.csv"})
