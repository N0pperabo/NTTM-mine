
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import { decryptBytes, encryptBytes, decryptEnvelope, encryptEnvelope, isEncrypted, getEffectiveKey } from './crypto_utils.mjs';
import { execSync } from 'node:child_process';

const BLOCKED_AD_DOMAINS = [
  'googlesyndication.com',
  'doubleclick.net',
  'google-analytics.com',
  'googletagmanager.com',
  'googletagservices.com',
  'adservice.google.com',
  'taboola.com',
  'outbrain.com',
  'scorecardresearch.com',
  'mc.yandex.ru',
  'adnxs.com',
  'criteo.com',
  'criteo.net',
  'pubmatic.com',
  'rubiconproject.com',
  'openx.net',
  'smartadserver.com',
  'adform.net',
  'adroll.com',
  'popads.net',
  'popcash.net',
  'propellerads.com',
  'clickadu.com',
  'adsterra.com',
  'trafficjunky.com',
  'exoclick.com',
  'juicyads.com',
  'hotjar.com',
  'clarity.ms',
  'mouseflow.com'
];

const BLOCKED_AD_PATTERNS = [
  /googlesyndication/i,
  /doubleclick/i,
  /google-analytics/i,
  /adservice/i,
  /taboola/i,
  /outbrain/i,
  /scorecardresearch/i,
  /mc\.yandex/i,
  /yandex\.[a-z.]+\/metrika/i,
  /(?:^|[./_-])(analytics|telemetry|trackers?|adsystem|adnxs|advertising|popups?)(?:[./_-]|$)/i,
  /[./_-]ads?[./_-]/i
];

function isAdOrTracker(urlStr, targetUrl) {
  if (!urlStr) return false;
  try {
    const parsed = new URL(urlStr);
    const host = parsed.hostname.toLowerCase();

    if (targetUrl) {
      try {
        const targetHost = new URL(targetUrl).hostname.toLowerCase();
        if (host === targetHost || host.endsWith('.' + targetHost)) {
          return false;
        }
      } catch (e) {}
    }

    for (const domain of BLOCKED_AD_DOMAINS) {
      if (host === domain || host.endsWith('.' + domain)) {
        return true;
      }
    }

    const testString = host + parsed.pathname;
    for (const rx of BLOCKED_AD_PATTERNS) {
      if (rx.test(testString)) {
        return true;
      }
    }
  } catch (e) {}
  return false;
}

async function setupAdBlocker(page, targetUrl) {
  await page.route('**/*', async (route) => {
    try {
      const req = route.request();
      if (req.isNavigationRequest() && req.frame() === page.mainFrame()) {
        return route.continue();
      }

      if (isAdOrTracker(req.url(), targetUrl)) {
        return route.abort();
      }

      return route.continue();
    } catch (err) {
      return route.continue().catch(() => {});
    }
  });
}

async function autoScrollPage(page, delaySec = 0, scrollSteps = 3) {
  try {
    console.log(`[Daemon] Auto-scrolling page (steps: ${scrollSteps})...`);
    await page.evaluate(async ({ delay, steps }) => {
      await new Promise((resolve) => {
        let totalHeight = 0;
        const distance = 400;
        const docElem = document.documentElement;
        const body = document.body;

        try {
          document.querySelectorAll('img[loading="lazy"], iframe[loading="lazy"]').forEach((el) => {
            el.setAttribute('loading', 'eager');
          });
        } catch (e) {}

        const targetLimit = Math.min(Math.max(steps, 1) * 800, 3000);
        const pageHeight = Math.max(docElem.scrollHeight, body ? body.scrollHeight : 0, 800);
        const maxScroll = Math.min(pageHeight, targetLimit);
        const totalSteps = Math.max(1, Math.ceil(maxScroll / distance));
        const stepInterval = delay > 0 ? Math.max(25, Math.floor((delay * 1000) / (totalSteps * 1.5))) : 25;

        const timer = setInterval(() => {
          window.scrollBy(0, distance);
          totalHeight += distance;

          window.dispatchEvent(new Event('scroll'));
          window.dispatchEvent(new Event('resize'));

          if (totalHeight >= maxScroll) {
            clearInterval(timer);
            window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
            window.dispatchEvent(new Event('scroll'));
            window.dispatchEvent(new Event('resize'));
            resolve();
          }
        }, stepInterval);
      });
    }, { delay: delaySec, steps: scrollSteps });

    await page.waitForTimeout(400);
    await page.evaluate(() => {
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
      window.dispatchEvent(new Event('scroll'));
    }).catch(() => {});
  } catch (e) {
    console.warn('[Daemon] Auto-scroll warning:', e.message);
  }
}

async function autoJanitorCleanupReleases(token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN, repo = process.env.GITHUB_REPOSITORY) {
  if (!token || !repo) {
    console.log('[Janitor] Skipping release cleanup: missing GITHUB_TOKEN or GITHUB_REPOSITORY.');
    return;
  }
  try {
    console.log(`[Janitor] Checking releases for pruning on ${repo}...`);
    const headers = {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${token}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'GitHub-Offline-Browser-Janitor'
    };

    const res = await fetch(`https://api.github.com/repos/${repo}/releases?per_page=100`, { headers });
    if (!res.ok) {
      console.warn(`[Janitor] Failed to fetch releases: HTTP ${res.status}`);
      return;
    }

    const releases = await res.json();
    if (!Array.isArray(releases) || releases.length === 0) {
      console.log('[Janitor] No releases found to evaluate.');
      return;
    }

    releases.sort((a, b) => {
      const timeA = new Date(b.created_at || b.published_at || 0).getTime();
      const timeB = new Date(a.created_at || a.published_at || 0).getTime();
      return timeA - timeB;
    });

    const now = Date.now();
    const MAX_AGE_MS = 24 * 60 * 60 * 1000;
    const MAX_KEEP_COUNT = 20;

    const toDelete = [];
    releases.forEach((rel, index) => {
      const createdAt = new Date(rel.created_at || rel.published_at || 0).getTime();
      const isOlderThan24h = (now - createdAt) > MAX_AGE_MS;
      const isBeyondLimit = index >= MAX_KEEP_COUNT;

      if (isOlderThan24h || isBeyondLimit) {
        toDelete.push({
          id: rel.id,
          tag_name: rel.tag_name,
          reason: isOlderThan24h ? 'older than 24h' : 'exceeds retention limit of 20'
        });
      }
    });

    if (toDelete.length === 0) {
      console.log(`[Janitor] All ${releases.length} releases are within retention policy.`);
      return;
    }

    console.log(`[Janitor] Pruning ${toDelete.length} old release(s)...`);
    for (const item of toDelete) {
      try {
        console.log(`[Janitor] Deleting release ${item.tag_name} (ID: ${item.id}) [${item.reason}]`);
        const delRes = await fetch(`https://api.github.com/repos/${repo}/releases/${item.id}`, {
          method: 'DELETE',
          headers
        });
        if (delRes.status === 204 || delRes.ok) {
          console.log(`[Janitor] Deleted release ${item.tag_name}`);
        } else {
          console.warn(`[Janitor] Could not delete release ${item.tag_name}: HTTP ${delRes.status}`);
        }

        if (item.tag_name) {
          await fetch(`https://api.github.com/repos/${repo}/git/refs/tags/${encodeURIComponent(item.tag_name)}`, {
            method: 'DELETE',
            headers
          }).catch(() => {});
        }
      } catch (delErr) {
        console.warn(`[Janitor] Failed to delete ${item.tag_name}:`, delErr.message);
      }
    }
    console.log('[Janitor] Release cleanup completed.');
  } catch (err) {
    console.warn('[Janitor] Auto Janitor error:', err.message);
  }
}

const GITHUB_TOKEN = process.env.GITHUB_TOKEN || '';
const GITHUB_REPOSITORY = process.env.GITHUB_REPOSITORY || '';
const IDLE_TIMEOUT_MIN = parseFloat(process.env.IDLE_TIMEOUT_MINUTES || '30');
const MAX_RUN_HOURS = parseFloat(process.env.MAX_HOURS || '5.5');
const AUTO_CHAIN = (process.env.AUTO_CHAIN || 'true').toLowerCase() === 'true';
const BRANCH = process.env.BRANCH || 'main';
const ENCRYPTION_KEY = (process.env.ENCRYPTION_KEY || '').trim();

const API_BASE = 'https://api.github.com';
const POLL_INTERVAL_MS = 1000;

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
        message: 'chore(ci): update sync state',
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

  console.log('[Daemon] Initializing persistent hot Chromium browser...');
  const browser = await chromium.launch({
    headless: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--disable-web-security',
      '--disable-blink-features=AutomationControlled',
      '--disable-infobars',
      '--no-first-run',
      '--no-default-browser-check',
      '--window-size=1920,1080',
      '--start-maximized',
      '--lang=en-US,en'
    ]
  });
  console.log('[Daemon] 🚀 Hot Playwright Chromium browser online and ready!');

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
          let rawBuffer;
          if (item.download_url) {
            const reqRes = await fetch(item.download_url, { headers: getHeaders() });
            rawBuffer = Buffer.from(await reqRes.arrayBuffer());
          } else {
            const fileMeta = await (await fetch(item.url, { headers: getHeaders() })).json();
            rawBuffer = Buffer.from(fileMeta.content, 'base64');
          }

          let jsonText;
          if (isEncrypted(rawBuffer)) {
            console.log('[Daemon] 🔒 Request envelope is encrypted with AES-256-GCM. Decrypting...');
            const dec = decryptEnvelope(rawBuffer, ENCRYPTION_KEY);
            jsonText = dec.toString('utf8');
          } else {
            jsonText = rawBuffer.toString('utf8');
          }
          requestData = JSON.parse(jsonText);
        } catch (err) {
          console.error(`[Daemon] Error loading or decrypting request envelope: ${err.message}`);
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
    if (browser) {
      await browser.close().catch(() => {});
    }
    // Clean up old releases
    await autoJanitorCleanupReleases(GITHUB_TOKEN, GITHUB_REPOSITORY).catch(() => {});
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
    session_storage = null,
    custom_delay_seconds = 0,
    auto_scroll = false,
    scroll_steps = 0
  } = requestData;

  const contextOptions = {
    viewport: {
      width: viewport.width || 1920,
      height: viewport.height || 1080
    },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    locale: 'en-US',
    timezoneId: 'America/New_York',
    extraHTTPHeaders: {
      'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8',
      'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
      'Sec-Ch-Ua-Mobile': '?0',
      'Sec-Ch-Ua-Platform': '"Windows"',
      ...custom_headers
    },
    acceptDownloads: true
  };

  if (storage_state && (storage_state.cookies?.length || storage_state.origins?.length)) {
    contextOptions.storageState = sanitizeStorageState(storage_state);
  }

  const context = await browser.newContext(contextOptions);

  if (storage_state && Array.isArray(storage_state.cookies) && storage_state.cookies.length > 0) {
    for (const c of storage_state.cookies) {
      if (c && c.name && c.value && (c.domain || c.url)) {
        await context.addCookies([c]).catch(() => {});
      }
    }
  }
    // Comprehensive Anti-Bot Stealth Masking
    await context.addInitScript(() => {
      try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        delete Object.getPrototypeOf(navigator).webdriver;
      } catch (e) {}

      try {
        if (!window.chrome) {
          window.chrome = {};
        }
        window.chrome.app = {
          isInstalled: false,
          InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
          RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
        };
        window.chrome.runtime = {
          OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
          OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
          PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
          PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
          PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
          RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
        };
      } catch (e) {}

      try {
        const fakePlugins = [
          { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
          { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
          { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }
        ];
        Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'fa'] });
      } catch (e) {}

      try {
        const getParameterProto = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
          if (parameter === 37445) return 'Google Inc. (NVIDIA)';
          if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
          return getParameterProto.apply(this, [parameter]);
        };
        if (typeof WebGL2RenderingContext !== 'undefined') {
          const getParameter2Proto = WebGL2RenderingContext.prototype.getParameter;
          WebGL2RenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Google Inc. (NVIDIA)';
            if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
            return getParameter2Proto.apply(this, [parameter]);
          };
        }
      } catch (e) {}

      try {
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
          parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(parameters);
      } catch (e) {}
    });

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

    const pages = context.pages();
    const page = pages.length > 0 ? pages[0] : await context.newPage();

    // Block ads and trackers before navigation
    await setupAdBlocker(page, target_url);

    // Cache all loaded image buffers, CSS stylesheets, and web fonts to embed into offline HTML
    const imageMap = new Map();
    const cssMap = new Map();
    const fontMap = new Map();
    let totalImageBytes = 0;
    const MAX_TOTAL_IMAGE_BYTES = 16 * 1024 * 1024; // 16 MB max for entire page image cache
    const MAX_SINGLE_IMAGE_BYTES = 1.5 * 1024 * 1024; // 1.5 MB max per image

    page.on('response', async (res) => {
      try {
        const url = res.url();
        const reqUrl = res.request() ? res.request().url() : url;
        const ct = (res.headers()['content-type'] || '').toLowerCase();
        const isImg =
          ct.startsWith('image/') ||
          /\.(png|jpe?g|webp|avif|gif|svg|bmp)(\?.*)?$/i.test(url) ||
          /\.(png|jpe?g|webp|avif|gif|svg|bmp)(\?.*)?$/i.test(reqUrl);

        if (isImg && totalImageBytes < MAX_TOTAL_IMAGE_BYTES) {
          const buffer = await res.body();
          if (buffer && buffer.length > 0 && buffer.length <= MAX_SINGLE_IMAGE_BYTES) {
            let mime = ct.startsWith('image/') ? ct.split(';')[0].trim() : 'image/jpeg';
            if (!mime || mime.includes('octet-stream')) {
              if (url.includes('.png') || reqUrl.includes('.png')) mime = 'image/png';
              else if (url.includes('.webp') || reqUrl.includes('.webp')) mime = 'image/webp';
              else if (url.includes('.svg') || reqUrl.includes('.svg')) mime = 'image/svg+xml';
              else if (url.includes('.gif') || reqUrl.includes('.gif')) mime = 'image/gif';
              else mime = 'image/jpeg';
            }
            const dataUrl = `data:${mime};base64,${buffer.toString('base64')}`;
            imageMap.set(url, dataUrl);
            imageMap.set(reqUrl, dataUrl);
            imageMap.set(url.split('?')[0], dataUrl);
            imageMap.set(reqUrl.split('?')[0], dataUrl);
            totalImageBytes += buffer.length;
          }
        } else if (ct.includes('text/css') || url.endsWith('.css') || url.includes('.css?')) {
          const text = await res.text();
          if (text) {
            cssMap.set(url, text);
            cssMap.set(reqUrl, text);
            cssMap.set(url.split('?')[0], text);
          }
        } else if (ct.includes('font') || /\.(woff2?|ttf|otf|eot)(\?.*)?$/i.test(url)) {
          const buffer = await res.body();
          if (buffer && buffer.length > 0) {
            const mime = ct.includes('font') ? ct.split(';')[0].trim() : (url.includes('.woff2') ? 'font/woff2' : 'font/woff');
            const dataUrl = `data:${mime};base64,${buffer.toString('base64')}`;
            fontMap.set(url, dataUrl);
            fontMap.set(reqUrl, dataUrl);
            fontMap.set(url.split('?')[0], dataUrl);
          }
        }
      } catch (e) {}
    });

    page.on('download'  , async (download) => {
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
          } else if (act.type === 'shorts_next') {
            let advancedShort = false;
            const nextBtnSelectors = [
              '#navigation-button-down button',
              'ytd-shorts #navigation-button-down button',
              'button[aria-label*="Next"]',
              'button[aria-label*="بعدی"]',
              '#shorts-container #navigation-button-down',
              'ytd-reel-video-renderer[is-active] + ytd-reel-video-renderer'
            ];
            for (const sel of nextBtnSelectors) {
              try {
                const btn = page.locator(sel).first();
                if ((await btn.count()) > 0 && (await btn.isVisible())) {
                  await btn.click({ timeout: 3500 });
                  advancedShort = true;
                  break;
                }
              } catch(e) {}
            }
            if (!advancedShort) {
              try {
                const container = page.locator('#shorts-container, ytd-shorts, body').first();
                await container.hover().catch(() => {});
                await page.mouse.wheel(0, 950);
                await page.keyboard.press('ArrowDown');
                await page.keyboard.press('PageDown');
              } catch(e) {}
            }
            const oldShortUrl = page.url();
            await page.waitForTimeout(2000);
            await page.waitForFunction((old) => window.location.href !== old, oldShortUrl, { timeout: 6000 }).catch(() => {});
            await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
            await page.waitForTimeout(1000);
          } else if (act.type === 'click') {
            let clicked = false;
            if (el) {
              try {
                await el.click({ timeout: 3500 });
                clicked = true;
              } catch (err1) {
                console.warn(`[Daemon] Normal click failed (${err1.message.slice(0, 80)}), trying force click...`);
              }

              if (!clicked) {
                try {
                  await el.click({ force: true, timeout: 2000 });
                  clicked = true;
                } catch (err2) {
                  console.warn(`[Daemon] Force click failed, trying DOM dispatchEvent...`);
                }
              }

              if (!clicked) {
                try {
                  await el.dispatchEvent('click');
                  clicked = true;
                } catch (err3) {}
              }
            }

            if (!clicked && act.href) {
              await page.goto(act.href, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
              clicked = true;
            }

            if (!clicked && act.x !== undefined && act.y !== undefined) {
              await page.mouse.click(act.x, act.y).catch(() => {});
              clicked = true;
            }

            await page.waitForTimeout(1000);
            await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
          } else if (act.type === 'submit') {
            if (el) {
              await el.evaluate((f) => {
                if (typeof f.requestSubmit === 'function') f.requestSubmit();
                else f.submit();
              }).catch(async () => {
                await el.press('Enter').catch(() => {});
              });
            } else {
              await page.keyboard.press('Enter');
            }
            await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
          } else if (act.type === 'select') {
            if (el) await el.selectOption(act.value || '');
          } else if (act.type === 'check') {
            if (el) await el.setChecked(!!act.checked);
          } else if (act.type === 'press') {
            if (el) {
              await el.press(act.key || 'Enter');
              if (act.key === 'Enter') {
                await el.evaluate((node) => {
                  const form = node.closest('form');
                  if (form) {
                    if (typeof form.requestSubmit === 'function') form.requestSubmit();
                    else form.submit();
                  }
                }).catch(() => {});
              }
            } else {
              await page.keyboard.press(act.key || 'Enter');
            }
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

    finalUrl = page.url() || target_url;
    pageTitle = await page.title().catch(() => '');

    // Optional auto-scroll for lazy-loaded assets (suppressed in replay mode unless explicitly requested)
    const isReplayMode = mode === 'replay';
    const rawAutoScroll = auto_scroll ?? requestData.auto_scroll;
    const rawScrollSteps = scroll_steps ?? requestData.scroll_steps;
    const hasExplicitScroll = (typeof rawScrollSteps === 'number' && rawScrollSteps > 0) || (typeof rawScrollSteps === 'string' && parseInt(rawScrollSteps, 10) > 0);
    const shouldAutoScroll = (!isReplayMode && Boolean(
      rawAutoScroll === true ||
      rawAutoScroll === 'true' ||
      hasExplicitScroll
    ) && rawAutoScroll !== false && rawAutoScroll !== 'false' && rawAutoScroll !== 0 && rawAutoScroll !== '0') || (isReplayMode && hasExplicitScroll);

    const delaySec = parseInt(custom_delay_seconds || 0, 10);
    if (shouldAutoScroll) {
      const steps = parseInt(rawScrollSteps || (typeof rawAutoScroll === 'number' ? rawAutoScroll : 3), 10) || 3;
      console.log(`[Daemon] 📜 Auto-scroll active (steps: ${steps}, delay: ${delaySec}s)...`);
      await autoScrollPage(page, delaySec, steps);
    } else if (delaySec > 0) {
      console.log(`[Daemon] ⏱️ Custom delay ${delaySec}s active (auto-scroll disabled)...`);
      await page.waitForTimeout(delaySec * 1000);
    }

    await page.waitForTimeout(1000);
    finalUrl = page.url() || target_url;
    pageTitle = await page.title().catch(() => '');

    const currentStorage = await context.storageState().catch(() => ({ cookies: [], origins: [] }));
    fs.writeFileSync(path.join(workDir, 'storage_state.json'), JSON.stringify(currentStorage, null, 2), 'utf8');

    if (!downloadedFile) {
      let serializedHtml = '';
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
          finalUrl = page.url() || target_url;
          serializedHtml = await serializeRenderedPage(page, finalUrl, imageMap, cssMap, fontMap);
          break;
        } catch (evalErr) {
          if ((evalErr.message.includes('Execution context was destroyed') || evalErr.message.includes('navigation')) && attempt < 3) {
            console.log(`[Daemon] 🔄 Navigation occurred during serialization (attempt ${attempt}). Waiting for new document...`);
            await page.waitForTimeout(3000);
            await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
            await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
            finalUrl = page.url() || target_url;
            pageTitle = await page.title().catch(() => '');
            continue;
          }
          throw evalErr;
        }
      }
      serializedHtml = await sweepAndInlineRemainingImages(serializedHtml, finalUrl, imageMap);
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

  // Ensure workDir is not empty before zipping
  const filesInWork = fs.existsSync(workDir) ? fs.readdirSync(workDir) : [];
  if (filesInWork.length === 0) {
    writeErrorArtifacts(workDir, requestId, 'Empty work directory produced');
  }

  // High-efficiency zipping of work directory (Optimal compression)
  if (process.platform === 'win32') {
    execSync(`powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('${workDir.replace(/'/g, "''")}', '${payloadZip.replace(/'/g, "''")}', [System.IO.Compression.CompressionLevel]::Optimal, $false)"`, { stdio: 'ignore' });
  } else {
    execSync(`cd "${workDir}" && zip -q -6 -r "${payloadZip}" . && cd "${cwd}"`, { stdio: 'ignore' });
  }

  // Mandatory AES-256-GCM encryption of payload.zip using effective key
  let zipBuffer = fs.readFileSync(payloadZip);
  const effectiveKey = getEffectiveKey(ENCRYPTION_KEY);
  console.log('[Daemon] 🔒 Encrypting payload.zip with AES-256-GCM (Protecting pages, sessions & downloaded files)...');
  const encZip = encryptBytes(zipBuffer, effectiveKey);
  fs.writeFileSync(payloadZip, encZip);
  zipBuffer = encZip;
  console.log('[Daemon] ✅ Payload encrypted successfully with AES-256-GCM! Size:', zipBuffer.length);

  // Split into 50MB chunks with in-flight SHA-256 calculation (Zero-redundancy I/O)
  const CHUNK_SIZE = 52428800;
  let partIndex = 1;
  const parts = [];
  for (let offset = 0; offset < zipBuffer.length; offset += CHUNK_SIZE) {
    const chunk = zipBuffer.subarray(offset, Math.min(offset + CHUNK_SIZE, zipBuffer.length));
    const partName = `payload.zip.part${String(partIndex).padStart(3, '0')}`;
    fs.writeFileSync(path.join(cwd, partName), chunk);
    const partSha = crypto.createHash('sha256').update(chunk).digest('hex');
    parts.push({
      filename: partName,
      size: chunk.length,
      sha256: partSha
    });
    partIndex++;
  }

  // Generate manifest
  const fullBuf = zipBuffer;
  const fullSha256 = crypto.createHash('sha256').update(fullBuf).digest('hex');
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
  execSync(`git tag -d "${tag}" 2>/dev/null || true`, { stdio: 'ignore' });

  console.log(`[Daemon] Creating release ${tag} and uploading ${parts.length} chunk(s)...`);
  execSync(`gh release create "${tag}" payload.zip.part* --target "${BRANCH}" --title "Response ${tag}" --notes "build: sync cache manifest"`, {
    stdio: 'inherit',
    env: { ...process.env, GH_TOKEN: GITHUB_TOKEN }
  });

  console.log(`[Daemon] Uploading manifest.json as completion signal...`);
  execSync(`gh release upload "${tag}" "${manifestPath}"`, {
    stdio: 'inherit',
    env: { ...process.env, GH_TOKEN: GITHUB_TOKEN }
  });

  // Clean up old releases
  await autoJanitorCleanupReleases(GITHUB_TOKEN, GITHUB_REPOSITORY).catch(() => {});

  // Cleanup local zip and parts safely
  try {
    if (fs.existsSync(payloadZip)) fs.unlinkSync(payloadZip);
    parts.forEach((p) => {
      const fp = path.join(cwd, p.filename);
      if (fs.existsSync(fp)) fs.unlinkSync(fp);
    });
    if (fs.existsSync(manifestPath)) fs.unlinkSync(manifestPath);
  } catch (cleanErr) {
    console.warn('[Daemon] Cleanup warning:', cleanErr.message);
  }
}

async function resolveElement(page, action) {
  async function pickVisibleOrFirst(loc) {
    try {
      const count = await loc.count();
      if (count === 0) return null;
      if (count === 1) return loc.first();
      for (let i = 0; i < count; i++) {
        const item = loc.nth(i);
        if (await item.isVisible().catch(() => false)) {
          return item;
        }
      }
      return loc.first();
    } catch {
      return null;
    }
  }

  // 1. Specific Selector
  if (action.selector) {
    try {
      const best = await pickVisibleOrFirst(page.locator(action.selector));
      if (best) return best;
    } catch {}
  }

  // 2. High-priority specialized selectors for YouTube Like / Interaction elements
  if (action.selector && (action.selector.includes('like') || action.selector.includes('ytSpecButtonShape'))) {
    const ytLikeSelectors = [
      'ytd-watch-metadata like-button-view-model button',
      'segmented-like-dislike-button-view-model like-button-view-model button',
      'like-button-view-model button',
      '#top-level-buttons-computed like-button-view-model button',
      'button[aria-label*="like" i]',
      'button[aria-label*="بپسندید" i]'
    ];
    for (const s of ytLikeSelectors) {
      try {
        const best = await pickVisibleOrFirst(page.locator(s));
        if (best) return best;
      } catch {}
    }
  }

  // 3. High-priority specialized selectors for Reddit Search and UI
  if (action.selector && (action.selector.includes('search') || action.name === 'q' || action.placeholder?.includes('Search'))) {
    const redditSearchSelectors = [
      'reddit-search-large input',
      'faceplate-search-input input',
      'input[name="q"]',
      'input[type="search"]',
      'input[placeholder*="Search" i]',
      '#search-input'
    ];
    for (const s of redditSearchSelectors) {
      try {
        const best = await pickVisibleOrFirst(page.locator(s));
        if (best) return best;
      } catch {}
    }
  }

  // 4. Specific XPath
  if (action.xpath) {
    try {
      const best = await pickVisibleOrFirst(page.locator('xpath=' + action.xpath));
      if (best) return best;
    } catch {}
  }

  // 5. Text hint
  if (action.text_hint) {
    try {
      const best = await pickVisibleOrFirst(page.getByText(action.text_hint, { exact: false }));
      if (best) return best;
    } catch {}
  }

  // 6. Placeholder
  if (action.placeholder) {
    try {
      const best = await pickVisibleOrFirst(page.getByPlaceholder(action.placeholder, { exact: false }));
      if (best) return best;
    } catch {}
  }

  // 7. Href
  if (action.href) {
    try {
      const best = await pickVisibleOrFirst(page.locator(`a[href="${action.href}"]`));
      if (best) return best;
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

async function serializeRenderedPage(page, baseUrl, imageMap = new Map(), cssMap = new Map(), fontMap = new Map()) {
  const imagesList = imageMap ? Array.from(imageMap.entries()) : [];
  const cssList = cssMap ? Array.from(cssMap.entries()) : [];
  const fontsList = fontMap ? Array.from(fontMap.entries()) : [];

  return await page.evaluate(async ({ base, images, cssItems, fontItems }) => {
    try {
      const imgLookup = new Map(images || []);
      const cssLookup = new Map(cssItems || []);
      const fontLookup = new Map(fontItems || []);

      // Helper to fetch resource and convert to base64 data url inside page context
      async function fetchToDataUrl(url, defaultMime = 'application/octet-stream') {
        try {
          const res = await fetch(url);
          if (!res.ok) return null;
          const mime = res.headers.get('content-type') || defaultMime;
          const blob = await res.blob();
          return await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result);
            reader.onerror = () => resolve(null);
            reader.readAsDataURL(blob);
          });
        } catch (e) {
          return null;
        }
      }

      // Helper to rewrite and inline all url(...) in CSS
      async function processCssText(rawCss, cssBaseUrl) {
        if (!rawCss) return '';
        const urlMatches = Array.from(rawCss.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/gi));
        let processedCss = rawCss;

        for (const match of urlMatches) {
          const rawUrl = match[1].trim();
          if (!rawUrl || rawUrl.startsWith('data:') || rawUrl.startsWith('#')) continue;

          try {
            const absUrl = new URL(rawUrl, cssBaseUrl).href;
            let dataUrl = fontLookup.get(absUrl) || imgLookup.get(absUrl);

            if (!dataUrl) {
              const isFont = /\.(woff2?|ttf|otf|eot)(\?.*)?$/i.test(absUrl);
              const defaultMime = isFont ? 'font/woff2' : 'image/png';
              dataUrl = await fetchToDataUrl(absUrl, defaultMime);
              if (dataUrl) {
                if (isFont) fontLookup.set(absUrl, dataUrl);
                else imgLookup.set(absUrl, dataUrl);
              }
            }

            if (dataUrl) {
              processedCss = processedCss.split(match[0]).join(`url("${dataUrl}")`);
            } else {
              processedCss = processedCss.split(match[0]).join(`url("${absUrl}")`);
            }
          } catch (e) {}
        }
        return processedCss;
      }

      // 1. INLINE ALL STYLESHEETS (<link rel="stylesheet">) INTO <style> TAGS
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
            const inlinedCss = await processCssText(css, href);
            const style = document.createElement('style');
            style.setAttribute('data-inlined-from', href);
            style.textContent = inlinedCss;
            link.replaceWith(style);
          }
        } catch (e) {}
      }

      // Also process any existing <style> tags on the page for url(...)
      const styleElements = Array.from(document.querySelectorAll('style'));
      for (const style of styleElements) {
        try {
          if (style.textContent && style.textContent.includes('url(')) {
            style.textContent = await processCssText(style.textContent, base);
          }
        } catch (e) {}
      }

      // 2. UNPACK ALL LAZY-LOADING ATTRIBUTES ACROSS ALL DOM ELEMENTS
      const LAZY_ATTRS = [
        'data-image', 'data-thumb', 'data-thumb_url', 'data-thumbnail',
        'data-mediumthumb', 'data-src', 'data-original', 'data-original-src',
        'data-lazy-src', 'data-lazy', 'data-cover', 'data-poster', 'data-bg',
        'data-background', 'data-bkg', 'data-url', 'data-file', 'data-actualsrc',
        'data-orig', 'data-photo', 'data-img', 'data-source', 'data-webp',
        'data-src-retina', 'data-hires'
      ];

      function isDummySrc(src) {
        if (!src) return true;
        const s = src.toLowerCase();
        if (s.startsWith('data:image/svg') || s.startsWith('data:image/gif') || s.startsWith('data:image/jxl')) return true;
        if (s.includes('blank') || s.includes('placeholder') || s.includes('transparent') || s.includes('spacer') || s.includes('loading')) return true;
        if (s.startsWith('data:') && s.length < 1200) return true;
        return false;
      }

      function getLazyUrl(el) {
        for (const attr of LAZY_ATTRS) {
          const val = el.getAttribute(attr);
          if (val && typeof val === 'string' && val.trim()) {
            return val.trim().split(' ')[0];
          }
        }
        const srcset = el.getAttribute('srcset') || el.getAttribute('data-srcset');
        if (srcset) {
          const first = srcset.split(',')[0].trim().split(' ')[0];
          if (first) return first;
        }
        return null;
      }

      // Unpack lazy attributes on all images, pictures, sources
      document.querySelectorAll('img, picture source').forEach((el) => {
        const currentSrc = el.getAttribute('src');
        const lazyUrl = getLazyUrl(el);
        if (lazyUrl && isDummySrc(currentSrc)) {
          el.setAttribute('src', lazyUrl);
        }
      });

      // Also unpack on div, span, a, figure, section
      document.querySelectorAll('div, span, a, figure, section').forEach((el) => {
        const lazyUrl = getLazyUrl(el);
        if (lazyUrl && !el.getAttribute('style')?.includes('url(')) {
          el.style.backgroundImage = `url("${lazyUrl}")`;
        }
      });

      // Helper to downscale and transcode rendered images to WebP 0.75
      function transcodeImage(imgEl) {
        if (!imgEl || !imgEl.complete || imgEl.naturalWidth <= 1 || imgEl.naturalHeight <= 1) return null;
        try {
          const MAX_DIM = 1000;
          let targetW = imgEl.naturalWidth;
          let targetH = imgEl.naturalHeight;
          if (targetW > MAX_DIM || targetH > MAX_DIM) {
            if (targetW >= targetH) {
              targetH = Math.round((targetH * MAX_DIM) / targetW);
              targetW = MAX_DIM;
            } else {
              targetW = Math.round((targetW * MAX_DIM) / targetH);
              targetH = MAX_DIM;
            }
          }
          const canvas = document.createElement('canvas');
          canvas.width = targetW;
          canvas.height = targetH;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(imgEl, 0, 0, targetW, targetH);
          const webp = canvas.toDataURL('image/webp', 0.75);
          if (webp && webp.startsWith('data:image/webp')) {
            return webp;
          }
          return canvas.toDataURL('image/jpeg', 0.8);
        } catch (e) {
          return null;
        }
      }

      // 3. INLINE ALL <img> ELEMENTS (Guarantee all images are data: URLs)
      const allImgs = Array.from(document.querySelectorAll('img'));
      for (const img of allImgs) {
        try {
          let src = img.getAttribute('src') || img.src || '';
          if (isDummySrc(src)) {
            const lazy = getLazyUrl(img);
            if (lazy) src = lazy;
          }
          if (!src) continue;

          let absSrc = src;
          try { absSrc = new URL(src, base).href; } catch (e) {}

          const isSvg = src.includes('.svg') || (img.currentSrc && img.currentSrc.includes('.svg'));
          let dataUrl = null;

          // Convert rendered image to downscaled WebP data URL
          if (!isSvg && img.complete && img.naturalWidth > 1) {
            dataUrl = transcodeImage(img);
          }

          // Fallback to pre-cached data URL from network responses
          if (!dataUrl) {
            dataUrl = imgLookup.get(absSrc) || imgLookup.get(src) || imgLookup.get(absSrc.split('?')[0]) || imgLookup.get(src.split('?')[0]);
          }

          // If still no dataUrl and it's already a non-dummy data: URL
          if (!dataUrl && src.startsWith('data:') && !isDummySrc(src)) {
            dataUrl = src;
          }

          // In-page fetch fallback if needed
          if (!dataUrl && !absSrc.startsWith('data:')) {
            dataUrl = await fetchToDataUrl(absSrc, 'image/jpeg');
            if (dataUrl) {
              imgLookup.set(absSrc, dataUrl);
            }
          }

          if (dataUrl) {
            img.src = dataUrl;
            img.setAttribute('src', dataUrl);
          } else {
            img.setAttribute('src', absSrc);
          }

          img.removeAttribute('srcset');
          img.removeAttribute('data-srcset');
        } catch (e) {}
      }

      // 4. INLINE BACKGROUND IMAGES IN ELEMENT STYLE ATTRIBUTES
      const bgElements = Array.from(document.querySelectorAll('*[style*="background"], *[style*="url("]'));
      for (const el of bgElements) {
        try {
          const style = el.getAttribute('style') || '';
          if (style.includes('url(')) {
            el.setAttribute('style', await processCssText(style, base));
          }
        } catch (e) {}
      }

      // 5. PRESERVE FORM INPUTS, TEXTAREAS, AND SELECT VALUES
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

      // 6. RESOLVE LINKS AND FORMS TO ABSOLUTE URLS
      // Fix YouTube guide entries and SPA custom elements lacking static href
      document.querySelectorAll('ytd-guide-entry-renderer, ytd-mini-guide-entry-renderer').forEach((entry) => {
        try {
          const a = entry.querySelector('a#endpoint') || entry.querySelector('a');
          if (a && (!a.getAttribute('href') || a.getAttribute('href') === '#' || a.getAttribute('href').startsWith('javascript:'))) {
            const endpointUrl = entry.__data?.data?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url
              || entry.data?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url;
            if (endpointUrl) {
              const abs = new URL(endpointUrl, base).href;
              a.setAttribute('href', abs);
            } else {
              const text = (a.getAttribute('title') || a.getAttribute('aria-label') || entry.innerText || '').toLowerCase();
              if (text.includes('short') || text.includes('کوته') || text.includes('شورت')) {
                a.setAttribute('href', new URL('/shorts', base).href);
              }
            }
          }
        } catch (e) {}
      });

      document.querySelectorAll('a').forEach((a) => {
        try {
          if (a.href) a.setAttribute('href', a.href);
          a.setAttribute('target', '_self');
        } catch (e) {}
      });

      document.querySelectorAll('form').forEach((f) => {
        try {
          if (f.action) f.setAttribute('action', f.action);
        } catch (e) {}
      });

      // 7. NEUTRALIZE SCRIPTS TO PREVENT OUTBOUND CLIENT CALLS
      document.querySelectorAll('script').forEach((s) => {
        s.setAttribute('type', 'text/plain');
        s.removeAttribute('src');
      });

      // 8. REMOVE BASE TAG
      document.querySelectorAll('base').forEach((b) => b.remove());

      const docType = document.doctype ? `<!DOCTYPE ${document.doctype.name}>` : '<!DOCTYPE html>';
      return `${docType}\n${document.documentElement.outerHTML}`;
    } catch (e) {
      return document.documentElement.outerHTML;
    }
  }, { base: baseUrl, images: imagesList, cssItems: cssList, fontItems: fontsList });
}


async function sweepAndInlineRemainingImages(html, baseUrl, imageMap) {
  if (!html) return html;

  const attrRegex = /(?:src|data-(?:image|thumb|thumb_url|src|lazy|lazy-src|original|poster|cover|bg|background|webp|mediumthumb|hires|file|url))\s*=\s*(["'])(https?:\/\/[^\s"']+)\1/gi;
  const cssUrlRegex = /url\(\s*(["']?)(https?:\/\/[^\s"')]+)\1\s*\)/gi;

  const urlsToFetch = new Set();
  let match;
  while ((match = attrRegex.exec(html)) !== null) {
    const u = match[2];
    if (!imageMap.has(u) && !imageMap.has(u.split('?')[0])) {
      urlsToFetch.add(u);
    }
  }
  while ((match = cssUrlRegex.exec(html)) !== null) {
    const u = match[2];
    if (!imageMap.has(u) && !imageMap.has(u.split('?')[0])) {
      urlsToFetch.add(u);
    }
  }

  const ignoredExtRegex = /\.(woff2?|ttf|otf|eot|mp4|webm|mkv|avi|mov|flv|wmv|m4v|mp3|wav|ogg|m4a|flac|aac|zip|tar|gz|rar|7z|pdf|exe|iso)(\?.*)?$/i;
  const imageExtRegex = /\.(jpe?g|png|webp|avif|gif|svg|bmp)(\?.*)?$/i;
  const imageLikeRegex = /(?:\/image|\/img|\/thumb|\/poster|\/cover|\/pics|\/avatar|\/panel)/i;

  const candidateUrls = Array.from(urlsToFetch).filter((u) => {
    if (ignoredExtRegex.test(u)) return false;
    return imageExtRegex.test(u) || imageLikeRegex.test(u);
  }).slice(0, 25);

  if (candidateUrls.length > 0) {
    console.log(`[Sweeper] 🧹 Found ${candidateUrls.length} remaining images to fetch directly via Node.js...`);
    const CONCURRENCY = 8;
    for (let i = 0; i < candidateUrls.length; i += CONCURRENCY) {
      const batch = candidateUrls.slice(i, i + CONCURRENCY);
      await Promise.all(
        batch.map(async (imgUrl) => {
          try {
            const res = await fetch(imgUrl, {
              headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': baseUrl,
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
              },
              signal: AbortSignal.timeout(6000)
            });
            if (res.ok) {
              const ct = (res.headers.get('content-type') || '').toLowerCase();
              if (ct.includes('font') || ct.startsWith('video/') || ct.startsWith('audio/') || (ct.includes('octet-stream') && !imageExtRegex.test(imgUrl))) {
                return;
              }
              const cl = parseInt(res.headers.get('content-length') || '0', 10);
              if (cl > 1.5 * 1024 * 1024) return;

              let mime = ct.startsWith('image/') ? ct.split(';')[0].trim() : 'image/jpeg';
              if (mime.includes('octet-stream')) {
                if (imgUrl.includes('.png')) mime = 'image/png';
                else if (imgUrl.includes('.webp')) mime = 'image/webp';
                else if (imgUrl.includes('.svg')) mime = 'image/svg+xml';
                else if (imgUrl.includes('.gif')) mime = 'image/gif';
                else mime = 'image/jpeg';
              }
              const buf = Buffer.from(await res.arrayBuffer());
              if (buf.length > 0 && buf.length <= 1.5 * 1024 * 1024) {
                const dataUrl = `data:${mime};base64,${buf.toString('base64')}`;
                imageMap.set(imgUrl, dataUrl);
                imageMap.set(imgUrl.split('?')[0], dataUrl);
              }
            }
          } catch (err) {}
        })
      );
    }
  }

  // Replace external image URLs in HTML
  let processedHtml = html;
  for (const [rawUrl, dataUrl] of imageMap.entries()) {
    if (processedHtml.includes(rawUrl)) {
      processedHtml = processedHtml.replaceAll(rawUrl, dataUrl);
    }
  }

  // Ensure any <img ...> that has data-image / data-thumb in data: URL format has its src set!
  processedHtml = processedHtml.replace(/<img\b([^>]*?)>/gi, (fullTag, attrs) => {
    const hasValidSrc = /src=["']data:image\/(?:jpeg|png|webp|avif)/i.test(attrs);
    if (!hasValidSrc) {
      const lazyDataMatch = attrs.match(/data-(?:image|thumb|thumb_url|src|original|poster|cover)=["'](data:image\/[^"']+)["']/i);
      if (lazyDataMatch) {
        const realDataUrl = lazyDataMatch[1];
        if (/src=["'][^"']*["']/i.test(attrs)) {
          return `<img ${attrs.replace(/src=["'][^"']*["']/i, `src="${realDataUrl}"`)}>`;
        } else {
          return `<img src="${realDataUrl}" ${attrs}>`;
        }
      }
    }
    return fullTag;
  });

  return processedHtml;
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

export { autoJanitorCleanupReleases, autoScrollPage, setupAdBlocker };

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    const scriptPath = fileURLToPath(import.meta.url);
    return path.resolve(process.argv[1]).toLowerCase() === path.resolve(scriptPath).toLowerCase();
  } catch (e) {
    return false;
  }
}

if (isDirectRun()) {
  main().catch((err) => {
    console.error('[Daemon Fatal Error]', err);
    process.exit(1);
  });
}
