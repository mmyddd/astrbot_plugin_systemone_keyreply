/* ============================================================
   TypeSafe 配置中心 · 应用编排
   路由 / 主题 / 数据加载 / 事件绑定 / 启动
   ============================================================ */
(function () {
  'use strict';

  const TS = window.TS;
  const $ = TS.$, $$ = TS.$$, show = TS.show, format = TS.format, state = TS.state;

  const VIEWS = ['config', 'qa', 'status', 'try'];
  let statusTimer = 0;

  /* ── 状态提示 ─────────────────────────────────────────── */
  function setStatus(text, kind) {
    const chip = $('#data-status');
    if (!chip) return;
    chip.textContent = text;
    chip.dataset.state = kind || 'idle';
    chip.title = text;
    if (statusTimer) window.clearTimeout(statusTimer);
    if (kind === 'success') {
      statusTimer = window.setTimeout(() => {
        chip.textContent = state.lastSyncAt ? '同步于 ' + format.time(state.lastSyncAt) : '已就绪';
        chip.dataset.state = 'idle';
      }, 4000);
    }
  }

  function setBusy(busy) {
    const button = $('#refresh-btn');
    if (button) button.classList.toggle('is-busy', Boolean(busy));
  }

  /* ── 路由 ─────────────────────────────────────────────── */
  function currentView() {
    const raw = String(window.location.hash || '').replace(/^#\/?/, '');
    const view = raw.split('?')[0].split('/').filter(Boolean)[0];
    return VIEWS.indexOf(view) >= 0 ? view : 'config';
  }

  function applyRoute() {
    const view = currentView();
    state.route.view = view;
    $$('[data-view-panel]').forEach(panel => {
      show(panel, panel.dataset.viewPanel === view);
    });
    $$('[data-nav]').forEach(link => {
      link.classList.toggle('is-active', link.dataset.nav === view);
    });
    if (!window.location.hash) window.history.replaceState(null, '', '#/config');
  }

  /* ── 主题 ─────────────────────────────────────────────── */
  function applyTheme(dark) {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    const meta = $('#theme-color');
    if (meta) meta.setAttribute('content', dark ? '#090c12' : '#f5f6fa');
    try { window.localStorage.setItem('ts-theme', dark ? 'dark' : 'light'); } catch (e) { /* 忽略 */ }
  }

  function initTheme() {
    let stored = null;
    try { stored = window.localStorage.getItem('ts-theme'); } catch (e) { stored = null; }
    if (stored === 'dark' || stored === 'light') return stored === 'dark';
    return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  /* ── 加载 ─────────────────────────────────────────────── */
  async function loadConfig() {
    const config = await TS.api.configGet();
    state.config = config || {};
    TS.config.render();
    state.lastSyncAt = new Date().toISOString();
  }

  async function loadStatus() {
    const status = await TS.api.status();
    state.status = status || null;
    TS.views.renderStatus();
  }

  async function loadAll(silent) {
    if (!silent) setStatus('正在加载配置…', 'busy');
    setBusy(true);
    try {
      await Promise.all([loadConfig(), loadStatus(), TS.qa.load()]);
      setStatus(silent ? '已同步' : '配置已就绪', 'success');
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      setStatus('加载失败：' + message, 'error');
      TS.toast('加载失败：' + message, 'err');
      const sections = $('#config-sections');
      if (sections && !sections.children.length) {
        sections.innerHTML = '<div class="card"><div class="card-body"><div class="empty">'
          + TS.esc(message) + '</div></div></div>';
      }
    } finally {
      setBusy(false);
    }
  }

  /* ── 事件绑定 ─────────────────────────────────────────── */
  function bind() {
    $('#refresh-btn')?.addEventListener('click', () => loadAll(false));
    $('#theme-toggle')?.addEventListener('click', () => {
      applyTheme(document.documentElement.dataset.theme !== 'dark');
    });
    $('#save-btn')?.addEventListener('click', () => TS.config.save());
    $('#reload-defaults')?.addEventListener('click', () => {
      if (window.confirm('把所有配置项填入内置默认值？确认后仍需点击保存才会写入。')) {
        TS.config.fillDefaults();
      }
    });
    $('#probe-btn')?.addEventListener('click', () => TS.views.probe());
    $('#try-btn')?.addEventListener('click', () => TS.views.runTry());
    $('#try-add-recent')?.addEventListener('click', () => {
      if (state.tryRecent.length >= 10) {
        TS.toast('最多添加 10 条模拟上下文', 'err');
        return;
      }
      state.tryRecent.push({ sender: '群友', text: '' });
      TS.views.renderRecent();
    });
    const input = $('#try-input');
    if (input) {
      input.addEventListener('keydown', event => {
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') TS.views.runTry();
      });
    }

    window.addEventListener('hashchange', applyRoute);
    window.addEventListener('keydown', event => {
      const tag = (event.target && event.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (event.key === 'r' || event.key === 'R') { loadAll(false); }
      if (event.key === 's' || event.key === 'S') {
        if ((event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          TS.config.save();
        }
      }
    });
    window.addEventListener('beforeunload', event => {
      if (state.dirty.size) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  /* ── 启动 ─────────────────────────────────────────────── */
  async function boot() {
    applyTheme(initTheme());
    applyRoute();
    bind();
    TS.qa.bind();
    TS.views.renderRecent();
    TS.views.renderTryResult();

    if (!TS.bridge.isEmbedded()) {
      setStatus('未连接 AstrBot Bridge', 'error');
      $('#config-sections').innerHTML =
        '<div class="card"><div class="card-body"><div class="empty">'
        + '本页面需要通过 AstrBot 面板的插件页进入，才能读写插件配置。</div></div></div>';
      const qaRows = $('#qa-rows');
      if (qaRows) {
        qaRows.innerHTML = '<div class="empty">'
          + '本页面需要通过 AstrBot 面板的插件页进入，才能读取问答表。</div>';
      }
      return;
    }

    try {
      await TS.bridge.ready();
    } catch (error) {
      setStatus('Bridge 初始化失败', 'error');
    }

    TS.bridge.onContext(() => {
      applyTheme(TS.bridge.isDark() || initTheme());
    });

    await loadAll(false);
  }

  Object.assign(TS, { app: { loadAll: loadAll, boot: boot } });

  document.addEventListener('DOMContentLoaded', boot);
})();
