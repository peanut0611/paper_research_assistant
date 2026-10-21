"""Loopback-only HTTP adapter for local RAG testing."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError

from paper_assistant.answer_generation import describe_api_error
from paper_assistant.interactive import LocalPaperSession


ASSETS = Path(__file__).parent / "web"
MAX_BODY_BYTES = 32768


class PaperHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, index_path: Path) -> None:
        self.index_path = index_path
        self._resource = None
        self._fingerprint = None
        self._resource_lock = threading.Lock()
        self.question_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), PaperRequestHandler)

    def resource(self) -> LocalPaperSession:
        with self._resource_lock:
            fingerprint = tuple((name, (self.index_path / name).stat().st_mtime_ns,
                                 (self.index_path / name).stat().st_size)
                                for name in ("manifest.json", "chunks.jsonl", "embeddings.npy"))
            if self._resource is None or fingerprint != self._fingerprint:
                self._resource = LocalPaperSession(self.index_path)
                self._fingerprint = fingerprint
            return self._resource


class PaperRequestHandler(BaseHTTPRequestHandler):
    server_version = "PaperAssistant"

    def log_message(self, format: str, *args) -> None:
        # Do not log questions, headers, credentials or request bodies.
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

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
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path in assets:
            name, mime = assets[self.path]
            self._send(200, (ASSETS / name).read_bytes(), mime)
        elif self.path == "/api/status":
            try:
                resource = self.server.resource()
                self._json(200, {"ready": True, "key_ready": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                                 "files": sorted({chunk.filename for chunk in resource.index.chunks}),
                                 "chunk_count": len(resource.index.chunks)})
            except (ValueError, OSError, TypeError, KeyError):
                self._json(200, {"ready": False, "key_ready": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                                 "files": [], "chunk_count": 0,
                                 "error": "未找到可用索引，请先执行 python -m uv run python scripts/build_dense_index.py --recursive"})
        else:
            self._json(404, {"error": "页面不存在。"})

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.headers.get('Host', '')}"
        if (not self._valid_host() or (origin is not None and origin != expected_origin)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._json(403, {"error": "仅接受本机页面提交的问题。"})
            return
        if self.path != "/api/ask":
            self._json(404, {"error": "接口不存在；PDF 上传暂未启用。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "请求必须为 JSON。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("Invalid body size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) - {"question", "method", "top_k", "token_count_mode"}:
                raise ValueError("Invalid request fields")
        except (ValueError, UnicodeError):
            self._json(400, {"error": "请求格式无效或问题过长。"})
            return
        if not self.server.question_lock.acquire(blocking=False):
            self._json(409, {"error": "已有问题正在处理，请稍后再试。"})
            return
        try:
            result = self.server.resource().ask(payload.get("question"), method=payload.get("method", "hybrid"),
                                                top_k=payload.get("top_k", 3),
                                                token_count_mode=payload.get("token_count_mode", "local"))
            self._json(200, result)
        except APITimeoutError:
            self._json(504, {"error": "请求超时，没有自动重试。请检查 API 用量后再决定是否重试。"})
        except APIConnectionError:
            self._json(502, {"error": "无法连接 OpenAI，请检查网络。没有自动重试。"})
        except APIStatusError as error:
            self._json(502, {"error": describe_api_error(error)})
        except ValueError:
            self._json(400, {"error": "无法完成问答，请检查问题长度、密钥和检索设置。"})
        except OSError:
            self._json(503, {"error": "无法读取索引或模型缓存，请检查本地文件。"})
        except Exception:
            self._json(500, {"error": "本次问答发生异常，没有自动重试。请用命令行工具检查本地配置。"})
        finally:
            self.server.question_lock.release()
