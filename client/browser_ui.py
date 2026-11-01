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
    and the integrated browser shell.
    """

    cache_dir: Path = Path.home() / ".gh_offline_browser" / "cache"
    shell_html: str = ""
    start_html: str = ""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

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

        # History navigation
        self.history: List[Dict[str, str]] = []
        self.history_index: int = -1

        # Current navigation state
        self.current_url: str = "about:blank"
        self.is_busy: bool = False
        self.is_batch_mode: bool = False
        self.action_queue: List[Dict[str, Any]] = []
        self.current_delay_seconds: int = 0

    def start_local_server(self) -> int:
        """Starts the local HTTP server on a free port."""
        LocalAssetHandler.cache_dir = self.transport.cache_base
        LocalAssetHandler.shell_html = self._generate_shell_html()
        LocalAssetHandler.start_html = self._generate_start_page_html()

        server = HTTPServer(("127.0.0.1", 0), LocalAssetHandler)
        self.server_port = server.server_port

        def run_server():
            server.serve_forever()

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Local UI server running at http://127.0.0.1:{self.server_port}/")
        return self.server_port

    def launch(self):
        """Launches the PyWebView window."""
        port = self.start_local_server()
        url = f"http://127.0.0.1:{port}/"

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
        """Generates the top-level browser shell with modern Anti-Slop compliant UI."""
        return r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>N0PPERABO - مرورگر آفلاین NTTM</title>
  <style>
    :root {
      --bg-base: #090b10;
      --bg-surface: #11141c;
      --bg-surface-elevated: #161b26;
      --border-subtle: #232a3b;
      --border-focus: #3b82f6;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #ef4444;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --toolbar-height: 56px;
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
      gap: 10px;
      padding: 0 14px;
      z-index: 100;
    }

    /* Brand Logo & Name */
    .brand-badge {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 4px 8px;
      background: transparent;
      border: 1px solid transparent;
      border-radius: 6px;
      cursor: pointer;
      transition: background 0.15s ease, border-color 0.15s ease;
      flex-shrink: 0;
    }
    .brand-badge:hover {
      background: rgba(255, 255, 255, 0.04);
      border-color: var(--border-subtle);
    }
    .brand-logo-container {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 24px;
    }
    .toolbar-logo {
      height: 22px;
      width: auto;
      object-fit: contain;
    }
    .brand-title {
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.8px;
      color: var(--text);
    }

    /* Navigation Group */
    .nav-group, .actions-group {
      display: flex;
      align-items: center;
      gap: 4px;
      flex-shrink: 0;
    }
    .btn-nav {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      width: 32px;
      height: 32px;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease;
    }
    .btn-nav:hover {
      background: rgba(255, 255, 255, 0.05);
      border-color: var(--text-muted);
      color: var(--text);
    }
    .btn-nav:active {
      background: rgba(255, 255, 255, 0.08);
    }

    /* Address Bar */
    .url-bar-container {
      flex: 1;
      display: flex;
      align-items: center;
      background: var(--bg-base);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 2px 10px;
      gap: 8px;
      transition: border-color 0.15s ease;
      min-width: 180px;
    }
    .url-bar-container:focus-within {
      border-color: var(--border-focus);
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
      color: var(--text);
      font-size: 13px;
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
    .btn-go {
      background: var(--primary);
      color: #ffffff;
      border: none;
      padding: 5px 14px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 12.5px;
      font-weight: 600;
      transition: background 0.15s ease;
      flex-shrink: 0;
    }
    .btn-go:hover {
      background: var(--primary-hover);
    }
    .btn-go:active {
      transform: translateY(1px);
    }

    /* Mode Toggle */
    .mode-toggle {
      display: flex;
      align-items: center;
      gap: 7px;
      background: transparent;
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-subtle);
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
      user-select: none;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .mode-toggle:hover {
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
    }
    .mode-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent-emerald);
    }
    .mode-toggle.batch .mode-dot {
      background: var(--accent-amber);
    }

    /* Daemon Fast Mode Toggle */
    .daemon-btn {
      display: flex;
      align-items: center;
      gap: 7px;
      background: transparent;
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-subtle);
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
      user-select: none;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .daemon-btn:hover {
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
    }
    .daemon-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--text-dim);
      transition: background 0.15s ease;
    }
    .daemon-btn.active {
      border-color: rgba(16, 185, 129, 0.4);
      color: #34d399;
      background: rgba(16, 185, 129, 0.08);
    }
    .daemon-btn.active .daemon-dot {
      background: var(--accent-emerald);
    }

    /* Custom Delay Controls */
    .delay-container {
      display: flex;
      align-items: center;
      gap: 6px;
      background: transparent;
      padding: 4px 8px;
      border-radius: 6px;
      border: 1px solid var(--border-subtle);
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .delay-container:hover {
      background: rgba(255, 255, 255, 0.04);
    }
    .delay-container.active {
      border-color: rgba(245, 158, 11, 0.5);
      background: rgba(245, 158, 11, 0.08);
    }
    .delay-btn {
      display: flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
      font-size: 12px;
      user-select: none;
      color: var(--text-muted);
    }
    .delay-container.active .delay-btn {
      color: #fbbf24;
      font-weight: 600;
    }
    .delay-input-box {
      display: flex;
      align-items: center;
      gap: 4px;
      border-right: 1px solid var(--border-subtle);
      padding-right: 6px;
      margin-right: 2px;
    }
    .delay-input-box input {
      width: 40px;
      background: var(--bg-base);
      border: 1px solid var(--border-subtle);
      color: #fbbf24;
      padding: 2px 4px;
      border-radius: 4px;
      font-size: 12px;
      font-family: ui-monospace, monospace;
      font-weight: 600;
      text-align: center;
      outline: none;
      direction: ltr;
    }
    .delay-input-box input:focus {
      border-color: #fbbf24;
    }

    /* Action Buttons */
    .action-btn {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text);
      padding: 5px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .action-btn:hover {
      background: rgba(255, 255, 255, 0.05);
      border-color: var(--text-muted);
    }
    .action-btn:active {
      background: rgba(255, 255, 255, 0.08);
    }

    /* Batch Queue Bar */
    .queue-bar {
      display: none;
      background: var(--bg-surface-elevated);
      border-bottom: 1px solid var(--border-subtle);
      padding: 6px 14px;
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
      scrollbar-width: none;
    }
    .queue-item {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11.5px;
      font-family: ui-monospace, monospace;
      display: flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }
    .queue-item .type {
      color: var(--primary);
      font-weight: 600;
    }

    /* Content Area */
    .content-area {
      flex: 1;
      position: relative;
      background: #ffffff;
    }
    iframe {
      width: 100%;
      height: 100%;
      border: none;
      display: block;
    }

    /* Status Bar */
    .status-bar {
      height: 24px;
      background: var(--bg-surface);
      border-top: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 12px;
      font-size: 11.5px;
      color: var(--text-dim);
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
      background: rgba(9, 11, 16, 0.85);
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      z-index: 200;
    }
    .loading-overlay.active { display: flex; }
    .cyber-spinner {
      width: 36px;
      height: 36px;
      border: 2px solid var(--border-subtle);
      border-top-color: var(--primary);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    .loading-text {
      font-size: 13px;
      color: var(--text-muted);
      font-weight: 500;
    }

    /* Settings Modal */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.7);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }
    .modal-overlay.active { display: flex; }
    .modal {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      width: 480px;
      max-width: 90vw;
      padding: 20px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
    }
    .modal h2 {
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 16px;
      color: var(--text);
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
      font-weight: 500;
    }
    .form-group input {
      width: 100%;
      background: var(--bg-base);
      border: 1px solid var(--border-subtle);
      color: var(--text);
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 13px;
      font-family: ui-monospace, monospace;
      outline: none;
      direction: ltr;
    }
    .form-group input:focus {
      border-color: var(--border-focus);
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 18px;
    }
    .btn-modal-cancel {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12.5px;
    }
    .btn-modal-cancel:hover {
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
    }
    .btn-modal-save {
      background: var(--primary);
      border: none;
      color: #fff;
      padding: 6px 16px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12.5px;
      font-weight: 600;
    }
    .btn-modal-save:hover {
      background: var(--primary-hover);
    }
  </style>
</head>
<body>

  <!-- Top Toolbar -->
  <div class="toolbar">
    <!-- Brand Emblem -->
    <div class="brand-badge" onclick="window.shellApp.goHome()" title="صفحه اصلی N0PPERABO">
      <div class="brand-logo-container">
        <img src="/assets/logo.svg" alt="N0pperabo" class="toolbar-logo" />
      </div>
      <span class="brand-title">NTTM</span>
    </div>

    <!-- Navigation Buttons with SVGs -->
    <div class="nav-group">
      <button class="btn-nav" onclick="window.shellApp.goBack()" title="بازگشت (Back)">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
      </button>
      <button class="btn-nav" onclick="window.shellApp.goForward()" title="جلو (Forward)">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
      </button>
      <button class="btn-nav" onclick="window.shellApp.reload()" title="تازه‌سازی (Reload)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
      </button>
      <button class="btn-nav" onclick="window.shellApp.goHome()" title="صفحه خانگی">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
      </button>
    </div>

    <!-- URL Bar -->
    <div class="url-bar-container">
      <span class="url-icon">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </span>
      <input type="text" class="url-input" id="url-input" placeholder="آدرس وب‌سایت را وارد کنید یا جستجو نمایید..." />
      <button class="btn-go" onclick="window.shellApp.navigateFromBar()">برو</button>
    </div>

    <!-- Mode & Daemon Toggles -->
    <div class="actions-group">
      <div class="mode-toggle" id="mode-toggle" onclick="window.shellApp.toggleMode()" title="تغییر حالت اجرای اکشن‌ها">
        <span class="mode-dot"></span>
        <span id="mode-text">مد مستقیم</span>
      </div>

      <div class="daemon-btn" id="daemon-toggle" onclick="window.shellApp.toggleDaemon()" title="رانر فعال در پس‌زمینه">
        <span class="daemon-dot"></span>
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

      <button class="action-btn" id="btn-batch-send" style="display:none; color:var(--accent-amber); border-color:rgba(245,158,11,0.4);" onclick="window.shellApp.triggerBatch()" title="ارسال تغییرات به گیت‌هاب">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        <span>ارسال رویدادها</span>
      </button>

      <button class="action-btn" id="btn-shorts-nav" style="display:none;" onclick="window.shellApp.triggerShortsNext()" title="پیمایش شورتز یوتیوب (ویدیوی بعدی)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/><line x1="19" y1="3" x2="19" y2="21"/></svg>
        <span>شورتز بعدی</span>
      </button>

      <button class="action-btn" onclick="window.shellApp.openSettings()" title="تنظیمات توکن و ریپازیتوری">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>تنظیمات</span>
      </button>
    </div>
  </div>

  <!-- Batch Queue Bar -->
  <div class="queue-bar" id="queue-bar">
    <span style="font-size:12px; font-weight:600; color:var(--accent-amber);">صف رویدادها:</span>
    <div class="queue-items" id="queue-items"></div>
    <button class="action-btn" style="color:var(--accent-rose); border-color:rgba(239,68,68,0.3);" onclick="window.shellApp.clearQueue()">پاکسازی صف</button>
  </div>

  <!-- Main Web Content -->
  <div class="content-area">
    <iframe id="browser-frame" src="/start" sandbox="allow-scripts allow-forms allow-same-origin allow-popups"></iframe>
    <div class="loading-overlay" id="loading-overlay">
      <div class="cyber-spinner"></div>
      <div class="loading-text" id="loading-text">در حال ارسال اکشن به گیت‌هاب...</div>
    </div>
  </div>

  <!-- Bottom Status Bar -->
  <div class="status-bar">
    <div class="status-text" id="status-text">
      <span>آماده</span>
    </div>
    <div id="status-details">NTTM Stealth Core v2.0</div>
  </div>

  <!-- Settings Modal -->
  <div class="modal-overlay" id="settings-modal">
    <div class="modal">
      <h2>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        تنظیمات ارتباط با مخزن گیت‌هاب
      </h2>
      <div class="form-group">
        <label>توکن دسترسی شخصی (Personal Access Token - PAT):</label>
        <input type="password" id="cfg-token" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" />
      </div>
      <div class="form-group">
        <label>مخزن هدف (Target Repository):</label>
        <input type="text" id="cfg-repo" placeholder="owner/repo-name" />
      </div>
      <div class="modal-actions">
        <button class="btn-modal-cancel" onclick="window.shellApp.closeSettings()">انصراف</button>
        <button class="btn-modal-save" onclick="window.shellApp.saveSettings()">ذخیره تنظیمات</button>
      </div>
    </div>
  </div>

  <script>
    window.shellApp = {
      isBatchMode: false,
      isDaemonActive: false,
      isCustomDelayActive: false,
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
              urlInput.value = loc;
              this.updateShortsButton(loc);
            }
          } catch(err) {}
          this.injectDeepInterceptor();
        });

        setTimeout(() => this.injectDeepInterceptor(), 500);

        setInterval(() => {
          if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.get_current_url().then(res => {
              if (res && res.url) {
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
        const frame = document.getElementById('browser-frame');
        let currentUrl = '';
        try {
          currentUrl = frame.contentWindow.location.href;
        } catch(e) {}
        if (!currentUrl && window.pywebview && window.pywebview.api) {
          window.pywebview.api.get_current_url().then(res => {
            if (res && res.url) this.executeShortsNext(res.url);
          });
          return;
        }
        this.executeShortsNext(currentUrl);
      },

      executeShortsNext: function(currentUrl) {
        let videoId = '';
        const m = currentUrl.match(/\/shorts\/([a-zA-Z0-9_-]+)/);
        if (m && m[1]) videoId = m[1];

        this.showLoading('در حال دریافت شورتز بعدی از گیت‌هاب...');
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.navigate_shorts_next(videoId).then(res => {
            if (res && res.error) {
              this.hideLoading();
              alert('خطا در بارگذاری شورتز بعدی: ' + res.error);
            }
          }).catch(err => {
            this.hideLoading();
            alert('خطای سیستمی: ' + err);
          });
        }
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
        this.showLoading('در حال بارگذاری صفحه از طریق رانر گیت‌هاب...');
        this.updateShortsButton(url);
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.navigate(url).then(res => {
            if (res && res.error) {
              this.hideLoading();
              alert('خطا: ' + res.error);
            }
          });
        } else {
          document.getElementById('browser-frame').src = url;
        }
      },

      goHome: function() {
        document.getElementById('browser-frame').src = '/start';
        document.getElementById('url-input').value = '';
        this.updateShortsButton('');
      },

      goBack: function() {
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.go_back();
        } else {
          window.history.back();
        }
      },

      goForward: function() {
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.go_forward();
        } else {
          window.history.forward();
        }
      },

      reload: function() {
        if (window.pywebview && window.pywebview.api) {
          this.showLoading('در حال بازخوانی صفحه...');
          window.pywebview.api.reload();
        } else {
          document.getElementById('browser-frame').contentWindow.location.reload();
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
          div.innerHTML = '<span class="type">' + item.type + '</span> <span>' + (item.selector || item.text || item.key || '') + '</span>';
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
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.send_batch().then(res => {
            this.clearQueue();
          });
        }
      },

      openSettings: function() {
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.get_config().then(cfg => {
            document.getElementById('cfg-token').value = cfg.token || '';
            document.getElementById('cfg-repo').value = cfg.repo || '';
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
        if (!token || !repo) {
          alert('لطفاً هر دو فیلد توکن و ریپازیتوری را پر کنید.');
          return;
        }
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.save_config(token, repo).then(res => {
            if (res.success) {
              alert('تنظیمات با موفقیت ذخیره شد.');
              this.closeSettings();
            } else {
              alert('خطا در ذخیره: ' + res.error);
            }
          });
        }
      },

      injectDeepInterceptor: function() {
        const frame = document.getElementById('browser-frame');
        try {
          const doc = frame.contentDocument || frame.contentWindow.document;
          if (!doc || doc._injectedNTTM) return;
          doc._injectedNTTM = true;

          const script = doc.createElement('script');
          script.textContent = `
            (function() {
              function getBestSelector(el) {
                if (!el || el === document.body) return 'body';
                if (el.id) return '#' + el.id;
                if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                if (el.className && typeof el.className === 'string') {
                  const c = el.className.trim().split(/\s+/)[0];
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
        """Generates clean, Anti-Slop compliant start page adhering to DESIGN.md."""
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
      --bg-base: #090b10;
      --bg-surface: #11141c;
      --bg-surface-elevated: #161b26;
      --border-subtle: #232a3b;
      --border-focus: #3b82f6;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', Tahoma, sans-serif;
    }}
    body {{
      background: var(--bg-base);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}

    .container {{
      max-width: 680px;
      width: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 28px;
    }}

    /* Hero Branding */
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
      padding: 8px;
    }}
    .hero-logo {{
      width: 110px;
      height: auto;
      object-fit: contain;
    }}
    .hero h1 {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 1.5px;
      color: var(--text);
    }}
    .hero p {{
      color: var(--text-muted);
      font-size: 13.5px;
      line-height: 1.5;
    }}

    /* Search Box */
    .search-box {{
      width: 100%;
      display: flex;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 3px;
      transition: border-color 0.15s ease;
    }}
    .search-box:focus-within {{
      border-color: var(--border-focus);
    }}
    .search-input-wrapper {{
      flex: 1;
      display: flex;
      align-items: center;
      padding: 0 12px;
      gap: 8px;
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
      color: var(--text);
      font-size: 13.5px;
      padding: 9px 0;
      outline: none;
    }}
    .search-box input::placeholder {{
      color: var(--text-dim);
    }}
    .btn-search {{
      background: var(--primary);
      border: none;
      color: #fff;
      font-size: 13px;
      font-weight: 600;
      padding: 0 18px;
      border-radius: 4px;
      cursor: pointer;
      transition: background 0.15s ease;
    }}
    .btn-search:hover {{
      background: var(--primary-hover);
    }}

    /* Quick Grid */
    .quick-grid {{
      width: 100%;
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 8px;
    }}
    .quick-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 12px 6px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      transition: all 0.15s ease;
      text-decoration: none;
      color: var(--text);
    }}
    .quick-card:hover {{
      background: var(--bg-surface-elevated);
      border-color: var(--text-dim);
    }}
    .quick-icon-svg {{
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-muted);
    }}
    .quick-card:hover .quick-icon-svg {{
      color: var(--text);
    }}
    .quick-name {{
      font-size: 11.5px;
      font-weight: 500;
      color: var(--text-muted);
    }}
    .quick-card:hover .quick-name {{
      color: var(--text);
    }}

    /* Info Grid */
    .info-grid {{
      width: 100%;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .info-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 16px;
      font-size: 12px;
      line-height: 1.6;
    }}
    .info-card h3 {{
      font-size: 13px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 6px;
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
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 11px;
      font-family: ui-monospace, monospace;
      font-weight: 600;
    }}
    .badge-ok {{
      background: rgba(16, 185, 129, 0.12);
      color: var(--accent-emerald);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .cfg-link {{
      color: var(--primary);
      cursor: pointer;
      text-decoration: underline;
      font-weight: 500;
    }}
    .cfg-link:hover {{
      color: var(--primary-hover);
    }}

    .footer {{
      font-size: 11px;
      color: var(--text-dim);
      font-family: ui-monospace, monospace;
      letter-spacing: 0.5px;
      margin-top: 8px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Hero Branding -->
    <div class="hero">
      <div class="hero-logo-box">
        <img src="/assets/logo.svg" alt="N0pperabo Logo" class="hero-logo" />
      </div>
      <h1>N0PPERABO</h1>
      <p>مرورگر غیرمستقیم NTTM با تانل رانر گیت‌هاب و موتور Stealth</p>
    </div>

    <!-- Search Input Bar -->
    <div class="search-box">
      <div class="search-input-wrapper">
        <span class="search-icon">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <input type="text" id="start-query" placeholder="جستجو در اینترنت یا وارد کردن آدرس وب‌سایت..." onkeydown="if(event.key==='Enter') doSearch()" />
      </div>
      <button class="btn-search" onclick="doSearch()">جستجو</button>
    </div>

    <!-- Quick Access Grid with SVGs -->
    <div class="quick-grid">
      <div class="quick-card" onclick="quickNav('https://www.google.com')">
        <div class="quick-icon-svg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v8M8 12h8"/></svg>
        </div>
        <span class="quick-name">Google</span>
      </div>
      <div class="quick-card" onclick="quickNav('https://duckduckgo.com')">
        <div class="quick-icon-svg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/></svg>
        </div>
        <span class="quick-name">DuckDuckGo</span>
      </div>
      <div class="quick-card" onclick="quickNav('https://www.youtube.com')">
        <div class="quick-icon-svg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        </div>
        <span class="quick-name">YouTube</span>
      </div>
      <div class="quick-card" onclick="quickNav('https://fa.wikipedia.org')">
        <div class="quick-icon-svg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
        </div>
        <span class="quick-name">Wikipedia</span>
      </div>
      <div class="quick-card" onclick="quickNav('https://github.com/explore')">
        <div class="quick-icon-svg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
        </div>
        <span class="quick-name">GitHub</span>
      </div>
      <div class="quick-card" onclick="quickNav('https://stackoverflow.com')">
        <div class="quick-icon-svg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
        </div>
        <span class="quick-name">Stack Overflow</span>
      </div>
    </div>

    <!-- Info Grid -->
    <div class="info-grid">
      <div class="info-card">
        <h3>راهنمای حالت‌های کارکرد</h3>
        <p><strong>حالت مستقیم (Direct):</strong> کلیک‌ها و ارسال فرم‌ها بلافاصله به گیت‌هاب ارسال شده و صفحه جدید دریافت می‌شود.</p>
        <p><strong>حالت دسته‌ای (Batch):</strong> مناسب برای فرم‌های چندمرحله‌ای و لاگین. اقدامات پشت‌سرهم ثبت شده و در یک رانر گیت‌هاب اجرا می‌شوند.</p>
      </div>

      <div class="info-card">
        <h3>وضعیت اتصال مخزن</h3>
        <p style="margin-bottom: 6px;"><strong>توکن دسترسی:</strong> <span class="badge badge-ok">{token_status}</span></p>
        <p style="margin-bottom: 6px;"><strong>مخزن هدف:</strong> <span dir="ltr" style="color:var(--text-muted); font-family: ui-monospace, monospace;">{repo_status}</span></p>
        <p style="margin-bottom: 10px;"><strong>کوکی‌های فعال:</strong> <span class="badge badge-ok">{cookies_count} عدد</span></p>
        <p><a class="cfg-link" onclick="parent.window.shellApp.openSettings()">تغییر تنظیمات و کلیدهای ارتباطی</a></p>
      </div>
    </div>

    <div class="footer">
      N0PPERABO NTTM CORE • POWERED BY GITHUB RUNNERS
    </div>
  </div>

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
  </script>
</body>
</html>
"""
