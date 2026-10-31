
// Cloudflare Challenge / Turnstile Automatic Detector & Solver
async function handleCloudflareChallenge(page) {
  try {
    // 1. FAST CHECK: Is Cloudflare challenge present?
    const checkIsChallenge = async () => {
      const title = (await page.title().catch(() => '')).toLowerCase();
      const isCfTitle =
        title.includes('just a moment') ||
        title.includes('checking your browser') ||
        title.includes('attention required') ||
        title.includes('ddos-guard') ||
        title.includes('security check') ||
        title.includes('چند لحظه صبر کنید') ||
        title.includes('یک لحظه صبر کنید') ||
        title.includes('در حال بررسی') ||
        title.includes('لطفا صبر کنید') ||
        title.includes('تایید امنیتی');

      const hasCfElement = await page.evaluate(() => {
        try {
          const text = (document.body ? document.body.innerText : '') || '';
          return !!(
            document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
            document.querySelector('iframe[src*="challenge-platform"]') ||
            document.querySelector('#challenge-stage') ||
            document.querySelector('#challenge-running') ||
            document.querySelector('#challenge-form') ||
            document.querySelector('.cf-turnstile') ||
            document.querySelector('#turnstile-wrapper') ||
            document.querySelector('input[name="cf-turnstile-response"]') ||
            window._cf_chl_opt ||
            text.includes('Verify you are human') ||
            text.includes('Checking if the site connection is secure') ||
            text.includes('در حال انجام تایید امنیتی') ||
            text.includes('تایید موفقیت آمیز بود') ||
            text.includes('در انتظار هدایت') ||
            text.includes('Cloudflare')
          );
        } catch (e) {
          return false;
        }
      }).catch(() => false);

      return isCfTitle || hasCfElement;
    };

    if (!(await checkIsChallenge())) {
      // Zero overhead: No Cloudflare challenge detected!
      return false;
    }

    console.log('[Cloudflare] 🛡️ Cloudflare Challenge / Turnstile detected!');
    console.log('[Cloudflare] ⏳ Waiting up to 15 seconds for challenge & Turnstile iframe to fully render...');

    // 2. Wait up to 15s for the challenge iframe or widget to appear
    const iframeLocator = page.locator('iframe[src*="challenges.cloudflare.com"], iframe[src*="challenge-platform"]').first();
    let iframeVisible = false;
    try {
      await iframeLocator.waitFor({ state: 'visible', timeout: 15000 });
      iframeVisible = true;
      console.log('[Cloudflare] Turnstile challenge iframe is now visible.');
    } catch (e) {
      console.log('[Cloudflare] Iframe wait finished or challenge is running via background JS.');
    }

    // Let the widget settle and execute initial telemetry
    await page.waitForTimeout(2000);

    // 3. Attempt to locate and click the checkbox with human-like mouse movement
    let clicked = false;

    // Strategy A: frameLocator to find inner checkbox or label
    try {
      const cfFrame = page.frameLocator('iframe[src*="challenges.cloudflare.com"], iframe[src*="challenge-platform"]').first();
      const checkbox = cfFrame.locator('input[type="checkbox"], .ctp-checkbox-label, label, #challenge-stage, body').first();
      if ((await checkbox.count()) > 0) {
        const box = await checkbox.boundingBox();
        if (box && box.width > 0 && box.height > 0) {
          const clickX = box.x + Math.min(30, box.width / 2);
          const clickY = box.y + box.height / 2;
          console.log(`[Cloudflare] 🖱️ Moving mouse naturally to inner checkbox: x=${clickX}, y=${clickY}`);
          await page.mouse.move(clickX, clickY, { steps: 25 });
          await page.waitForTimeout(300);
          await page.mouse.down();
          await page.waitForTimeout(150);
          await page.mouse.up();
          clicked = true;
          console.log('[Cloudflare] ✅ Clicked checkbox via Strategy A (frameLocator).');
        }
      }
    } catch (errA) {}

    // Strategy B: If inner locator was blocked by closed shadow root, click the iframe bounding box at the checkbox offset
    if (!clicked && iframeVisible) {
      try {
        const box = await iframeLocator.boundingBox();
        if (box) {
          const targetX = box.x + 30;
          const targetY = box.y + box.height / 2;
          console.log(`[Cloudflare] 🖱️ Moving mouse to iframe bounding box coordinates: x=${targetX}, y=${targetY}`);
          await page.mouse.move(targetX, targetY, { steps: 25 });
          await page.waitForTimeout(300);
          await page.mouse.down();
          await page.waitForTimeout(150);
          await page.mouse.up();
          clicked = true;
          console.log('[Cloudflare] ✅ Clicked checkbox via Strategy B (Iframe Coordinates).');
        }
      } catch (errB) {}
    }

    console.log('[Cloudflare] ⏳ Waiting up to 35 seconds for verification & redirect to destination site...');

    // 4. Wait for clearance and redirect (up to 35 seconds)
    const waitStartTime = Date.now();
    while (Date.now() - waitStartTime < 35000) {
      await page.waitForTimeout(1000);

      // Check if redirect already occurred or challenge is completely gone
      const isStillInChallenge = await checkIsChallenge();
      if (!isStillInChallenge) {
        console.log(`[Cloudflare] 🎉 Successfully cleared Cloudflare! Destination loaded: "${await page.title().catch(() => '')}"`);
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
        await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(2000);
        return true;
      }

      // Check if it's currently showing "Success / Waiting for redirect"
      const isRedirectPending = await page.evaluate(() => {
        const t = (document.body ? document.body.innerText : '') || '';
        return t.includes('تایید موفقیت آمیز بود') || t.includes('در انتظار هدایت') || t.includes('Waiting to be redirected') || t.includes('Redirecting');
      }).catch(() => false);

      if (isRedirectPending) {
        console.log('[Cloudflare] ⏳ Verification succeeded! Actively waiting for destination navigation to complete...');
        // Wait for actual navigation
        await page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {});
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
        await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(2000);
        console.log(`[Cloudflare] 🎉 Navigation completed! Current page: "${await page.title().catch(() => '')}"`);
        return true;
      }
    }

    console.log('[Cloudflare] Finished challenge handling cycle.');
    return true;
  } catch (err) {
    console.warn('[Cloudflare] Error in handler:', err.message);
    return false;
  }
}

// Auto-scroll page smoothly to trigger lazy-loaded images (e.g. comic covers, movie posters, video thumbs)
async function autoScrollPage(page, delaySec = 0) {
  try {
    await page.evaluate(async (delay) => {
      await new Promise((resolve) => {
        let totalHeight = 0;
        const distance = 500;
        const maxScroll = Math.min(document.body.scrollHeight || 10000, 35000);
        
        // If delay is set, stretch scrolling across the delay period
        const totalSteps = Math.max(1, Math.ceil(maxScroll / distance));
        const stepInterval = delay > 0 ? Math.max(50, Math.floor((delay * 1000) / (totalSteps * 1.5))) : 50;

        const timer = setInterval(() => {
          const currentMax = Math.min(document.body.scrollHeight || 10000, 35000);
          window.scrollBy(0, distance);
          totalHeight += distance;

          // Dispatch scroll and resize events so IntersectionObservers and scroll listeners trigger
          window.dispatchEvent(new Event('scroll'));
          window.dispatchEvent(new Event('resize'));

          if (totalHeight >= currentMax) {
            clearInterval(timer);
            // Scroll back to top so view is positioned at start
            window.scrollTo({ top: 0, behavior: 'instant' });
            window.dispatchEvent(new Event('scroll'));
            resolve();
          }
        }, stepInterval);
      });
    }, delaySec);

    await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
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

    const page = await context.newPage();

    // Cache all loaded image buffers, CSS stylesheets, and web fonts to embed into offline HTML
    const imageMap = new Map();
    const cssMap = new Map();
    const fontMap = new Map();

    page.on('response', async (res) => {
      try {
        const url = res.url();
        const reqUrl = res.request() ? res.request().url() : url;
        const ct = (res.headers()['content-type'] || '').toLowerCase();
        const isImg =
          ct.startsWith('image/') ||
          /\.(png|jpe?g|webp|avif|gif|svg|bmp)(\?.*)?$/i.test(url) ||
          /\.(png|jpe?g|webp|avif|gif|svg|bmp)(\?.*)?$/i.test(reqUrl);

        if (isImg) {
          const buffer = await res.body();
          if (buffer && buffer.length > 0 && buffer.length <= 25 * 1024 * 1024) {
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

    // 0. FIRST STEP: Cloudflare Challenge / Turnstile Check & Solve
    console.log('[Runner] 🔍 Checking for Cloudflare challenge / Turnstile...');
    await handleCloudflareChallenge(page);

    finalUrl = page.url() || target_url;
    pageTitle = await page.title().catch(() => '');

    // If still on Cloudflare challenge screen, DO NOT SCROLL!
    const currentTitle = (pageTitle || '').toLowerCase();
    const isStillCloudflare =
      currentTitle.includes('just a moment') ||
      currentTitle.includes('checking your browser') ||
      currentTitle.includes('attention required') ||
      currentTitle.includes('security check') ||
      currentTitle.includes('ddos-guard') ||
      currentTitle.includes('چند لحظه صبر کنید') ||
      currentTitle.includes('یک لحظه صبر کنید') ||
      currentTitle.includes('در حال بررسی') ||
      currentTitle.includes('تایید امنیتی');

    if (!isStillCloudflare) {
      // Auto-scroll to trigger lazy loading of images (comic covers, thumbnails, etc.)
      const delaySec = parseInt(custom_delay_seconds || 0, 10);
      if (delaySec > 0) {
        console.log(`[Runner] ⏱️ Custom delay ${delaySec}s active. Actively scrolling & triggering lazy loaders for full duration...`);
        await autoScrollPage(page, delaySec);
      } else {
        await autoScrollPage(page, 0);
      }
    } else {
      console.log('[Runner] ⚠️ Page is on Cloudflare challenge screen. Skipping auto-scroll as requested.');
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

          // If already a real data URL (not a dummy placeholder), skip
          if (src.startsWith('data:') && !isDummySrc(src)) continue;

          let absSrc = src;
          try { absSrc = new URL(src, base).href; } catch (e) {}

          let dataUrl = imgLookup.get(absSrc) || imgLookup.get(src) || imgLookup.get(absSrc.split('?')[0]) || imgLookup.get(src.split('?')[0]);

          // Canvas fallback if loaded in DOM
          if (!dataUrl && img.complete && img.naturalWidth > 1) {
            try {
              const canvas = document.createElement('canvas');
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
              const ctx = canvas.getContext('2d');
              ctx.drawImage(img, 0, 0);
              dataUrl = canvas.toDataURL('image/jpeg', 0.88);
            } catch (e) {}
          }

          // In-page fetch fallback
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

  const imageExtRegex = /\.(jpe?g|png|webp|avif|gif|svg|bmp)(\?.*)?$/i;
  const imageLikeRegex = /(?:\/image|\/img|\/thumb|\/poster|\/cover|\/pics|\/avatar|\/videos|\/panel)/i;

  const candidateUrls = Array.from(urlsToFetch).filter(
    (u) => imageExtRegex.test(u) || imageLikeRegex.test(u)
  );

  if (candidateUrls.length > 0) {
    console.log(`[Sweeper] 🧹 Found ${candidateUrls.length} remaining images to fetch directly via Node.js...`);
    const CONCURRENCY = 10;
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
              signal: AbortSignal.timeout(10000)
            });
            if (res.ok) {
              const ct = (res.headers.get('content-type') || '').toLowerCase();
              let mime = ct.startsWith('image/') ? ct.split(';')[0].trim() : 'image/jpeg';
              if (mime.includes('octet-stream')) {
                if (imgUrl.includes('.png')) mime = 'image/png';
                else if (imgUrl.includes('.webp')) mime = 'image/webp';
                else if (imgUrl.includes('.svg')) mime = 'image/svg+xml';
                else if (imgUrl.includes('.gif')) mime = 'image/gif';
                else mime = 'image/jpeg';
              }
              const buf = Buffer.from(await res.arrayBuffer());
              if (buf.length > 0 && buf.length <= 25 * 1024 * 1024) {
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

main().catch((err) => {
  console.error('[Fatal Error]', err);
  process.exit(1);
});
