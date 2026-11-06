"""Rebuild a browsable page from a Playwright HAR zip (recordHar, content=attach).

The runner ships every response the browser received in work/traffic.har.zip
(har.har + body files referenced by response.content._file). This module mirrors
those bodies under <extracted>/replay/<host>/<path> and rewrites the main document
so the existing local ui_server can render the page fully offline. <a href> stays
original so the shell interaction dispatch keeps working.
"""
import base64
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

_REPLAY_ROOT = "replay"
_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')


def _canon(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    return u.netloc.lower() + (u.path or "/")


def _mirror_rel(url):
    u = urlsplit(url)
    path = u.path or "/"
    if path.endswith("/"):
        path += "index.html"
    parts = []
    for seg in path.split("/"):
        if seg in ("", ".", ".."):
            continue
        parts.append(_INVALID.sub("_", seg)[:120])
    if not parts:
        parts = ["index.html"]
    return u.netloc.lower() + "/" + "/".join(parts)


class _Bundle:
    def __init__(self, zip_path):
        with zipfile.ZipFile(zip_path) as zf:
            har_names = [n for n in zf.namelist() if n.lower().endswith(".har")]
            if not har_names:
                raise ValueError("no .har member in archive")
            self.har = json.loads(zf.read(har_names[0]).decode("utf-8", "replace"))
            names = set(zf.namelist())
            self.entries = []
            for entry in self.har.get("log", {}).get("entries", []):
                try:
                    resp = entry.get("response") or {}
                    status = int(resp.get("status") or 0)
                    content = resp.get("content") or {}
                    body = None
                    file_ref = content.get("_file")
                    if file_ref and file_ref in names:
                        body = zf.read(file_ref)
                    elif content.get("text"):
                        body = (
                            base64.b64decode(content["text"])
                            if content.get("encoding") == "base64"
                            else content["text"].encode("utf-8", "replace")
                        )
                    if body is None:
                        continue
                    self.entries.append({
                        "url": (entry.get("request") or {}).get("url", ""),
                        "status": status,
                        "mime": (content.get("mimeType") or "").split(";")[0].strip().lower(),
                        "resource": entry.get("_resourceType", ""),
                        "body": body,
                    })
                except Exception:
                    continue


def _pick_document(bundle, final_url):
    docs = [
        e for e in bundle.entries
        if e["resource"] == "document" or e["mime"].startswith("text/html")
    ]
    if not docs:
        return None
    wanted_host = urlsplit(final_url).netloc.lower() if final_url else ""
    pool = [e for e in docs if urlsplit(e["url"]).netloc.lower() == wanted_host] if wanted_host else docs
    ok = [e for e in pool if 200 <= e["status"] < 300]
    return (ok or pool)[-1]


def rebuild_from_har(har_zip, extracted_dir, request_id, final_url=""):
    """Mirror captured responses and rewrite the main document for offline serving.

    Returns the absolute path of the rebuilt document, or None when the archive
    cannot be used (transport then falls back to the DOM snapshot).
    """
    bundle = _Bundle(har_zip)
    if not bundle.entries:
        return None

    replay_root = Path(extracted_dir) / _REPLAY_ROOT
    replay_root.mkdir(parents=True, exist_ok=True)

    url_map = {}
    for ent in bundle.entries:
        rel = _mirror_rel(ent["url"])
        target = replay_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ent["body"])
        canon = _canon(ent["url"])
        if canon:
            url_map[canon] = rel

    doc = _pick_document(bundle, final_url)
    if doc is None:
        return None

    doc_rel = _mirror_rel(doc["url"])
    doc_abs = replay_root / doc_rel
    doc_abs.parent.mkdir(parents=True, exist_ok=True)

    html = doc["body"].decode("utf-8", "replace")
    html = _rewrite_html(html, url_map, doc["url"], doc_rel)
    doc_abs.write_text(html, encoding="utf-8")
    return str(doc_abs)


def _rewrite_html(html, url_map, doc_url, doc_rel):
    html = re.sub(
        r"<meta[^>]+http-equiv\s*=\s*[\"']Content-Security-Policy[\"'][^>]*>",
        "", html, flags=re.I,
    )
    if "<meta charset" not in html[:2048].lower():
        html = re.sub(r"<head([^>]*)>", r'<head\1><meta charset="utf-8">', html, count=1, flags=re.I)

    def mapper(u):
        if not u or u.startswith(("#", "data:", "javascript:", "mailto:", "blob:", "about:")):
            return u
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("/"):
            host = urlsplit(doc_url).netloc.lower()
            u = f"https://{host}{u}"
        canon = _canon(u)
        rel = url_map.get(canon) if canon else None
        if rel is None and canon:
            rel = url_map.get(canon.split("?")[0])
        if rel is None:
            return u
        depth = doc_rel.count("/")
        return quote("../" * depth + rel, safe="/%:@&=+$,;~*!()'()-._")

    def attr_repl(m):
        attr, q, val = m.group(1), m.group(2), m.group(3)
        return f"{attr}={q}{mapper(val)}{q}"

    html = re.sub(r"\b(src|poster|data-src)\s*=\s*([\"'])([^\"']+)\2", attr_repl, html, flags=re.I)

    # <a href> must stay original (interaction dispatch sends the real URL);
    # only <link> resource references (stylesheets, icons, preloads) get mapped.
    def link_repl(m):
        return re.sub(
            r"\bhref\s*=\s*([\"'])([^\"']+)\1",
            lambda mm: f"href={mm.group(1)}{mapper(mm.group(2))}{mm.group(1)}",
            m.group(0), flags=re.I,
        )

    html = re.sub(r"<link\b[^>]*>", link_repl, html, flags=re.I)

    def css_repl(m):
        return f"url({m.group(1)}{mapper(m.group(2))}{m.group(1)})"

    html = re.sub(r"url\(\s*([\"']?)(https?://[^)\"']+|/[^)\"']+)\1\s*\)", css_repl, html, flags=re.I)
    return html
