import base64
import concurrent.futures
import hashlib
import json
import logging
import os
import shutil
import threading
import time
import urllib.parse
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .config import BrowserConfig
from .session_manager import SessionManager
from .crypto import (
    encrypt_bytes,
    decrypt_bytes,
    encrypt_envelope,
    decrypt_envelope,
    is_encrypted,
    get_effective_key,
)

logger = logging.getLogger("GitHubTransport")


@dataclass
class FetchResult:
    """Represents the outcome of an offline fetch operation."""

    success: bool
    response_type: str = "page"  # "page" or "file"
    index_html_path: Optional[str] = None
    downloaded_file_path: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    error_message: str = ""


class ParallelProgressTracker:
    """Thread-safe progress reporter for parallel chunk downloads."""

    def __init__(self, parts: List[Dict[str, Any]], status_cb: Callable[[str], None]):
        self.total_parts = len(parts)
        self.part_sizes = {p["filename"]: p.get("size", 0) for p in parts}
        self.total_bytes = sum(self.part_sizes.values())
        self.downloaded_per_part = {p["filename"]: 0 for p in parts}
        self.completed_parts: set = set()
        self.status_cb = status_cb
        self._lock = threading.Lock()
        self._last_report_time = 0.0
        self._last_reported_pct = -1

    def on_bytes(self, filename: str, downloaded_bytes: int):
        with self._lock:
            self.downloaded_per_part[filename] = downloaded_bytes
            total_dl = sum(self.downloaded_per_part.values())
            now = time.time()
            if self.total_bytes > 0:
                pct = min(100, int((total_dl / self.total_bytes) * 100))
                # Throttle callbacks to avoid flooding UI (every 2% or 0.4s)
                if (pct >= self._last_reported_pct + 2 or (now - self._last_report_time) >= 0.4) and pct != self._last_reported_pct:
                    self._last_reported_pct = pct
                    self._last_report_time = now
                    done_count = len(self.completed_parts)
                    dl_mb = total_dl / (1024 * 1024)
                    tot_mb = self.total_bytes / (1024 * 1024)
                    self.status_cb(
                        f"دانلود موازی قطعات: {pct}% ({dl_mb:.1f}/{tot_mb:.1f} MB) - {done_count}/{self.total_parts} قطعه"
                    )

    def on_complete(self, filename: str):
        with self._lock:
            self.completed_parts.add(filename)
            if self.part_sizes.get(filename, 0) > 0:
                self.downloaded_per_part[filename] = self.part_sizes[filename]
            done_count = len(self.completed_parts)
            self.status_cb(f"قطعه {filename} با موفقیت دریافت شد ({done_count}/{self.total_parts}).")


class GitHubTransport:
    """
    Communicates with GitHub REST API, dispatches fetcher.yml workflow,
    polls for res-<request_id> releases, and downloads & reassembles 50MB chunks.
    """

    def __init__(self, config: BrowserConfig, session_manager: SessionManager):
        self.config = config
        self.session_manager = session_manager
        self.api_base = "https://api.github.com"
        self.cache_base = Path.home() / ".gh_offline_browser" / "cache"
        self.cache_base.mkdir(parents=True, exist_ok=True)

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token.strip()}"
        return headers

    def normalize_target_url(self, target_url: str) -> str:
        """
        Converts search queries or missing schemes to valid absolute URLs.
        """
        raw = target_url.strip()
        if not raw:
            return "https://www.google.com"

        # Check if it already has http:// or https://
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw

        # Check if it's a domain name (contains dot and no spaces)
        if " " not in raw and "." in raw and not raw.startswith("/"):
            return f"https://{raw}"

        # Otherwise treat as search query
        query_encoded = urllib.parse.quote_plus(raw)
        search_template = self.config.default_search_url or "https://www.google.com/search?q={query}"
        if "{query}" in search_template:
            return search_template.format(query=query_encoded)
        return f"{search_template}{query_encoded}"

    def fetch(
        self,
        target_url: str,
        mode: str = "navigate",
        method: str = "GET",
        content_type: str = "",
        body_bytes: bytes = b"",
        actions: Optional[List[Dict[str, Any]]] = None,
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        custom_delay_seconds: int = 0,
        custom_scroll_steps: int = 0,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> FetchResult:
        """
        Executes an end-to-end asynchronous fetch via GitHub Actions.
        """

        def update_status(msg: str):
            logger.info(msg)
            if status_callback:
                try:
                    status_callback(msg)
                except Exception as e:
                    logger.debug(f"Status callback error: {e}")

        if not self.config.is_configured():
            return FetchResult(
                success=False,
                error_message="تنظیمات گیت‌هاب (توکن PAT یا نام مخزن) تکمیل نشده است. لطفاً ابتدا در بخش تنظیمات وارد کنید.",
            )

        final_target_url = self.normalize_target_url(target_url) if mode == "navigate" else target_url

        # 1. Generate unique request_id
        timestamp_part = int(time.time())
        random_part = uuid.uuid4().hex[:6]
        request_id = f"{timestamp_part}_{random_part}"
        parts_info = []
        if custom_delay_seconds > 0:
            parts_info.append(f"تأخیر {custom_delay_seconds} ثانیه‌ای")
        if custom_scroll_steps > 0:
            parts_info.append(f"{custom_scroll_steps} مرحله اسکرول")
        if parts_info:
            update_status(f"ایجاد پاکت درخواست {request_id} (با {' و '.join(parts_info)} در رانر)...")
        else:
            update_status(f"ایجاد پاکت درخواست با شناسه: {request_id}")

        # 2. Prepare envelope
        storage_state, session_storage = self.session_manager.get_envelope_state(target_url=final_target_url)

        envelope = {
            "request_id": request_id,
            "target_url": final_target_url,
            "mode": mode,
            "http_method": method.upper(),
            "content_type": content_type or "",
            "body_base64": base64.b64encode(body_bytes).decode("utf-8") if body_bytes else "",
            "custom_headers": custom_headers
            or {
                "Referer": final_target_url,
                "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            "viewport": {"width": 1366, "height": 768},
            "custom_delay_seconds": custom_delay_seconds,
            "auto_scroll": bool(custom_scroll_steps > 0),
            "scroll_steps": custom_scroll_steps,
            "actions": actions or [],
            "intercepted_ajax": intercepted_ajax
            or {
                "captured": False,
                "url": "",
                "method": "POST",
                "headers": {},
                "body_base64": "",
            },
            "storage_state": storage_state,
            "session_storage": session_storage,
        }

        # 3. Upload requests/{request_id}.json to GitHub
        try:
            update_status("در حال آپلود پاکت به مخزن گیت‌هاب...")
            upload_success, upload_err = self._upload_request_file(request_id, envelope)
            if not upload_success:
                return FetchResult(
                    success=False,
                    error_message=f"خطا در ارسال فایل درخواست به گیت‌هاب: {upload_err}",
                )

            # 4. Check if Daemon is active or trigger fetcher workflow
            daemon_active = self.is_daemon_alive()
            if daemon_active:
                update_status("⚡ رانر فعال دائمی (Daemon) آنلاین است! پردازش فوق سریع بدون نیاز به بوت رانر...")
            else:
                update_status("در حال ارسال سیگنال اجرای رانر مجزا (workflow_dispatch)...")
                dispatch_success, dispatch_err = self._trigger_workflow(request_id)
                if not dispatch_success:
                    return FetchResult(
                        success=False,
                        error_message=f"خطا در اجرای اکشن گیت‌هاب: {dispatch_err}",
                    )

            # 5. Poll for release res-{request_id}
            update_status("در انتظار شروع و اتمام پردازش رانر در گیت‌هاب...")
            release_data, poll_err = self._poll_release_completion(
                request_id, update_status
            )
            if not release_data:
                return FetchResult(
                    success=False,
                    error_message=f"خطا در دریافت پاسخ از رانر: {poll_err}",
                )

            # 6. Download manifest and chunks in parallel (max_workers=6)
            update_status("دریافت فایل مانیفست و شروع دانلود قطعات...")
            cache_req_dir = self.cache_base / request_id
            cache_req_dir.mkdir(parents=True, exist_ok=True)

            manifest_asset = self._find_asset(release_data, "manifest.json")
            if not manifest_asset:
                return FetchResult(
                    success=False,
                    error_message="فایل manifest.json در رلیس یافت نشد.",
                )

            manifest_path = cache_req_dir / "manifest.json"
            try:
                self._download_file_resumable(
                    manifest_asset, manifest_path, update_status, "manifest.json"
                )
            except Exception as mf_err:
                tag = release_data.get("tag_name", f"res-{request_id}")
                fresh_rel = self._fetch_release_by_tag(tag)
                if fresh_rel:
                    fresh_mf = self._find_asset(fresh_rel, "manifest.json")
                    if fresh_mf:
                        release_data = fresh_rel
                        manifest_asset = fresh_mf
                        self._download_file_resumable(
                            manifest_asset, manifest_path, update_status, "manifest.json"
                        )
                    else:
                        raise mf_err
                else:
                    raise mf_err

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            parts = manifest.get("parts", [])
            if not parts:
                return FetchResult(
                    success=False,
                    error_message="مانیفست فاقد قطعات فشرده (parts) است.",
                )

            # Concurrent parallel download of 50MB chunks
            try:
                part_paths = self._download_chunks_parallel(
                    release_data=release_data,
                    parts=parts,
                    cache_req_dir=cache_req_dir,
                    update_status=update_status,
                    max_workers=6,
                )
            except Exception as dl_err:
                return FetchResult(
                    success=False,
                    error_message=f"خطا در دانلود موازی قطعات رلیس: {dl_err}",
                )

            # 7. Assemble chunks into payload.zip
            update_status("در حال سرهم‌بندی قطعات و اعتبارسنجی آرشیو...")
            assembled_zip_path = cache_req_dir / "payload.zip"
            self._assemble_chunks(part_paths, assembled_zip_path)

            # Verify full archive checksum
            expected_full_sha = manifest.get("full_sha256", "")
            if expected_full_sha:
                calculated_full_sha = self._compute_sha256(assembled_zip_path)
                if calculated_full_sha.lower() != expected_full_sha.lower():
                    return FetchResult(
                        success=False,
                        error_message="مغایرت چک‌سام SHA-256 فایل تجمیع‌شده با مانیفست رانر.",
                    )

            # Decrypt payload.zip if encrypted with AES-256-GCM
            with open(assembled_zip_path, "rb") as f:
                assembled_bytes = f.read()

            if is_encrypted(assembled_bytes):
                update_status("در حال رمزگشایی بسته پاسخ با کلید اختصاصی AES-256-GCM...")
                try:
                    decrypted_zip = decrypt_envelope(assembled_bytes, self.config.encryption_key)
                    with open(assembled_zip_path, "wb") as f:
                        f.write(decrypted_zip)
                except Exception as dec_err:
                    return FetchResult(
                        success=False,
                        error_message=f"خطا در رمزگشایی بسته دریافتی با کلید امنیتی: {dec_err}",
                    )

            # 8. Unzip payload
            update_status("در حال استخراج محتوای وب...")
            extracted_dir = cache_req_dir / "extracted"
            if extracted_dir.exists():
                shutil.rmtree(extracted_dir)
            extracted_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(assembled_zip_path, "r") as zip_ref:
                zip_ref.extractall(extracted_dir)

            # 9. Update session if storage_state exists
            remote_storage_state = extracted_dir / "storage_state.json"
            if remote_storage_state.exists():
                self.session_manager.update_from_remote(remote_storage_state)

            # 10. Read meta.json
            meta_path = extracted_dir / "meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read meta.json: {e}")

            # 11. Cleanup remote release & tag asynchronously/silently
            self._cleanup_remote_release(release_data.get("id"), f"res-{request_id}")

            # 12. Check page vs file response
            response_type = meta.get("response_type", "page")
            if response_type == "file":
                filename = meta.get("download_filename", "downloaded_file")
                src_file = extracted_dir / "download" / filename
                if not src_file.exists():
                    # Check if file is in extracted root
                    if (extracted_dir / filename).exists():
                        src_file = extracted_dir / filename

                dest_dir = Path(self.config.downloads_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / filename
                if src_file.exists():
                    shutil.copy2(src_file, dest_file)
                    update_status(f"فایل با موفقیت در {dest_file} ذخیره شد.")
                    return FetchResult(
                        success=True,
                        response_type="file",
                        downloaded_file_path=str(dest_file),
                        meta=meta,
                    )
                else:
                    return FetchResult(
                        success=False,
                        error_message=f"فایل دانلودی {filename} در بسته دریافتی یافت نشد.",
                        meta=meta,
                    )

            # Web page response
            index_html = extracted_dir / "index.html"
            if not index_html.exists():
                return FetchResult(
                    success=False,
                    error_message="فایل index.html در بسته دریافتی موجود نیست.",
                    meta=meta,
                )

            update_status("صفحه وب با موفقیت آماده شد.")
            return FetchResult(
                success=True,
                response_type="page",
                index_html_path=str(index_html),
                meta=meta,
            )

        except Exception as e:
            logger.exception("Unexpected error during fetch")
            return FetchResult(success=False, error_message=f"خطای غیرمنتظره: {str(e)}")

    fetch_page = fetch

    def _upload_request_file(self, request_id: str, envelope: dict) -> Tuple[bool, str]:
        """Uploads requests/{request_id}.json via PUT /repos/{owner}/{repo}/contents/..."""
        owner, repo = self._parse_repo()
        url = f"{self.api_base}/repos/{owner}/{repo}/contents/requests/{request_id}.json"

        # Mandatory AES-256-GCM encryption on all request envelopes
        content_bytes = encrypt_envelope(envelope, self.config.encryption_key)
        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        payload = {
            "message": f"Dispatch request {request_id}",
            "content": content_b64,
            "branch": self.config.branch or "main",
        }

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.put(
                    url, headers=self._get_headers(), json=payload, timeout=(15, 60)
                )
                if resp.status_code in (200, 201):
                    return True, ""
                if attempt == max_retries:
                    return False, f"HTTP {resp.status_code}: {resp.text}"
            except (
                requests.exceptions.Timeout,
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
            ) as e:
                logger.warning(f"Upload request attempt {attempt}/{max_retries} failed: {e}")
                if attempt == max_retries:
                    return False, f"خطای شبکه/تایم‌اوت در ارسال به گیت‌هاب: {str(e)}"
                time.sleep(1)
            except Exception as e:
                return False, str(e)
        return False, "Failed to upload request after retries" 

    def _trigger_workflow(self, request_id: str) -> Tuple[bool, str]:
        """Dispatches fetcher.yml via POST /repos/{owner}/{repo}/actions/workflows/..."""
        owner, repo = self._parse_repo()
        url = f"{self.api_base}/repos/{owner}/{repo}/actions/workflows/fetcher.yml/dispatches"

        payload = {
            "ref": self.config.branch or "main",
            "inputs": {"request_id": request_id},
        }

        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=(15, 30))
            if resp.status_code in (200, 204):
                return True, ""
            return False, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, str(e)

    def is_daemon_alive(self) -> bool:
        """Checks if a daemon worker workflow is currently active or queued."""
        if not self.config.is_configured():
            return False
        try:
            owner, repo = self._parse_repo()
            url = f"{self.api_base}/repos/{owner}/{repo}/actions/workflows/daemon.yml/runs"
            resp = requests.get(
                url,
                headers=self._get_headers(),
                params={"status": "in_progress", "per_page": 5},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("total_count", 0) > 0:
                    return True

            resp_queued = requests.get(
                url,
                headers=self._get_headers(),
                params={"status": "queued", "per_page": 5},
                timeout=10,
            )
            if resp_queued.status_code == 200:
                data_q = resp_queued.json()
                if data_q.get("total_count", 0) > 0:
                    return True

            return False
        except Exception as e:
            logger.debug(f"is_daemon_alive check error: {e}")
            return False

    def start_daemon(self, idle_timeout_min: int = 30) -> Tuple[bool, str]:
        """Dispatches daemon.yml workflow to start persistent worker."""
        if not self.config.is_configured():
            return False, "تنظیمات گیت‌هاب کامل نیست."
        try:
            owner, repo = self._parse_repo()
            url = f"{self.api_base}/repos/{owner}/{repo}/actions/workflows/daemon.yml/dispatches"
            payload = {
                "ref": self.config.branch or "main",
                "inputs": {
                    "idle_timeout_minutes": str(idle_timeout_min),
                    "max_hours": "5.5",
                    "auto_chain": "true",
                },
            }
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=20)
            if resp.status_code in (200, 204):
                return True, "دستور اجرای رانر دائمی ارسال شد."
            return False, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, str(e)

    def stop_daemon(self) -> Tuple[bool, str]:
        """Cancels any running daemon.yml workflow runs."""
        if not self.config.is_configured():
            return False, "تنظیمات گیت‌هاب کامل نیست."
        try:
            owner, repo = self._parse_repo()
            url = f"{self.api_base}/repos/{owner}/{repo}/actions/workflows/daemon.yml/runs"
            resp = requests.get(
                url,
                headers=self._get_headers(),
                params={"status": "in_progress", "per_page": 10},
                timeout=10,
            )
            cancelled_count = 0
            if resp.status_code == 200:
                for run in resp.json().get("workflow_runs", []):
                    run_id = run.get("id")
                    cancel_url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
                    c_resp = requests.post(cancel_url, headers=self._get_headers(), timeout=10)
                    if c_resp.status_code in (200, 202):
                        cancelled_count += 1
            return True, f"تعداد {cancelled_count} رانر متوقف شد."
        except Exception as e:
            return False, str(e)

    def _poll_release_completion(
        self, request_id: str, status_cb: Callable[[str], None]
    ) -> Tuple[Optional[dict], str]:
        """
        Polls GET /repos/{owner}/{repo}/releases/tags/res-{request_id} until
        manifest.json appears in assets (indicating workflow finished).
        """
        owner, repo = self._parse_repo()
        tag = f"res-{request_id}"
        url = f"{self.api_base}/repos/{owner}/{repo}/releases/tags/{tag}"

        start_time = time.time()
        timeout = self.config.timeout_seconds
        interval = getattr(self.config, "poll_interval_seconds", 1.0) or 1.0
        if interval > 1.0:
            interval = 1.0

        poll_count = 0
        while time.time() - start_time < timeout:
            poll_count += 1
            try:
                resp = requests.get(url, headers=self._get_headers(), timeout=15)
                if resp.status_code == 200:
                    release = resp.json()
                    assets = release.get("assets", [])
                    has_manifest = any(a.get("name") == "manifest.json" for a in assets)
                    if has_manifest:
                        # Manifest was uploaded last, meaning runner completed packaging
                        time.sleep(1.0)
                        return release, ""
                    else:
                        chunk_count = sum(
                            1 for a in assets if "payload.zip.part" in a.get("name", "")
                        )
                        status_cb(
                            f"رانر در حال آپلود داده‌ها است... ({chunk_count} قطعه آپلود شد)"
                        )
                elif resp.status_code == 404:
                    elapsed = int(time.time() - start_time)
                    status_cb(f"در انتظار آماده‌سازی رانر گیت‌هاب... ({elapsed}s)")
                else:
                    logger.debug(f"Poll status: {resp.status_code}")
            except Exception as e:
                logger.debug(f"Poll request exception: {e}")

            time.sleep(interval)

        return None, f"زمان انتظار به پایان رسید ({timeout} ثانیه). رانر پاسخ نداد."

    def _find_asset(self, release: dict, filename: str) -> Optional[dict]:
        for asset in release.get("assets", []):
            if asset.get("name") == filename:
                return asset
        return None

    def _fetch_release_by_tag(self, tag: str) -> Optional[dict]:
        """Fetches fresh release metadata from GitHub API by tag."""
        if not tag:
            return None
        owner, repo = self._parse_repo()
        url = f"{self.api_base}/repos/{owner}/{repo}/releases/tags/{tag}"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Fetch release by tag {tag} failed: {e}")
        return None

    def _download_chunks_parallel(
        self,
        release_data: dict,
        parts: List[Dict[str, Any]],
        cache_req_dir: Path,
        update_status: Callable[[str], None],
        max_workers: int = 6,
    ) -> List[Path]:
        """
        Downloads release chunks concurrently with ThreadPoolExecutor(max_workers=6),
        verifies SHA-256 integrity for each chunk with retries, tracks aggregate live
        progress, and returns the ordered list of part paths.
        """
        if not parts:
            raise ValueError("مانیفست فاقد قطعات فشرده است.")

        tag = release_data.get("tag_name", "")
        part_assets = {}
        for part_info in parts:
            part_name = part_info["filename"]
            part_asset = self._find_asset(release_data, part_name)
            if not part_asset:
                raise FileNotFoundError(f"قطعه {part_name} در رلیس گیت‌هاب موجود نیست.")
            part_assets[part_name] = (part_info, part_asset)

        num_parts = len(parts)
        effective_workers = min(max_workers, num_parts)
        tracker = ParallelProgressTracker(parts, update_status)
        update_status(f"شروع دانلود موازی {num_parts} قطعه با {effective_workers} اتصال همزمان...")

        part_paths: Dict[str, Path] = {}

        def fetch_single_part(part_name: str) -> Tuple[str, Path]:
            part_info, part_asset = part_assets[part_name]
            expected_sha = part_info["sha256"]
            part_dest = cache_req_dir / part_name

            def on_bytes(dl_bytes: int):
                tracker.on_bytes(part_name, dl_bytes)

            self._download_chunk_with_retry(
                part_asset,
                part_dest,
                expected_sha,
                status_cb=update_status,
                label=part_name,
                progress_hook=on_bytes,
                tag=tag,
            )
            tracker.on_complete(part_name)
            return part_name, part_dest

        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
            future_to_part = {
                executor.submit(fetch_single_part, p["filename"]): p["filename"]
                for p in parts
            }
            for future in concurrent.futures.as_completed(future_to_part):
                p_name = future_to_part[future]
                try:
                    name, dest = future.result()
                    part_paths[name] = dest
                except Exception as exc:
                    for f in future_to_part:
                        f.cancel()
                    raise RuntimeError(f"خطا در دانلود موازی قطعه {p_name}: {exc}") from exc

        # Return ordered parts according to manifest
        return [part_paths[p["filename"]] for p in parts]

    def _download_release_asset(
        self,
        asset: dict,
        dest_path: Path,
        expected_sha: str = "",
        status_cb: Optional[Callable[[str], None]] = None,
        label: str = "",
        max_retries: int = 5,
        progress_hook: Optional[Callable[[int], None]] = None,
        tag: str = "",
    ) -> None:
        """Downloads a single release asset with optional retry, resume and SHA-256 verification."""
        cb = status_cb or (lambda msg: None)
        lbl = label or asset.get("name", "asset")
        if expected_sha:
            self._download_chunk_with_retry(
                asset, dest_path, expected_sha, cb, lbl, max_retries=max_retries, progress_hook=progress_hook, tag=tag
            )
        else:
            self._download_file_resumable(asset, dest_path, cb, lbl, progress_hook=progress_hook)

    def _download_chunk_with_retry(
        self,
        asset: dict,
        dest_path: Path,
        expected_sha: str,
        status_cb: Callable[[str], None],
        label: str,
        max_retries: int = 5,
        progress_hook: Optional[Callable[[int], None]] = None,
        tag: str = "",
    ) -> None:
        """Downloads a chunk with HTTP Range Resume, fresh asset refresh on retry, and SHA-256 verification up to 5 retries."""
        current_asset = asset
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1 and tag:
                    fresh_release = self._fetch_release_by_tag(tag)
                    if fresh_release:
                        fresh_asset = self._find_asset(fresh_release, label)
                        if fresh_asset:
                            current_asset = fresh_asset
                self._download_file_resumable(current_asset, dest_path, status_cb, label, progress_hook=progress_hook)
                actual_sha = self._compute_sha256(dest_path)
                if actual_sha.lower() == expected_sha.lower():
                    return  # Match!
                else:
                    logger.warning(
                        f"Checksum mismatch for {label} (attempt {attempt}). Retrying..."
                    )
                    if dest_path.exists():
                        dest_path.unlink()
            except Exception as e:
                logger.warning(
                    f"Download failure for {label} on attempt {attempt}: {e}. Retrying..."
                )
                time.sleep(1.5 * attempt)

        raise RuntimeError(f"دانلود قطعه {label} پس از {max_retries} تلاش ناموفق بود.")

    def _download_file_resumable(
        self,
        asset: dict,
        dest_path: Path,
        status_cb: Callable[[str], None],
        label: str,
        progress_hook: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Performs resumable download of an asset with Range header and browser_download_url fallback."""
        asset_url = asset.get("url")
        browser_url = asset.get("browser_download_url")

        existing_size = dest_path.stat().st_size if dest_path.exists() else 0
        total_size = asset.get("size", 0)

        if existing_size > 0 and existing_size == total_size:
            if progress_hook:
                progress_hook(existing_size)
            return

        headers = self._get_headers()
        headers["Accept"] = "application/octet-stream"
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        # Prefer asset_url with octet-stream for private/authenticated repos
        download_target = asset_url or browser_url

        resp = None
        try:
            resp = requests.get(
                download_target, headers=headers, stream=True, timeout=30, allow_redirects=True
            )
            resp.raise_for_status()
        except requests.HTTPError as http_err:
            # Fallback to browser_download_url if asset_url returned 404 (e.g. rotated asset ID)
            if (resp is None or resp.status_code == 404) and browser_url and browser_url != download_target:
                logger.warning(
                    f"Asset URL {download_target} returned 404 for {label}. Falling back to browser_download_url: {browser_url}"
                )
                alt_headers = {}
                if self.config.github_token:
                    alt_headers["Authorization"] = f"Bearer {self.config.github_token.strip()}"
                if existing_size > 0:
                    alt_headers["Range"] = f"bytes={existing_size}-"
                resp = requests.get(
                    browser_url, headers=alt_headers, stream=True, timeout=30, allow_redirects=True
                )
                resp.raise_for_status()
            else:
                raise http_err

        is_partial = resp.status_code == 206
        mode = "ab" if (is_partial and existing_size > 0) else "wb"
        downloaded = existing_size if is_partial else 0

        with open(dest_path, mode) as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB buffer
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_hook:
                        progress_hook(downloaded)
                    elif total_size > 0:
                        pct = min(100, int((downloaded / total_size) * 100))
                        if downloaded % (5 * 1024 * 1024) == 0:  # Report every 5MB
                            status_cb(f"دانلود {label}: {pct}%")

    def _assemble_chunks(self, part_paths: List[Path], output_path: Path) -> None:
        """Concatenates parts in order into single zip archive."""
        sorted_parts = sorted(part_paths, key=lambda p: p.name)
        with open(output_path, "wb") as outfile:
            for part in sorted_parts:
                with open(part, "rb") as infile:
                    shutil.copyfileobj(infile, outfile, length=4 * 1024 * 1024)

    def _compute_sha256(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(4 * 1024 * 1024):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _cleanup_remote_release(self, release_id: Optional[int], tag: str) -> None:
        """Deletes remote release and tag silently to prevent storage bloat."""
        if not release_id:
            return
        owner, repo = self._parse_repo()

        def do_delete():
            try:
                # Delete release
                rel_url = f"{self.api_base}/repos/{owner}/{repo}/releases/{release_id}"
                requests.delete(rel_url, headers=self._get_headers(), timeout=10)
                # Delete git tag ref
                tag_url = f"{self.api_base}/repos/{owner}/{repo}/git/refs/tags/{tag}"
                requests.delete(tag_url, headers=self._get_headers(), timeout=10)
            except Exception as e:
                logger.debug(f"Remote cleanup non-fatal error: {e}")

        # Run non-blocking in background thread
        import threading

        threading.Thread(target=do_delete, daemon=True).start()

    def _parse_repo(self) -> Tuple[str, str]:
        parts = self.config.github_repo.strip().split("/")
        if len(parts) != 2:
            raise ValueError(
                f"نام مخزن گیت‌هاب نامعتبر است: '{self.config.github_repo}'. ساختار باید 'owner/repo' باشد."
            )
        return parts[0].strip(), parts[1].strip()
