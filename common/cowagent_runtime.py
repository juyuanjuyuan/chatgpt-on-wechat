import os
import re
import time
from typing import Optional

import requests

from common.log import logger


class MCPRuntimeClient:
    def __init__(self):
        self.base_url = os.getenv("MCP_BASE_URL", "http://mcp-api-server:8001")
        self.prompt_cache_ttl = int(os.getenv("PROMPT_CACHE_SECONDS", "30"))
        self._prompt_cache = {"at": 0, "content": ""}

    def _post(self, path: str, payload: dict):
        try:
            requests.post(f"{self.base_url}{path}", json=payload, timeout=5)
        except Exception as e:
            logger.warning(f"[CowAgent] MCP post failed {path}: {e}")

    def log_message(self, external_id: str, session_key: str, channel: str, sender: str, message_type: str, content: str):
        self._post(
            "/messages",
            {
                "external_id": external_id,
                "session_key": session_key,
                "channel": channel,
                "sender": sender,
                "message_type": message_type,
                "content": content,
            },
        )

    def upsert_candidate(self, external_id: str, nickname: Optional[str] = None, city: Optional[str] = None):
        payload = {"external_id": external_id}
        if nickname:
            payload["nickname"] = nickname
        if city:
            payload["city"] = city
        self._post("/candidates/upsert", payload)

    def add_event(self, external_id: str, session_key: str, event_type: str, payload: Optional[dict] = None):
        self._post("/events", {"external_id": external_id, "session_key": session_key, "event_type": event_type, "payload": payload or {}})

    def upload_photo(self, external_id: str, session_key: str, channel: str, image_path: str):
        try:
            with open(image_path, "rb") as f:
                requests.post(
                    f"{self.base_url}/media/upload",
                    data={"external_id": external_id, "session_key": session_key, "channel": channel},
                    files={"file": (os.path.basename(image_path), f, "image/jpeg")},
                    timeout=10,
                )
        except Exception as e:
            logger.warning(f"[CowAgent] upload photo failed: {e}")

    def get_active_prompt(self) -> str:
        now = time.time()
        if now - self._prompt_cache["at"] < self.prompt_cache_ttl and self._prompt_cache["content"]:
            return self._prompt_cache["content"]
        try:
            resp = requests.get(f"{self.base_url}/prompts/current", timeout=5)
            if resp.ok:
                content = resp.json().get("content") or ""
                self._prompt_cache = {"at": now, "content": content}
                return content
        except Exception as e:
            logger.warning(f"[CowAgent] fetch prompt failed: {e}")
        return ""


def is_underage(content: str) -> bool:
    txt = content.lower()
    return bool(re.search(r"(\b1[0-7]\b|未成年|高中生|初中生|17岁|16岁|15岁)", txt))


def is_photo_refusal(content: str) -> bool:
    return bool(re.search(r"(不发|不想发|拒绝|不方便发|不提供照片|不传照片)", content))


_REFUSAL_COUNTER = {}

def increase_refusal_and_check_stop(session_key: str) -> bool:
    n = _REFUSAL_COUNTER.get(session_key, 0) + 1
    _REFUSAL_COUNTER[session_key] = n
    return n > 2
