import csv
import io
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .auth import create_token, get_current_user, hash_password, require_admin, verify_password
from .database import get_db
from .models import Candidate, CandidateStatus, Conversation, Event, MediaAsset, Message, Prompt, User, UserRole
from .schemas import CandidateUpsert, EventCreate, LoginRequest, MessageCreate, PromptPublish, PromptRollback, ReviewUpdate, TokenResponse

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
            "notes": c.notes,
            "refusal_count": c.refusal_count,
        },
        "messages": [
            {"id": m.id, "sender": m.sender, "message_type": m.message_type, "content": m.content, "created_at": m.created_at}
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
    db.commit()
    return {"id": m.id, "candidate_id": c.id, "conversation_id": conv.id}


@app.get('/conversations')
def list_conversations(page: int = 1, page_size: int = 20, db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = db.query(Conversation)
    total = q.count()
    rows = q.order_by(Conversation.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [{"id": r.id, "session_key": r.session_key, "channel": r.channel, "candidate_id": r.candidate_id} for r in rows]}


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
    photo_candidates = (
        db.query(func.count(func.distinct(MediaAsset.candidate_id))).filter(func.date(MediaAsset.created_at) == today).scalar() or 0
    )
    conversion_rate = round((photo_candidates / new_candidates * 100), 2) if new_candidates else 0
    return {
        "today_new_candidates": new_candidates,
        "today_photo_candidates": photo_candidates,
        "today_photo_conversion_rate": conversion_rate,
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
    if not p:
        return {"version": "none", "content": ""}
    return {"version": p.version, "content": p.content}


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
