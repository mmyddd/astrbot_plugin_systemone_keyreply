/* ============================================================
   SystemOne 配置中心 · 数据层
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
    qaTest(payload) { return api.post('console/qa/test', payload); },
    qaDelete(payload) { return api.post('console/qa/delete', payload); }
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
    qaTableKey: 'global',
    qaGroups: [],
    qaDirty: false,
    qaTestResult: null,
    lastSyncAt: null
  };

  /* ── 确认对话框 ─────────────────────────────────────────
     插件页运行在 sandbox iframe 中且未开启 allow-modals，
     window.confirm() 会被浏览器忽略并返回 false，
     因此所有二次确认必须走页面内的自定义对话框。
     ─────────────────────────────────────────────────────── */
  let confirmResolve = null;

  function ensureConfirmModal() {
    let modal = $('#app-confirm');
    if (modal) return modal;

    modal = el('div', 'modal');
    modal.id = 'app-confirm';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('hidden', '');

    const card = el('div', 'modal-card modal-card-sm');

    const head = el('header', 'modal-head');
    const titles = el('div');
    titles.appendChild(el('h2', null, '确认操作'));
    titles.appendChild(el('p', null, ''));
    head.appendChild(titles);
    card.appendChild(head);

    const body = el('div', 'modal-body');
    const msg = el('p', 'confirm-message');
    msg.id = 'app-confirm-message';
    body.appendChild(msg);
    card.appendChild(body);

    const foot = el('footer', 'modal-foot');
    const right = el('div', 'modal-foot-right');
    const cancel = el('button', 'btn btn-ghost', '取消');
    cancel.type = 'button';
    cancel.id = 'app-confirm-cancel';
    const ok = el('button', 'btn btn-primary', '确定');
    ok.type = 'button';
    ok.id = 'app-confirm-ok';
    right.appendChild(cancel);
    right.appendChild(ok);
    foot.appendChild(right);
    card.appendChild(foot);

    modal.appendChild(card);
    (document.body || document.documentElement).appendChild(modal);

    const finish = (value) => {
      modal.setAttribute('hidden', '');
      document.body.style.overflow = '';
      const resolve = confirmResolve;
      confirmResolve = null;
      if (resolve) resolve(value);
    };
    cancel.addEventListener('click', () => finish(false));
    ok.addEventListener('click', () => finish(true));
    modal.addEventListener('click', (ev) => { if (ev.target === modal) finish(false); });
    window.addEventListener('keydown', (ev) => {
      if (modal.hasAttribute('hidden')) return;
      if (ev.key === 'Escape') finish(false);
      if (ev.key === 'Enter') finish(true);
    });
    return modal;
  }

  /**
   * 页面内确认框（替代被 sandbox 屏蔽的 window.confirm）。
   * 返回 Promise<boolean>。
   */
  function confirmDialog(message, options) {
    const opts = options || {};
    return new Promise((resolve) => {
      const modal = ensureConfirmModal();
      const title = modal.querySelector('.modal-head h2');
      const msg = modal.querySelector('#app-confirm-message');
      const ok = modal.querySelector('#app-confirm-ok');
      if (title) title.textContent = opts.title || '确认操作';
      if (msg) msg.textContent = message;
      if (ok) {
        ok.textContent = opts.okLabel || '确定';
        ok.className = 'btn ' + (opts.danger ? 'btn-danger-solid' : 'btn-primary');
      }
      // 若已有未决确认，先按取消处理，避免 Promise 永久挂起
      if (confirmResolve) {
        const prev = confirmResolve;
        confirmResolve = null;
        prev(false);
      }
      confirmResolve = resolve;
      modal.removeAttribute('hidden');
      document.body.style.overflow = 'hidden';
      if (ok && typeof ok.focus === 'function') ok.focus();
    });
  }

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
    format: format, bridge: bridge, api: api, state: state, toast: toast,
    confirmDialog: confirmDialog
  });
})();
