from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

from .auth import RateLimitError, RollingRateLimiter, SessionManager, load_secret
from .exporter import export_snapshot
from .stale_cache import StaleCache
from .store import ConflictError, Store


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_handler(
    store: Store,
    static_root: Path,
    export_root: Path,
    session_manager: SessionManager | None = None,
    auth_required: bool = False,
    cookie_secure: bool = False,
):
    if auth_required and session_manager is None:
        raise ValueError("session manager required when authentication is enabled")
    blocked_reviewers = {
        unquote(value).strip()
        for value in os.environ.get("ANNOTATION_BLOCKED_REVIEWERS", "").split(",")
        if unquote(value).strip()
    }
    summary_cache = StaleCache(store.summary, 15)
    dashboard_cache = StaleCache(store.dashboard, 15)
    summary_cache.get()
    progress_cache = {"at": 0.0, "value": None}
    progress_cache_lock = threading.Lock()
    bind_limiter = RollingRateLimiter()
    write_limiter = RollingRateLimiter()
    submit_limiter = RollingRateLimiter()
    recheck_limiter = RollingRateLimiter()

    def cached_progress_dashboard():
        with progress_cache_lock:
            stamp = time.monotonic()
            if progress_cache["value"] is None or stamp - progress_cache["at"] >= 20:
                progress_cache["value"] = store.progress_dashboard()
                progress_cache["at"] = stamp
            return progress_cache["value"]

    class Handler(SimpleHTTPRequestHandler):
        server_version = "HumanAnnotation/1"

        def log_message(self, fmt, *args):
            # Keep request metadata only; payloads, invite codes and model text are excluded.
            print("%s - %s" % (self.address_string(), fmt % args))

        def json_response(self, value, status=200, headers=None):
            data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 262144:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def client_ip(self):
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            return (forwarded or self.client_address[0])[:128]

        def current_session(self, optional=False):
            if not auth_required:
                return None
            try:
                payload = session_manager.from_cookie_header(self.headers.get("Cookie", ""))
                if not store.bound_invite_is_active(payload["invite"], payload["name"]):
                    raise PermissionError("登录身份已停用")
                return payload
            except PermissionError:
                if optional:
                    return None
                raise

        def auth(self, write=False):
            if auth_required:
                session = self.current_session()
                actor = session["name"]
                if write:
                    supplied = self.headers.get("X-CSRF-Token", "")
                    if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
                        raise PermissionError("请求校验失败，请刷新页面后重试")
            else:
                actor = unquote(self.headers.get("X-Reviewer", "")).strip() or os.environ.get(
                    "ANNOTATION_DEMO_REVIEWER", "演示标注员"
                )
                if not actor or len(actor) > 64 or any(ord(char) < 32 for char in actor):
                    raise PermissionError("reviewer identity required")
            if actor in blocked_reviewers:
                raise PermissionError("reviewer blocked")
            return actor

        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/health":
                return self.json_response({"ok": True, "schema": 9, **summary_cache.get()})
            if path == "/api/session":
                if not auth_required:
                    return self.json_response({
                        "authenticated": True,
                        "reviewer": os.environ.get("ANNOTATION_DEMO_REVIEWER", "演示标注员"),
                        "csrf": "",
                    })
                session = self.current_session(optional=True)
                if not session:
                    return self.json_response({"authenticated": False})
                return self.json_response({
                    "authenticated": True,
                    "reviewer": session["name"],
                    "csrf": session["csrf"],
                })
            if path.startswith("/api/"):
                try:
                    actor = self.auth()
                except PermissionError as exc:
                    return self.json_response({"error": str(exc)}, 401)
                if path == "/api/summary":
                    return self.json_response(summary_cache.get())
                if path == "/api/dashboard":
                    return self.json_response(dashboard_cache.get())
                if path == "/api/progress-dashboard":
                    return self.json_response(cached_progress_dashboard())
                if path == "/api/reviewer-leaderboard":
                    q = parse_qs(parsed.query)
                    return self.json_response(store.reviewer_leaderboard(
                        (q.get("date") or [""])[0]
                    ))
                if path == "/api/recheck-pick":
                    q = parse_qs(parsed.query)
                    pool = (q.get("pool") or [""])[0]
                    mode = (q.get("mode") or [""])[0]
                    item = store.recheck_pick(
                        pool,
                        ordinal=(q.get("ordinal") or [None])[0] if mode == "ordinal" else None,
                        random_pick=mode == "random",
                        start=(q.get("start") or [1])[0],
                        end=(q.get("end") or [2147483647])[0],
                        pending_only=(q.get("pending") or ["0"])[0] == "1",
                    )
                    return self.json_response(
                        item or {"error": "该组不在当前复核池，或复核池暂无数据"},
                        200 if item else 404,
                    )
                if path == "/api/random-special-item":
                    q = parse_qs(parsed.query)
                    item = store.random_special_item(
                        (q.get("kind") or [""])[0],
                        (q.get("start") or [1])[0],
                        (q.get("end") or [2147483647])[0],
                        actor,
                    )
                    return self.json_response(
                        item or {"error": "当前批次暂无可复标数据"},
                        200 if item else 404,
                    )
                if path == "/api/browse":
                    q = parse_qs(parsed.query)
                    return self.json_response(store.browse(
                        (q.get("q") or [""])[0], (q.get("kind") or ["all"])[0],
                        (q.get("min_wrong") or [0])[0], (q.get("min_unknown") or [0])[0],
                        (q.get("limit") or [100])[0], (q.get("offset") or [0])[0],
                    ))
                if path == "/api/queue":
                    q = parse_qs(parsed.query)
                    rows = store.queue(
                        (q.get("status") or ["unreviewed"])[0], (q.get("q") or [""])[0],
                        (q.get("limit") or [50])[0], (q.get("after") or [0])[0],
                    )
                    return self.json_response({"items": rows})
                if path == "/api/by-ordinal":
                    q = parse_qs(parsed.query)
                    item = store.item_by_ordinal((q.get("ordinal") or [1])[0])
                    return self.json_response(item or {"error": "not found"}, 200 if item else 404)
                if path == "/api/navigate":
                    q = parse_qs(parsed.query)
                    item = store.navigate(
                        (q.get("ordinal") or [0])[0], (q.get("direction") or [1])[0],
                        (q.get("status") or [""])[0], (q.get("start") or [1])[0],
                        (q.get("end") or [2147483647])[0], (q.get("kind") or [""])[0], actor,
                    )
                    return self.json_response(item or {"error": "not found"}, 200 if item else 404)
                if path.startswith("/api/items/"):
                    item = store.item(path.rsplit("/", 1)[-1])
                    return self.json_response(item or {"error": "not found"}, 200 if item else 404)
                return self.json_response({"error": "not found"}, 404)

            if path == "/" or path == "/index.html":
                target = static_root / "dashboard.html"
            else:
                name = path.lstrip("/")
                root = static_root.resolve()
                target = (root / name).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    return self.send_error(404)
                if not target.is_file():
                    return self.send_error(404)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self.body()
                if path == "/api/auth/bind":
                    if not auth_required:
                        raise PermissionError("邀请码登录未启用")
                    bind_limiter.check("bind:" + self.client_ip(), 10, 300)
                    result = store.bind_invite(
                        body.get("invite_code", ""), body.get("display_name", ""),
                        self.client_ip(), self.headers.get("User-Agent", ""),
                    )
                    token, payload = session_manager.issue(result["display_name"], result["code_hash"])
                    return self.json_response({
                        "authenticated": True,
                        "reviewer": result["display_name"],
                        "csrf": payload["csrf"],
                        "action": result["action"],
                    }, headers={"Set-Cookie": session_manager.set_cookie(token, cookie_secure)})
                if path == "/api/auth/logout":
                    session = self.current_session(optional=True)
                    if session:
                        supplied = self.headers.get("X-CSRF-Token", "")
                        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
                            raise PermissionError("请求校验失败，请刷新页面后重试")
                        store.record_auth_event(
                            "logout", session["name"], client_ip=self.client_ip(),
                            user_agent=self.headers.get("User-Agent", ""),
                        )
                    return self.json_response(
                        {"ok": True},
                        headers={"Set-Cookie": session_manager.clear_cookie(cookie_secure)},
                    )

                actor = self.auth(write=True)
                write_limiter.check("write:" + actor.casefold(), 90, 60)
                if path.endswith("/recheck"):
                    recheck_limiter.check("recheck:" + actor.casefold(), 60, 60)
                elif path.endswith("/submit") or path == "/api/exports":
                    submit_limiter.check("submit:" + actor.casefold(), 20, 60)
                if path == "/api/claims":
                    return self.json_response(store.claim(body["event_id"], actor, body["idempotency_key"]))
                if path.startswith("/api/items/") and path.endswith(("/draft", "/submit")):
                    event_id = path.split("/")[3]
                    return self.json_response(store.save(
                        event_id, actor, body["idempotency_key"], body["version"],
                        body["slots"], path.endswith("/submit"),
                    ))
                if path.startswith("/api/items/") and path.endswith("/recheck"):
                    event_id = path.split("/")[3]
                    return self.json_response(store.save_recheck(
                        event_id, body.get("slot"), actor, body["idempotency_key"],
                        body.get("verdict"), body.get("note", ""), body.get("pool", ""),
                        body.get("final_verdict"), body.get("final_r"), body.get("final_h"),
                        body.get("final_reason_code"),
                    ))
                if path == "/api/exports":
                    target = export_snapshot(store.db_path, export_root)
                    return self.json_response({"export_id": target.name})
                return self.json_response({"error": "not found"}, 404)
            except RateLimitError as exc:
                return self.json_response({"error": str(exc)}, 429, {"Retry-After": "60"})
            except PermissionError as exc:
                return self.json_response({"error": str(exc)}, 401)
            except ConflictError as exc:
                return self.json_response({"error": "conflict", "detail": str(exc)}, 409)
            except KeyError as exc:
                return self.json_response({"error": "not found", "detail": str(exc)}, 404)
            except (ValueError, json.JSONDecodeError) as exc:
                return self.json_response({"error": "invalid request", "detail": str(exc)}, 400)
            except Exception:
                return self.json_response({"error": "internal error"}, 500)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18068)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("ANNOTATION_DB", "review.sqlite3")))
    parser.add_argument("--audit", type=Path, default=Path(os.environ.get("ANNOTATION_AUDIT", "audit.jsonl")))
    parser.add_argument("--static", type=Path, default=Path(os.environ.get("ANNOTATION_STATIC", Path(__file__).with_name("static"))))
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--exports", type=Path, default=Path(os.environ.get("ANNOTATION_EXPORTS", "exports")))
    args = parser.parse_args()
    store = Store(args.db, args.audit)
    if args.registry:
        print(json.dumps({"imported": store.import_registry(args.registry)}))
    auth_required = os.environ.get("ANNOTATION_AUTH_REQUIRED", "0").lower() in {"1", "true", "yes"}
    session_manager = None
    if auth_required:
        secret_file = os.environ.get("ANNOTATION_SESSION_SECRET_FILE", "")
        if not secret_file:
            raise RuntimeError("ANNOTATION_SESSION_SECRET_FILE is required")
        session_manager = SessionManager(
            load_secret(Path(secret_file)),
            int(os.environ.get("ANNOTATION_SESSION_TTL", 7 * 24 * 3600)),
            os.environ.get("ANNOTATION_COOKIE_PATH", "/"),
        )
    cookie_secure = os.environ.get("ANNOTATION_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
    print(f"listening on {args.host}:{args.port} auth={'required' if auth_required else 'legacy'}")
    ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(store, args.static, args.exports, session_manager, auth_required, cookie_secure),
    ).serve_forever()


if __name__ == "__main__":
    main()
