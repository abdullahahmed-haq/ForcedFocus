from __future__ import annotations

import os
import json
import time
import hmac
import mimetypes
import logging
import queue
from pathlib import Path
from urllib.parse import urlparse, unquote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from forcefocus.constants import (
    API_VERSION,
    PRODUCT_VERSION,
    STATE_SCHEMA_VERSION,
    SOUNDS_DIR,
    WEB_DIR,
    WEB_HOST,
    WEB_PORT,
)

class HTTPAPIManager:
    def __init__(self, daemon):
        self.daemon = daemon

    def http_server(self):
        try:
            server = EmbeddedHTTPServer((WEB_HOST, WEB_PORT), EmbeddedWebHandler)
            server.daemon_ref = self.daemon
            server.web_dir = WEB_DIR
            logging.info(
                "Web UI listening at http://%s:%d (serving from %s)",
                WEB_HOST,
                WEB_PORT,
                WEB_DIR,
            )
            server.serve_forever()
        except Exception as exc:
            logging.error("HTTP server failed: %s", exc)

class EmbeddedHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_ref = None
    web_dir = WEB_DIR

class EmbeddedWebHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _is_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin in ("http://localhost:7070", "http://127.0.0.1:7070"):
            return True
        if origin.startswith("chrome-extension://"):
            ext_id = origin.replace("chrome-extension://", "")
            try:
                daemon = self.server.daemon_ref
                allowed_ids = getattr(daemon, "settings", {}).get("allowed_extension_ids", ["hcgpgflhkpdccdjkkobofpaemcgjmhdc"])
                if isinstance(allowed_ids, list):
                    return ext_id in allowed_ids or "*" in allowed_ids
                if isinstance(allowed_ids, str):
                    return ext_id == allowed_ids or allowed_ids == "*"
            except Exception:
                pass
        return False

    def _is_host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        host_name = host.split(":")[0] if host else ""
        return host_name in ("localhost", "127.0.0.1")

    def _is_api_token_valid(self) -> bool:
        token = self.headers.get("X-API-Token")
        if not token:
            return False
        daemon = self.server.daemon_ref
        return hasattr(daemon, "api_token") and hmac.compare_digest(
            token, daemon.api_token
        )

    def _get_cors_origin(self) -> str:
        origin = self.headers.get("Origin")
        if origin and self._is_origin_allowed():
            return origin
        return "http://127.0.0.1:7070"

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self._get_cors_origin())
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Answer Chrome extension preflight requests before authenticated mutations.

        Extension requests that include ``Content-Type: application/json`` and
        ``X-API-Token`` are preflighted by Chrome.  Without this response the
        browser blocks the request before it can reach ``do_POST``.
        """
        if not self._is_host_allowed() or not self._is_origin_allowed():
            self.send_error(403, "Forbidden")
            return

        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._get_cors_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, command: dict) -> None:
        self._send_json(self.server.daemon_ref.command_service.dispatch(command))

    def _send_file(self, filepath: Path, allowed_root: Path | None = None):
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404)
            return
        try:
            filepath.resolve().relative_to((allowed_root or self.server.web_dir).resolve())
        except ValueError:
            self.send_error(403)
            return

        mime, _ = mimetypes.guess_type(str(filepath))
        if mime is None:
            mime = "application/octet-stream"

        body = filepath.read_bytes()
        if filepath.suffix == ".html":
            html_str = body.decode("utf-8", errors="ignore")
            daemon = self.server.daemon_ref
            token = getattr(daemon, "api_token", "")
            inject_js = f'<script>window.apiToken = "{token}";</script>'
            if "<head>" in html_str:
                html_str = html_str.replace("<head>", f"<head>{inject_js}", 1)
            else:
                html_str = inject_js + html_str
            body = html_str.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", self._get_cors_origin())
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        MAX_BODY = 10 * 1024 * 1024
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > MAX_BODY:
            logging.error("Body size %d exceeds MAX_BODY %d", length, MAX_BODY)
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if not self._is_host_allowed():
            self.send_error(403, "Forbidden: invalid Host header")
            return

        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/")
        if not path:
            path = "/"

        if path.startswith("/api/") and not self._is_origin_allowed():
            self._send_json({"status": "error", "error_code": "UNAUTHORIZED", "message": "CORS policy: Origin not allowed."}, 403)
            return

        config_revealing_endpoints = {
            "/api/settings",
            "/api/lists",
            "/api/perma-blocklist",
            "/api/schedules/recurring",
            "/api/templates",
            "/api/groups",
        }
        if path in config_revealing_endpoints:
            if not self._is_api_token_valid():
                self._send_json({"status": "error", "error_code": "UNAUTHORIZED", "message": "Unauthorized: invalid or missing API token."}, 401)
                return

        daemon = self.server.daemon_ref

        if path == "/api/version":
            self._send_json(
                {
                    "status": "ok",
                    "product_version": PRODUCT_VERSION,
                    "api_version": API_VERSION,
                    "state_schema_version": STATE_SCHEMA_VERSION,
                }
            )
        elif path == "/api/health":
            self._dispatch({"action": "health"})
        elif path == "/api/status":
            self._dispatch({"action": "status"})
        elif path == "/api/schedules/recurring":
            self._dispatch({"action": "get_recurring_schedules"})
        elif path == "/api/templates":
            self._dispatch({"action": "get_templates"})
        elif path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", self._get_cors_origin())
            self.end_headers()
            
            q = queue.Queue(maxsize=10)
            daemon.notifications_manager.register_sse_listener(q)
            
            last_written_body = None
            last_written_time = 0.0
            
            try:
                while True:
                    status_data = daemon.session_manager.cmd_get_status()
                    body = json.dumps(status_data)
                    now = time.time()
                    
                    if body != last_written_body or now - last_written_time >= 10.0:
                        self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last_written_body = body
                        last_written_time = now
                        
                    timeout = 0.5 if daemon.state.session.active else 5.0
                    try:
                        q.get(timeout=timeout)
                        while not q.empty():
                            try:
                                q.get_nowait()
                            except queue.Empty:
                                break
                    except queue.Empty:
                        pass
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                daemon.notifications_manager.unregister_sse_listener(q)
            return
        elif path == "/api/session-domains":
            self._dispatch({"action": "get_session_domains"})
        elif path == "/api/lists":
            self._dispatch({"action": "get_lists"})
        elif path == "/api/sounds":
            self._dispatch({"action": "get_sounds"})
        elif path == "/api/settings":
            self._dispatch({"action": "get_settings"})
        elif path == "/api/groups":
            self._dispatch({"action": "get_groups"})
        elif path == "/api/perma-blocklist":
            self._dispatch({"action": "get_perma_blocklist"})
        elif path == "/api/history":
            query_params = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p) if parsed.query else {}
            self._dispatch({"action": "get_history", "query": query_params})
        elif path == "/api/prayer":
            self._dispatch({"action": "get_prayer"})
        elif path == "/" or path == "":
            self._send_file(self.server.web_dir / "html" / "index.html")
        elif path == "/menubar":
            self._send_file(self.server.web_dir / "html" / "menubar.html")
        elif path == "/settings.html" or path == "/settings":
            self._send_file(self.server.web_dir / "html" / "settings.html")
        elif path.startswith("/assets/sounds/"):
            relative_sound = path.removeprefix("/assets/sounds/")
            self._send_file(SOUNDS_DIR / relative_sound, SOUNDS_DIR)
        else:
            self._send_file(self.server.web_dir / path.lstrip("/"))

    def do_POST(self):
        if not self._is_host_allowed():
            self.send_error(403, "Forbidden: invalid Host header")
            return

        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/")
        if not path:
            path = "/"

        if not self._is_origin_allowed():
            self._send_json({"status": "error", "error_code": "UNAUTHORIZED", "message": "CORS policy: Origin not allowed."}, 403)
            return

        if not self._is_api_token_valid():
            self._send_json({"status": "error", "error_code": "UNAUTHORIZED", "message": "Unauthorized: invalid or missing API token."}, 401)
            return

        body = self._read_body()
        if path == "/api/start":
            cmd = {
                "action": "start",
                "duration_minutes": body.get("duration", 120),
                "mode": body.get("mode", "blacklist"),
                "session_type": body.get("session_type", "standard"),
                "focus_minutes": body.get("focus_minutes", 25),
                "break_minutes": body.get("break_minutes", 5),
                "cycles": body.get("cycles", 4),
                "groups": body.get("groups", []),
                "intent": body.get("intent", ""),
                "intent_tasks": body.get("intent_tasks", []),
            }
            if "schedule_in" in body:
                cmd["schedule_in_minutes"] = body["schedule_in"]
            if "schedule_at" in body:
                cmd["schedule_at_time"] = body["schedule_at"]
            self._dispatch(cmd)
        elif path == "/api/cancel-schedule":
            self._dispatch({**body, "action": "cancel_schedule"})
        elif path == "/api/prayer/skip":
            self._dispatch({**body, "action": "skip_prayer"})
        elif path == "/api/intent":
            self._dispatch({**body, "action": "set_intent"})
        elif path == "/api/settings":
            self._dispatch({**body, "action": "save_settings"})
        elif path == "/api/upload-sound":
            self._dispatch({**body, "action": "upload_sound"})
        elif path == "/api/delete-sound":
            self._dispatch({**body, "action": "delete_sound"})
        elif path == "/api/stop":
            self._dispatch({"action": "stop", "key": body.get("key", "")})
        elif path == "/api/cancel-stop":
            self._dispatch({"action": "cancel_stop"})
        elif path == "/api/schedules/recurring":
            self._dispatch({**body, "action": "add_recurring_schedule"})
        elif path.startswith("/api/schedules/recurring/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                self._dispatch({**body, "action": "update_recurring_schedule", "id": parts[3]})
            elif len(parts) == 5 and parts[4] == "pause":
                self._dispatch({**body, "action": "pause_recurring_schedule", "id": parts[3]})
            elif len(parts) == 5 and parts[4] == "resume":
                self._dispatch({**body, "action": "resume_recurring_schedule", "id": parts[3]})
            elif len(parts) == 5 and parts[4] == "duplicate":
                self._dispatch({**body, "action": "duplicate_recurring_schedule", "id": parts[3]})
            else:
                self._send_json({"status": "error", "error_code": "NOT_FOUND", "message": "Unknown endpoint."}, 404)
        elif path == "/api/templates":
            self._dispatch({**body, "action": "add_template"})
        elif path.startswith("/api/templates/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[3] == "start":
                self._dispatch({"action": "start_template", "id": parts[2]})
            elif len(parts) == 4 and parts[3] == "duplicate":
                self._dispatch({**body, "action": "duplicate_template", "id": parts[2]})
            elif len(parts) == 3:
                self._dispatch({**body, "action": "update_template", "id": parts[2]})
            else:
                self._send_json({"status": "error", "error_code": "NOT_FOUND", "message": "Unknown endpoint."}, 404)
        elif path.startswith("/api/lists/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[3] == "bulk":
                cmd = {
                    "action": "add_domains",
                    "list": parts[2],
                    "domains": body.get("domains", []),
                }
                self._dispatch(cmd)
            else:
                cmd = {
                    "action": "add_domain",
                    "list": parts[2],
                    "domain": body.get("domain", ""),
                }
                self._dispatch(cmd)
        elif path == "/api/groups":
            cmd = {
                "action": "add_group",
                "name": body.get("name", ""),
                "domains": body.get("domains", []),
            }
            self._dispatch(cmd)
        elif path == "/api/perma-blocklist":
            cmd = {
                "action": "add_perma_block",
                "domain": body.get("domain", ""),
                "domains": body.get("domains", []),
            }
            self._dispatch(cmd)
        elif path == "/api/perma-blocklist/unblock":
            cmd = {
                "action": "request_perma_unblock",
                "domain": body.get("domain", ""),
                "key": body.get("key", ""),
            }
            self._dispatch(cmd)
        elif path == "/api/perma-blocklist/cancel-unblock":
            cmd = {
                "action": "cancel_perma_unblock",
                "domain": body.get("domain", ""),
            }
            self._dispatch(cmd)
        else:
            self._send_json({"status": "error", "error_code": "NOT_FOUND", "message": "Unknown endpoint."}, 404)

    def do_DELETE(self):
        if not self._is_host_allowed():
            self.send_error(403, "Forbidden: invalid Host header")
            return

        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/")
        if not path:
            path = "/"

        if not self._is_origin_allowed():
            self._send_json({"status": "error", "error_code": "UNAUTHORIZED", "message": "CORS policy: Origin not allowed."}, 403)
            return

        if not self._is_api_token_valid():
            self._send_json({"status": "error", "error_code": "UNAUTHORIZED", "message": "Unauthorized: invalid or missing API token."}, 401)
            return

        parts = path.strip("/").split("/")

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "lists":
            cmd = {
                "action": "remove_domain",
                "list": parts[2],
                "domain": "/".join(parts[3:]),
            }
            self._dispatch(cmd)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "groups":
            cmd = {
                "action": "remove_group",
                "name": parts[2],
            }
            self._dispatch(cmd)
        elif len(parts) == 4 and parts[0] == "api" and parts[1] == "schedules" and parts[2] == "recurring":
            cmd = {
                "action": "remove_recurring_schedule",
                "id": parts[3]
            }
            self._dispatch(cmd)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "templates":
            self._dispatch({"action": "remove_template", "id": parts[2]})
        elif len(parts) == 2 and parts[0] == "api" and parts[1] == "history":
            self._dispatch({"action": "clear_history"})
        else:
            self._send_json({"status": "error", "error_code": "NOT_FOUND", "message": "Unknown endpoint."}, 404)

    def do_OPTIONS(self):
        if not self._is_host_allowed():
            self.send_error(403, "Forbidden: invalid Host header")
            return

        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._get_cors_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token")
        self.end_headers()

    def __getattr__(self, name):
        if name.startswith("do_"):
            return lambda: self._send_json({"status": "error", "error_code": "METHOD_NOT_ALLOWED", "message": "Method not allowed."}, 405)
        raise AttributeError(name)
