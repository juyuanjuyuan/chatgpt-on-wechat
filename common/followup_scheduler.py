import os
import threading
import time

import openai

from common.cowagent_runtime import MCPRuntimeClient
from common.log import logger
from config import conf

_FOLLOWUP_PROMPT_CACHE = {"at": 0, "content": ""}
_FOLLOWUP_PROMPT_TTL = 300  # re-read file every 5 min


def _env_bool(key: str, default: str) -> bool:
    """Parse env as bool; accept 1, true, yes (case-insensitive)."""
    val = os.getenv(key, default).lower().strip()
    return val in ("1", "true", "yes")


def _load_evaluator_prompt() -> str:
    now = time.time()
    if now - _FOLLOWUP_PROMPT_CACHE["at"] < _FOLLOWUP_PROMPT_TTL and _FOLLOWUP_PROMPT_CACHE["content"]:
        return _FOLLOWUP_PROMPT_CACHE["content"]
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "followup_evaluator.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        _FOLLOWUP_PROMPT_CACHE["at"] = now
        _FOLLOWUP_PROMPT_CACHE["content"] = content
        return content
    except Exception as e:
        logger.warning(f"[FollowupScheduler] failed to load evaluator prompt: {e}")
        return ""


def _format_conversation_for_ai(conv: dict) -> str:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    lines = []
    lines.append(f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)")
    nickname = conv.get("nickname") or conv.get("external_id", "未知")
    lines.append(f"候选人: {nickname}")
    lines.append(f"跟进次数: {conv.get('followup_count', 0)}")
    lines.append(f"最后活跃: {conv.get('last_active_at', '未知')}")
    lines.append("--- 对话记录 ---")
    for msg in conv.get("recent_messages", []):
        role = "北北" if msg["sender"] == "assistant" else "候选人"
        content = msg.get("content") or "[非文本消息]"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


class FollowupScheduler:
    def __init__(self):
        self._followup_interval = int(os.getenv("FOLLOWUP_INTERVAL_SECONDS", "3600"))
        self._min_idle_hours = float(os.getenv("FOLLOWUP_MIN_IDLE_HOURS", "2"))
        self._max_attempts = int(os.getenv("FOLLOWUP_MAX_ATTEMPTS", "3"))
        self._followup_enabled = _env_bool("FOLLOWUP_ENABLED", "false")
        self._profile_enabled = _env_bool("PROFILE_EXTRACTION_ENABLED", "true")
        self._profile_interval = int(os.getenv("PROFILE_EXTRACTION_INTERVAL_SECONDS", "300"))
        self._profile_idle_minutes = int(os.getenv("PROFILE_EXTRACTION_IDLE_MINUTES", "20"))
        self._profile_limit = int(os.getenv("PROFILE_EXTRACTION_BATCH_LIMIT", "50"))
        self._mcp = MCPRuntimeClient()
        self._thread = None
        self._running = False
        self._last_followup_run = 0.0
        self._last_profile_run = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            f"[FollowupScheduler] started – followup_enabled={self._followup_enabled}, "
            f"followup_interval={self._followup_interval}s, idle={self._min_idle_hours}h, max_attempts={self._max_attempts}, "
            f"profile_enabled={self._profile_enabled}, profile_interval={self._profile_interval}s, "
            f"profile_idle={self._profile_idle_minutes}min"
        )

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._run_once()
            except Exception as e:
                logger.error(f"[FollowupScheduler] run_once error: {e}")
            time.sleep(5)

    # ------------------------------------------------------------------
    def _run_once(self):
        now = time.time()
        if self._followup_enabled and now - self._last_followup_run >= self._followup_interval:
            self._run_followups()
            self._last_followup_run = now
        if self._profile_enabled and now - self._last_profile_run >= self._profile_interval:
            self._run_profile_extractions()
            self._last_profile_run = now

    # ------------------------------------------------------------------
    def _run_followups(self):
        conversations = self._mcp.get_pending_followups(
            min_idle_hours=self._min_idle_hours,
            max_followups=self._max_attempts,
        )
        logger.info(f"[FollowupScheduler] scanned {len(conversations)} pending conversations")
        if not conversations:
            return

        sent = 0
        skipped = 0
        for conv in conversations:
            channel = conv.get("channel", "")
            if channel == "web":
                skipped += 1
                continue

            followup_msg = self._evaluate(conv)
            if not followup_msg:
                skipped += 1
                continue

            try:
                self._send_followup(conv, followup_msg)
                sent += 1
            except Exception as e:
                logger.error(
                    f"[FollowupScheduler] send failed for session={conv.get('session_key')}: {e}"
                )
                skipped += 1

        logger.info(f"[FollowupScheduler] done – sent={sent}, skipped={skipped}")

    # ------------------------------------------------------------------
    def _run_profile_extractions(self):
        conversations = self._mcp.get_pending_profile_extractions(
            idle_minutes=self._profile_idle_minutes,
            limit=self._profile_limit,
        )
        logger.info(f"[FollowupScheduler] scanned {len(conversations)} conversations for profile extraction")
        updated = 0
        skipped = 0
        for conv in conversations:
            try:
                result = self._mcp.extract_candidate_profile(conv["conversation_id"])
                if result.get("updated"):
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    f"[FollowupScheduler] profile extraction failed for conversation={conv.get('conversation_id')}: {e}"
                )
                skipped += 1
        logger.info(f"[FollowupScheduler] profile extraction done – updated={updated}, skipped={skipped}")

    # ------------------------------------------------------------------
    def _evaluate(self, conv: dict) -> str:
        """Call AI to evaluate whether this conversation needs a followup.

        Returns the followup message text, or empty string if no followup needed.
        """
        system_prompt = _load_evaluator_prompt()
        if not system_prompt:
            logger.warning("[FollowupScheduler] evaluator prompt is empty, skipping")
            return ""

        user_content = _format_conversation_for_ai(conv)

        try:
            api_key = conf().get("open_ai_api_key")
            api_base = conf().get("open_ai_api_base")
            if api_base:
                openai.api_base = api_base

            response = openai.ChatCompletion.create(
                api_key=api_key,
                model=conf().get("model", "gpt-3.5-turbo"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.7,
                max_tokens=120,
            )
            answer = response.choices[0]["message"]["content"].strip()
            if answer.upper().startswith("NO_FOLLOWUP"):
                return ""
            return answer
        except Exception as e:
            logger.error(f"[FollowupScheduler] AI evaluation error: {e}")
            return ""

    # ------------------------------------------------------------------
    def _send_followup(self, conv: dict, message: str):
        """Send followup message via channel and record it in MCP."""
        session_key = conv["session_key"]
        external_id = conv["external_id"]
        channel_name = conv.get("channel", "")

        self._send_via_channel(channel_name, external_id, session_key, message)

        self._mcp.log_message(
            external_id=external_id,
            session_key=session_key,
            channel=channel_name,
            sender="assistant",
            message_type="text",
            content=message,
        )

        self._mcp.add_event(
            external_id=external_id,
            session_key=session_key,
            event_type="auto_followup",
            payload={"message": message},
        )

        self._sync_session_memory(session_key, message)

        logger.info(f"[FollowupScheduler] sent followup to {external_id} via {channel_name}")

    # ------------------------------------------------------------------
    def _send_via_channel(self, channel_name: str, receiver: str, session_key: str, text: str):
        """Push the followup message through the active channel."""
        from bridge.context import Context, ContextType
        from bridge.reply import Reply, ReplyType

        try:
            from app import get_channel_manager
            mgr = get_channel_manager()
            if mgr is None:
                logger.warning("[FollowupScheduler] channel manager not available")
                return
        except Exception:
            logger.warning("[FollowupScheduler] cannot import channel manager")
            return

        ch = mgr.get_channel(channel_name) or mgr.channel
        if ch is None:
            logger.warning(f"[FollowupScheduler] no channel available for '{channel_name}'")
            return

        context = Context(ContextType.TEXT, text)
        context["receiver"] = receiver
        context["session_id"] = session_key
        context["isgroup"] = False
        reply = Reply(ReplyType.TEXT, text)
        ch.send(reply, context)

    # ------------------------------------------------------------------
    @staticmethod
    def _sync_session_memory(session_key: str, message: str):
        """Append the followup message to the in-memory session so the
        conversation stays coherent when the candidate replies later."""
        try:
            from bridge.bridge import Bridge
            bot = Bridge().get_bot("chat")
            if bot and hasattr(bot, "sessions"):
                session = bot.sessions.build_session(session_key)
                session.add_reply(message)
        except Exception as e:
            logger.debug(f"[FollowupScheduler] session sync skipped: {e}")
