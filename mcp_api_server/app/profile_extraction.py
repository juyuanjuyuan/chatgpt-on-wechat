import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Candidate, CandidateStatus, Conversation, Message
from .profile_extraction_utils import (
    candidate_status_label,
    clean_profile_value,
    is_profile_extraction_candidate_status,
    normalize_candidate_status,
)

PROFILE_EXTRACTION_EVENT_TYPE = "candidate_profile_extracted"
PROFILE_EXTRACTION_PROMPT_CACHE = {"at": 0.0, "content": ""}
PROFILE_EXTRACTION_PROMPT_TTL = 300


def _resolve_prompt_path(filename: str) -> Path:
    current = Path(__file__).resolve()
    candidates = [
        current.parents[2] / "prompts" / filename,  # repo layout
        current.parents[1] / "prompts" / filename,  # container layout (/app/app -> /app/prompts)
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _first_env(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


def get_profile_extraction_channels() -> list[str]:
    raw = os.getenv("PROFILE_EXTRACTION_CHANNELS", "wechatcom_app,wework")
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_profile_extraction_prompt() -> str:
    now = datetime.now(timezone.utc).timestamp()
    if now - PROFILE_EXTRACTION_PROMPT_CACHE["at"] < PROFILE_EXTRACTION_PROMPT_TTL and PROFILE_EXTRACTION_PROMPT_CACHE["content"]:
        return PROFILE_EXTRACTION_PROMPT_CACHE["content"]
    path = _resolve_prompt_path("candidate_profile_extractor.md")
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"failed to load profile extractor prompt: {exc}") from exc
    PROFILE_EXTRACTION_PROMPT_CACHE["at"] = now
    PROFILE_EXTRACTION_PROMPT_CACHE["content"] = content
    return content
def extract_json_object(raw_text: str) -> dict:
    if not raw_text:
        raise ValueError("empty response")
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def build_profile_extraction_context(candidate: Candidate, conversation: Conversation, messages: list[Message]) -> str:
    lines = [
        f"候选人 external_id: {candidate.external_id}",
        f"当前昵称: {candidate.nickname or '未知'}",
        f"当前城市: {candidate.city or '未知'}",
        f"当前状态: {candidate_status_label(candidate.status)} ({candidate.status.value})",
        f"渠道: {conversation.channel}",
        f"session_key: {conversation.session_key}",
        f"最后活跃: {conversation.last_active_at.isoformat() if conversation.last_active_at else '未知'}",
        "--- 对话历史 ---",
    ]
    for msg in messages:
        role = "候选人" if (msg.sender or "").lower() not in {"assistant", "bot", "ai"} else "北北"
        content = msg.content or f"[{msg.message_type or '消息'}]"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def run_profile_extraction(candidate: Candidate, conversation: Conversation, messages: list[Message]) -> dict:
    import openai

    api_key = _first_env("OPENAI_API_KEY", "OPEN_AI_API_KEY", "open_ai_api_key")
    api_base = _first_env("OPENAI_API_BASE", "OPEN_AI_API_BASE", "open_ai_api_base")
    model = os.getenv("PROFILE_EXTRACTION_MODEL", _first_env("OPENAI_MODEL", "OPEN_AI_MODEL", "MODEL", default="gpt-3.5-turbo"))
    temperature = float(os.getenv("PROFILE_EXTRACTION_TEMPERATURE", "0.1"))
    max_tokens = int(os.getenv("PROFILE_EXTRACTION_MAX_TOKENS", "220"))
    if not api_key:
        raise RuntimeError("open_ai_api_key is not configured")
    if api_base:
        openai.api_base = api_base
    response = openai.ChatCompletion.create(
        api_key=api_key,
        model=model,
        messages=[
            {"role": "system", "content": load_profile_extraction_prompt()},
            {"role": "user", "content": build_profile_extraction_context(candidate, conversation, messages)},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=45,
    )
    content = response.choices[0]["message"]["content"].strip()
    payload = extract_json_object(content)
    normalized_status = normalize_candidate_status(payload.get("status"))
    return {
        "raw": payload,
        "nickname": clean_profile_value(payload.get("nickname"), 128),
        "city": clean_profile_value(payload.get("city"), 64),
        "status": CandidateStatus(normalized_status) if normalized_status else None,
        "confidence": clean_profile_value(payload.get("confidence"), 32),
        "reasoning": clean_profile_value(payload.get("reasoning"), 256),
        "model": model,
    }
