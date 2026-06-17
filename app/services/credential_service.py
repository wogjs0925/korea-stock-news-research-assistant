from __future__ import annotations

import os
import re


SERVICE_NAME = "StockAILab"


class CredentialStoreUnavailableError(RuntimeError):
    pass


class CredentialStoreError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
    except Exception as exc:
        raise CredentialStoreUnavailableError("keyring is not available") from exc
    return keyring


def set_secret(name: str, value: str) -> None:
    if not value:
        raise ValueError("secret value is empty")
    try:
        _keyring().set_password(SERVICE_NAME, name, value)
    except Exception as exc:
        raise CredentialStoreError("failed to save secret") from exc


def get_secret(name: str) -> str | None:
    try:
        value = _keyring().get_password(SERVICE_NAME, name)
    except CredentialStoreUnavailableError:
        return None
    except Exception:
        return None
    return value or None


def has_secret(name: str) -> bool:
    return bool(get_secret(name))


def delete_secret(name: str) -> None:
    try:
        _keyring().delete_password(SERVICE_NAME, name)
    except Exception as exc:
        message = str(exc).lower()
        if "not found" in message or "no password" in message:
            return
        raise CredentialStoreError("failed to delete secret") from exc


def secret_source(name: str, env_name: str | None = None) -> str:
    if has_secret(name):
        return "keyring"
    if os.getenv(env_name or name):
        return "environment"
    return "not_configured"


def resolve_secret(name: str, env_name: str | None = None) -> str | None:
    return get_secret(name) or os.getenv(env_name or name)


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:2]}****{value[-2:]}"


def mask_sensitive_text(value: str) -> str:
    patterns = [
        r"sk-[A-Za-z0-9_\-]+",
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"(?i)(X-Naver-Client-Secret:\s*)[^\s]+",
        r"(?i)(api_key=)[^&\s]+",
        r"(?i)(apiKey=)[^&\s]+",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    ]
    masked = value
    for pattern in patterns:
        masked = re.sub(pattern, lambda m: f"{m.group(1)}***REDACTED***" if m.groups() else "***REDACTED***", masked)
    return masked
