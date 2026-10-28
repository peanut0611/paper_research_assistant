"""Loopback-only HTTP adapter for the literature research Agent."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from paper_assistant.web.jobs import ResearchJobService, WebResearchError


ASSETS = Path(__file__).parent / "static"
MAX_BODY_BYTES = 32768


class PaperHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, *, project_root: Path | None = None) -> None:
        self.research = ResearchJobService(project_root or Path.cwd(), threading.Lock())
        super().__init__(("127.0.0.1", port), PaperRequestHandler)

    def server_close(self) -> None:
        self.research.close()
        super().server_close()


class PaperRequestHandler(BaseHTTPRequestHandler):
    server_version = "PaperAssistant"

    def log_message(self, format: str, *args) -> None:
        # Do not log questions, headers, credentials or request bodies.
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self) -> None:
        if not self._valid_host():
            self._json(403, {"error": "请通过本机地址访问页面。"})
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/research.js": ("research.js", "text/javascript; charset=utf-8"),
                  "/conversation.js": ("conversation.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path in assets:
            name, mime = assets[self.path]
            self._send(200, (ASSETS / name).read_bytes(), mime)
        elif self.path == "/api/research/status":
            self._json(200, self.server.research.status())
        elif self.path.startswith("/api/research/jobs/") and self.path.endswith("/conversation"):
            try:
                task_id = self.path.removeprefix("/api/research/jobs/").removesuffix("/conversation")
                self._json(200, self.server.research.conversation(task_id))
            except WebResearchError as error:
                self._json(error.status, {"error": str(error)})
        elif self.path.startswith("/api/research/jobs/"):
            try:
                self._json(200, self.server.research.snapshot(self.path.removeprefix("/api/research/jobs/")))
            except WebResearchError as error:
                self._json(error.status, {"error": str(error)})
        else:
            self._json(404, {"error": "页面不存在。"})

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.headers.get('Host', '')}"
        if (not self._valid_host() or (origin is not None and origin != expected_origin)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._json(403, {"error": "仅接受本机页面提交的研究任务。"})
            return
        if self.path != "/api/research/jobs" and not (
                self.path.startswith("/api/research/jobs/") and self.path.endswith(("/stop", "/conversation", "/conversation/new", "/conversation/reset", "/conversation/select"))):
            self._json(404, {"error": "接口不存在。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "请求必须为 JSON。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("Invalid body size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid request fields")
        except (ValueError, UnicodeError):
            self._json(400, {"error": "请求格式无效或内容过长。"})
            return
        try:
            if self.path == "/api/research/jobs":
                self._json(202, self.server.research.start(payload))
            elif self.path.endswith(("/conversation/new", "/conversation/reset", "/conversation/select")):
                task_path, action = self.path.rsplit("/", 1)
                task_id = task_path.removeprefix("/api/research/jobs/").removesuffix("/conversation")
                self._json(200, self.server.research.manage_conversation(task_id, action, payload))
            elif self.path.endswith("/conversation/stop"):
                if payload:
                    raise WebResearchError("停止请求不接受额外参数。")
                task_id = self.path.removeprefix("/api/research/jobs/").removesuffix("/conversation/stop")
                self._json(200, self.server.research.stop_conversation(task_id))
            elif self.path.endswith("/conversation"):
                task_id = self.path.removeprefix("/api/research/jobs/").removesuffix("/conversation")
                self._json(202, self.server.research.ask(task_id, payload))
            else:
                if payload:
                    raise WebResearchError("停止请求不接受额外参数。")
                task_id = self.path.removeprefix("/api/research/jobs/").removesuffix("/stop")
                self._json(200, self.server.research.stop(task_id))
        except WebResearchError as error:
            self._json(error.status, {"error": str(error)})
        except (OSError, ValueError):
            self._json(503, {"error": "无法创建或读取本地研究任务，请检查本地文件和终端。"})
