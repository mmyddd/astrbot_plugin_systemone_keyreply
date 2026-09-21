/* ============================================================
   TypeSafe 配置中心 · 数据层
   状态容器 / Bridge 封装 / API 客户端 / 格式化 / 工具
   ============================================================ */
(function () {
  'use strict';

  const TS = (window.TS = window.TS || {});

  /* ── DOM 工具 ─────────────────────────────────────────── */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function show(node, visible) {
    if (!node) return;
    if (visible) node.removeAttribute('hidden');
    else node.setAttribute('hidden', '');
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  /* ── 格式化 ───────────────────────────────────────────── */
  const format = {
    ms(value) {
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) return '—';
      if (n < 1000) return Math.round(n) + ' ms';
      if (n < 60000) return (n / 1000).toFixed(2) + ' 秒';
      return Math.floor(n / 60000) + ' 分 ' + Math.round((n % 60000) / 1000) + ' 秒';
    },
    time(value) {
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return String(value || '—');
      const pad = n => String(n).padStart(2, '0');
      return pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
    },
    percent(value, digits) {
      const n = Number(value);
      if (!Number.isFinite(n)) return '—';
      return n.toFixed(digits === undefined ? 0 : digits) + '%';
    },
    num(value) {
      const n = Number(value);
      return Number.isFinite(n) ? String(n) : '—';
    },
    lines(text) {
      return String(text || '')
        .split(/[\n,，]/)
        .map(item => item.trim())
        .filter(Boolean);
    },
    list(value) {
      if (Array.isArray(value)) return value.map(v => String(v).trim()).filter(Boolean);
      if (value === null || value === undefined || value === '') return [];
      return format.lines(value);
    }
  };

  /* ── Bridge ───────────────────────────────────────────── */
  const bridge = {
    isEmbedded() {
      return Boolean(
        window.AstrBotPluginPage && typeof window.AstrBotPluginPage.apiGet === 'function'
      );
    },
    async ready() {
      if (!this.isEmbedded()) return null;
      try {
        state.bridgeContext = await window.AstrBotPluginPage.ready();
      } catch (error) {
        state.bridgeContext = null;
        throw error;
      }
      return state.bridgeContext;
    },
    isDark() {
      const ctx = state.bridgeContext;
      return Boolean(ctx && ctx.isDark);
    },
    onContext(handler) {
      const api = window.AstrBotPluginPage;
      if (!api) return function () {};
      const listener = typeof api.onContext === 'function'
        ? api.onContext.bind(api)
        : api.onContextChange
          ? api.onContextChange.bind(api)
          : null;
      if (!listener) return function () {};
      const off = listener(handler);
      return typeof off === 'function' ? off : function () {};
    }
  };

  function requireBridge() {
    if (!bridge.isEmbedded()) throw new Error('页面需要通过 AstrBot 插件页打开');
    return window.AstrBotPluginPage;
  }

  /* ── API 客户端 ───────────────────────────────────────── */
  const api = {
    get(endpoint, params) { return requireBridge().apiGet(endpoint, params); },
    post(endpoint, body) { return requireBridge().apiPost(endpoint, body); },
    configGet() { return api.get('console/config'); },
    configUpdate(patch) { return api.post('console/config/update', patch); },
    configReset(fields) { return api.post('console/config/reset', { fields: fields }); },
    status() { return api.get('console/status'); },
    probe() { return api.post('console/probe', {}); },
    tryMessage(payload) { return api.post('console/try', payload); },
    qaList() { return api.get('console/qa/list'); },
    qaSave(payload) { return api.post('console/qa/save', payload); },
    qaImport(payload) { return api.post('console/qa/import', payload); },
    qaTest(payload) { return api.post('console/qa/test', payload); }
  };

  /* ── 状态容器 ─────────────────────────────────────────── */
  const state = {
    bridgeContext: null,
    route: { view: 'config' },
    config: null,
    dirty: new Set(),
    saving: false,
    status: null,
    tryInput: '',
    tryRecent: [],
    tryResult: null,
    qa: null,
    qaScope: { scope: 'global', scope_id: '' },
    qaDraft: [],
    qaDirty: false,
    qaTestResult: null,
    lastSyncAt: null
  };

  /* ── 轻提示 ───────────────────────────────────────────── */
  function toast(message, kind) {
    const stack = $('#toast-stack');
    if (!stack) return;
    const node = el('div', 'toast' + (kind === 'ok' ? ' is-ok' : kind === 'err' ? ' is-err' : ''), message);
    stack.appendChild(node);
    window.setTimeout(() => {
      node.style.transition = 'opacity .25s ease';
      node.style.opacity = '0';
      window.setTimeout(() => node.remove(), 260);
    }, kind === 'err' ? 5200 : 3000);
  }

  Object.assign(TS, {
    $: $, $$: $$, esc: esc, show: show, el: el,
    format: format, bridge: bridge, api: api, state: state, toast: toast
  });
})();
