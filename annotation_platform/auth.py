from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from pathlib import Path


COOKIE_NAME = "annotation_session"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def load_secret(path: Path) -> bytes:
    raw = Path(path).read_bytes().strip()
    try:
        decoded = bytes.fromhex(raw.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        decoded = raw
    if len(decoded) < 32:
        raise ValueError("session secret must contain at least 32 bytes")
    return decoded


class SessionManager:
    def __init__(self, secret: bytes, ttl_seconds=7 * 24 * 3600, cookie_path="/"):
        if len(secret) < 32:
            raise ValueError("session secret must contain at least 32 bytes")
        self.secret = secret
        self.ttl_seconds = int(ttl_seconds)
        self.cookie_path = cookie_path

    def issue(self, display_name: str, code_hash: str, now_epoch=None):
        issued_at = int(time.time() if now_epoch is None else now_epoch)
        payload = {
            "v": 1,
            "name": display_name,
            "invite": code_hash,
            "csrf": secrets.token_urlsafe(24),
            "iat": issued_at,
            "exp": issued_at + self.ttl_seconds,
        }
        encoded = _b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return encoded + "." + signature, payload

    def verify(self, token: str, now_epoch=None) -> dict:
        try:
            encoded, supplied = str(token or "").split(".", 1)
            expected = _b64encode(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                raise PermissionError("登录状态无效")
            payload = json.loads(_b64decode(encoded).decode("utf-8"))
            current = int(time.time() if now_epoch is None else now_epoch)
            if payload.get("v") != 1 or int(payload.get("exp", 0)) <= current:
                raise PermissionError("登录已过期")
            if not payload.get("name") or not payload.get("invite") or not payload.get("csrf"):
                raise PermissionError("登录状态无效")
            return payload
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError("登录状态无效") from exc

    def from_cookie_header(self, header: str) -> dict:
        cookie = SimpleCookie()
        cookie.load(header or "")
        morsel = cookie.get(COOKIE_NAME)
        if not morsel:
            raise PermissionError("请先登录")
        return self.verify(morsel.value)

    def set_cookie(self, token: str, secure=False) -> str:
        parts = [
            "{}={}".format(COOKIE_NAME, token),
            "Path={}".format(self.cookie_path),
            "Max-Age={}".format(self.ttl_seconds),
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)

    def clear_cookie(self, secure=False) -> str:
        parts = [
            "{}=".format(COOKIE_NAME),
            "Path={}".format(self.cookie_path),
            "Max-Age=0",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)


class RateLimitError(ValueError):
    pass


class RollingRateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int = 60, now_epoch=None):
        stamp = time.monotonic() if now_epoch is None else float(now_epoch)
        cutoff = stamp - int(window_seconds)
        with self.lock:
            bucket = self.events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= int(limit):
                raise RateLimitError("操作过于频繁，请稍后再试")
            bucket.append(stamp)
