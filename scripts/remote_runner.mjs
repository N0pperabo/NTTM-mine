
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { decryptBytes, decryptEnvelope, isEncrypted, getEffectiveKey } from './crypto_utils.mjs';

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
    console.log(`[Runner] Auto-scrolling page (steps: ${scrollSteps})...`);
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
    console.warn('[Runner] Auto-scroll warning:', e.message);
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
      const decBuf = decryptEnvelope(fileBuf, encryptionKey);
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
    custom_delay_seconds = 0,
    auto_scroll = false,
    scroll_steps = 0
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

    // Hydrate storage state (cookies + localStorage)
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

    page.on('download' , async (download) => {
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

            case 'shorts_next':
              console.log('[Runner] Navigating to next YouTube Short...');
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
                    console.log(`[Runner] Clicked Shorts next button via: ${sel}`);
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
                  console.log('[Runner] Scrolled mouse wheel & pressed ArrowDown on Shorts container');
                } catch(e) {}
              }
              const oldShortUrl = page.url();
              await page.waitForTimeout(2000);
              await page.waitForFunction((old) => window.location.href !== old, oldShortUrl, { timeout: 6000 }).catch(() => {});
              await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
              await page.waitForTimeout(1000);
              break;

            case 'click':
              let clicked = false;
              if (el) {
                try {
                  await el.click({ timeout: 3500 });
                  clicked = true;
                } catch (err1) {
                  console.warn(`[Runner] Normal click failed (${err1.message.slice(0, 80)}), trying force click...`);
                }

                if (!clicked) {
                  try {
                    await el.click({ force: true, timeout: 2000 });
                    clicked = true;
                  } catch (err2) {
                    console.warn(`[Runner] Force click failed, trying DOM dispatchEvent...`);
                  }
                }

                if (!clicked) {
                  try {
                    await el.dispatchEvent('click');
                    clicked = true;
                  } catch (err3) {}
                }
              }

              if (!clicked && action.href) {
                console.log(`[Runner] Click target not found, navigating directly to action.href: ${action.href}`);
                await page.goto(action.href, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
                clicked = true;
              }

              if (!clicked && action.x !== undefined && action.y !== undefined) {
                await page.mouse.click(action.x, action.y).catch(() => {});
                clicked = true;
              }

              await page.waitForTimeout(1000);
              await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
              break;

            case 'submit':
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
                if (action.key === 'Enter') {
                  await el.evaluate((node) => {
                    const form = node.closest('form');
                    if (form) {
                      if (typeof form.requestSubmit === 'function') form.requestSubmit();
                      else form.submit();
                    }
                  }).catch(() => {});
                }
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
      console.log(`[Runner] 📜 Auto-scroll active (steps: ${steps}, delay: ${delaySec}s)...`);
      await autoScrollPage(page, delaySec, steps);
    } else if (delaySec > 0) {
      console.log(`[Runner] ⏱️ Custom delay ${delaySec}s active (auto-scroll disabled)...`);
      await page.waitForTimeout(delaySec * 1000);
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
      let serializedHtml = '';
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
          finalUrl = page.url() || target_url;
          serializedHtml = await serializeRenderedPage(page, finalUrl, imageMap, cssMap, fontMap);
          break;
        } catch (evalErr) {
          if ((evalErr.message.includes('Execution context was destroyed') || evalErr.message.includes('navigation')) && attempt < 3) {
            console.log(`[Runner] 🔄 Navigation occurred during serialization (attempt ${attempt}). Waiting for new document...`);
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
    // Clean up old releases
    await autoJanitorCleanupReleases(process.env.GITHUB_TOKEN, process.env.GITHUB_REPOSITORY).catch(() => {});
  }
}

// Fallback chain for element resolution with visible-first prioritization
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

  // 7. Href for links
  if (action.href) {
    try {
      const best = await pickVisibleOrFirst(page.locator(`a[href="${action.href}"]`));
      if (best) return best;
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

// Check for common binary/download MIME types

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
    console.error('[Fatal Error]', err);
    process.exit(1);
  });
}
