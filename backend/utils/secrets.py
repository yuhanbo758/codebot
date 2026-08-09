"""配置接口的敏感字段脱敏与掩码合并工具。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


MASKED_SECRET = "********"
_SECRET_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "encrypt_key",
    "webhook_url",
    "authorization",
    "cookie",
    "private_key",
)


def is_secret_key(key: object) -> bool:
    """判断配置字段名是否可能承载凭据。"""
    normalized = str(key).strip().lower()
    return any(marker in normalized for marker in _SECRET_MARKERS)


def redact_secrets(value: Any, *, parent_key: str = "") -> Any:
    """递归复制配置，并将非空敏感值替换为固定掩码。"""
    if isinstance(value, dict):
        return {
            key: redact_secrets(item, parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item, parent_key=parent_key) for item in value]
    if is_secret_key(parent_key) and value not in (None, ""):
        return MASKED_SECRET
    return deepcopy(value)


def merge_masked_secrets(existing: Any, updates: Any, *, parent_key: str = "") -> Any:
    """合并前端配置；收到掩码时保留已有密钥，空字符串仍表示主动清空。"""
    if isinstance(existing, dict) and isinstance(updates, dict):
        merged = deepcopy(existing)
        for key, value in updates.items():
            merged[key] = merge_masked_secrets(
                existing.get(key), value, parent_key=str(key)
            )
        return merged
    if is_secret_key(parent_key) and updates == MASKED_SECRET:
        return deepcopy(existing)
    return deepcopy(updates)
