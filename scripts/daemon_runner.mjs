import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execSync } from 'node:child_process';

const GITHUB_TOKEN = process.env.GITHUB_TOKEN || '';
const GITHUB_REPOSITORY = process.env.GITHUB_REPOSITORY || '';
const IDLE_TIMEOUT_MIN = parseFloat(process.env.IDLE_TIMEOUT_MINUTES || '30');
const MAX_RUN_HOURS = parseFloat(process.env.MAX_HOURS || '5.5');
const AUTO_CHAIN = (process.env.AUTO_CHAIN || 'true').toLowerCase() === 'true';
const BRANCH = process.env.BRANCH || 'main';

const API_BASE = 'https://api.github.com';
const POLL_INTERVAL_MS = 2000;

function getHeaders() {
  return {
    'Accept': 'application/vnd.github+json',
    'Authorization': `Bearer ${GITHUB_TOKEN}`,
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'GitHub-Offline-Browser-Daemon'
  };
}

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function listPendingRequests() {
  if (!GITHUB_REPOSITORY) return [];
  const url = `${API_BASE}/repos/${GITHUB_REPOSITORY}/contents/requests`;
  try {
    const res = await fetch(url, { headers: getHeaders() });
    if (res.status === 200) {
      const items = await res.json();
      if (Array.isArray(items)) {
        return items.filter((i) => i.name.endsWith('.json') && i.type === 'file');
      }
    } else if (res.status === 404) {
      return [];
    } else {
      console.warn(`[Daemon] List requests returned HTTP ${res.status}`);
    }
  } catch (err) {
    console.warn(`[Daemon] Error fetching requests list:`, err.message);
  }
  return [];
}

async function deleteRequestFile(filePath, sha) {
  const url = `${API_BASE}/repos/${GITHUB_REPOSITORY}/contents/${filePath}`;
  try {
    const res = await fetch(url, {
      method: 'DELETE',
      headers: getHeaders(),
      body: JSON.stringify({
        message: `Processed ${filePath}`,
        sha: sha,
        branch: BRANCH
      })
    });
    return res.status === 200;
  } catch (err) {
    console.error(`[Daemon] Failed to delete ${filePath}:`, err.message);
    return false;
  }
}

async function triggerNextWorker() {
  console.log('[Daemon] Max run time approaching. Spawning successor daemon worker...');
  const url = `${API_BASE}/repos/${GITHUB_REPOSITORY}/actions/workflows/daemon.yml/dispatches`;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify({
        ref: BRANCH,
        inputs: {
          idle_timeout_minutes: String(IDLE_TIMEOUT_MIN),
          max_hours: String(MAX_RUN_HOURS),
          auto_chain: 'true'
        }
      })
    });
    if (res.status === 204 || res.status === 200) {
      console.log('[Daemon] Successor worker spawned successfully!');
    } else {
      console.warn(`[Daemon] Spawn successor returned HTTP ${res.status}`);
    }
  } catch (err) {
    console.error('[Daemon] Error spawning successor worker:', err.message);
  }
}

async function main() {
  console.log('====================================================');
  console.log('🚀 GitHub Offline Browser: Daemon Worker Started');
  console.log(`Repository: ${GITHUB_REPOSITORY}`);
  console.log(`Max Run Time: ${MAX_RUN_HOURS} hours`);
  console.log(`Idle Timeout: ${IDLE_TIMEOUT_MIN} minutes`);
  console.log(`Auto Chain: ${AUTO_CHAIN}`);
  console.log('====================================================');

  const startTime = Date.now();
  let lastActivityTime = Date.now();
  const maxRunMs = MAX_RUN_HOURS * 3600 * 1000;
  const idleTimeoutMs = IDLE_TIMEOUT_MIN * 60 * 1000;

  // 1. Launch persistent Playwright Chromium browser in memory
  let chromium;
  try {
    const pw = await import('playwright');
    chromium = pw.chromium;
  } catch (e) {
    console.error('Playwright not found:', e);
    process.exit(1);
  }

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });

  console.log('[Daemon] Chromium browser instance initialized and hot in memory.');

  try {
    while (true) {
      const now = Date.now();

      // Check max run time handoff
      if (now - startTime >= maxRunMs) {
        console.log(`[Daemon] Worker has run for ${MAX_RUN_HOURS} hours.`);
        if (AUTO_CHAIN) {
          await triggerNextWorker();
        }
        break;
      }

      // Check idle timeout
      if (now - lastActivityTime >= idleTimeoutMs) {
        console.log(`[Daemon] No requests received for ${IDLE_TIMEOUT_MIN} minutes. Conserving Action quota and shutting down.`);
        break;
      }

      // Check pending requests
      const pendingItems = await listPendingRequests();
      if (pendingItems.length > 0) {
        // Sort items by name (timestamp is in name: timestamp_rand.json)
        pendingItems.sort((a, b) => a.name.localeCompare(b.name));
        const item = pendingItems[0];
        const requestId = item.name.replace(/\.json$/i, '');

        console.log(`\n----------------------------------------------------`);
        console.log(`[Daemon] ⚡ Processing request: ${requestId} (${item.path})`);
        console.log(`----------------------------------------------------`);

        // Fetch request envelope
        let requestData = null;
        try {
          const reqRes = await fetch(item.download_url || item.url, { headers: getHeaders() });
          if (item.download_url) {
            requestData = await reqRes.json();
          } else {
            const fileMeta = await reqRes.json();
            const decoded = Buffer.from(fileMeta.content, 'base64').toString('utf8');
            requestData = JSON.parse(decoded);
          }
        } catch (err) {
          console.error(`[Daemon] Error loading request envelope: ${err.message}`);
          await deleteRequestFile(item.path, item.sha);
          continue;
        }

        // Process request with hot browser
        const workDir = path.join(process.cwd(), 'work');
        if (fs.existsSync(workDir)) {
          fs.rmSync(workDir, { recursive: true, force: true });
        }
        fs.mkdirSync(workDir, { recursive: true });

        try {
          await processRequest(browser, requestData, requestId, workDir);
          await packageAndRelease(requestId, workDir);
        } catch (procErr) {
          console.error(`[Daemon] Processing error:`, procErr);
          writeErrorArtifacts(workDir, requestId, procErr.message);
          await packageAndRelease(requestId, workDir);
        }

        // Delete processed request envelope
        console.log(`[Daemon] Cleaning up request envelope ${item.path}...`);
        await deleteRequestFile(item.path, item.sha);

        // Reset idle timer
        lastActivityTime = Date.now();
        console.log(`[Daemon] ✅ Request ${requestId} completed! Returning to standby mode.`);

        // Clean work dir
        if (fs.existsSync(workDir)) {
          fs.rmSync(workDir, { recursive: true, force: true });
        }

        // Loop immediately in case more requests are queued
        continue;
      }

      // Sleep before polling again
      await sleep(POLL_INTERVAL_MS);
    }
  } finally {
    console.log('[Daemon] Closing browser and exiting worker.');
    await browser.close().catch(() => {});
  }
}

async function processRequest(browser, requestData, requestId, workDir) {
  const downloadDir = path.join(workDir, 'download');
  fs.mkdirSync(downloadDir, { recursive: true });

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
    session_storage = null
  } = requestData;

  const contextOptions = {
    viewport: {
      width: viewport.width || 1366,
      height: viewport.height || 768
    },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    acceptDownloads: true
  };

  if (storage_state && (storage_state.cookies?.length || storage_state.origins?.length)) {
    contextOptions.storageState = sanitizeStorageState(storage_state);
  }

  const context = await browser.newContext(contextOptions);
  let downloadedFile = null;
  let actionsExecuted = 0;
  let finalUrl = target_url;
  let pageTitle = '';
  let httpStatus = 200;

  try {
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
        } catch (e) {}
      }, session_storage);
    }

    if (custom_headers && Object.keys(custom_headers).length > 0) {
      await context.setExtraHTTPHeaders(custom_headers);
    }

    const page = await context.newPage();

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
      } catch (err) {
        console.error('[Daemon] Download save error:', err);
      }
    });

    if (http_method === 'POST' || (body_base64 && body_base64.length > 0)) {
      await page.route(target_url, async (route) => {
        const req = route.request();
        if (req.isNavigationRequest()) {
          const postData = body_base64 ? Buffer.from(body_base64, 'base64') : Buffer.from('');
          const headers = { ...req.headers(), ...custom_headers };
          if (content_type) headers['content-type'] = content_type;
          await route.continue({ method: http_method || 'POST', postData, headers });
        } else {
          await route.continue();
        }
      });
    }

    if (mode === 'replay') {
      const resp = await page.goto(target_url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => null);
      if (resp) httpStatus = resp.status();
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});

      for (let i = 0; i < actions.length; i++) {
        const act = actions[i];
        try {
          const el = await resolveElement(page, act);
          if (act.type === 'fill') {
            if (el) await el.fill(act.value || '');
            else if (act.x !== undefined && act.y !== undefined) {
              await page.mouse.click(act.x, act.y);
              await page.keyboard.type(act.value || '');
            }
          } else if (act.type === 'click') {
            if (el) await el.click({ timeout: 5000 });
            else if (act.x !== undefined && act.y !== undefined) await page.mouse.click(act.x, act.y);
            await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
          } else if (act.type === 'select') {
            if (el) await el.selectOption(act.value || '');
          } else if (act.type === 'check') {
            if (el) await el.setChecked(!!act.checked);
          } else if (act.type === 'press') {
            if (el) await el.press(act.key || 'Enter');
            else await page.keyboard.press(act.key || 'Enter');
            await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
          } else if (act.type === 'wait') {
            await page.waitForTimeout(act.wait_after_ms || 1000);
          } else if (act.type === 'scroll') {
            await page.mouse.wheel(0, act.y || 300);
          }
          if (act.wait_after_ms) await page.waitForTimeout(act.wait_after_ms);
          actionsExecuted++;
        } catch (e) {}
      }

      if (actionsExecuted === 0 && intercepted_ajax?.captured) {
        await executeAjaxFallback(page, intercepted_ajax);
      }
    } else if (mode === 'ajax_fallback' && intercepted_ajax?.captured) {
      await page.goto(target_url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      await executeAjaxFallback(page, intercepted_ajax);
    } else {
      const resp = await page.goto(target_url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => null);
      if (resp) {
        httpStatus = resp.status();
        const headers = resp.headers();
        const contentTypeHeader = headers['content-type'] || '';
        const disposition = headers['content-disposition'] || '';
        if (disposition.toLowerCase().includes('attachment') || isBinaryMime(contentTypeHeader)) {
          const buffer = await resp.body();
          let filename = 'downloaded_file';
          const match = disposition.match(/filename\*?=['"]?(?:UTF-8'')?([^'";\n]+)['"]?/i);
          if (match && match[1]) filename = decodeURIComponent(match[1]);
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

    await page.waitForTimeout(1000);
    finalUrl = page.url() || target_url;
    pageTitle = await page.title().catch(() => '');

    const currentStorage = await context.storageState().catch(() => ({ cookies: [], origins: [] }));
    fs.writeFileSync(path.join(workDir, 'storage_state.json'), JSON.stringify(currentStorage, null, 2), 'utf8');

    if (!downloadedFile) {
      const serializedHtml = await serializeRenderedPage(page, finalUrl);
      const indexPath = path.join(workDir, 'index.html');
      fs.writeFileSync(indexPath, serializedHtml, 'utf8');

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
        file_size: fs.statSync(indexPath).size,
        mime_type: 'text/html',
        error_message: ''
      };
      fs.writeFileSync(path.join(workDir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
    } else {
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
  } finally {
    await context.close().catch(() => {});
  }
}

async function packageAndRelease(requestId, workDir) {
  const cwd = process.cwd();
  const payloadZip = path.join(cwd, 'payload.zip');
  if (fs.existsSync(payloadZip)) fs.unlinkSync(payloadZip);

  // Clean old parts
  fs.readdirSync(cwd).filter((f) => f.startsWith('payload.zip.part')).forEach((f) => fs.unlinkSync(path.join(cwd, f)));

  // Zip work directory
  execSync(`cd "${workDir}" && zip -r "${payloadZip}" . && cd "${cwd}"`, { stdio: 'ignore' });

  // Split into 50MB chunks
  execSync(`split -b 52428800 -d --numeric-suffixes=1 -a 3 "${payloadZip}" payload.zip.part`, { stdio: 'ignore' });

  // Generate manifest
  const fullBuf = fs.readFileSync(payloadZip);
  const fullSha256 = crypto.createHash('sha256').update(fullBuf).digest('hex');
  const partFiles = fs.readdirSync(cwd).filter((f) => f.startsWith('payload.zip.part')).sort();
  const parts = partFiles.map((p) => {
    const b = fs.readFileSync(path.join(cwd, p));
    return {
      filename: p,
      size: b.length,
      sha256: crypto.createHash('sha256').update(b).digest('hex')
    };
  });

  const manifest = {
    request_id: requestId,
    total_size: fullBuf.length,
    full_sha256: fullSha256,
    parts
  };

  const manifestPath = path.join(cwd, 'manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8');

  // Create GitHub Release using gh CLI
  const tag = `res-${requestId}`;
  execSync(`gh release delete "${tag}" --yes 2>/dev/null || true`, { stdio: 'ignore' });
  execSync(`git push --delete origin "${tag}" 2>/dev/null || true`, { stdio: 'ignore' });

  console.log(`[Daemon] Creating release ${tag} and uploading ${parts.length} chunk(s)...`);
  execSync(`gh release create "${tag}" payload.zip.part* --title "Response ${tag}" --notes "Daemon response payload"`, {
    stdio: 'inherit',
    env: { ...process.env, GH_TOKEN: GITHUB_TOKEN }
  });

  console.log(`[Daemon] Uploading manifest.json as completion signal...`);
  execSync(`gh release upload "${tag}" "${manifestPath}"`, {
    stdio: 'inherit',
    env: { ...process.env, GH_TOKEN: GITHUB_TOKEN }
  });

  // Cleanup local zip and parts
  if (fs.existsSync(payloadZip)) fs.unlinkSync(payloadZip);
  partFiles.forEach((p) => fs.unlinkSync(path.join(cwd, p)));
  if (fs.existsSync(manifestPath)) fs.unlinkSync(manifestPath);
}

async function resolveElement(page, action) {
  if (action.selector) {
    try {
      const loc = page.locator(action.selector).first();
      if ((await loc.count()) > 0) return loc;
    } catch {}
  }
  if (action.xpath) {
    try {
      const loc = page.locator('xpath=' + action.xpath).first();
      if ((await loc.count()) > 0) return loc;
    } catch {}
  }
  if (action.text_hint) {
    try {
      const loc = page.getByText(action.text_hint, { exact: false }).first();
      if ((await loc.count()) > 0) return loc;
    } catch {}
  }
  return null;
}

async function executeAjaxFallback(page, ajax) {
  try {
    await page.evaluate(async (req) => {
      let bodyData = undefined;
      if (req.body_base64 && req.method !== 'GET' && req.method !== 'HEAD') {
        try { bodyData = atob(req.body_base64); } catch { bodyData = req.body_base64; }
      }
      try {
        const res = await window.fetch(req.url, {
          method: req.method || 'POST',
          headers: req.headers || {},
          body: bodyData
        });
        return { ok: res.ok, status: res.status };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    }, ajax);
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  } catch (err) {}
}

async function serializeRenderedPage(page, baseUrl) {
  return await page.evaluate((base) => {
    try {
      document.querySelectorAll('input').forEach((input) => {
        if (input.type === 'checkbox' || input.type === 'radio') {
          if (input.checked) input.setAttribute('checked', 'checked');
          else input.removeAttribute('checked');
        } else {
          input.setAttribute('value', input.value || '');
        }
      });
      document.querySelectorAll('textarea').forEach((ta) => {
        ta.textContent = ta.value || '';
      });
      document.querySelectorAll('select').forEach((sel) => {
        Array.from(sel.options).forEach((opt) => {
          if (opt.selected) opt.setAttribute('selected', 'selected');
          else opt.removeAttribute('selected');
        });
      });
      let baseEl = document.querySelector('base');
      if (!baseEl) {
        baseEl = document.createElement('base');
        document.head.insertBefore(baseEl, document.head.firstChild);
      }
      baseEl.setAttribute('href', base);
      const docType = document.doctype ? `<!DOCTYPE ${document.doctype.name}>` : '<!DOCTYPE html>';
      return `${docType}\n${document.documentElement.outerHTML}`;
    } catch (e) {
      return document.documentElement.outerHTML;
    }
  }, baseUrl);
}

function isBinaryMime(mime) {
  if (!mime) return false;
  const m = mime.toLowerCase();
  return (
    m.includes('application/zip') ||
    m.includes('application/octet-stream') ||
    m.includes('application/pdf') ||
    m.includes('audio/') ||
    m.includes('video/')
  );
}

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

function writeErrorArtifacts(workDir, requestId, errorMessage) {
  fs.mkdirSync(workDir, { recursive: true });
  const errorHtml = `<!DOCTYPE html><html lang="fa" dir="rtl"><body style="background:#0f172a;color:#f8fafc;padding:30px;"><div style="background:#1e293b;padding:20px;border-radius:10px;border:1px solid #ef4444;"><h2>خطا در پردازش</h2><pre style="direction:ltr;">${errorMessage}</pre></div></body></html>`;
  fs.writeFileSync(path.join(workDir, 'index.html'), errorHtml, 'utf8');
  fs.writeFileSync(path.join(workDir, 'storage_state.json'), JSON.stringify({ cookies: [], origins: [] }, null, 2), 'utf8');
  const meta = {
    request_id: requestId,
    status: 'error',
    response_type: 'page',
    requested_url: '',
    final_url: '',
    http_status: 500,
    title: 'Daemon Processing Error',
    actions_executed: 0,
    download_filename: '',
    file_size: Buffer.byteLength(errorHtml),
    mime_type: 'text/html',
    error_message: errorMessage
  };
  fs.writeFileSync(path.join(workDir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
}

main().catch((err) => {
  console.error('[Daemon Fatal Error]', err);
  process.exit(1);
});
