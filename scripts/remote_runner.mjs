// Auto-scroll page to trigger lazy-loaded images (e.g. comic covers)
async function autoScrollPage(page) {
  try {
    await page.evaluate(async () => {
      await new Promise((resolve) => {
        let totalHeight = 0;
        const distance = 400;
        const timer = setInterval(() => {
          const scrollHeight = document.body.scrollHeight || 3000;
          window.scrollBy(0, distance);
          totalHeight += distance;
          if (totalHeight >= scrollHeight || totalHeight >= 4500) {
            clearInterval(timer);
            window.scrollTo(0, 0);
            resolve();
          }
        }, 100);
      });
    });
    await page.waitForLoadState('networkidle', { timeout: 3000 }).catch(() => {});
  } catch (e) {}
}

import fs from 'node:fs';
import path from 'node:path';
import { decryptBytes, isEncrypted } from './crypto_utils.mjs';

async function main() {
  const requestId = process.argv[2] || process.env.REQUEST_ID;
  if (!requestId) {
    console.error('Error: REQUEST_ID not provided.');
    process.exit(1);
  }

  const cwd = process.cwd();
  const requestFilePath = path.join(cwd, 'requests', `${requestId}.json`);
  const workDir = path.join(cwd, 'work');
  const downloadDir = path.join(workDir, 'download');

  fs.mkdirSync(workDir, { recursive: true });
  fs.mkdirSync(downloadDir, { recursive: true });

  if (!fs.existsSync(requestFilePath)) {
    console.error(`Request envelope not found at: ${requestFilePath}`);
    writeErrorArtifacts(workDir, requestId, `Request envelope not found: ${requestFilePath}`);
    return;
  }

  let requestData;
  const encryptionKey = (process.env.ENCRYPTION_KEY || '').trim();
  try {
    const fileBuf = fs.readFileSync(requestFilePath);
    let jsonText;
    if (isEncrypted(fileBuf)) {
      console.log('[Runner] 🔒 Request envelope is encrypted with AES-256-GCM. Decrypting...');
      const decBuf = decryptBytes(fileBuf, encryptionKey);
      jsonText = decBuf.toString('utf8');
    } else {
      jsonText = fileBuf.toString('utf8');
    }
    requestData = JSON.parse(jsonText);
  } catch (err) {
    console.error('[Runner] Error parsing or decrypting request envelope:', err.message);
    writeErrorArtifacts(workDir, requestId, `Invalid or encrypted request: ${err.message}`);
    return;
  }

  const {
    target_url,
    mode = 'navigate',
    http_method = 'GET',
    content_type = '',
    body_base64 = '',
    custom_headers = {},
    viewport = { width: 1366, height: 768 },
    actions = [],
    intercepted_ajax = null,
    storage_state = null,
    session_storage = null,
    custom_delay_seconds = 0
  } = requestData;

  console.log(`[Runner] Processing request ${requestId} in mode: ${mode}`);

  let browser;
  let downloadedFile = null;
  let actionsExecuted = 0;
  let finalUrl = target_url;
  let pageTitle = '';
  let httpStatus = 200;

  try {
    let chromium;
    try {
      const pw = await import('playwright');
      chromium = pw.chromium;
    } catch (e) {
      throw new Error(`Playwright dependency not found. Please run 'npm install playwright': ${e.message}`);
    }

    browser = await chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-web-security']
    });

    const contextOptions = {
      viewport: {
        width: viewport.width || 1366,
        height: viewport.height || 768
      },
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      acceptDownloads: true
    };

    // Hydrate storage state (cookies + localStorage)
    if (storage_state && (storage_state.cookies?.length || storage_state.origins?.length)) {
      contextOptions.storageState = sanitizeStorageState(storage_state);
    }

    const context = await browser.newContext(contextOptions);

    // Hydrate sessionStorage via init script
    if (session_storage && typeof session_storage === 'object' && Object.keys(session_storage).length > 0) {
      await context.addInitScript((storageMap) => {
        try {
          const currentOrigin = window.location.origin;
          for (const [origin, items] of Object.entries(storageMap)) {
            if (origin === currentOrigin || currentOrigin.includes(origin) || origin.includes(currentOrigin)) {
              for (const [k, v] of Object.entries(items)) {
                window.sessionStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
              }
            }
          }
        } catch (e) {
          console.warn('SessionStorage injection warning:', e);
        }
      }, session_storage);
    }

    // Set custom headers
    if (custom_headers && Object.keys(custom_headers).length > 0) {
      await context.setExtraHTTPHeaders(custom_headers);
    }

    const page = await context.newPage();

    // Cache all loaded image buffers and CSS stylesheets to embed into offline HTML
    const imageMap = new Map();
    const cssMap = new Map();
    page.on('response', async (res) => {
      try {
        const url = res.url();
        const ct = res.headers()['content-type'] || '';
        if (ct.startsWith('image/')) {
          const buffer = await res.body();
          if (buffer && buffer.length > 0 && buffer.length <= 25 * 1024 * 1024) {
            imageMap.set(url, `data:${ct};base64,${buffer.toString('base64')}`);
          }
        } else if (ct.includes('text/css') || url.endsWith('.css') || url.includes('.css?')) {
          const text = await res.text();
          if (text) {
            cssMap.set(url, text);
          }
        }
      } catch (e) {}
    });

    // Listen for file downloads
    page.on('download', async (download) => {
      try {
        const filename = download.suggestedFilename() || `download_${Date.now()}`;
        const savePath = path.join(downloadDir, filename);
        await download.saveAs(savePath);
        const stats = fs.statSync(savePath);
        downloadedFile = {
          filename,
          path: savePath,
          size: stats.size,
          mime_type: 'application/octet-stream'
        };
        console.log(`[Runner] Download captured: ${filename} (${stats.size} bytes)`);
      } catch (err) {
        console.error('[Runner] Download save error:', err);
      }
    });

    // Check if initial request needs POST routing with body
    if (http_method === 'POST' || (body_base64 && body_base64.length > 0)) {
      await page.route(target_url, async (route) => {
        const req = route.request();
        if (req.isNavigationRequest()) {
          const postData = body_base64 ? Buffer.from(body_base64, 'base64') : Buffer.from('');
          const headers = { ...req.headers(), ...custom_headers };
          if (content_type) {
            headers['content-type'] = content_type;
          }
          await route.continue({
            method: http_method || 'POST',
            postData,
            headers
          });
        } else {
          await route.continue();
        }
      });
    }

    // Execute based on mode
    if (mode === 'replay') {
      console.log(`[Runner] Executing replay mode on: ${target_url}`);
      const mainResponse = await page.goto(target_url, {
        waitUntil: 'domcontentloaded',
        timeout: 45000
      }).catch((e) => {
        console.warn('[Runner] Navigation warning:', e.message);
        return null;
      });

      if (mainResponse) {
        httpStatus = mainResponse.status();
      }

      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});

      // Replay actions sequentially
      for (let i = 0; i < actions.length; i++) {
        const action = actions[i];
        console.log(`[Runner] Action ${i + 1}/${actions.length}: ${action.type}`);
        try {
          const el = await resolveElement(page, action);

          switch (action.type) {
            case 'fill':
              if (el) {
                await el.fill(action.value || '');
              } else if (action.x !== undefined && action.y !== undefined) {
                await page.mouse.click(action.x, action.y);
                await page.keyboard.type(action.value || '');
              }
              break;

            case 'click':
              if (el) {
                await el.click({ timeout: 5000 });
              } else if (action.x !== undefined && action.y !== undefined) {
                await page.mouse.click(action.x, action.y);
              }
              await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
              break;

            case 'select':
              if (el) {
                await el.selectOption(action.value || '');
              }
              break;

            case 'check':
              if (el) {
                await el.setChecked(!!action.checked);
              }
              break;

            case 'press':
              if (el) {
                await el.press(action.key || 'Enter');
              } else {
                await page.keyboard.press(action.key || 'Enter');
              }
              await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
              break;

            case 'wait':
              await page.waitForTimeout(action.wait_after_ms || 1000);
              break;

            case 'scroll':
              await page.mouse.wheel(0, action.y || 300);
              break;

            default:
              console.warn(`[Runner] Unknown action type: ${action.type}`);
          }

          if (action.wait_after_ms) {
            await page.waitForTimeout(action.wait_after_ms);
          }
          actionsExecuted++;
        } catch (actErr) {
          console.warn(`[Runner] Action ${i + 1} (${action.type}) failed:`, actErr.message);
        }
      }

      // If action replay failed or was empty and AJAX fallback is available
      if (actionsExecuted === 0 && intercepted_ajax?.captured) {
        console.log('[Runner] Attempting AJAX fallback inside page context...');
        await executeAjaxFallback(page, intercepted_ajax);
      }
    } else if (mode === 'ajax_fallback' && intercepted_ajax?.captured) {
      console.log(`[Runner] Navigating to page context for AJAX fallback: ${target_url}`);
      await page.goto(target_url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      await executeAjaxFallback(page, intercepted_ajax);
    } else {
      // mode: navigate, form, download
      console.log(`[Runner] Navigating directly to: ${target_url}`);
      const mainResponse = await page.goto(target_url, {
        waitUntil: 'domcontentloaded',
        timeout: 45000
      }).catch((e) => {
        console.warn('[Runner] Direct navigation error:', e.message);
        return null;
      });

      if (mainResponse) {
        httpStatus = mainResponse.status();
        const headers = mainResponse.headers();
        const contentTypeHeader = headers['content-type'] || '';
        const disposition = headers['content-disposition'] || '';

        // Check if response is an attachment or binary file
        const isAttachment = disposition.toLowerCase().includes('attachment');
        const isBinary = isBinaryMime(contentTypeHeader);

        if (isAttachment || isBinary) {
          console.log('[Runner] Detected binary/attachment response directly from response headers.');
          const buffer = await mainResponse.body();
          let filename = 'downloaded_file';
          const match = disposition.match(/filename\*?=['"]?(?:UTF-8'')?([^'";\n]+)['"]?/i);
          if (match && match[1]) {
            filename = decodeURIComponent(match[1]);
          } else {
            const parsedUrl = new URL(target_url);
            const pathBase = path.basename(parsedUrl.pathname);
            if (pathBase && pathBase.includes('.')) {
              filename = pathBase;
            }
          }
          const filePath = path.join(downloadDir, filename);
          fs.writeFileSync(filePath, buffer);
          downloadedFile = {
            filename,
            path: filePath,
            size: buffer.length,
            mime_type: contentTypeHeader || 'application/octet-stream'
          };
        }
      }

      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    }

    // Auto-scroll to trigger lazy loading of images (comic covers, thumbnails, etc.)
    await autoScrollPage(page);

    // Apply custom delay if requested (e.g. for captchas, Cloudflare turnstile, dynamic rendering)
    const delaySec = parseInt(custom_delay_seconds || 0, 10);
    if (delaySec > 0) {
      console.log(`[Runner] ⏱️ Waiting custom delay of ${delaySec} seconds before capture...`);
      await page.waitForTimeout(delaySec * 1000);
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
      await autoScrollPage(page);
    }

    // Wait a brief moment for any pending download event to register
    await page.waitForTimeout(1000);

    finalUrl = page.url() || target_url;
    pageTitle = await page.title().catch(() => '');

    // Export storage state (cookies + localStorage)
    const currentStorageState = await context.storageState().catch(() => ({ cookies: [], origins: [] }));
    fs.writeFileSync(
      path.join(workDir, 'storage_state.json'),
      JSON.stringify(currentStorageState, null, 2),
      'utf8'
    );

    // Save webpage if not a file download
    if (!downloadedFile) {
      console.log('[Runner] Serializing rendered DOM snapshot...');
      const serializedHtml = await serializeRenderedPage(page, finalUrl, imageMap, cssMap);
      const indexPath = path.join(workDir, 'index.html');
      fs.writeFileSync(indexPath, serializedHtml, 'utf8');

      const fileSize = fs.statSync(indexPath).size;
      const meta = {
        request_id: requestId,
        status: 'success',
        response_type: 'page',
        requested_url: target_url,
        final_url: finalUrl,
        http_status: httpStatus,
        title: pageTitle || 'Offline Webpage',
        actions_executed: actionsExecuted,
        download_filename: '',
        file_size: fileSize,
        mime_type: 'text/html',
        error_message: ''
      };
      fs.writeFileSync(path.join(workDir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
      console.log(`[Runner] Page captured successfully. Size: ${fileSize} bytes.`);
    } else {
      console.log(`[Runner] File download recorded: ${downloadedFile.filename}`);
      const meta = {
        request_id: requestId,
        status: 'success',
        response_type: 'file',
        requested_url: target_url,
        final_url: finalUrl,
        http_status: httpStatus,
        title: downloadedFile.filename,
        actions_executed: actionsExecuted,
        download_filename: downloadedFile.filename,
        file_size: downloadedFile.size,
        mime_type: downloadedFile.mime_type || 'application/octet-stream',
        error_message: ''
      };
      fs.writeFileSync(path.join(workDir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
    }
  } catch (err) {
    console.error('[Runner] Unhandled execution error:', err);
    writeErrorArtifacts(workDir, requestId, err.message, target_url);
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

// Fallback chain for element resolution
async function resolveElement(page, action) {
  // 1. Primary CSS selector
  if (action.selector) {
    try {
      const loc = page.locator(action.selector).first();
      if ((await loc.count()) > 0) return loc;
    } catch {}
  }

  // 2. XPath
  if (action.xpath) {
    try {
      const loc = page.locator('xpath=' + action.xpath).first();
      if ((await loc.count()) > 0) return loc;
    } catch {}
  }

  // 3. Text hint
  if (action.text_hint) {
    try {
      const loc = page.getByText(action.text_hint, { exact: false }).first();
      if ((await loc.count()) > 0) return loc;
    } catch {}
  }

  return null;
}

// AJAX fallback inside page context
async function executeAjaxFallback(page, ajax) {
  try {
    await page.evaluate(async (req) => {
      let bodyData = undefined;
      if (req.body_base64 && req.method !== 'GET' && req.method !== 'HEAD') {
        try {
          bodyData = atob(req.body_base64);
        } catch {
          bodyData = req.body_base64;
        }
      }
      try {
        const response = await window.fetch(req.url, {
          method: req.method || 'POST',
          headers: req.headers || {},
          body: bodyData
        });
        return { ok: response.ok, status: response.status };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    }, ajax);
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  } catch (err) {
    console.warn('[Runner] AJAX fallback error:', err.message);
  }
}

// DOM Serializer strictly preserving inputs, buttons, selects, forms and data-* attributes
async function serializeRenderedPage(page, baseUrl, imageMap = new Map(), cssMap = new Map()) {
  const imagesList = imageMap ? Array.from(imageMap.entries()) : [];
  const cssList = cssMap ? Array.from(cssMap.entries()) : [];
  return await page.evaluate(async ({ base, images, cssItems }) => {
    try {
      const imgLookup = new Map(images || []);
      const cssLookup = new Map(cssItems || []);

      // 1. Inline all external stylesheets (<link rel="stylesheet">) into <style> tags
      const linkElements = Array.from(document.querySelectorAll('link[rel="stylesheet"], link[as="style"]'));
      for (const link of linkElements) {
        try {
          const href = link.href;
          if (!href) continue;
          let css = cssLookup.get(href) || '';
          if (!css) {
            for (const sheet of document.styleSheets) {
              try {
                if (sheet.href === href) {
                  const rules = Array.from(sheet.cssRules || []);
                  css = rules.map((r) => r.cssText).join('\n');
                  break;
                }
              } catch (e) {}
            }
          }
          if (!css) {
            try {
              const res = await fetch(href);
              if (res.ok) css = await res.text();
            } catch (e) {}
          }
          if (css) {
            const style = document.createElement('style');
            style.setAttribute('data-inlined-from', href);
            style.textContent = css;
            link.replaceWith(style);
          }
        } catch (e) {}
      }

      // Unpack lazy loading data attributes (e.g. on comic/manga/media websites)
      document.querySelectorAll('img, picture source').forEach((el) => {
        const lazySrc = el.getAttribute('data-src') || el.getAttribute('data-original') || el.getAttribute('data-lazy-src') || el.getAttribute('srcset');
        if (lazySrc && (!el.getAttribute('src') || el.getAttribute('src').startsWith('data:image/svg') || el.getAttribute('src').includes('blank'))) {
          el.setAttribute('src', lazySrc.split(' ')[0]);
        }
      });

      // Inline images with captured Base64 data URLs
      document.querySelectorAll('img').forEach((img) => {
        const src = img.src;
        if (imgLookup.has(src)) {
          img.src = imgLookup.get(src);
        } else {
          // If not in map, try canvas conversion if complete
          try {
            if (img.complete && img.naturalWidth > 0 && !img.src.startsWith('data:')) {
              const canvas = document.createElement('canvas');
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
              const ctx = canvas.getContext('2d');
              ctx.drawImage(img, 0, 0);
              img.src = canvas.toDataURL('image/jpeg', 0.85);
            }
          } catch (e) {}
        }
      });

      // Inline background images in CSS styles
      document.querySelectorAll('*[style*="background"]').forEach((el) => {
        const style = el.getAttribute('style') || '';
        const match = style.match(/url\(['"]?(https?:\/\/[^'"]+)['"]?\)/i);
        if (match && imgLookup.has(match[1])) {
          el.style.backgroundImage = `url("${imgLookup.get(match[1])}")`;
        }
      });

      // 1. Preserve input, textarea, and select live values into attributes
      document.querySelectorAll('input').forEach((input) => {
        if (input.type === 'checkbox' || input.type === 'radio') {
          if (input.checked) {
            input.setAttribute('checked', 'checked');
          } else {
            input.removeAttribute('checked');
          }
        } else {
          input.setAttribute('value', input.value || '');
        }
      });

      document.querySelectorAll('textarea').forEach((textarea) => {
        textarea.textContent = textarea.value || '';
      });

      document.querySelectorAll('select').forEach((select) => {
        Array.from(select.options).forEach((opt) => {
          if (opt.selected) {
            opt.setAttribute('selected', 'selected');
          } else {
            opt.removeAttribute('selected');
          }
        });
      });

      // Convert all link hrefs to absolute URLs and force target to _self
      document.querySelectorAll('a').forEach((a) => {
        try {
          if (a.href) a.setAttribute('href', a.href);
          a.setAttribute('target', '_self');
        } catch (e) {}
      });

      // Convert all form actions to absolute URLs
      document.querySelectorAll('form').forEach((f) => {
        try {
          if (f.action) f.setAttribute('action', f.action);
        } catch (e) {}
      });

      // Neutralize scripts so client WebView2 NEVER executes external tracking or live AJAX
      document.querySelectorAll('script').forEach((s) => {
        s.setAttribute('type', 'text/plain');
        s.removeAttribute('src');
      });

      // Remove base tag to completely isolate client WebView2 from external domains
      document.querySelectorAll('base').forEach((b) => b.remove());

      // 3. Serialize live full HTML
      const docType = document.doctype
        ? `<!DOCTYPE ${document.doctype.name}${document.doctype.publicId ? ` PUBLIC "${document.doctype.publicId}"` : ''}${!document.doctype.publicId && document.doctype.systemId ? ' SYSTEM' : ''}${document.doctype.systemId ? ` "${document.doctype.systemId}"` : ''}>`
        : '<!DOCTYPE html>';

      return `${docType}\n${document.documentElement.outerHTML}`;
    } catch (e) {
      return document.documentElement.outerHTML;
    }
  }, { base: baseUrl, images: imagesList, cssItems: cssList });
}

// Check for common binary/download MIME types
function isBinaryMime(mime) {
  if (!mime) return false;
  const m = mime.toLowerCase();
  return (
    m.includes('application/zip') ||
    m.includes('application/octet-stream') ||
    m.includes('application/x-zip') ||
    m.includes('application/pdf') ||
    m.includes('application/x-tar') ||
    m.includes('application/gzip') ||
    m.includes('audio/') ||
    m.includes('video/') ||
    m.includes('image/')
  );
}

// Validate and sanitize storageState for Playwright
function sanitizeStorageState(raw) {
  const sanitized = { cookies: [], origins: [] };
  if (Array.isArray(raw.cookies)) {
    sanitized.cookies = raw.cookies.filter(
      (c) => c && typeof c.name === 'string' && typeof c.value === 'string' && typeof c.domain === 'string'
    );
  }
  if (Array.isArray(raw.origins)) {
    sanitized.origins = raw.origins.filter(
      (o) => o && typeof o.origin === 'string' && Array.isArray(o.localStorage)
    );
  }
  return sanitized;
}

// Error recovery artifacts so GitHub Action pipeline NEVER fails
function writeErrorArtifacts(workDir, requestId, errorMessage, requestedUrl = '') {
  fs.mkdirSync(workDir, { recursive: true });
  const errorHtml = `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>خطا در دریافت صفحه</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; margin: 0; }
    .card { background: #1e293b; border-radius: 12px; padding: 30px; max-width: 700px; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #ef4444; }
    h1 { color: #ef4444; margin-top: 0; font-size: 24px; }
    p { line-height: 1.6; color: #cbd5e1; }
    pre { background: #0b1120; padding: 15px; border-radius: 8px; color: #fca5a5; overflow-x: auto; direction: ltr; text-align: left; }
    .meta { font-size: 13px; color: #94a3b8; border-top: 1px solid #334155; padding-top: 15px; margin-top: 20px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>خطا در پردازش درخواست در رانر گیت‌هاب</h1>
    <p>متاسفانه رانر Playwright در پردازش این آدرس یا اجرای اکشن‌ها با خطا مواجه شد:</p>
    <pre>${escapeHtml(errorMessage)}</pre>
    <div class="meta">
      <strong>شناسه درخواست:</strong> ${escapeHtml(requestId)}<br>
      <strong>آدرس درخواستی:</strong> <span dir="ltr">${escapeHtml(requestedUrl)}</span>
    </div>
  </div>
</body>
</html>`;

  fs.writeFileSync(path.join(workDir, 'index.html'), errorHtml, 'utf8');
  fs.writeFileSync(path.join(workDir, 'storage_state.json'), JSON.stringify({ cookies: [], origins: [] }, null, 2), 'utf8');

  const meta = {
    request_id: requestId,
    status: 'error',
    response_type: 'page',
    requested_url: requestedUrl,
    final_url: requestedUrl,
    http_status: 500,
    title: 'Error Processing Request',
    actions_executed: 0,
    download_filename: '',
    file_size: Buffer.byteLength(errorHtml),
    mime_type: 'text/html',
    error_message: errorMessage
  };
  fs.writeFileSync(path.join(workDir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

main().catch((err) => {
  console.error('[Fatal Error]', err);
  process.exit(1);
});
