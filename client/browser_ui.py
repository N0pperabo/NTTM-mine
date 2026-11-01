import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import webview

from .config import BrowserConfig
from .session_manager import SessionManager
from .transport import FetchResult, GitHubTransport

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
            if not logo_file.exists():
                desktop_logo = Path.home() / "Desktop" / "N0pperabo_logo_transparent.svg"
                if desktop_logo.exists():
                    logo_file = desktop_logo
            if logo_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(logo_file, "rb") as f:
                    self.wfile.write(f.read())
                return

        if path.startswith("/cache/"):
            rel_path = path[len("/cache/") :]
            file_path = self.cache_dir / rel_path
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                # Guess mime type
                mime = "text/html; charset=utf-8" if file_path.suffix == ".html" else "application/octet-stream"
                if file_path.suffix == ".css":
                    mime = "text/css; charset=utf-8"
                elif file_path.suffix in (".js", ".mjs"):
                    mime = "application/javascript"
                elif file_path.suffix in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                    mime = f"image/{file_path.suffix.lstrip('.')}"
                self.send_header("Content-Type", mime)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self' 'unsafe-inline' data: blob: http://127.0.0.1:*; img-src 'self' data: blob: http://127.0.0.1:*; style-src 'self' 'unsafe-inline' data:; font-src 'self' data:; connect-src 'none'; form-action 'none';",
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


class OfflineBrowserUI:
    """
    PyWebView Shell, Action Queue UI, AJAX Trap & Deep DOM Interceptor.
    """

    def __init__(self, transport: GitHubTransport, session_manager: SessionManager):
        self.transport = transport
        self.config = transport.config
        self.session_manager = session_manager
        self.window = None
        self.server_port = 0
        self.server_thread = None
        self._browser_mode: bool = False
        self._js_events: List[str] = []
        self._js_events_lock = threading.Lock()

        # History navigation
        self.history: List[Dict[str, str]] = []
        self.history_index: int = -1

        # Current navigation state
        self.current_url: str = "about:blank"
        self.is_busy: bool = False
        self.is_batch_mode: bool = False
        self.action_queue: List[Dict[str, Any]] = []
        self.current_delay_seconds: int = 0

    def push_js_event(self, js_code: str):
        with self._js_events_lock:
            self._js_events.append(js_code)

    def pop_js_events(self) -> List[str]:
        with self._js_events_lock:
            events = list(self._js_events)
            self._js_events.clear()
            return events

    def start_local_server(self) -> int:
        """Starts the local HTTP server on a free port."""
        LocalAssetHandler.cache_dir = self.transport.cache_base
        LocalAssetHandler.shell_html = self._generate_shell_html()
        LocalAssetHandler.start_html = self._generate_start_page_html()
        LocalAssetHandler.ui = self
        LocalAssetHandler.api = WebviewApiBridge(self)

        server = HTTPServer(("127.0.0.1", 0), LocalAssetHandler)
        self.server_port = server.server_port

        def run_server():
            server.serve_forever()

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Local UI server running at http://127.0.0.1:{self.server_port}/")
        return self.server_port

    def launch(self, browser_mode: bool = False):
        """Launches the PyWebView window or opens the system default browser."""
        self._browser_mode = browser_mode
        port = self.start_local_server()
        url = f"http://127.0.0.1:{port}/"

        if browser_mode:
            import webbrowser
            logger.info(f"Opening default browser at {url} (--browser mode)...")
            webbrowser.open(url)
            logger.info("NTTM Server is active. Press Ctrl+C in terminal to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Browser mode closed by user.")
            return

        api = WebviewApiBridge(self)

        self.window = webview.create_window(
            title="N0PPERABO - مرورگر آفلاین NTTM",
            url=url,
            js_api=api,
            width=1366,
            height=850,
            min_size=(1024, 700),
            background_color="#050608",
        )
        webview.start(debug=False)

    # ---------------- UI Action Handlers ----------------

    def set_delay(self, seconds: Any):
        try:
            self.current_delay_seconds = max(0, int(seconds or 0))
        except Exception:
            self.current_delay_seconds = 0
        logger.info(f"Custom delay set to {self.current_delay_seconds}s in Python UI state.")

    def get_and_consume_delay(self, param_delay: Any = 0) -> int:
        """Returns the delay in seconds and resets it back to 0."""
        delay = 0
        try:
            delay = int(param_delay or 0)
        except Exception:
            delay = 0
        if delay <= 0:
            delay = self.current_delay_seconds

        # Reset after consuming so delay is one-shot
        self.current_delay_seconds = 0
        return max(0, delay)

    def dispatch_navigate(self, target_url: str, custom_delay_seconds: int = 0):
        """Handles URL bar or link navigation."""
        if self.is_busy:
            self._notify_ui("سیستم در حال حاضر مشغول پردازش درخواست قبلی است.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)

        def task():
            self.is_busy = True
            msg = f"در حال آماده‌سازی و ارسال به گیت‌هاب (با تأخیر {delay} ثانیه‌ای در رانر)..." if delay > 0 else "در حال آماده‌سازی و ارسال به گیت‌هاب..."
            self._set_loading(True, msg)

            def on_status(msg):
                self._update_status(msg)

            res = self.transport.fetch(
                target_url=target_url,
                mode="navigate",
                custom_delay_seconds=delay,
                status_callback=on_status,
            )
            self._handle_fetch_result(res, target_url)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def dispatch_form_submit(
        self, action_url: str, method: str, content_type: str, body_base64: str, custom_delay_seconds: int = 0
    ):
        """Handles form submission."""
        if self.is_busy:
            self._notify_ui("درخواست قبلی هنوز در حال پردازش است.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)

        def task():
            self.is_busy = True
            msg = f"در حال ارسال اطلاعات فرم به گیت‌هاب (با تأخیر {delay} ثانیه‌ای در رانر)..." if delay > 0 else "در حال ارسال اطلاعات فرم به گیت‌هاب..."
            self._set_loading(True, msg)

            body_bytes = base64.b64decode(body_base64) if body_base64 else b""

            res = self.transport.fetch(
                target_url=action_url,
                mode="form",
                method=method,
                content_type=content_type,
                body_bytes=body_bytes,
                custom_delay_seconds=delay,
                status_callback=self._update_status,
            )
            self._handle_fetch_result(res, action_url)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def dispatch_replay(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_delay_seconds: int = 0,
    ):
        """Handles single or batch replay execution."""
        if self.is_busy:
            self._notify_ui("درخواست قبلی در حال پردازش است.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)

        def task():
            self.is_busy = True
            msg = f"در حال بازپخش {len(actions)} اکشن در رانر (با تأخیر {delay} ثانیه‌ای)..." if delay > 0 else f"در حال بازپخش {len(actions)} اکشن در رانر..."
            self._set_loading(True, msg)

            mode = "replay"
            if not actions and intercepted_ajax and intercepted_ajax.get("captured"):
                mode = "ajax_fallback"

            res = self.transport.fetch(
                target_url=target_url or self.current_url,
                mode=mode,
                actions=actions,
                intercepted_ajax=intercepted_ajax,
                custom_delay_seconds=delay,
                status_callback=self._update_status,
            )
            self._handle_fetch_result(res, target_url or self.current_url)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def _handle_fetch_result(self, result: FetchResult, requested_url: str):
        if not result.success:
            self._notify_ui(f"خطا: {result.error_message}", is_error=True)
            return

        if result.response_type == "file":
            filename = result.meta.get("download_filename") or Path(result.downloaded_file_path or "").name
            msg = f"فایل با موفقیت دانلود شد: {filename}"
            self._notify_ui(msg, is_file=True)
            return

        # Web page response
        if result.index_html_path:
            p = Path(result.index_html_path)
            # URL path under local server: /cache/<request_id>/extracted/index.html
            rel = p.relative_to(self.transport.cache_base)
            local_frame_url = f"/cache/{rel.as_posix()}"
            final_url = result.meta.get("final_url", requested_url)
            title = result.meta.get("title", final_url)

            # Update history
            self.current_url = final_url
            if self.history_index < len(self.history) - 1:
                self.history = self.history[: self.history_index + 1]
            self.history.append({"url": final_url, "local_url": local_frame_url, "title": title})
            self.history_index = len(self.history) - 1

            # Tell shell to load iframe and update address bar
            self._call_js(
                f"window.shellApp.onPageLoaded('{local_frame_url}', '{final_url}', {json.dumps(title)})"
            )

    # ---------------- Helper UI Communicators ----------------

    def _call_js(self, js_code: str):
        if self.window:
            try:
                self.window.evaluate_js(js_code)
            except Exception as e:
                logger.debug(f"JS evaluation error: {e}")
        self.push_js_event(js_code)

    def _set_loading(self, is_loading: bool, message: str = ""):
        js = f"window.shellApp.setLoading({json.dumps(is_loading)}, {json.dumps(message)})"
        self._call_js(js)

    def _update_status(self, message: str):
        js = f"window.shellApp.updateStatus({json.dumps(message)})"
        self._call_js(js)

    def _notify_ui(self, message: str, is_error: bool = False, is_file: bool = False):
        js = f"window.shellApp.showToast({json.dumps(message)}, {json.dumps(is_error)}, {json.dumps(is_file)})"
        self._call_js(js)

    def open_downloads_folder(self):
        """Opens user's Downloads/GitHubBrowser folder."""
        d = Path(self.config.downloads_dir)
        d.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(d))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            self._notify_ui(f"باز کردن پوشه با خطا مواجه شد: {e}", is_error=True)

    def clear_session(self):
        """Clears all session cookies and local storage."""
        self.session_manager.clear_session()
        self._notify_ui("تمام کوکی‌ها و اطلاعات نشست با موفقیت پاکسازی شدند.")

    def go_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            entry = self.history[self.history_index]
            self.current_url = entry["url"]
            self._call_js(
                f"window.shellApp.onPageLoaded('{entry['local_url']}', '{entry['url']}', {json.dumps(entry['title'])})"
            )

    def go_forward(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            entry = self.history[self.history_index]
            self.current_url = entry["url"]
            self._call_js(
                f"window.shellApp.onPageLoaded('{entry['local_url']}', '{entry['url']}', {json.dumps(entry['title'])})"
            )

    def reload(self):
        if self.current_url and self.current_url != "about:blank":
            self.dispatch_navigate(self.current_url)

    def go_home(self):
        self._call_js("window.shellApp.onPageLoaded('/start', '', 'صفحه شروع آفلاین')")

    # ---------------- HTML UI Templates ----------------
    def _generate_shell_html(self) -> str:
        """Generates pure OLED black UI shell with white buttons and complete onPageLoaded handler."""
        return r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>N0PPERABO - مرورگر آفلاین NTTM</title>
  <style>
    :root {
      --bg-base: #000000;
      --bg-surface: #0c0c0c;
      --bg-surface-elevated: #161616;
      --border-subtle: #262626;
      --border-focus: #ffffff;
      --btn-white-bg: #ffffff;
      --btn-white-text: #000000;
      --text: #ffffff;
      --text-muted: #a3a3a3;
      --text-dim: #737373;
      --toolbar-height: 60px;
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', Tahoma, sans-serif;
    }
    body {
      background: var(--bg-base);
      color: var(--text);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      height: 100vh;
      user-select: none;
    }

    /* Top Toolbar */
    .toolbar {
      height: var(--toolbar-height);
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 16px;
      z-index: 100;
    }

    /* Brand Logo & Name */
    .btn-home-box {
      background: #ffffff;
      color: #000000;
      border: none;
      padding: 7px 18px;
      border-radius: 6px;
      font-size: 13.5px;
      font-weight: 800;
      letter-spacing: 0.5px;
      cursor: pointer;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .btn-home-box:hover {
      background: #e5e5e5;
    }
    .btn-home-box:active {
      transform: scale(0.96);
    }

    /* Navigation Buttons */
    .nav-group, .actions-group {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }
    .btn-nav {
      background: #141414;
      border: 1px solid #333333;
      color: #ffffff;
      width: 34px;
      height: 34px;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease;
    }
    .btn-nav:hover {
      background: #ffffff;
      color: #000000;
      border-color: #ffffff;
    }
    .btn-nav:active {
      transform: scale(0.95);
    }

    /* Address Bar */
    .url-bar-container {
      flex: 1;
      display: flex;
      align-items: center;
      background: #000000;
      border: 1px solid #333333;
      border-radius: 6px;
      padding: 3px 10px;
      gap: 8px;
      transition: border-color 0.15s ease;
      min-width: 200px;
    }
    .url-bar-container:focus-within {
      border-color: #ffffff;
    }
    .url-icon {
      display: flex;
      align-items: center;
      color: var(--text-dim);
    }
    .url-input {
      flex: 1;
      background: transparent;
      border: none;
      color: #ffffff;
      font-size: 13.5px;
      font-family: ui-monospace, 'SF Mono', 'Segoe UI Mono', monospace;
      outline: none;
      direction: ltr;
      text-align: left;
      padding: 6px 2px;
      user-select: text;
    }
    .url-input::placeholder {
      color: var(--text-dim);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Tahoma, sans-serif;
      direction: rtl;
      text-align: right;
    }

    /* White Action Buttons */
    .btn-white {
      background: #ffffff;
      color: #000000;
      border: none;
      padding: 6px 16px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .btn-white:hover {
      background: #e5e5e5;
    }
    .btn-white:active {
      transform: scale(0.96);
    }

    /* Action Buttons (Dark with white border) */
    .btn-action-dark {
      background: #141414;
      border: 1px solid #444444;
      color: #ffffff;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12.5px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .btn-action-dark:hover {
      background: #ffffff;
      color: #000000;
      border-color: #ffffff;
    }
    .btn-action-dark:active {
      transform: scale(0.96);
    }

    /* Mode & Daemon Badges */
    .badge-toggle {
      display: flex;
      align-items: center;
      gap: 6px;
      background: #141414;
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid #333333;
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
      color: #ffffff;
      user-select: none;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .badge-toggle:hover {
      border-color: #ffffff;
    }
    .badge-toggle.active {
      background: #ffffff;
      color: #000000;
      border-color: #ffffff;
    }
    .dot-indicator {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #10b981;
    }
    .badge-toggle.batch .dot-indicator {
      background: #f59e0b;
    }
    .badge-toggle.active .dot-indicator {
      background: #000000;
    }

    /* Custom Delay Controls */
    .delay-container {
      display: flex;
      align-items: center;
      gap: 6px;
      background: #141414;
      padding: 4px 8px;
      border-radius: 6px;
      border: 1px solid #333333;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .delay-container:hover {
      border-color: #ffffff;
    }
    .delay-container.active {
      background: #ffffff;
      border-color: #ffffff;
    }
    .delay-btn {
      display: flex;
      align-items: center;
      gap: 4px;
      cursor: pointer;
      font-size: 12px;
      user-select: none;
      color: #ffffff;
      font-weight: 600;
    }
    .delay-container.active .delay-btn {
      color: #000000;
    }
    .delay-input-box {
      display: flex;
      align-items: center;
      gap: 4px;
      border-right: 1px solid #333333;
      padding-right: 6px;
      margin-right: 2px;
    }
    .delay-input-box input {
      width: 40px;
      background: #000000;
      border: 1px solid #444444;
      color: #ffffff;
      padding: 2px 4px;
      border-radius: 4px;
      font-size: 12px;
      font-family: ui-monospace, monospace;
      font-weight: 700;
      text-align: center;
      outline: none;
      direction: ltr;
    }

    /* Batch Queue Bar */
    .queue-bar {
      display: none;
      background: #0c0c0c;
      border-bottom: 1px solid #262626;
      padding: 8px 16px;
      align-items: center;
      gap: 12px;
      z-index: 90;
    }
    .queue-bar.active { display: flex; }
    .queue-items {
      flex: 1;
      display: flex;
      gap: 6px;
      overflow-x: auto;
    }
    .queue-item {
      background: #161616;
      border: 1px solid #333333;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11.5px;
      font-family: ui-monospace, monospace;
      display: flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }

    /* Content Area */
    .content-area {
      flex: 1;
      position: relative;
      background: #000000;
    }
    iframe {
      width: 100%;
      height: 100%;
      border: none;
      display: block;
      background: #000000;
    }

    /* Status Bar */
    .status-bar {
      height: 26px;
      background: #0c0c0c;
      border-top: 1px solid #262626;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 14px;
      font-size: 12px;
      color: var(--text-muted);
    }
    .status-text {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    /* Loading Overlay */
    .loading-overlay {
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.88);
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 14px;
      z-index: 200;
    }
    .loading-overlay.active { display: flex; }
    .spinner-ring {
      width: 40px;
      height: 40px;
      border: 3px solid #262626;
      border-top-color: #ffffff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    .loading-text {
      font-size: 13.5px;
      color: #ffffff;
      font-weight: 600;
    }

    /* Settings Modal */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.85);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }
    .modal-overlay.active { display: flex; }
    .modal {
      background: #0f0f0f;
      border: 1px solid #333333;
      border-radius: 8px;
      width: 480px;
      max-width: 90vw;
      padding: 24px;
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.9);
    }
    .modal h2 {
      font-size: 16px;
      font-weight: 700;
      margin-bottom: 18px;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .form-group {
      margin-bottom: 14px;
    }
    .form-group label {
      display: block;
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 6px;
      font-weight: 600;
    }
    .form-group input {
      width: 100%;
      background: #000000;
      border: 1px solid #333333;
      color: #ffffff;
      padding: 9px 12px;
      border-radius: 6px;
      font-size: 13px;
      font-family: ui-monospace, monospace;
      outline: none;
      direction: ltr;
    }
    .form-group input:focus {
      border-color: #ffffff;
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 20px;
    }
    .btn-modal-cancel {
      background: transparent;
      border: 1px solid #444444;
      color: #ffffff;
      padding: 7px 16px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
    }
    .btn-modal-cancel:hover {
      background: #222222;
    }
    .btn-modal-save {
      background: #ffffff;
      border: none;
      color: #000000;
      padding: 7px 20px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 700;
    }
    .btn-modal-save:hover {
      background: #e5e5e5;
    }
  </style>
</head>
<body>

  <!-- Top Toolbar -->
  <div class="toolbar">
    <!-- Home Button -->
    <button class="btn-home-box" onclick="window.shellApp.goHome()" title="صفحه اصلی (Home)">Home</button>

    <!-- Navigation Buttons -->
    <div class="nav-group">
      <button class="btn-nav" onclick="window.shellApp.goBack()" title="بازگشت (Back)">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
      </button>
      <button class="btn-nav" onclick="window.shellApp.goForward()" title="جلو (Forward)">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
      </button>
      <button class="btn-nav" onclick="window.shellApp.reload()" title="تازه‌سازی (Reload)">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
      </button>
      <button class="btn-nav" onclick="window.shellApp.goHome()" title="صفحه خانگی">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
      </button>
    </div>

    <!-- URL Bar -->
    <div class="url-bar-container">
      <span class="url-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </span>
      <input type="text" class="url-input" id="url-input" placeholder="آدرس سایت یا جستجو در گوگل..." />
      <button class="btn-white" onclick="window.shellApp.navigateFromBar()">برو</button>
    </div>

    <!-- Actions & Toggles -->
    <div class="actions-group">
      <div class="badge-toggle" id="mode-toggle" onclick="window.shellApp.toggleMode()" title="تغییر وضعیت اجرای مستقیم/دسته‌ای">
        <span class="dot-indicator"></span>
        <span id="mode-text">مد مستقیم</span>
      </div>

      <div class="badge-toggle" id="daemon-toggle" onclick="window.shellApp.toggleDaemon()" title="رانر دائم در پس‌زمینه">
        <span class="dot-indicator" style="background:#555;"></span>
        <span id="daemon-text">دیمون</span>
      </div>

      <div class="delay-container" id="delay-box">
        <div class="delay-input-box">
          <input type="number" id="delay-val" min="1" max="60" value="5" title="ثانیه تاخیر پس از رندر صفحه" onchange="window.shellApp.updateCustomDelay()" />
        </div>
        <div class="delay-btn" onclick="window.shellApp.toggleCustomDelay()" title="فعال/غیرفعال‌سازی تاخیر سفارشی">
          <span>تاخیر</span>
        </div>
      </div>

      <button class="btn-white" id="btn-batch-send" style="display:none;" onclick="window.shellApp.triggerBatch()" title="ارسال تغییرات به گیت‌هاب">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        <span>ارسال رویدادها</span>
      </button>

      <button class="btn-white" id="btn-shorts-nav" style="display:none;" onclick="window.shellApp.triggerShortsNext()" title="پیمایش شورتز یوتیوب (ویدیوی بعدی)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/><line x1="19" y1="3" x2="19" y2="21"/></svg>
        <span>شورتز بعدی</span>
      </button>

      <button class="btn-action-dark" onclick="window.shellApp.openSettings()" title="تنظیمات توکن و ریپازیتوری">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>تنظیمات</span>
      </button>
    </div>
  </div>

  <!-- Batch Queue Bar -->
  <div class="queue-bar" id="queue-bar">
    <span style="font-size:12px; font-weight:700; color:#f59e0b;">صف رویدادها:</span>
    <div class="queue-items" id="queue-items"></div>
    <button class="btn-action-dark" style="color:#ef4444; border-color:#552222;" onclick="window.shellApp.clearQueue()">پاکسازی صف</button>
  </div>

  <!-- Main Web Content -->
  <div class="content-area">
    <iframe id="browser-frame" src="/start" sandbox="allow-scripts allow-forms allow-same-origin allow-popups"></iframe>
    <div class="loading-overlay" id="loading-overlay">
      <div class="spinner-ring"></div>
      <div class="loading-text" id="loading-text">در حال پردازش درخواست در گیت‌هاب...</div>
    </div>
  </div>

  <!-- Bottom Status Bar -->
  <div class="status-bar">
    <div class="status-text" id="status-text">
      <span>آماده</span>
    </div>
    <div>NTTM Core • Pure Black Edition</div>
  </div>

  <!-- Settings Modal -->
  <div class="modal-overlay" id="settings-modal">
    <div class="modal">
      <h2>تنظیمات اتصال به ریپازیتوری گیت‌هاب</h2>
      <div class="form-group">
        <label>توکن دسترسی شخصی (GitHub PAT):</label>
        <input type="password" id="cfg-token" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" />
      </div>
      <div class="form-group">
        <label>ریپازیتوری هدف (Target Repository):</label>
        <input type="text" id="cfg-repo" placeholder="owner/repo-name" />
      </div>
      <div class="form-group">
        <label>کلید رمزگذاری سرتاسری (Encryption Key - اختیاری):</label>
        <input type="password" id="cfg-enc" placeholder="کلید رمزگذاری امنیتی پکت‌ها (اختیاری)" />
      </div>
      <div class="modal-actions">
        <button class="btn-modal-cancel" onclick="window.shellApp.closeSettings()">انصراف</button>
        <button class="btn-modal-save" onclick="window.shellApp.saveSettings()">ذخیره تنظیمات</button>
      </div>
    </div>
  </div>

  <script>
    // Browser bridge polyfill for --browser mode (Chrome / Edge / Firefox)
    if (!window.pywebview) {
      window.pywebview = {
        api: new Proxy({}, {
          get: function(target, prop) {
            return function(...args) {
              return fetch('/api/call', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ method: prop, args: args })
              })
              .then(res => res.json())
              .then(data => {
                if (data && data.status === 'ok') return data.result;
                throw new Error(data ? data.message : 'API call error');
              });
            };
          }
        })
      };

      setInterval(async () => {
        try {
          const res = await fetch('/api/events');
          if (res.ok) {
            const events = await res.json();
            if (Array.isArray(events)) {
              for (const code of events) {
                try {
                  const fn = new Function(code);
                  fn();
                } catch(err) {
                  console.error('Event execution failed:', err, code);
                }
              }
            }
          }
        } catch(e) {}
      }, 350);
    }

    window.shellApp = {
      isBatchMode: false,
      isDaemonActive: false,
      isCustomDelayActive: false,
      currentLiveUrl: '',
      queue: [],

      init: function() {
        const urlInput = document.getElementById('url-input');
        urlInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') this.navigateFromBar();
        });

        const frame = document.getElementById('browser-frame');
        frame.addEventListener('load', () => {
          this.hideLoading();
          try {
            const loc = frame.contentWindow.location.href;
            if (loc && !loc.includes('/start')) {
              urlInput.value = this.currentLiveUrl || loc;
              this.updateShortsButton(this.currentLiveUrl || loc);
            }
          } catch(err) {}
          this.injectDeepInterceptor();
        });

        setTimeout(() => this.injectDeepInterceptor(), 500);

        setInterval(() => {
          if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.get_current_url().then(res => {
              if (res && res.url && res.url !== 'about:blank') {
                if (!document.activeElement || document.activeElement.id !== 'url-input') {
                  if (!res.url.includes('/start')) {
                    urlInput.value = res.url;
                  }
                }
                this.updateShortsButton(res.url);
              }
            }).catch(()=>{});
          }
        }, 1500);
      },

      onPageLoaded: function(localUrl, targetUrl, title) {
        this.hideLoading();
        this.currentLiveUrl = targetUrl || '';
        const urlInput = document.getElementById('url-input');
        if (targetUrl && urlInput) {
          urlInput.value = targetUrl;
        }
        const frame = document.getElementById('browser-frame');
        if (frame) {
          frame.src = localUrl;
        }
        if (title) {
          document.title = title + ' - N0PPERABO NTTM';
        }
        this.updateShortsButton(targetUrl);
      },

      setLoading: function(isLoading, msg) {
        if (isLoading) {
          this.showLoading(msg);
        } else {
          this.hideLoading();
        }
      },

      updateStatus: function(msg) {
        const el = document.getElementById('status-text');
        if (el) el.innerHTML = '<span>' + msg + '</span>';
      },

      showToast: function(msg, isError, isFile) {
        this.updateStatus(msg);
      },

      showLoading: function(text) {
        document.getElementById('loading-text').textContent = text || 'در حال ارسال اکشن به گیت‌هاب...';
        document.getElementById('loading-overlay').classList.add('active');
        document.getElementById('status-text').innerHTML = '<span>' + (text || 'در حال پردازش...') + '</span>';
      },

      hideLoading: function() {
        document.getElementById('loading-overlay').classList.remove('active');
        document.getElementById('status-text').innerHTML = '<span>آماده</span>';
      },

      navigateFromBar: function() {
        let val = document.getElementById('url-input').value.trim();
        if (!val) return;
        if (!val.startsWith('http://') && !val.startsWith('https://')) {
          if (val.includes('.') && !val.includes(' ')) {
            val = 'https://' + val;
          } else {
            val = 'https://www.google.com/search?q=' + encodeURIComponent(val);
          }
        }
        this.navigate(val);
      },

      navigate: function(url) {
        const delay = this.isCustomDelayActive ? parseInt(document.getElementById('delay-val').value, 10) || 5 : 0;
        this.showLoading('در حال بارگذاری صفحه از طریق رانر گیت‌هاب...');
        this.updateShortsButton(url);
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.navigate(url, delay);
        } else {
          document.getElementById('browser-frame').src = url;
        }
      },

      goHome: function() {
        document.getElementById('browser-frame').src = '/start';
        document.getElementById('url-input').value = '';
        this.currentLiveUrl = '';
        this.updateShortsButton('');
      },

      goBack: function() {
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.go_back();
        }
      },

      goForward: function() {
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.go_forward();
        }
      },

      reload: function() {
        if (window.pywebview && window.pywebview.api) {
          this.showLoading('در حال بازخوانی صفحه...');
          window.pywebview.api.reload();
        }
      },

      updateShortsButton: function(url) {
        const btn = document.getElementById('btn-shorts-nav');
        if (btn) {
          if (url && (url.includes('/shorts/') || url.includes('/shorts'))) {
            btn.style.display = 'flex';
          } else {
            btn.style.display = 'none';
          }
        }
      },

      triggerShortsNext: function() {
        this.showLoading('در حال دریافت شورتز بعدی...');
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.navigate_shorts_next('');
        }
      },

      toggleMode: function() {
        this.isBatchMode = !this.isBatchMode;
        const toggle = document.getElementById('mode-toggle');
        const text = document.getElementById('mode-text');
        const sendBtn = document.getElementById('btn-batch-send');
        const qBar = document.getElementById('queue-bar');

        if (this.isBatchMode) {
          toggle.classList.add('batch');
          text.textContent = 'مد دسته‌ای';
          sendBtn.style.display = 'flex';
          qBar.classList.add('active');
        } else {
          toggle.classList.remove('batch');
          text.textContent = 'مد مستقیم';
          sendBtn.style.display = 'none';
          qBar.classList.remove('active');
        }

        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.set_batch_mode(this.isBatchMode);
        }
      },

      toggleDaemon: function() {
        this.isDaemonActive = !this.isDaemonActive;
        const btn = document.getElementById('daemon-toggle');
        const text = document.getElementById('daemon-text');

        if (this.isDaemonActive) {
          btn.classList.add('active');
          text.textContent = 'دیمون فعال';
        } else {
          btn.classList.remove('active');
          text.textContent = 'دیمون';
        }

        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.toggle_daemon(this.isDaemonActive);
        }
      },

      toggleCustomDelay: function() {
        this.isCustomDelayActive = !this.isCustomDelayActive;
        const container = document.getElementById('delay-box');
        if (this.isCustomDelayActive) {
          container.classList.add('active');
        } else {
          container.classList.remove('active');
        }
        this.updateCustomDelay();
      },

      updateCustomDelay: function() {
        const valInput = document.getElementById('delay-val');
        let sec = parseInt(valInput.value, 10);
        if (isNaN(sec) || sec < 1) sec = 1;
        if (sec > 60) sec = 60;
        valInput.value = sec;

        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.set_custom_delay(this.isCustomDelayActive ? sec : 0);
        }
      },

      queueAction: function(action) {
        this.queue.push(action);
        this.renderQueue();
      },

      renderQueue: function() {
        const container = document.getElementById('queue-items');
        container.innerHTML = '';
        this.queue.forEach((item, idx) => {
          const div = document.createElement('div');
          div.className = 'queue-item';
          div.innerHTML = '<span style="color:#ffffff; font-weight:700;">' + item.type + '</span> <span>' + (item.selector || item.text || item.key || '') + '</span>';
          container.appendChild(div);
        });
      },

      clearQueue: function() {
        this.queue = [];
        this.renderQueue();
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.clear_batch_queue();
        }
      },

      triggerBatch: function() {
        if (this.queue.length === 0) {
          alert('صف رویدادها خالی است.');
          return;
        }
        this.showLoading('در حال ارسال صف اکشن‌ها به گیت‌هاب...');
        const delay = this.isCustomDelayActive ? parseInt(document.getElementById('delay-val').value, 10) || 5 : 0;
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.run_batch_actions(this.currentLiveUrl, this.queue, delay);
          this.clearQueue();
        }
      },

      openSettings: function() {
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.get_config().then(cfg => {
            document.getElementById('cfg-token').value = cfg.token || cfg.github_token || '';
            document.getElementById('cfg-repo').value = cfg.repo || cfg.github_repo || '';
            document.getElementById('cfg-enc').value = cfg.encryption_key || '';
            document.getElementById('settings-modal').classList.add('active');
          });
        } else {
          document.getElementById('settings-modal').classList.add('active');
        }
      },

      closeSettings: function() {
        document.getElementById('settings-modal').classList.remove('active');
      },

      saveSettings: function() {
        const token = document.getElementById('cfg-token').value.trim();
        const repo = document.getElementById('cfg-repo').value.trim();
        const encKey = document.getElementById('cfg-enc').value.trim();
        if (!token || !repo) {
          alert('لطفاً هر دو فیلد توکن و ریپازیتوری را پر کنید.');
          return;
        }
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.save_config(token, repo, "main", "", encKey).then(res => {
            if (res && res.success) {
              alert('تنظیمات با موفقیت ذخیره شد.');
              this.closeSettings();
            } else {
              alert('خطا در ذخیره: ' + ((res && res.error) || 'خطای نامشخص'));
            }
          });
        }
      },

      injectDeepInterceptor: function() {
        const frame = document.getElementById('browser-frame');
        try {
          const doc = frame.contentDocument || frame.contentWindow.document;
          const win = frame.contentWindow;
          if (!doc || !win || doc._injectedNTTM) return;

          try {
            const loc = win.location;
            if (!loc || !loc.pathname || !loc.pathname.startsWith('/cache/')) {
              return;
            }
          } catch(e) {
            return;
          }

          doc._injectedNTTM = true;

          const script = doc.createElement('script');
          script.textContent = `
            (function() {
              function getBestSelector(el) {
                if (!el || el === document.body) return 'body';
                if (el.id) return '#' + el.id;
                if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                if (el.className && typeof el.className === 'string') {
                  const c = el.className.trim().split(/\\s+/)[0];
                  if (c) return el.tagName.toLowerCase() + '.' + c;
                }
                return el.tagName.toLowerCase();
              }

              document.addEventListener('click', function(e) {
                let target = e.target.closest('a') || e.target.closest('button') || e.target.closest('input[type="submit"]') || e.target;
                if (target) {
                  let href = target.getAttribute('href');
                  if (href && !href.startsWith('javascript:') && !href.startsWith('#')) {
                    e.preventDefault();
                    e.stopPropagation();
                    const absoluteUrl = new URL(href, window.location.href).href;
                    window.parent.shellApp.navigate(absoluteUrl);
                    return false;
                  }

                  if (window.parent.shellApp.isBatchMode) {
                    e.preventDefault();
                    e.stopPropagation();
                    window.parent.shellApp.queueAction({
                      type: 'click',
                      selector: getBestSelector(target)
                    });
                    return false;
                  }
                }
              }, true);

              document.addEventListener('submit', function(e) {
                if (window.parent.shellApp.isBatchMode) {
                  e.preventDefault();
                  e.stopPropagation();
                  window.parent.shellApp.queueAction({
                    type: 'submit',
                    selector: getBestSelector(e.target)
                  });
                  return false;
                }
              }, true);
            })();
          `;
          doc.head.appendChild(script);
        } catch(err) {}
      }
    };

    window.addEventListener('DOMContentLoaded', () => window.shellApp.init());
  </script>
</body>
</html>
"""

    def _generate_start_page_html(self) -> str:
        """Generates the pure-black home screen with NTTM branding, full-width Digital Flame pyramid animation in white-to-grey spectrum, and clean dark UI."""
        token = getattr(self.config, "github_token", "")
        token_status = "ثبت شده" if token else "تنظیم نشده"
        repo_status = getattr(self.config, "github_repo", "") or "تنظیم نشده"
        cookies = getattr(self.session_manager, "cookies", [])
        cookies_count = len(cookies)

        return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>N0PPERABO - NTTM Home</title>
  <style>
    :root {{
      --bg-base: #000000;
      --bg-surface: #0a0a0a;
      --bg-surface-elevated: #141414;
      --border-subtle: #262626;
      --border-focus: #ffffff;
      --text: #ffffff;
      --text-muted: #a3a3a3;
      --text-dim: #737373;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', Tahoma, sans-serif;
    }}
    body {{
      background: #000000;
      color: #ffffff;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px;
    }}

    /* Digital Flame Canvas: anchors behind the UI across the full width */
    #flame-canvas {{
      position: fixed;
      inset: 0;
      width: 100%;
      height: 100%;
      z-index: 0;
      pointer-events: none;
      opacity: 0;
      transition: opacity 1.2s ease 0.2s;
    }}
    #flame-canvas.on {{
      opacity: 1;
    }}

    .container {{
      max-width: 960px;
      width: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 26px;
      position: relative;
      z-index: 1;
      margin: auto 0;
    }}

    /* Huge Hero Branding */
    .hero {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      text-align: center;
    }}
    .hero-logo-box {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 10px;
      width: 100%;
    }}
    .hero-logo {{
      width: 680px;
      max-width: min(92vw, 680px);
      height: auto;
      object-fit: contain;
      filter: drop-shadow(0 0 1px rgba(255, 255, 255, 0.8));
      animation: igniteIn 1.1s cubic-bezier(0.16, 1, 0.3, 1) backwards;
    }}
    .hero h1 {{
      font-size: 28px;
      font-weight: 900;
      letter-spacing: 4px;
      color: #ffffff;
      animation: riseIn 0.7s cubic-bezier(0.16, 1, 0.3, 1) 0.12s backwards;
    }}
    .hero p {{
      color: var(--text-muted);
      font-size: 14px;
      line-height: 1.6;
      animation: riseIn 0.7s cubic-bezier(0.16, 1, 0.3, 1) 0.2s backwards;
    }}

    /* Arrival choreography: entrance plays once, then hands over to hover states. */
    @keyframes igniteIn {{
      0% {{
        opacity: 0;
        transform: scale(0.94);
        filter: brightness(0);
      }}
      55% {{
        opacity: 1;
        filter: brightness(2.2) drop-shadow(0 0 1px rgba(255, 255, 255, 0.8));
      }}
      100% {{
        opacity: 1;
        transform: scale(1);
        filter: brightness(1) drop-shadow(0 0 1px rgba(255, 255, 255, 0.8));
      }}
    }}
    @keyframes riseIn {{
      from {{
        opacity: 0;
        transform: translateY(26px);
      }}
      to {{
        opacity: 1;
        transform: none;
      }}
    }}

    /* Search Box */
    .search-box {{
      width: 100%;
      display: flex;
      background: #0a0a0a;
      border: 1px solid #333333;
      border-radius: 8px;
      padding: 4px;
      transition: border-color 0.15s ease;
    }}
    .search-box:focus-within {{
      border-color: #ffffff;
      box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.1);
    }}
    .search-input-wrapper {{
      flex: 1;
      display: flex;
      align-items: center;
      padding: 0 14px;
      gap: 10px;
    }}
    .search-icon {{
      color: var(--text-dim);
      display: flex;
      align-items: center;
    }}
    .search-box input {{
      flex: 1;
      background: transparent;
      border: none;
      color: #ffffff;
      font-size: 14px;
      padding: 10px 0;
      outline: none;
    }}
    .search-box input::placeholder {{
      color: var(--text-dim);
    }}
    .btn-search {{
      background: #ffffff;
      border: none;
      color: #000000;
      font-size: 14px;
      font-weight: 700;
      padding: 0 24px;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .btn-search:hover {{
      background: #e5e5e5;
    }}
    .btn-search:active {{
      transform: scale(0.97);
    }}

    /* Fast Access Grid */
    .quick-grid {{
      width: 100%;
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 10px;
    }}
    .quick-card {{
      background: #0a0a0a;
      border: 1px solid #262626;
      border-radius: 8px;
      padding: 14px 8px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      transition: all 0.15s ease;
      text-decoration: none;
      color: #ffffff;
      animation: riseIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) backwards;
    }}
    .quick-card:nth-child(1) {{ animation-delay: 0.28s; }}
    .quick-card:nth-child(2) {{ animation-delay: 0.34s; }}
    .quick-card:nth-child(3) {{ animation-delay: 0.40s; }}
    .quick-card:nth-child(4) {{ animation-delay: 0.46s; }}
    .quick-card:nth-child(5) {{ animation-delay: 0.52s; }}
    .quick-card:nth-child(6) {{ animation-delay: 0.58s; }}
    .quick-card:hover {{
      background: #ffffff;
      border-color: #ffffff;
      color: #000000;
      transform: translateY(-3px);
    }}
    .quick-card:active {{
      transform: translateY(-1px) scale(0.97);
    }}
    .quick-card:focus-visible {{
      outline: 2px solid #ffffff;
      outline-offset: 2px;
    }}
    .quick-icon-svg {{
      width: 22px;
      height: 22px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #ffffff;
    }}
    .quick-card:hover .quick-icon-svg {{
      color: #000000;
    }}
    .quick-name {{
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
    }}
    .quick-card:hover .quick-name {{
      color: #000000;
    }}

    /* Info Grid */
    .info-grid {{
      width: 100%;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .info-card {{
      background: #0a0a0a;
      border: 1px solid #262626;
      border-radius: 8px;
      padding: 18px;
      font-size: 12.5px;
      line-height: 1.6;
      animation: riseIn 0.7s cubic-bezier(0.16, 1, 0.3, 1) 0.66s backwards;
    }}
    .info-card h3 {{
      font-size: 13.5px;
      font-weight: 700;
      color: #ffffff;
      margin-bottom: 10px;
    }}
    .info-card p {{
      color: var(--text-muted);
      margin-bottom: 8px;
    }}
    .info-card p:last-child {{
      margin-bottom: 0;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-family: ui-monospace, monospace;
      font-weight: 700;
    }}
    .badge-ok {{
      background: #141414;
      color: #ffffff;
      border: 1px solid #444444;
    }}
    .cfg-link {{
      color: #ffffff;
      cursor: pointer;
      text-decoration: underline;
      font-weight: 600;
    }}
    .cfg-link:hover {{
      color: #a3a3a3;
    }}
    .cfg-link:focus-visible {{
      outline: 2px solid #ffffff;
      outline-offset: 2px;
    }}

    .footer {{
      font-size: 11.5px;
      color: #8a8a8a;
      font-family: ui-monospace, monospace;
      letter-spacing: 1px;
      margin-top: 10px;
      text-align: center;
      animation: riseIn 0.7s cubic-bezier(0.16, 1, 0.3, 1) 0.78s backwards;
      /* Keeps the engine label readable where the white fire passes beneath it. */
      text-shadow: 0 0 6px #000000, 0 0 3px rgba(0, 0, 0, 0.9);
    }}

    /* Motion off: entrance choreography stops; the engine paints ONE frozen
       digital-flame frame plus a static spark bed, nothing loops. */
    @media (prefers-reduced-motion: reduce) {{
      .hero-logo, .hero h1, .hero p, .quick-card, .info-card, .footer {{
        animation: none !important;
      }}
      #flame-canvas {{
        transition: none;
      }}
    }}
  </style>
</head>
<body>
  <canvas id="flame-canvas" aria-hidden="true"></canvas>
  <main class="container">
    <!-- Huge Hero Branding -->
    <div class="hero">
      <div class="hero-logo-box">
        <img src="/assets/logo.svg" alt="N0pperabo Logo" class="hero-logo" />
      </div>
      <h1>NTTM</h1>
      <p>مرورگر آفلاین NTTM بر بستر تانل گیت‌هاب رانر با مخفی‌کاری پیشرفته</p>
    </div>

    <!-- Search Input Bar -->
    <div class="search-box">
      <div class="search-input-wrapper">
        <span class="search-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <input type="text" id="start-query" placeholder="جستجو در اینترنت یا وارد کردن آدرس وب‌سایت..." onkeydown="if(event.key==='Enter') doSearch()" />
      </div>
      <button class="btn-search" onclick="doSearch()">جستجو</button>
    </div>

    <!-- Fast Access Grid -->
    <div class="quick-grid">
      <div class="quick-card" role="button" tabindex="0" onclick="quickNav('https://www.google.com')">
        <div class="quick-icon-svg">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><path d="M12 8v8M8 12h8"/></svg>
        </div>
        <span class="quick-name">Google</span>
      </div>
      <div class="quick-card" role="button" tabindex="0" onclick="quickNav('https://duckduckgo.com')">
        <div class="quick-icon-svg">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/></svg>
        </div>
        <span class="quick-name">DuckDuckGo</span>
      </div>
      <div class="quick-card" role="button" tabindex="0" onclick="quickNav('https://www.youtube.com')">
        <div class="quick-icon-svg">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        </div>
        <span class="quick-name">YouTube</span>
      </div>
      <div class="quick-card" role="button" tabindex="0" onclick="quickNav('https://fa.wikipedia.org')">
        <div class="quick-icon-svg">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
        </div>
        <span class="quick-name">Wikipedia</span>
      </div>
      <div class="quick-card" role="button" tabindex="0" onclick="quickNav('https://github.com/explore')">
        <div class="quick-icon-svg">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
        </div>
        <span class="quick-name">GitHub</span>
      </div>
      <div class="quick-card" role="button" tabindex="0" onclick="quickNav('https://stackoverflow.com')">
        <div class="quick-icon-svg">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
        </div>
        <span class="quick-name">Stack Overflow</span>
      </div>
    </div>

    <!-- Info Grid -->
    <div class="info-grid">
      <div class="info-card">
        <h3>راهنمای حالت‌های کارکرد</h3>
        <p><strong>حالت مستقیم:</strong> کلیک‌ها و ارسال فرم‌ها بلافاصله به گیت‌هاب ارسال شده و صفحه جدید دریافت می‌شود.</p>
        <p><strong>حالت دسته‌ای:</strong> اقدامات پشت‌سرهم در صف ثبت شده و همه با هم در یک رانر گیت‌هاب اجرا می‌شوند.</p>
      </div>

      <div class="info-card">
        <h3>وضعیت ارتباط با مخزن</h3>
        <p style="margin-bottom: 6px;"><strong>توکن دسترسی:</strong> <span class="badge badge-ok">{token_status}</span></p>
        <p style="margin-bottom: 6px;"><strong>مخزن هدف:</strong> <span dir="ltr" style="color:#ffffff; font-family: ui-monospace, monospace;">{repo_status}</span></p>
        <p style="margin-bottom: 10px;"><strong>کوکی‌های فعال:</strong> <span class="badge badge-ok">{cookies_count} عدد</span></p>
        <p><a class="cfg-link" role="button" tabindex="0" onclick="parent.window.shellApp.openSettings()">تغییر تنظیمات و کلیدهای ارتباطی</a></p>
      </div>
    </div>

    <footer class="footer">
      N0PPERABO NTTM ENGINE • PURE BLACK STEALTH
    </footer>
  </main>

  <script>
    function quickNav(url) {{
      parent.window.shellApp.navigate(url);
    }}

    function doSearch() {{
      const query = document.getElementById('start-query').value.trim();
      if (!query) return;
      if (query.startsWith('http://') || query.startsWith('https://')) {{
        quickNav(query);
      }} else if (query.includes('.') && !query.includes(' ')) {{
        quickNav('https://' + query);
      }} else {{
        quickNav('https://www.google.com/search?q=' + encodeURIComponent(query));
      }}
    }}

    // Keyboard activation for the role="button" tiles.
    document.querySelectorAll('[role="button"]').forEach(function (el) {{
      el.addEventListener('keydown', function (e) {{
        if (e.key === 'Enter' || e.key === ' ') {{
          e.preventDefault();
          el.click();
        }}
      }});
    }});

    // Ember drift: rising spark particles that complement the real white
    // fire strip along the bottom edge. Additive compositing (the classic
    // canvas flame walkthrough technique, thecodeplayer.com / davepagurek.com)
    // makes overlaps glow, grayscale only to match the N0PPERABO monochrome face.
    // The main fire lives in .fire-line (CC0 sprite strip, pure CSS playback).
    // Digital Flame Engine: Procedural layered pyramid flame in white-to-grey spectrum.
    // Base spans 100% of the screen width, tapering upwards to a graceful apex.
    (function digitalFlame() {{
      const canvas = document.getElementById('flame-canvas');
      if (!canvas || !canvas.getContext) return;
      const ctx = canvas.getContext('2d');
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      let W = 0, H = 0;

      function resize() {{
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        W = window.innerWidth;
        H = window.innerHeight;
        canvas.width = Math.floor(W * dpr);
        canvas.height = Math.floor(H * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }}
      window.addEventListener('resize', resize);
      resize();

      // Floating spark embers
      const sparks = [];
      const SPARK_COUNT = 85;
      for (let i = 0; i < SPARK_COUNT; i++) {{
        sparks.push({{
          x: Math.random() * (W || 1000),
          y: Math.random() * (H || 800),
          vx: (Math.random() - 0.5) * 0.7,
          vy: -(1.2 + Math.random() * 2.2),
          size: 1.1 + Math.random() * 2.4,
          alpha: 0.3 + Math.random() * 0.65,
          life: Math.random() * 120
        }});
      }}

      function draw(now) {{
        ctx.clearRect(0, 0, W, H);
        const t = now * 0.0018;

        // Pyramid Dimensions: Anchored at base, gracefully peaks below the hero logo
        const flameHeight = Math.min(H * 0.42, 380);
        const linesCount = 76;
        const cx = W / 2;

        // 1. Ambient Base Glow
        const ambientGrad = ctx.createRadialGradient(cx, H, 20, cx, H - flameHeight * 0.45, W * 0.58);
        ambientGrad.addColorStop(0, 'rgba(255, 255, 255, 0.09)');
        ambientGrad.addColorStop(0.28, 'rgba(190, 200, 215, 0.045)');
        ambientGrad.addColorStop(0.65, 'rgba(70, 80, 95, 0.015)');
        ambientGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
        ctx.fillStyle = ambientGrad;
        ctx.fillRect(0, 0, W, H);

        // 2. Central Flame Tongues (Fluid volumetric fire body)
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        const tongues = [
          {{ scaleH: 0.94, offset: 0, wFactor: 0.24, alpha: 0.22 }},
          {{ scaleH: 0.84, offset: 2.1, wFactor: 0.38, alpha: 0.16 }},
          {{ scaleH: 0.72, offset: 4.2, wFactor: 0.54, alpha: 0.11 }}
        ];
        tongues.forEach((tg) => {{
          const topY = H - flameHeight * tg.scaleH;
          const sway = Math.sin(t * 2.4 + tg.offset) * 28;
          const tipX = cx + sway;
          const w = (W * tg.wFactor) * (1 + 0.12 * Math.sin(t * 3.2 + tg.offset));

          ctx.beginPath();
          ctx.moveTo(cx - w, H);
          ctx.bezierCurveTo(
            cx - w * 0.72, H - flameHeight * 0.32,
            tipX - w * 0.22, H - flameHeight * 0.68,
            tipX, topY
          );
          ctx.bezierCurveTo(
            tipX + w * 0.22, H - flameHeight * 0.68,
            cx + w * 0.72, H - flameHeight * 0.32,
            cx + w, H
          );
          ctx.closePath();

          const grad = ctx.createLinearGradient(cx, H, tipX, topY);
          grad.addColorStop(0, `rgba(255, 255, 255, ${{tg.alpha}})`);
          grad.addColorStop(0.45, `rgba(210, 220, 235, ${{(tg.alpha * 0.75).toFixed(3)}})`);
          grad.addColorStop(0.85, `rgba(255, 255, 255, ${{(tg.alpha * 1.3).toFixed(3)}})`);
          grad.addColorStop(1, 'rgba(255, 255, 255, 0)');
          ctx.fillStyle = grad;
          ctx.fill();
        }});
        ctx.restore();

        // 3. Layered Geometric Wave Lines (Rhythmic digital contour flame)
        ctx.save();
        for (let i = 0; i < linesCount; i++) {{
          const u = i / (linesCount - 1); // 0 at bottom, 1 at peak
          const y = H - u * flameHeight;

          // Pyramid silhouette: full width at bottom (u=0), tapering to apex (u=1)
          const taper = Math.pow(1 - u, 1.22);
          const upwardPhase = t * 3.6 - u * 8.2;
          const waveSway = Math.sin(upwardPhase * 0.65 + u * 4.2) * (u * 42);
          const centerLine = cx + waveSway;

          const breath = 1 + 0.14 * Math.sin(upwardPhase * 1.1 + i * 0.22);
          const halfWidth = (W * 0.53) * taper * breath + (1 - u) * (W * 0.08);

          if (halfWidth < 4) continue;

          const left = centerLine - halfWidth;
          const right = centerLine + halfWidth;

          ctx.beginPath();
          const steps = 38;
          for (let s = 0; s <= steps; s++) {{
            const frac = s / steps;
            const px = left + frac * (right - left);
            const bell = Math.sin(frac * Math.PI);

            const ripple1 = Math.sin(frac * 11.5 + upwardPhase * 2.1) * (6.5 * bell * (1 - u * 0.45));
            const ripple2 = Math.cos(frac * 23.0 - t * 4.2) * (3.2 * bell * (1 - u * 0.7));
            const py = y - (ripple1 + ripple2);

            if (s === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
          }}

          // White-to-Grey Gradient: Blazing white at center/top, silver mid, charcoal edges
          const lineGrad = ctx.createLinearGradient(left, y, right, y);
          const edgeLum = Math.floor(45 + 75 * (1 - u));
          const midLum = Math.floor(185 + 55 * u);
          const centerLum = 255;

          const alphaCore = Math.min(1.0, 0.45 + 0.55 * u);
          const alphaMid = 0.32 + 0.38 * u;
          const alphaEdge = 0.08 + 0.18 * (1 - u);

          lineGrad.addColorStop(0, `rgba(${{edgeLum}}, ${{edgeLum}}, ${{edgeLum}}, ${{alphaEdge.toFixed(3)}})`);
          lineGrad.addColorStop(0.24, `rgba(${{midLum}}, ${{midLum}}, ${{midLum}}, ${{alphaMid.toFixed(3)}})`);
          lineGrad.addColorStop(0.5, `rgba(${{centerLum}}, ${{centerLum}}, ${{centerLum}}, ${{alphaCore.toFixed(3)}})`);
          lineGrad.addColorStop(0.76, `rgba(${{midLum}}, ${{midLum}}, ${{midLum}}, ${{alphaMid.toFixed(3)}})`);
          lineGrad.addColorStop(1, `rgba(${{edgeLum}}, ${{edgeLum}}, ${{edgeLum}}, ${{alphaEdge.toFixed(3)}})`);

          ctx.strokeStyle = lineGrad;
          ctx.lineWidth = Math.max(1, 2.7 * (1 - u * 0.6));
          ctx.stroke();

          // Intense core beam along the pyramid spine
          if (i % 3 === 0 && u > 0.12) {{
            ctx.beginPath();
            const coreSpan = halfWidth * (0.22 + 0.22 * (1 - u));
            ctx.moveTo(centerLine - coreSpan, y);
            ctx.lineTo(centerLine + coreSpan, y);
            ctx.strokeStyle = `rgba(255, 255, 255, ${{(0.65 * (1 - u * 0.25)).toFixed(3)}})`;
            ctx.lineWidth = Math.max(1, 1.6 * (1 - u * 0.5));
            ctx.stroke();
          }}
        }}
        ctx.restore();

        // 4. Floating Rising Sparks within Pyramid Bounds
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        sparks.forEach((sp) => {{
          sp.y += sp.vy;
          sp.x += sp.vx + Math.sin(t * 3.2 + sp.life * 0.09) * 0.55;
          sp.life += 1;

          if (sp.y < H - flameHeight * 1.05 || sp.life > 140) {{
            sp.y = H + 6;
            sp.x = Math.random() * W;
            sp.life = 0;
            sp.vy = -(1.2 + Math.random() * 2.4);
          }}

          const uSpark = (H - sp.y) / flameHeight;
          const maxDist = (W * 0.54) * Math.max(0.06, Math.pow(1 - Math.min(1, uSpark), 1.15));
          const distFromCenter = Math.abs(sp.x - cx);

          if (distFromCenter < maxDist * 1.12) {{
            const sparkFade = 1 - Math.min(1, sp.life / 140);
            const sparkAlpha = sp.alpha * sparkFade;

            ctx.fillStyle = `rgba(255, 255, 255, ${{sparkAlpha.toFixed(3)}})`;
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, sp.size * sparkFade, 0, Math.PI * 2);
            ctx.fill();

            // Luminous halo
            ctx.fillStyle = `rgba(210, 225, 240, ${{(sparkAlpha * 0.38).toFixed(3)}})`;
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, sp.size * 2.6 * sparkFade, 0, Math.PI * 2);
            ctx.fill();
          }}
        }});
        ctx.restore();
      }}

      canvas.classList.add('on');

      if (!reduced) {{
        function loop(now) {{
          requestAnimationFrame(loop);
          if (document.hidden) return;
          draw(now);
        }}
        requestAnimationFrame(loop);
      }} else {{
        draw(1200);
      }}
    }})();
  </script>
</body>
</html>
"""


class WebviewApiBridge:
    """Python bridge exposed to JavaScript under window.pywebview.api."""

    def __init__(self, ui: OfflineBrowserUI):
        # Must use leading underscore so pywebview does not inspect ui.window.native recursively
        self._ui = ui

    def get_current_url(self) -> Dict[str, str]:
        return {"url": self._ui.current_url or ""}

    def set_delay(self, seconds: Any = 0):
        self._ui.set_delay(seconds)

    def set_custom_delay(self, seconds: Any = 0):
        self._ui.set_delay(seconds)

    def get_delay(self) -> int:
        return self._ui.current_delay_seconds

    def navigate(self, target_url: str, custom_delay_seconds: int = 0):
        self._ui.dispatch_navigate(target_url, custom_delay_seconds)

    def navigate_shorts_next(self, current_video_id: str = "") -> Dict[str, Any]:
        actions = [{"type": "press", "key": "ArrowDown"}]
        self._ui.dispatch_replay(self._ui.current_url, actions)
        return {"status": "ok"}

    def submit_form(
        self, action_url: str, method: str, content_type: str, body_base64: str, custom_delay_seconds: int = 0
    ):
        self._ui.dispatch_form_submit(action_url, method, content_type, body_base64, custom_delay_seconds)

    def dispatch_replay(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_delay_seconds: int = 0,
    ):
        self._ui.dispatch_replay(target_url, actions, intercepted_ajax, custom_delay_seconds)

    def run_batch_actions(self, target_url: str, actions: List[Dict[str, Any]], custom_delay_seconds: int = 0):
        self._ui.dispatch_replay(target_url, actions, None, custom_delay_seconds)

    def set_batch_mode(self, enabled: bool):
        self._ui.is_batch_mode = bool(enabled)

    def clear_batch_queue(self):
        self._ui.action_queue.clear()

    def send_batch(self, actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        items = actions if actions is not None else list(self._ui.action_queue)
        if items:
            self._ui.action_queue.clear()
            self._ui.dispatch_replay(self._ui.current_url, items)
        return {"status": "ok"}

    def open_downloads_folder(self):
        self._ui.open_downloads_folder()

    def clear_session(self):
        self._ui.clear_session()

    def go_back(self):
        self._ui.go_back()

    def go_forward(self):
        self._ui.go_forward()

    def reload(self):
        self._ui.reload()

    def go_home(self):
        self._ui.go_home()

    def get_config(self) -> Dict[str, Any]:
        return {
            "token": getattr(self._ui.config, "github_token", ""),
            "repo": getattr(self._ui.config, "github_repo", ""),
            "branch": getattr(self._ui.config, "branch", "main"),
            "encryption_key": getattr(self._ui.config, "encryption_key", ""),
        }

    def get_daemon_status(self) -> Dict[str, Any]:
        alive = self._ui.transport.is_daemon_alive()
        return {"alive": alive}

    def toggle_daemon(self, enable: Optional[bool] = None) -> Dict[str, Any]:
        alive = self._ui.transport.is_daemon_alive()
        target = not alive if enable is None else bool(enable)
        if not target and alive:
            ok, msg = self._ui.transport.stop_daemon()
            return {"alive": False, "msg": "رانر زنده متوقف شد."}
        elif target and not alive:
            ok, msg = self._ui.transport.start_daemon(idle_timeout_min=30)
            return {"alive": True, "msg": "درخواست راه‌اندازی رانر زنده ارسال شد. ظرف ۳۰ الی ۴۵ ثانیه آماده می‌شود..."}
        return {"alive": alive, "msg": ""}

    def save_config(
        self, token: str, repo: str, branch: str = "main", search_url: str = "", encryption_key: str = ""
    ) -> Dict[str, Any]:
        try:
            self._ui.config.github_token = token.strip()
            self._ui.config.github_repo = repo.strip()
            if branch:
                self._ui.config.branch = branch.strip()
            if search_url:
                self._ui.config.default_search_url = search_url.strip()
            self._ui.config.encryption_key = encryption_key.strip()
            self._ui.config.save()
            return {"success": True}
        except Exception as e:
            logger.error(f"Save config error: {e}")
            return {"success": False, "error": str(e)}
