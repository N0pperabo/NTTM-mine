"""Rebuild a browsable page from a Playwright HAR zip (recordHar, content=attach).

The runner ships every response the browser received in work/traffic.har.zip
(har.har + body files referenced by response.content._file). This module mirrors
those bodies under <extracted>/replay/<host>/<path>, rewrites the main document
and every captured stylesheet to relative paths, and injects a shim that maps
runtime fetch/XHR/element URLs onto the mirror so SPAs render offline.
<a href> stays original so the shell interaction dispatch keeps working.
"""
import base64
import hashlib
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

_REPLAY_ROOT = "replay"
_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')
_MAX_MANIFEST_KEYS = 4000

_SHIM_TEMPLATE = """(function(){
  function __nttmBatch(){
    try { return !!(window.parent && window.parent.shellApp && window.parent.shellApp.isBatchMode); } catch(e) { return false; }
  }
  var __nttmBlocked = { click:1, auxclick:1, dblclick:1, mousedown:1, mouseup:1, pointerdown:1, pointerup:1, touchstart:1, touchend:1, contextmenu:1, keydown:1, keyup:1, keypress:1, submit:1, input:1, change:1 };
  var __nttmNoDefault = { click:1, auxclick:1, dblclick:1, contextmenu:1, submit:1 };
  var __nttmAdd = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(type, fn, opts){
    try {
      if (typeof fn === 'function' && __nttmBlocked[String(type)] && String(fn).indexOf('__nttmQueue') === -1) {
        var __fn = fn;
        var wrapped = function(e){
          if (__nttmBatch()) {
            if (__nttmNoDefault[String(e.type)] && e.cancelable !== false) e.preventDefault();
            return undefined;
          }
          return __fn.apply(this, arguments);
        };
        fn.__nttmWrapped = wrapped;
        return __nttmAdd.call(this, type, wrapped, opts);
      }
    } catch(e) {}
    return __nttmAdd.call(this, type, fn, opts);
  };
  var __nttmRemove = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.removeEventListener = function(type, fn, opts){
    try { if (fn && fn.__nttmWrapped) return __nttmRemove.call(this, type, fn.__nttmWrapped, opts); } catch(e) {}
    return __nttmRemove.call(this, type, fn, opts);
  };
  var BASE = __BASE__;
  var DOC_HOST = __DOCHOST__;
  var M = __KEYS__;
  function map(u){
    try{
      if(u==null) return u;
      if(typeof u==='object'){ if(u instanceof Request) u=u.url; else u=String(u); }
      u=String(u);
      if(!u || u.charAt(0)==='#' || u.indexOf('data:')===0 || u.indexOf('blob:')===0 || u.indexOf('javascript:')===0 || u.indexOf('about:')===0) return u;
      if(u.indexOf('../')===0 || u.indexOf('./')===0) return u;
      var abs=null;
      if(u.indexOf('//')===0) abs=new URL('https:'+u);
      else if(u.indexOf('http://')===0||u.indexOf('https://')===0) abs=new URL(u);
      else if(u.charAt(0)==='/') abs=new URL('https://'+DOC_HOST+u);
      else return u;
      var h=abs.hostname;
      try{ if(location.hostname && h===location.hostname) h=DOC_HOST; }catch(e){}
      var rel=M[h+abs.pathname];
      if(rel===undefined) return u;
      return BASE+rel;
    }catch(e){ return u; }
  }
  var _f=window.fetch;
  if(_f){ window.fetch=function(){
    try{
      var a=arguments;
      if(typeof a[0]==='string') a=[map(a[0])].concat([].slice.call(a,1));
      else if(a[0]&&a[0].url) a=[new Request(map(a[0].url),a[0])].concat([].slice.call(a,1));
      return _f.apply(this,a);
    }catch(e){ return _f.apply(this,arguments); }
  }; }
  var _o=XMLHttpRequest && XMLHttpRequest.prototype.open;
  if(_o){ XMLHttpRequest.prototype.open=function(){
    try{ var a=[].slice.call(arguments); if(a.length>1) a[1]=map(a[1]); return _o.apply(this,a); }
    catch(e){ return _o.apply(this,arguments); }
  }; }
  ['HTMLScriptElement','HTMLImageElement','HTMLLinkElement','HTMLMediaElement','HTMLSourceElement','HTMLIFrameElement','HTMLTrackElement','HTMLEmbedElement'].forEach(function(t){
    var P=window[t]&&window[t].prototype; if(!P) return;
    ['src','href','poster','srcset'].forEach(function(a){
      var d=Object.getOwnPropertyDescriptor(P,a);
      if(d&&d.set){ try{ Object.defineProperty(P,a,{configurable:true,get:d.get,set:function(v){ try{ var m=map(v); if(m!==v) v=m; }catch(e){} d.set.call(this,v); }}); }catch(e){} }
    });
  });
  var _sa=Element.prototype.setAttribute;
  Element.prototype.setAttribute=function(n,v){
    try{
      var tag=(this.tagName||'').toLowerCase();
      n=String(n).toLowerCase();
      if(tag!=='a'&&tag!=='area'&&(n==='src'||n==='poster'||n==='data-src'||n==='srcset'||(n==='href'&&tag==='link'))){ var m=map(v); if(m!==v) v=m; }
    }catch(e){}
    return _sa.call(this,n,v);
  };
  try{
    function fix(el){
      if(!el||!el.getAttribute) return;
      var tag=(el.tagName||'').toLowerCase(); if(tag==='a'||tag==='area') return;
      ['src','poster','data-src','srcset','href'].forEach(function(a){
        if(a==='href'&&tag!=='link') return;
        var v=el.getAttribute(a); if(!v) return;
        var m=map(v); if(m!==v) el.setAttribute(a,m);
      });
    }
    var mo=new MutationObserver(function(ms){
      ms.forEach(function(mu){
        if(mu.type==='attributes'){ fix(mu.target); }
        else{ for(var i=0;i<mu.addedNodes.length;i++){ var n=mu.addedNodes[i]; if(n.nodeType===1){ fix(n); if(n.querySelectorAll) n.querySelectorAll('img,source,script,link,audio,video').forEach(fix); } } }
      });
    });
    if(document.documentElement) mo.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['src','href','srcset','poster','data-src']});
  }catch(e){}
})();"""


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


def _ext_fix(rel, mime):
    # Browsers refuse stylesheets/scripts served without a recognizable extension+MIME.
    low = rel.lower()
    if mime == "text/css" and not low.endswith(".css"):
        return rel + ".css"
    if "javascript" in mime and not low.endswith((".js", ".mjs")):
        return rel + ".js"
    return rel


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
    docs = [e for e in bundle.entries if e["resource"] == "document"]
    if not docs:
        docs = [e for e in bundle.entries if e["mime"].startswith("text/html")]
    if not docs:
        return None
    fu = urlsplit(final_url) if final_url else None

    def score(e):
        u = urlsplit(e["url"])
        s = 0
        if fu:
            if u.netloc.lower() == (fu.netloc or "").lower():
                s += 8
            p_doc = (u.path or "/").rstrip("/")
            p_fin = (fu.path or "/").rstrip("/")
            if p_doc == p_fin:
                s += 24
            elif p_fin and p_doc.startswith(p_fin + "/"):
                s += 8
        if 200 <= e["status"] < 300:
            s += 4
        if e["mime"].startswith("text/html"):
            s += 2
        s += min(len(e["body"]) // 4096, 24)
        return s

    return max(docs, key=score)


def rebuild_from_har(har_zip, extracted_dir, request_id, final_url="", replay_url_base=None):
    """Mirror captured responses, rewrite documents/stylesheets and the runtime
    shim, and return the absolute path of the rebuilt page (None on failure).
    replay_url_base: absolute URL prefix of the mirror on the local server
    (e.g. /cache/<id>/extracted/replay/) so the shim survives SPA history rewrites."""
    bundle = _Bundle(har_zip)
    if not bundle.entries:
        return None

    replay_root = Path(extracted_dir) / _REPLAY_ROOT
    replay_root.mkdir(parents=True, exist_ok=True)

    url_map = {}
    rels = []
    for ent in bundle.entries:
        rel = _ext_fix(_mirror_rel(ent["url"]), ent["mime"])
        rels.append(rel)
        canon = _canon(ent["url"])
        if canon:
            url_map[canon] = rel

    css_entries = []
    for ent, rel in zip(bundle.entries, rels):
        if ent["mime"] == "text/css" or ent["resource"] == "stylesheet":
            css_entries.append((ent, rel))
            continue
        target = replay_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(ent["body"])
        except Exception:
            url_map.pop(_canon(ent["url"]), None)

    for ent, rel in css_entries:
        target = replay_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            css_text = ent["body"].decode("utf-8", "replace")
            target.write_text(_rewrite_css(css_text, url_map, ent["url"], rel), encoding="utf-8")
        except Exception:
            url_map.pop(_canon(ent["url"]), None)

    doc = _pick_document(bundle, final_url)
    if doc is None:
        return None

    doc_rel = _mirror_rel(doc["url"])
    doc_abs = replay_root / doc_rel
    doc_abs.parent.mkdir(parents=True, exist_ok=True)

    html = doc["body"].decode("utf-8", "replace")
    html = _rewrite_html(html, url_map, doc["url"], doc_rel)
    html = _inject_shim(html, doc_rel, doc["url"], url_map, replay_url_base)
    doc_abs.write_text(html, encoding="utf-8")
    return str(doc_abs)


def _rewrite_css(css, url_map, entry_url, css_rel):
    depth = css_rel.count("/")

    def mapper(u):
        u = u.strip()
        if not u or u.startswith(("#", "data:", "about:")):
            return u
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http://") or u.startswith("https://"):
            pass
        elif u.startswith("/"):
            u = f"https://{urlsplit(entry_url).netloc.lower()}{u}"
        else:
            return u
        canon = _canon(u)
        rel = url_map.get(canon) if canon else None
        if rel is None and canon:
            rel = url_map.get(canon.split("?")[0])
        if rel is None:
            return u
        return quote("../" * depth + rel, safe="/%:@&=+$,;~*!()'()-._")

    def url_repl(m):
        return f"url({m.group(1)}{mapper(m.group(2))}{m.group(1)})"

    css = re.sub(r"url\(\s*([\"']?)(?!data:)([^)\"']+)\1\s*\)", url_repl, css, flags=re.I)

    def imp_repl(m):
        return f"@import {m.group(1)}{mapper(m.group(2))}{m.group(1)}"

    css = re.sub(r"@import\s+([\"'])([^\"']+)\1", imp_repl, css, flags=re.I)
    return css


def _rewrite_html(html, url_map, doc_url, doc_rel):
    html = re.sub(
        r"<meta[^>]+http-equiv\s*=\s*[\"']Content-Security-Policy[\"'][^>]*>",
        "", html, flags=re.I,
    )
    html = re.sub(r"<base\b[^>]*>", "", html, flags=re.I)
    depth = doc_rel.count("/")

    def mapper(u):
        if not u or u.startswith(("#", "data:", "javascript:", "mailto:", "blob:", "about:")):
            return u
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http://") or u.startswith("https://"):
            pass
        elif u.startswith("/"):
            u = f"https://{urlsplit(doc_url).netloc.lower()}{u}"
        else:
            return u
        canon = _canon(u)
        rel = url_map.get(canon) if canon else None
        if rel is None and canon:
            rel = url_map.get(canon.split("?")[0])
        if rel is None:
            return u
        return quote("../" * depth + rel, safe="/%:@&=+$,;~*!()'()-._")

    def attr_repl(m):
        attr, q, val = m.group(1), m.group(2), m.group(3)
        return f"{attr}={q}{mapper(val)}{q}"

    html = re.sub(r"\b(src|poster|data-src)\s*=\s*([\"'])([^\"']+)\2", attr_repl, html, flags=re.I)

    def srcset_repl(m):
        attr, q, val = m.group(1), m.group(2), m.group(3)
        parts = []
        for cand in val.split(","):
            bits = cand.strip().split()
            if bits:
                bits[0] = mapper(bits[0])
            parts.append(" ".join(bits))
        return f"{attr}={q}{', '.join(parts)}{q}"

    html = re.sub(r"\b(srcset)\s*=\s*([\"'])([^\"']+)\2", srcset_repl, html, flags=re.I)

    def link_repl(m):
        return re.sub(
            r"\bhref\s*=\s*([\"'])([^\"']+)\1",
            lambda mm: f"href={mm.group(1)}{mapper(mm.group(2))}{mm.group(1)}",
            m.group(0), flags=re.I,
        )

    html = re.sub(r"<link\b[^>]*>", link_repl, html, flags=re.I)

    def css_repl(m):
        return f"url({m.group(1)}{mapper(m.group(2))}{m.group(1)})"

    html = re.sub(r"url\(\s*([\"']?)(?!data:)(https?://[^)\"']+|/[^)\"']+)\1\s*\)", css_repl, html, flags=re.I)
    return html


def _inject_shim(html, doc_rel, doc_url, url_map, replay_url_base=None):
    keys = sorted(url_map.keys())[:_MAX_MANIFEST_KEYS]
    manifest = {k: url_map[k] for k in keys}
    base = replay_url_base if replay_url_base else ("../" * doc_rel.count("/"))
    shim = (
        _SHIM_TEMPLATE
        .replace("__BASE__", json.dumps(base))
        .replace("__DOCHOST__", json.dumps(urlsplit(doc_url).netloc.lower()))
        .replace("__KEYS__", json.dumps(manifest))
    )
    tag = "<script>" + shim + "</script>"
    if re.search(r"<head[^>]*>", html, flags=re.I):
        return re.sub(r"<head([^>]*)>", lambda m: f"<head{m.group(1)}>{tag}", html, count=1, flags=re.I)
    return tag + html
