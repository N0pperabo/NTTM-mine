"""
HTML UI Templates for N0PPERABO offline browser.
Contains generators for the full multi-tab browser shell
and the OLED-black start page with Digital Flame animation.
"""

from typing import Any, Optional


def generate_shell_html() -> str:
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

    /* Custom Delay & Scroll Controls */
    .delay-container, .scroll-container {
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
    .delay-container:hover, .scroll-container:hover {
      border-color: #ffffff;
    }
    .delay-container.active, .scroll-container.active {
      background: #ffffff;
      border-color: #ffffff;
    }
    .delay-btn, .scroll-btn {
      display: flex;
      align-items: center;
      gap: 4px;
      cursor: pointer;
      font-size: 12px;
      user-select: none;
      color: #ffffff;
      font-weight: 600;
    }
    .delay-container.active .delay-btn, .scroll-container.active .scroll-btn {
      color: #000000;
    }
    .delay-input-box, .scroll-input-box {
      display: flex;
      align-items: center;
      gap: 4px;
      border-right: 1px solid #333333;
      padding-right: 6px;
      margin-right: 2px;
    }
    .delay-input-box input, .scroll-input-box input {
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

    /* Multi-Tab Bar */
    .tabs-bar {
      height: 38px;
      background: #000000;
      border-bottom: 1px solid #1c1c1c;
      display: flex;
      align-items: center;
      padding: 0 8px;
      user-select: none;
      gap: 6px;
      z-index: 95;
    }
    .tabs-list {
      display: flex;
      align-items: center;
      gap: 4px;
      flex: 1;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .tabs-list::-webkit-scrollbar { display: none; }
    .tab-item {
      height: 29px;
      min-width: 140px;
      max-width: 220px;
      background: #0d0d0d;
      border: 1px solid #242424;
      border-radius: 6px;
      display: flex;
      align-items: center;
      padding: 0 8px;
      gap: 8px;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .tab-item:hover {
      background: #141414;
      border-color: #383838;
    }
    .tab-item.active {
      background: #1c1c1c;
      border-color: #ffffff;
    }
    .tab-icon {
      width: 14px;
      height: 14px;
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #737373;
    }
    .tab-item.active .tab-icon { color: #ffffff; }
    .tab-title {
      flex: 1;
      font-size: 12px;
      font-weight: 500;
      color: #a3a3a3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      direction: ltr;
      text-align: right;
    }
    .tab-item.active .tab-title {
      color: #ffffff;
      font-weight: 600;
    }
    .tab-spinner {
      width: 11px;
      height: 11px;
      border: 1.5px solid rgba(255, 255, 255, 0.2);
      border-top-color: #ffffff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      flex-shrink: 0;
    }
    .tab-close {
      width: 18px;
      height: 18px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: transparent;
      border: none;
      color: #737373;
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      transition: background 0.15s, color 0.15s;
    }
    .tab-close:hover {
      background: rgba(239, 68, 68, 0.2);
      color: #ef4444;
    }
    .btn-new-tab {
      width: 28px;
      height: 28px;
      border-radius: 6px;
      background: #0d0d0d;
      border: 1px solid #242424;
      color: #a3a3a3;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: background 0.15s, color 0.15s, border-color 0.15s;
      flex-shrink: 0;
    }
    .btn-new-tab:hover {
      background: #181818;
      color: #ffffff;
      border-color: #ffffff;
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

    /* Storage DB (History & Bookmarks) Styles */
    .storage-tabs {
      display: flex;
      gap: 8px;
      border-bottom: 1px solid #262626;
      margin-bottom: 14px;
      padding-bottom: 8px;
    }
    .storage-tab-btn {
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 600;
      padding: 6px 14px;
      cursor: pointer;
      border-radius: 6px;
      transition: all 0.15s;
    }
    .storage-tab-btn.active {
      color: #ffffff;
      background: #1a1a1a;
      border-color: #333333;
    }
    .storage-search {
      width: 100%;
      background: #000000;
      border: 1px solid #333333;
      color: #ffffff;
      padding: 9px 12px;
      border-radius: 6px;
      font-size: 13px;
      margin-bottom: 12px;
      outline: none;
      box-sizing: border-box;
    }
    .storage-search:focus {
      border-color: #ffffff;
    }
    .storage-list {
      max-height: 320px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding-right: 2px;
    }
    .storage-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px;
      background: #080808;
      border: 1px solid #222222;
      border-radius: 6px;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .storage-item:hover {
      background: #161616;
      border-color: #444444;
    }
    .storage-item-info {
      display: flex;
      flex-direction: column;
      gap: 3px;
      overflow: hidden;
      flex: 1;
    }
    .storage-item-title {
      font-size: 13px;
      font-weight: 600;
      color: #ffffff;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .storage-item-url {
      font-size: 11px;
      color: var(--text-muted);
      direction: ltr;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-align: right;
    }
    .storage-item-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-right: 12px;
      flex-shrink: 0;
    }
    .storage-badge {
      font-size: 10px;
      padding: 2px 7px;
      border-radius: 10px;
      background: #1f2937;
      color: #9ca3af;
      font-weight: 600;
    }
    .storage-del-btn {
      background: transparent;
      border: none;
      color: #ef4444;
      cursor: pointer;
      font-size: 14px;
      padding: 2px 6px;
      border-radius: 4px;
      line-height: 1;
    }
    .storage-del-btn:hover {
      background: rgba(239, 68, 68, 0.15);
    }
  </style>
</head>
<body>

  <!-- Top Tabs Bar -->
  <div class="tabs-bar">
    <div class="tabs-list" id="tabs-list"></div>
    <button class="btn-new-tab" onclick="window.shellApp.createNewTab()" title="تب جدید (Ctrl+T)">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
    </button>
  </div>

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

      <div class="scroll-container" id="scroll-box">
        <div class="scroll-input-box">
          <input type="number" id="scroll-val" min="1" max="20" value="3" title="تعداد اسکرول صفحه (هر واحد = 1 اسکرول صفحه)" onchange="window.shellApp.updateCustomScroll()" />
        </div>
        <div class="scroll-btn" id="scroll-btn" onclick="window.shellApp.toggleCustomScroll()" title="فعال/غیرفعال‌سازی اسکرول برای درخواست بعدی">
          <span>اسکرول</span>
        </div>
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

      <button class="btn-action-dark" onclick="window.shellApp.openStorageModal()" title="تاریخچه مرور و نشان‌ها (SQLite)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>تاریخچه</span>
      </button>

      <button class="btn-action-dark" id="btn-toggle-bookmark" onclick="window.shellApp.toggleCurrentBookmark()" title="نشان کردن این صفحه">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
        <span>نشان</span>
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
    <div id="frames-container" style="width:100%; height:100%; position:relative;"></div>
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

  <!-- Storage DB (History & Bookmarks) Modal -->
  <div class="modal-overlay" id="storage-modal">
    <div class="modal" style="width: 600px; max-width: 95vw;">
      <h2>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>پایگاه داده ذخیره‌سازی NTTM (SQLite)</span>
      </h2>
      <div class="storage-tabs">
        <button class="storage-tab-btn active" id="tab-btn-history" onclick="window.shellApp.switchStorageTab('history')">تاریخچه مرور (History)</button>
        <button class="storage-tab-btn" id="tab-btn-bookmarks" onclick="window.shellApp.switchStorageTab('bookmarks')">نشان‌شده‌ها (Bookmarks)</button>
      </div>
      <input type="text" class="storage-search" id="storage-search-input" placeholder="جستجو در آدرس یا عنوان..." oninput="window.shellApp.onStorageSearch(this.value)" />
      <div class="storage-list" id="storage-items-container">
        <!-- Rendered by JS -->
      </div>
      <div class="modal-actions" style="justify-content: space-between; align-items: center; margin-top: 16px;">
        <button class="btn-modal-cancel" style="color: #ef4444; border-color: #552222;" id="btn-storage-clear" onclick="window.shellApp.clearCurrentStorage()">پاکسازی تاریخچه</button>
        <button class="btn-modal-save" onclick="window.shellApp.closeStorageModal()">بستن</button>
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
      isCustomScrollActive: false,
      currentLiveUrl: '',
      queue: [],
      tabs: [],
      activeTabId: null,
      tabCounter: 0,

      init: function() {
        const urlInput = document.getElementById('url-input');
        urlInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') this.navigateFromBar();
        });

        // Global Keyboard shortcuts
        window.addEventListener('keydown', (e) => {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 't') {
            e.preventDefault();
            this.createNewTab();
          } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'w') {
            e.preventDefault();
            this.closeTab(this.activeTabId);
          }
        });

        // Start with Tab 1
        this.createNewTab('', false);
      },

      createNewTab: function(url = '', autoNavigate = false) {
        this.tabCounter++;
        const tabId = 'tab_' + this.tabCounter + '_' + Math.random().toString(36).substr(2, 5);
        const tab = {
          id: tabId,
          title: 'صفحه اصلی NTTM',
          url: url || '',
          frameSrc: url ? '' : '/start',
          history: [],
          historyIndex: -1,
          isLoading: false
        };
        this.tabs.push(tab);

        // Create dedicated iframe for this tab
        const container = document.getElementById('frames-container');
        const frame = document.createElement('iframe');
        frame.id = 'frame-' + tabId;
        frame.className = 'tab-frame';
        frame.style.width = '100%';
        frame.style.height = '100%';
        frame.style.border = 'none';
        frame.style.display = 'none';
        frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-same-origin allow-popups');
        frame.src = tab.frameSrc;

        frame.addEventListener('load', () => {
          const curTab = this.getTab(tabId);
          if (curTab) {
            curTab.isLoading = false;
            try {
              const loc = frame.contentWindow.location.href;
              if (loc && !loc.includes('/start')) {
                curTab.url = curTab.url || loc;
              }
            } catch(e) {}
          }
          if (this.activeTabId === tabId) {
            this.hideLoading();
            this.syncActiveTabUI();
          }
          this.renderTabs();
          this.injectDeepInterceptor(frame);
        });

        container.appendChild(frame);
        this.renderTabs();
        this.switchTab(tabId);

        if (url && autoNavigate) {
          this.navigate(url, undefined, tabId);
        }
        return tabId;
      },

      closeTab: function(tabId, e) {
        if (e) e.stopPropagation();
        if (this.tabs.length <= 1) {
          // Reset the only tab to start home
          const tab = this.tabs[0];
          tab.title = 'صفحه اصلی NTTM';
          tab.url = '';
          tab.frameSrc = '/start';
          const frame = document.getElementById('frame-' + tab.id);
          if (frame) frame.src = '/start';
          this.syncActiveTabUI();
          this.renderTabs();
          return;
        }

        const idx = this.tabs.findIndex(t => t.id === tabId);
        if (idx === -1) return;

        const frame = document.getElementById('frame-' + tabId);
        if (frame && frame.parentNode) {
          frame.parentNode.removeChild(frame);
        }

        this.tabs.splice(idx, 1);

        if (this.activeTabId === tabId) {
          const nextTab = this.tabs[Math.max(0, idx - 1)];
          this.switchTab(nextTab.id);
        } else {
          this.renderTabs();
        }
      },

      switchTab: function(tabId) {
        const tab = this.getTab(tabId);
        if (!tab) return;
        this.activeTabId = tabId;

        document.querySelectorAll('.tab-frame').forEach(f => {
          f.style.display = (f.id === 'frame-' + tabId) ? 'block' : 'none';
        });

        this.syncActiveTabUI();
        this.renderTabs();
      },

      getTab: function(id) {
        return this.tabs.find(t => t.id === id);
      },

      getActiveTab: function() {
        return this.getTab(this.activeTabId) || this.tabs[0] || null;
      },

      renderTabs: function() {
        const container = document.getElementById('tabs-list');
        if (!container) return;
        container.innerHTML = '';

        this.tabs.forEach((tab) => {
          const isActive = tab.id === this.activeTabId;
          const el = document.createElement('div');
          el.className = 'tab-item' + (isActive ? ' active' : '');
          el.onclick = () => this.switchTab(tab.id);

          const iconHtml = tab.isLoading
            ? '<div class="tab-spinner"></div>'
            : '<div class="tab-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg></div>';

          const titleText = tab.title || (tab.url ? tab.url.replace(/^https?:\/\//, '') : 'تب جدید');

          el.innerHTML = `
            ${iconHtml}
            <span class="tab-title" title="${tab.url || tab.title}">${titleText}</span>
            <button class="tab-close" onclick="window.shellApp.closeTab('${tab.id}', event)" title="بستن تب (Ctrl+W)">&times;</button>
          `;
          container.appendChild(el);
        });
      },

      syncActiveTabUI: function() {
        const tab = this.getActiveTab();
        if (!tab) return;

        this.currentLiveUrl = tab.url || '';
        const urlInput = document.getElementById('url-input');
        if (urlInput) {
          urlInput.value = tab.url || '';
        }
        document.title = (tab.title ? tab.title + ' • ' : '') + 'N0PPERABO NTTM';
        this.updateShortsButton(tab.url || '');
        if (tab.isLoading) {
          this.showLoading();
        } else {
          this.hideLoading();
        }
      },

      onPageLoaded: function(localUrl, targetUrl, title, tabId) {
        let tab = tabId ? this.getTab(tabId) : this.getActiveTab();
        if (!tab) tab = this.getActiveTab();
        if (!tab) return;

        tab.isLoading = false;
        tab.currentLiveUrl = targetUrl || '';
        tab.url = targetUrl || '';
        tab.title = title || targetUrl || 'صفحه آفلاین';
        tab.frameSrc = localUrl;

        this.isCustomScrollActive = false;
        document.getElementById('scroll-box')?.classList.remove('active');

        // History recording
        if (tab.historyIndex < tab.history.length - 1) {
          tab.history = tab.history.slice(0, tab.historyIndex + 1);
        }
        tab.history.push({ url: targetUrl, local_url: localUrl, title: tab.title });
        tab.historyIndex = tab.history.length - 1;

        const frame = document.getElementById('frame-' + tab.id);
        if (frame) {
          frame.src = localUrl;
        }

        if (tab.id === this.activeTabId) {
          this.hideLoading();
          this.syncActiveTabUI();
        }
        this.renderTabs();
      },

      onFileDownloaded: function(filename, tabId) {
        let tab = tabId ? this.getTab(tabId) : this.getActiveTab();
        if (tab) tab.isLoading = false;
        if (!tab || tab.id === this.activeTabId) {
          this.hideLoading();
        }
        this.renderTabs();
        this.showToast('فایل با موفقیت دانلود و ذخیره شد: ' + filename, false, true);
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

      showLoading: function(msg) {
        const overlay = document.getElementById('loading-overlay');
        const text = document.getElementById('loading-text');
        if (msg) text.textContent = msg;
        overlay.classList.add('active');
      },

      hideLoading: function() {
        document.getElementById('loading-overlay').classList.remove('active');
        this.setStatus('آماده', 'ready');
      },

      setStatus: function(text, state) {
        const el = document.getElementById('status-text');
        if (el) {
          el.innerHTML = '<span class="status-' + (state || 'ready') + '">' + text + '</span>';
        }
      },

      showToast: function(msg, isError, isFile) {
        const existing = document.querySelector('.toast-notify');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = 'toast-notify' + (isError ? ' error' : '');
        toast.innerHTML = '<span>' + msg + '</span>';

        if (isFile) {
          const btn = document.createElement('button');
          btn.textContent = 'باز کردن پوشه دانلود';
          btn.onclick = () => {
            if (window.pywebview && window.pywebview.api) {
              window.pywebview.api.open_downloads_folder();
            }
          };
          toast.appendChild(btn);
        }

        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 4500);
      },

      navigate: function(url, customDelay, tabId) {
        if (!url) return;
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
          url = 'https://' + url;
        }
        let tab = tabId ? this.getTab(tabId) : this.getActiveTab();
        if (!tab) tab = this.getActiveTab();
        if (!tab) return;

        const delay = customDelay !== undefined ? customDelay : (this.isCustomDelayActive ? parseInt(document.getElementById('delay-val')?.value || '5', 10) : 0);
        const scroll = this.isCustomScrollActive ? parseInt(document.getElementById('scroll-val')?.value || '3', 10) : 0;
        tab.url = url;
        tab.isLoading = true;
        tab.title = url.replace(/^https?:\/\//, '');

        if (tab.id === this.activeTabId) {
          document.getElementById('url-input').value = url;
          this.showLoading('در حال ارسال درخواست به گیت‌هاب...');
          this.setStatus('در حال ارسال درخواست به گیت‌هاب...', 'loading');
        }
        this.renderTabs();

        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.navigate(url, delay, tab.id, scroll);
        }
      },

      navigateFromBar: function() {
        const urlInput = document.getElementById('url-input');
        const val = urlInput.value.trim();
        if (!val) return;
        if (val.startsWith('http://') || val.startsWith('https://')) {
          this.navigate(val);
        } else if (val.includes('.') && !val.includes(' ')) {
          this.navigate('https://' + val);
        } else {
          this.navigate('https://www.google.com/search?q=' + encodeURIComponent(val));
        }
      },

      goBack: function() {
        const tab = this.getActiveTab();
        if (!tab || tab.historyIndex <= 0) return;
        tab.historyIndex--;
        const item = tab.history[tab.historyIndex];
        tab.url = item.url;
        tab.title = item.title;
        tab.frameSrc = item.local_url;
        const frame = document.getElementById('frame-' + tab.id);
        if (frame) frame.src = item.local_url;
        this.syncActiveTabUI();
        this.renderTabs();
      },

      goForward: function() {
        const tab = this.getActiveTab();
        if (!tab || tab.historyIndex >= tab.history.length - 1) return;
        tab.historyIndex++;
        const item = tab.history[tab.historyIndex];
        tab.url = item.url;
        tab.title = item.title;
        tab.frameSrc = item.local_url;
        const frame = document.getElementById('frame-' + tab.id);
        if (frame) frame.src = item.local_url;
        this.syncActiveTabUI();
        this.renderTabs();
      },

      reload: function() {
        const tab = this.getActiveTab();
        if (!tab) return;
        if (tab.url && !tab.url.includes('/start')) {
          this.navigate(tab.url, undefined, tab.id);
        } else {
          this.goHome();
        }
      },

      goHome: function() {
        const tab = this.getActiveTab();
        if (!tab) return;
        tab.url = '';
        tab.title = 'صفحه اصلی NTTM';
        tab.frameSrc = '/start';
        const frame = document.getElementById('frame-' + tab.id);
        if (frame) frame.src = '/start';
        this.syncActiveTabUI();
        this.renderTabs();
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
        const tab = this.getActiveTab();
        if (!tab) return;
        tab.isLoading = true;
        this.showLoading('در حال دریافت و اسکرول به شورتز بعدی...');
        this.renderTabs();
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.navigate_shorts_next('', tab.id);
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
          text.textContent = 'مد دسته‌ای (فعال)';
          sendBtn.style.display = 'flex';
          qBar.classList.add('active');
          this.showToast('مد دسته‌ای فعال شد: کلیک‌ها و تایپ‌ها در صف ضبط می‌شوند.');
          document.querySelectorAll('.tab-frame').forEach(f => this.injectDeepInterceptor(f));
        } else {
          toggle.classList.remove('batch');
          text.textContent = 'مد مستقیم';
          sendBtn.style.display = 'none';
          qBar.classList.remove('active');
          this.showToast('مد مستقیم فعال شد: اقدامات بلافاصله ارسال می‌شوند.');
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

      toggleCustomScroll: function() {
        this.isCustomScrollActive = !this.isCustomScrollActive;
        const container = document.getElementById('scroll-box');
        if (container) {
          if (this.isCustomScrollActive) {
            container.classList.add('active');
          } else {
            container.classList.remove('active');
          }
        }
        this.updateCustomScroll();
      },

      updateCustomScroll: function() {
        const valInput = document.getElementById('scroll-val');
        let count = parseInt(valInput ? valInput.value : '3', 10);
        if (isNaN(count) || count < 1) count = 1;
        if (count > 20) count = 20;
        if (valInput) valInput.value = count;

        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.set_custom_scroll(this.isCustomScrollActive ? count : 0);
        }
      },

      queueAction: function(action) {
        if (action.type === 'fill') {
          const last = this.queue[this.queue.length - 1];
          if (last && last.type === 'fill' && last.selector === action.selector) {
            last.value = action.value;
            this.renderQueue();
            return;
          }
        }
        this.queue.push(action);
        this.renderQueue();
      },

      removeQueueItem: function(idx) {
        if (idx >= 0 && idx < this.queue.length) {
          this.queue.splice(idx, 1);
          this.renderQueue();
        }
      },

      renderQueue: function() {
        const container = document.getElementById('queue-items');
        if (!container) return;
        container.innerHTML = '';
        if (this.queue.length === 0) {
          container.innerHTML = '<span style="color:#737373; font-size:11px;">هیچ اقدامی ثبت نشده است. روی صفحه کلیک یا تایپ کنید.</span>';
          return;
        }
        this.queue.forEach((item, idx) => {
          const div = document.createElement('div');
          div.className = 'queue-item';

          let typeColor = '#3b82f6';
          let typeText = item.type.toUpperCase();
          let summary = item.text_hint || item.selector || item.key || item.value || '';
          if (item.type === 'fill') {
            typeColor = '#06b6d4';
            summary = (item.name ? item.name + '=' : '') + '"' + (item.value || '') + '"';
          } else if (item.type === 'click') {
            typeColor = '#10b981';
            summary = item.text_hint ? ('"' + item.text_hint + '"') : (item.selector || 'element');
          } else if (item.type === 'press') {
            typeColor = '#a855f7';
            summary = 'کلید: ' + (item.key || 'Enter');
          } else if (item.type === 'select') {
            typeColor = '#f59e0b';
            summary = 'انتخاب: ' + (item.value || '');
          } else if (item.type === 'check') {
            typeColor = '#eab308';
            summary = (item.checked ? '✓ انتخاب' : '✗ عدم انتخاب') + ' ' + (item.selector || '');
          } else if (item.type === 'submit') {
            typeColor = '#ef4444';
            summary = 'ارسال فرم: ' + (item.selector || '');
          }

          div.innerHTML = '<span style="background:' + typeColor + '; color:#000000; font-weight:800; padding:1px 5px; border-radius:3px; font-size:10px;">' + typeText + '</span> ' +
            '<span style="color:#ffffff; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + (item.selector || '') + '">' + summary + '</span> ' +
            '<button onclick="window.shellApp.removeQueueItem(' + idx + ')" style="background:transparent; border:none; color:#ef4444; font-size:13px; font-weight:700; cursor:pointer; padding:0 3px; line-height:1;" title="حذف این اقدام">&times;</button>';
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
        if (!this.queue || this.queue.length === 0) {
          this.showToast('صف اقدامات خالی است. ابتدا روی صفحه کلیک یا تایپ کنید.', true);
          return;
        }
        const tab = this.getActiveTab();
        const targetUrl = (tab && tab.url) || this.currentLiveUrl || document.getElementById('url-input').value;
        if (!targetUrl || targetUrl.includes('/start') || targetUrl === 'about:blank') {
          this.showToast('آدرس صفحه مشخص نیست. لطفاً ابتدا به یک صفحه وب بروید.', true);
          return;
        }
        const delay = this.isCustomDelayActive ? parseInt(document.getElementById('delay-val').value, 10) || 5 : 0;
        const scroll = this.isCustomScrollActive ? parseInt(document.getElementById('scroll-val')?.value || '3', 10) : 0;
        this.showLoading('در حال ارسال ' + this.queue.length + ' اقدام زنجیره‌ای به گیت‌هاب...');
        const actionsToSend = [...this.queue];
        this.clearQueue();
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.run_batch_actions(targetUrl, actionsToSend, delay, tab ? tab.id : '', scroll);
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

      _currentStorageTab: 'history',

      openStorageModal: function() {
        this._currentStorageTab = 'history';
        document.getElementById('storage-modal').classList.add('active');
        document.getElementById('tab-btn-history').classList.add('active');
        document.getElementById('tab-btn-bookmarks').classList.remove('active');
        document.getElementById('storage-search-input').value = '';
        document.getElementById('btn-storage-clear').style.display = 'inline-block';
        this.loadStorageItems();
      },

      closeStorageModal: function() {
        document.getElementById('storage-modal').classList.remove('active');
      },

      switchStorageTab: function(tab) {
        this._currentStorageTab = tab;
        if (tab === 'history') {
          document.getElementById('tab-btn-history').classList.add('active');
          document.getElementById('tab-btn-bookmarks').classList.remove('active');
          document.getElementById('btn-storage-clear').style.display = 'inline-block';
        } else {
          document.getElementById('tab-btn-history').classList.remove('active');
          document.getElementById('tab-btn-bookmarks').classList.add('active');
          document.getElementById('btn-storage-clear').style.display = 'none';
        }
        document.getElementById('storage-search-input').value = '';
        this.loadStorageItems();
      },

      onStorageSearch: function(query) {
        if (!query.trim()) {
          this.loadStorageItems();
          return;
        }
        if (this._currentStorageTab === 'history') {
          if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.search_history(query, 30).then(items => {
              this.renderStorageList(items, 'history');
            });
          }
        } else {
          if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.get_bookmarks().then(items => {
              const q = query.toLowerCase();
              const filtered = (items || []).filter(b => (b.url || '').toLowerCase().includes(q) || (b.title || '').toLowerCase().includes(q));
              this.renderStorageList(filtered, 'bookmarks');
            });
          }
        }
      },

      loadStorageItems: function() {
        if (!window.pywebview || !window.pywebview.api) return;
        if (this._currentStorageTab === 'history') {
          window.pywebview.api.get_history(60).then(items => {
            this.renderStorageList(items, 'history');
          });
        } else {
          window.pywebview.api.get_bookmarks().then(items => {
            this.renderStorageList(items, 'bookmarks');
          });
        }
      },

      renderStorageList: function(items, type) {
        const container = document.getElementById('storage-items-container');
        if (!items || items.length === 0) {
          container.innerHTML = '<div style="padding: 24px; text-align: center; color: #666; font-size: 13px;">موردی یافت نشد.</div>';
          return;
        }
        let html = '';
        for (const item of items) {
          const title = item.title || item.url || 'بدون عنوان';
          const url = item.url || '';
          const visits = item.visit_count ? '<span class="storage-badge">' + item.visit_count + ' بازدید</span>' : '';
          const delAction = type === 'bookmarks'
            ? '<button class="storage-del-btn" onclick="event.stopPropagation(); window.shellApp.deleteBookmark(\'' + encodeURIComponent(url) + '\')" title="حذف نشان">&times;</button>'
            : '';

          html += '<div class="storage-item" onclick="window.shellApp.navigateFromStorage(\'' + encodeURIComponent(url) + '\')">';
          html += '  <div class="storage-item-info">';
          html += '    <div class="storage-item-title">' + this._escapeHtml(title) + '</div>';
          html += '    <div class="storage-item-url">' + this._escapeHtml(url) + '</div>';
          html += '  </div>';
          html += '  <div class="storage-item-meta">';
          html += visits;
          html += delAction;
          html += '  </div>';
          html += '</div>';
        }
        container.innerHTML = html;
      },

      _escapeHtml: function(str) {
        return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      },

      navigateFromStorage: function(encodedUrl) {
        const url = decodeURIComponent(encodedUrl);
        if (url) {
          this.closeStorageModal();
          this.navigate(url);
        }
      },

      deleteBookmark: function(encodedUrl) {
        const url = decodeURIComponent(encodedUrl);
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.remove_bookmark(url).then(() => {
            this.loadStorageItems();
            this.showToast('نشان حذف شد');
          });
        }
      },

      toggleCurrentBookmark: function() {
        const tab = this.getActiveTab();
        const activeUrl = (tab && tab.url) ? tab.url : (document.getElementById('url-input') ? document.getElementById('url-input').value : '');
        if (!activeUrl || activeUrl.startsWith('about:') || activeUrl === '/start') {
          this.showToast('آدرس نامعتبر است', true);
          return;
        }
        const activeTitle = (tab && tab.title) ? tab.title : activeUrl;
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.is_bookmarked(activeUrl).then(res => {
            if (res && res.bookmarked) {
              window.pywebview.api.remove_bookmark(activeUrl).then(() => {
                this.showToast('نشان حذف شد');
              });
            } else {
              window.pywebview.api.add_bookmark(activeUrl, activeTitle, 'default').then(() => {
                this.showToast('نشان اضافه شد ⭐');
              });
            }
          });
        }
      },

      clearCurrentStorage: function() {
        if (this._currentStorageTab === 'history') {
          if (confirm('آیا از پاکسازی کامل تاریخچه مطمئن هستید؟')) {
            if (window.pywebview && window.pywebview.api) {
              window.pywebview.api.clear_history().then(() => {
                this.loadStorageItems();
                this.showToast('تاریخچه پاکسازی شد');
              });
            }
          }
        }
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

      injectDeepInterceptor: function(targetFrame) {
        const frame = targetFrame || document.getElementById('frame-' + this.activeTabId);
        if (!frame) return;
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

          // Visual feedback styles for captured elements
          const style = doc.createElement('style');
          style.textContent = `
            @keyframes nttmCapturedFlash {
              0% { outline: 3px solid #ffffff !important; box-shadow: 0 0 14px rgba(255,255,255,0.9) !important; }
              100% { outline: 3px solid transparent !important; box-shadow: none !important; }
            }
            .nttm-captured-flash {
              animation: nttmCapturedFlash 0.75s ease-out !important;
            }
          `;
          doc.head.appendChild(style);

          const script = doc.createElement('script');
          script.textContent = `
            (function() {
              function flashElement(el) {
                if (!el || !el.classList) return;
                el.classList.remove('nttm-captured-flash');
                void el.offsetWidth;
                el.classList.add('nttm-captured-flash');
                setTimeout(() => el.classList.remove('nttm-captured-flash'), 800);
              }

              function getElementInfo(el) {
                if (!el || el === document.body || el === document.documentElement) {
                  return { selector: 'body', xpath: '//body' };
                }

                const rect = el.getBoundingClientRect();
                const x = Math.round(rect.left + rect.width / 2);
                const y = Math.round(rect.top + rect.height / 2);
                const textHint = (el.innerText || el.value || el.title || el.getAttribute('aria-label') || '').trim().slice(0, 50);

                // 1. Unique ID
                if (el.id && !/^\d/.test(el.id)) {
                  try {
                    if (document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
                      return {
                        selector: '#' + CSS.escape(el.id),
                        xpath: '//*[@id="' + el.id + '"]',
                        x: x, y: y, textHint: textHint
                      };
                    }
                  } catch(e) {}
                }

                // 2. Unique name attribute
                if (el.name && el.tagName) {
                  const tag = el.tagName.toLowerCase();
                  const sel = tag + '[name="' + CSS.escape(el.name) + '"]';
                  try {
                    if (document.querySelectorAll(sel).length === 1) {
                      return {
                        selector: sel,
                        xpath: '//' + tag + '[@name="' + el.name + '"]',
                        x: x, y: y, textHint: textHint, name: el.name
                      };
                    }
                  } catch(e) {}
                }

                // 3. Unique attributes
                const tag = el.tagName.toLowerCase();
                for (const attr of ['data-testid', 'aria-label', 'placeholder', 'title', 'role']) {
                  const val = el.getAttribute(attr);
                  if (val && val.length < 80) {
                    try {
                      const sel = tag + '[' + attr + '="' + CSS.escape(val) + '"]';
                      if (document.querySelectorAll(sel).length === 1) {
                        return {
                          selector: sel,
                          xpath: '//' + tag + '[@' + attr + '="' + val + '"]',
                          x: x, y: y, textHint: textHint, placeholder: (attr === 'placeholder' ? val : '')
                        };
                      }
                    } catch(e) {}
                  }
                }

                // 4. Hierarchical CSS path
                function getCssPath(curr) {
                  const parts = [];
                  let node = curr;
                  while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.body && parts.length < 5) {
                    let part = node.tagName.toLowerCase();
                    if (node.id && !/^\d/.test(node.id)) {
                      part += '#' + CSS.escape(node.id);
                      parts.unshift(part);
                      break;
                    }
                    let sibling = node;
                    let nth = 1;
                    while (sibling = sibling.previousElementSibling) {
                      if (sibling.tagName === node.tagName) nth++;
                    }
                    part += ':nth-of-type(' + nth + ')';
                    parts.unshift(part);
                    node = node.parentElement;
                  }
                  return parts.join(' > ');
                }

                // 5. XPath
                function getXPath(curr) {
                  if (curr.id) return '//*[@id="' + curr.id + '"]';
                  const segments = [];
                  let node = curr;
                  for (; node && node.nodeType === Node.ELEMENT_NODE && node !== document.documentElement; node = node.parentNode) {
                    let count = 1;
                    for (let sib = node.previousSibling; sib; sib = sib.previousSibling) {
                      if (sib.nodeType === 1 && sib.tagName === node.tagName) count++;
                    }
                    segments.unshift(node.tagName.toLowerCase() + '[' + count + ']');
                  }
                  return '/' + segments.join('/');
                }

                return {
                  selector: getCssPath(el),
                  xpath: getXPath(el),
                  x: x, y: y, textHint: textHint
                };
              }

              // --- 1. CLICK Interception ---
              document.addEventListener('click', function(e) {
                const target = e.target.closest('a') || e.target.closest('button') || e.target.closest('input[type="submit"]') || e.target.closest('input[type="button"]') || e.target.closest('[role="button"]') || e.target;
                if (!target) return;

                const link = target.closest('a');
                const href = link ? link.getAttribute('href') : null;
                const fullHref = (href && !href.startsWith('javascript:') && !href.startsWith('#')) ? new URL(href, window.location.href).href : '';

                // Middle-click or Ctrl+Click on link: open in new tab!
                if (link && fullHref && (e.button === 1 || e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  e.stopPropagation();
                  window.parent.shellApp.createNewTab(fullHref, true);
                  return false;
                }

                const isBatch = window.parent && window.parent.shellApp && window.parent.shellApp.isBatchMode;
                const tag = target.tagName.toLowerCase();
                const isInput = tag === 'input' && !['submit', 'button', 'checkbox', 'radio'].includes(target.type);
                const isTextarea = tag === 'textarea';

                // In Batch Mode:
                if (isBatch) {
                  if (isInput || isTextarea) return;

                  if (tag === 'input' && ['checkbox', 'radio'].includes(target.type)) {
                    setTimeout(() => {
                      const info = getElementInfo(target);
                      window.parent.shellApp.queueAction({
                        type: 'check',
                        selector: info.selector,
                        xpath: info.xpath,
                        checked: target.checked
                      });
                      flashElement(target);
                    }, 40);
                    return;
                  }

                  e.preventDefault();
                  e.stopPropagation();

                  const info = getElementInfo(target);
                  flashElement(target);

                  window.parent.shellApp.queueAction({
                    type: 'click',
                    selector: info.selector,
                    xpath: info.xpath,
                    text_hint: info.textHint,
                    href: fullHref,
                    x: info.x,
                    y: info.y
                  });
                  return false;
                }

                // In Direct Mode:
                if (link && fullHref) {
                  e.preventDefault();
                  e.stopPropagation();
                  window.parent.shellApp.navigate(fullHref);
                  return false;
                }
              }, true);

              // Middle click mouseup prevent default
              document.addEventListener('auxclick', function(e) {
                if (e.button === 1) {
                  const link = e.target.closest('a');
                  if (link && link.getAttribute('href')) {
                    e.preventDefault();
                    e.stopPropagation();
                    const fullHref = new URL(link.getAttribute('href'), window.location.href).href;
                    window.parent.shellApp.createNewTab(fullHref, true);
                  }
                }
              }, true);

              // --- 2. INPUT & CHANGE Interception ---
              function handleInput(e) {
                const isBatch = window.parent && window.parent.shellApp && window.parent.shellApp.isBatchMode;
                if (!isBatch) return;

                const target = e.target;
                if (!target || !target.tagName) return;
                const tag = target.tagName.toLowerCase();

                if (tag === 'input' || tag === 'textarea') {
                  if (['submit', 'button', 'checkbox', 'radio'].includes(target.type)) return;
                  const info = getElementInfo(target);
                  window.parent.shellApp.queueAction({
                    type: 'fill',
                    selector: info.selector,
                    xpath: info.xpath,
                    value: target.value,
                    name: target.name || target.id || '',
                    placeholder: target.placeholder || '',
                    text_hint: info.textHint,
                    x: info.x,
                    y: info.y
                  });
                } else if (tag === 'select') {
                  const info = getElementInfo(target);
                  window.parent.shellApp.queueAction({
                    type: 'select',
                    selector: info.selector,
                    xpath: info.xpath,
                    value: target.value,
                    text_hint: target.selectedOptions && target.selectedOptions[0] ? target.selectedOptions[0].text : target.value
                  });
                  flashElement(target);
                }
              }

              document.addEventListener('input', handleInput, true);
              document.addEventListener('change', handleInput, true);

              // --- 3. KEYDOWN Interception (Enter on forms / Shorts navigation) ---
              let isShortsScrolling = false;
              document.addEventListener('keydown', function(e) {
                const curUrl = (window.parent && window.parent.shellApp && window.parent.shellApp.getActiveTab && (window.parent.shellApp.getActiveTab().url || '')) || window.location.href;
                const isShorts = curUrl.includes('/shorts');

                // YouTube Shorts ArrowDown or PageDown key
                if (isShorts && (e.key === 'ArrowDown' || e.key === 'PageDown')) {
                  e.preventDefault();
                  if (!isShortsScrolling) {
                    isShortsScrolling = true;
                    setTimeout(() => { isShortsScrolling = false; }, 3000);
                    window.parent.shellApp.triggerShortsNext();
                  }
                  return false;
                }

                const isBatch = window.parent && window.parent.shellApp && window.parent.shellApp.isBatchMode;
                if (!isBatch) return;

                if (e.key === 'Enter') {
                  const target = e.target;
                  if (target && (target.tagName.toLowerCase() === 'input' || target.tagName.toLowerCase() === 'textarea')) {
                    e.preventDefault();
                    e.stopPropagation();

                    const info = getElementInfo(target);
                    window.parent.shellApp.queueAction({
                      type: 'fill',
                      selector: info.selector,
                      xpath: info.xpath,
                      value: target.value,
                      name: target.name || target.id || '',
                      x: info.x,
                      y: info.y
                    });

                    window.parent.shellApp.queueAction({
                      type: 'press',
                      key: 'Enter',
                      selector: info.selector,
                      xpath: info.xpath,
                      x: info.x,
                      y: info.y
                    });
                    flashElement(target);
                    return false;
                  }
                }
              }, true);

              // --- 4. YOUTUBE SHORTS WHEEL SCROLL Interception ---
              window.addEventListener('wheel', function(e) {
                const curUrl = (window.parent && window.parent.shellApp && window.parent.shellApp.getActiveTab && (window.parent.shellApp.getActiveTab().url || '')) || window.location.href;
                const isShorts = curUrl.includes('/shorts');
                if (!isShorts) return;

                // When user scrolls down on Shorts
                if (e.deltaY > 35 && !isShortsScrolling) {
                  isShortsScrolling = true;
                  setTimeout(() => { isShortsScrolling = false; }, 3000);
                  window.parent.shellApp.triggerShortsNext();
                }
              }, { passive: true });

              // --- 5. SUBMIT Interception ---
              document.addEventListener('submit', function(e) {
                const isBatch = window.parent && window.parent.shellApp && window.parent.shellApp.isBatchMode;
                if (isBatch) {
                  e.preventDefault();
                  e.stopPropagation();
                  const info = getElementInfo(e.target);
                  window.parent.shellApp.queueAction({
                    type: 'submit',
                    selector: info.selector,
                    xpath: info.xpath
                  });
                  flashElement(e.target);
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


def generate_start_page_html(
    config: Optional[Any] = None,
    session_manager: Optional[Any] = None,
    token_status: Optional[str] = None,
    repo_status: Optional[str] = None,
    cookies_count: Optional[int] = None,
) -> str:
    """Generates the pure-black home screen with NTTM branding, full-width Digital Flame pyramid animation in white-to-grey spectrum, and clean dark UI."""
    if token_status is None:
        token = getattr(config, "github_token", "") if config else ""
        token_status = "ثبت شده" if token else "تنظیم نشده"
    if repo_status is None:
        repo_status = getattr(config, "github_repo", "") if config else ""
        repo_status = repo_status or "تنظیم نشده"
    if cookies_count is None:
        cookies = getattr(session_manager, "cookies", []) if session_manager else []
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
