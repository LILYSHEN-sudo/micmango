#!/usr/bin/env python3
"""Local web control and history server for MicMango."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from history_store import HistoryStore


APP_DIR = Path(__file__).resolve().parent
INDEX_FILE = APP_DIR / "index.html"
MICMANGO = APP_DIR / "micmango.py"
PYTHON = APP_DIR / ".venv" / "bin" / "python"
PROCESS_LOG = Path("/tmp/micmango.log")
CONFIG_FILE = APP_DIR / "local-only" / "config.json"


def configured_device() -> str | int | None:
    try:
        payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    device = payload.get("audio_device")
    return device if isinstance(device, (str, int)) else None


class LocalAudioApp:
    def __init__(self, device: str | int | None):
        self.device = device if device is not None else configured_device()
        self.history = HistoryStore()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        self._last_error = ""

    def _log_tail(self) -> str:
        try:
            return PROCESS_LOG.read_text(encoding="utf-8", errors="replace")[-2000:].strip()
        except OSError:
            return ""

    def status(self) -> dict[str, object]:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            log_tail = self._log_tail()
            if self._process is not None and not running:
                self._last_error = log_tail or self._last_error
                self._process = None
            ready = running and "模型已就绪" in log_tail
            return {
                "app": "micmango",
                "running": running,
                "ready": ready,
                "state": "ready" if ready else ("starting" if running else "stopped"),
                "device": self.device if self.device is not None else "系统默认麦克风",
                "error": "" if running else self._last_error,
            }

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True, ""
            if not PYTHON.is_file() or not MICMANGO.is_file():
                return False, "找不到 MicMango 程序或 Python 环境。"
            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            path_parts = ["/opt/homebrew/bin", "/usr/local/bin", environment.get("PATH", "")]
            environment["PATH"] = ":".join(part for part in path_parts if part)
            self._last_error = ""
            try:
                with PROCESS_LOG.open("wb") as log_file:
                    command = [str(PYTHON), str(MICMANGO)]
                    if self.device is not None:
                        command.extend(["--device", str(self.device)])
                    command.extend(["--language", "auto"])
                    self._process = subprocess.Popen(
                        command,
                        cwd=str(APP_DIR),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                    )
            except OSError as exc:
                self._process = None
                self._last_error = f"无法启动语音输入：{exc}"
                return False, self._last_error
            return True, ""

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._process = None
                return True, ""
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            self._process = None
            return True, ""


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: LocalAudioApp):
        super().__init__(address, RequestHandler)
        self.app = app


class RequestHandler(BaseHTTPRequestHandler):
    server: AppServer

    def log_message(self, format_string: str, *args: object) -> None:
        sys.stdout.write(f"[{self.log_date_time_string()}] {format_string % args}\n")

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._serve_index()
            return
        if parsed.path == "/api/status":
            payload = self.server.app.status()
            payload["summary"] = self.server.app.history.summary()
            self._json(HTTPStatus.OK, payload)
            return
        if parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            selected_date = query.get("date", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "entries": self.server.app.history.entries(selected_date),
                    "summary": self.server.app.history.summary(),
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "页面不存在。"})

    def do_POST(self) -> None:
        if not self._local_origin_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"error": "请求来源不被允许。"})
            return
        if self.path == "/api/start":
            ok, error = self.server.app.start()
            self._json(
                HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": ok, "error": error, **self.server.app.status()},
            )
            return
        if self.path == "/api/stop":
            ok, error = self.server.app.stop()
            self._json(
                HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": ok, "error": error, **self.server.app.status()},
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})

    def _serve_index(self) -> None:
        try:
            content = INDEX_FILE.read_bytes()
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "找不到 index.html。"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _local_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


def server_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/status", timeout=0.5) as response:
            return json.load(response).get("app") == "micmango"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MicMango local history and controls")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--device", help="Input device name or index; defaults to config/macOS")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--no-start", action="store_true", help="Serve UI without starting dictation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if server_running(url):
        if args.open_browser:
            webbrowser.open(url)
        return 0
    if not INDEX_FILE.is_file():
        print(f"找不到界面文件：{INDEX_FILE}", file=sys.stderr)
        return 2

    app = LocalAudioApp(args.device)
    try:
        server = AppServer(("127.0.0.1", args.port), app)
    except OSError as exc:
        print(f"无法启动语音输入页面：{exc}", file=sys.stderr)
        return 2

    print("MicMango 控制台已启动")
    print(f"打开地址：{url}")
    if not args.no_start:
        threading.Thread(target=app.start, daemon=True).start()
    if args.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
