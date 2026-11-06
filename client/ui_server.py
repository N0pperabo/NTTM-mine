from __future__ import annotations

"""
Local asset and RPC server for N0PPERABO offline browser.
Serves static assets, cached offline web pages, shell/start page,
and provides the HTTP/JSON RPC bridge for external browser mode (--browser).
"""

import json
import logging
import urllib.parse
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .browser_ui import OfflineBrowserUI
    from .ui_bridge import WebviewApiBridge

logger = logging.getLogger("BrowserUI")


class LocalAssetHandler(SimpleHTTPRequestHandler):
    """
    HTTP server serving static assets, cached offline web pages,
    and the integrated browser shell. Also provides an HTTP/JSON RPC
    bridge when running in external browser mode (--browser).
    """

    cache_dir: Path = Path.home() / ".gh_offline_browser" / "cache"
    shell_html: str = ""
    start_html: str = ""
    ui: Optional["OfflineBrowserUI"] = None
    api: Optional["WebviewApiBridge"] = None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/call":
            length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                method_name = data.get("method", "")
                args = data.get("args", [])
                if not isinstance(args, list):
                    args = []

                if self.api and hasattr(self.api, method_name):
                    method = getattr(self.api, method_name)
                    result = method(*args)
                    resp = {"status": "ok", "result": result}
                else:
                    resp = {"status": "error", "message": f"Method {method_name} not found"}
            except Exception as e:
                logger.exception(f"API call error: {e}")
                resp = {"status": "error", "message": str(e)}

            resp_bytes = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/events":
            events = []
            if self.ui:
                events = self.ui.pop_js_events()
            resp_bytes = json.dumps(events).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if path in ("/", "/index.html", "/shell"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.shell_html.encode("utf-8"))
            return

        if path == "/start":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.start_html.encode("utf-8"))
            return

        if path in ("/logo.svg", "/assets/logo.svg"):
            logo_file = Path(__file__).parent / "assets" / "logo.svg"
            if logo_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(logo_file, "rb") as f:
                    self.wfile.write(f.read())
                return

        if path.startswith("/assets/fonts/"):
            font_file = Path(__file__).parent / "assets" / "fonts" / Path(path).name
            if font_file.exists() and font_file.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "font/woff2")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(font_file, "rb") as f:
                    self.wfile.write(f.read())
                return

        if path.startswith("/cache/"):
            rel_path = path[len("/cache/") :]
            file_path = self.cache_dir / rel_path
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                mime = {
                    ".css": "text/css; charset=utf-8",
                    ".mjs": "application/javascript",
                    ".json": "application/json",
                    ".woff2": "font/woff2",
                    ".woff": "font/woff",
                    ".ttf": "font/ttf",
                    ".otf": "font/otf",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".svg": "image/svg+xml",
                    ".webp": "image/webp",
                    ".avif": "image/avif",
                    ".ico": "image/x-icon",
                    ".mp3": "audio/mpeg",
                    ".m4a": "audio/mp4",
                    ".mp4": "video/mp4",
                    ".webm": "video/webm",
                }.get(file_path.suffix.lower())
                if file_path.suffix.lower() == ".html":
                    mime = "text/html; charset=utf-8"
                elif file_path.suffix.lower() in (".js", ".mjs"):
                    mime = "application/javascript"
                if not mime:
                    mime = "application/octet-stream"
                self.send_header("Content-Type", mime)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' data:; font-src 'self' data:; media-src 'self' blob: data:; connect-src 'self'; form-action 'none';",
                )
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
                return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        # Silence standard HTTP logs to avoid clutter
        pass
