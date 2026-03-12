from typing import Optional

STATUS_LABELS = {
    "pending_photo": "未发送照片",
    "pending_review": "已发送照片",
    "reviewing": "审核中",
    "passed": "已通过",
    "rejected": "已拒绝",
    "blacklisted": "黑名单",
    "underage_terminated": "未成年终止",
    "need_more_photo": "需补照片",
}
PROFILE_STATUS_ALIASES = {
    "pending_photo": "pending_photo",
    "未发送照片": "pending_photo",
    "未发照片": "pending_photo",
    "未发图": "pending_photo",
    "pending_review": "pending_review",
    "已发送照片": "pending_review",
    "已发照片": "pending_review",
    "已发图": "pending_review",
    "reviewing": "reviewing",
    "审核中": "reviewing",
    "passed": "passed",
    "已通过": "passed",
    "rejected": "rejected",
    "已拒绝": "rejected",
    "blacklisted": "blacklisted",
    "黑名单": "blacklisted",
    "underage_terminated": "underage_terminated",
    "未成年终止": "underage_terminated",
    "need_more_photo": "need_more_photo",
    "需补照片": "need_more_photo",
}
PROFILE_EMPTY_VALUES = {"", "unknown", "n/a", "null", "none", "未提及", "未知", "无", "暂无"}


def _status_value(status) -> Optional[str]:
    if status is None:
        return None
    if isinstance(status, str):
        return status
    return getattr(status, "value", str(status))


def candidate_status_label(status) -> str:
    value = _status_value(status)
    if not value:
        return "未知状态"
    return STATUS_LABELS.get(value, value)


def is_profile_extraction_candidate_status(status) -> bool:
    return _status_value(status) == "pending_photo"


def normalize_candidate_status(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return PROFILE_STATUS_ALIASES.get(text) or PROFILE_STATUS_ALIASES.get(text.lower())


def clean_profile_value(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in PROFILE_EMPTY_VALUES or text in PROFILE_EMPTY_VALUES:
        return None
    return text[:max_length]
