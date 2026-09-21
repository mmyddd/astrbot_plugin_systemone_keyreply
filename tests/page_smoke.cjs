/* ============================================================
   插件页冒烟测试
   在最小 DOM 桩下真实执行 pages/typesafe-console/js/*.js，
   捕获 ReferenceError（如曾经的 "$$ is not defined"）与渲染期异常。

   运行： node tests/page_smoke.cjs
   ============================================================ */
/* 页面冒烟测试 v2：更忠实的 DOM 桩（支持子树查询与 window.setTimeout）。 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const REPO = require('path').resolve(__dirname, '..');
const DIR = require('path').join(REPO, 'pages', 'typesafe-console', 'js') + require('path').sep;
const SCHEMA = require('path').join(REPO, '_conf_schema.json');

function matchSel(n, sel) {
  sel = String(sel).trim();
  if (sel.includes(',')) return sel.split(',').some(s => matchSel(n, s));
  if (sel.startsWith('#')) return n.id === sel.slice(1);
  // 类选择器：支持 .a 与 tag.a 两种写法
  if (sel.startsWith('.') || /^[a-z]+\./.test(sel)) {
    const m = sel.match(/^([a-z]*)\.([\w-]+)$/i);
    if (m) {
      const tagOk = !m[1] || n.tagName === m[1].toUpperCase();
      const cls = String(n.className || '').split(/\s+/);
      return tagOk && cls.indexOf(m[2]) >= 0;
    }
  }
  const attr = sel.match(/^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]$/);
  if (attr) {
    const key = attr[1].replace(/^data-/, '');
    const want = attr[2];
    const have = n.dataset[key];
    if (want === undefined) return have !== undefined && have !== null;
    return String(have) === String(want);
  }
  if (sel === 'input:checked') return n.tagName === 'INPUT' && n.checked;
  if (sel.startsWith('input')) return n.tagName === 'INPUT';
  if (sel.startsWith('select')) return n.tagName === 'SELECT';
  if (sel.startsWith('textarea')) return n.tagName === 'TEXTAREA';
  if (sel === 'a') return n.tagName === 'A';
  return n.tagName === sel.toUpperCase();
}

function makeEl(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    children: [], attrs: {}, dataset: {}, style: {},
    innerHTML: '', value: '', checked: false, type: '',
    id: '', placeholder: '', rows: 0, disabled: false, parentNode: null,
    _classes: new Set(),
    // className 与 classList 共享同一份状态，否则测试会看到不一致的结果
    get className() { return Array.from(this._classes).join(' '); },
    set className(v) {
      this._classes = new Set(String(v || '').split(/\s+/).filter(Boolean));
    },
    get textContent() {
      let out = this._text || '';
      for (const c of this.children) out += c.textContent || '';
      return out;
    },
    set textContent(v) {
      this._text = v === undefined || v === null ? '' : String(v);
      // 复刻浏览器行为：设置 textContent 会清空子节点
      for (const c of this.children) c.parentNode = null;
      this.children = [];
    },
    classList: {
      _owner: null,
      add(c) { this._owner._classes.add(c); },
      remove(c) { this._owner._classes.delete(c); },
      contains(c) { return this._owner._classes.has(c); },
      toggle(c, f) {
        if (f === undefined) { this._owner._classes.has(c) ? this._owner._classes.delete(c) : this._owner._classes.add(c); }
        else if (f) { this._owner._classes.add(c); }
        else { this._owner._classes.delete(c); }
      }
    },
    appendChild(c) { this.children.push(c); if (c) c.parentNode = this; return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    // 事件注册记录在 _ev，便于测试直接触发
    _ev: null,
    focus() {}, click() {},
    setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') this.id = v; },
    removeAttribute(k) { delete this.attrs[k]; }, getAttribute(k) { return this.attrs[k]; },
    addEventListener(ev, fn) { (this._ev = this._ev || {})[ev] = fn; },
    removeEventListener() {},
    querySelector(sel) {
      const direct = descendants(this).find(n => matchSel(n, sel));
      if (direct) return direct;
      return this._queryDescendant ? this._queryDescendant(sel) : null;
    },
    querySelectorAll(sel) {
      const direct = descendants(this).filter(n => matchSel(n, sel));
      if (direct.length) return direct;
      const d = this._queryDescendant ? this._queryDescendant(sel) : null;
      return d ? [d] : [];
    }
  };
  function descendants(n) { const out = []; const walk = x => { for (const c of x.children) { out.push(c); walk(c); } }; walk(n); return out; }
  // 极简后代选择器支持（形如 ".a b" / "#id h2"），仅用于测试中的少量查询
  node._queryDescendant = function (sel) {
    const parts = String(sel).trim().split(/\s+/);
    if (parts.length < 2) return null;
    const last = parts[parts.length - 1];
    const ancestors = parts.slice(0, -1);
    return descendants(node).find(cand => {
      if (!matchSel(cand, last)) return false;
      let p = cand.parentNode;
      let need = ancestors.length - 1;
      while (p && need >= 0) {
        if (matchSel(p, ancestors[need])) need--;
        p = p.parentNode;
      }
      return need < 0;
    }) || null;
  };
  node.classList._owner = node;
  return node;
}

const hosts = {};
const ROOT = makeEl('body');
for (const id of ['config-sections','toast-stack','status-body','try-result','try-recent','nav-dirty','action-note',
  'save-btn','refresh-btn','theme-toggle','probe-btn','probe-result','try-btn','try-input','try-add-recent',
  'data-status','theme-color','reload-defaults','try-recent-host',
  'qa-rows','qa-save-btn','qa-scope-bar','qa-mode-bar','qa-import-path','qa-import-btn','qa-add-row',
  'qa-add-scope','qa-test-input','qa-test-btn','qa-test-result','qa-nav-dirty','qa-new-kind','qa-new-id','qa-import-hint',
  'qa-cfg-modal','qa-cfg-title','qa-cfg-name','qa-cfg-id','qa-cfg-ids','qa-cfg-add','qa-cfg-save','qa-cfg-cancel','qa-cfg-close','qa-cfg-del']) {
  const n = makeEl('div'); n.id = id; n.setAttribute('id', id); hosts['#' + id] = n; ROOT.appendChild(n);
}

function allNodes() { const out = []; const walk = x => { for (const c of x.children) { out.push(c); walk(c); } }; walk(ROOT); return out; }

const document = {
  createElement: makeEl, createTextNode: t => ({ text: t }),
  querySelector(sel) {
    if (hosts[sel]) return hosts[sel];
    return allNodes().find(n => matchSel(n, sel)) || null;
  },
  querySelectorAll(sel) { return allNodes().filter(n => matchSel(n, sel)); },
  addEventListener() {}, documentElement: makeEl('html'), body: ROOT
};

const sandbox = { window: {}, document, console: { log(){}, warn(){}, error(){} }, Intl, Date, JSON, Math, process };
sandbox.window.document = document;
sandbox.window.setTimeout = setTimeout; sandbox.window.clearTimeout = clearTimeout;
sandbox.window.localStorage = { getItem: () => null, setItem() {} };
sandbox.window.matchMedia = () => ({ matches: false });
sandbox.window.addEventListener = () => {};
sandbox.window.location = { hash: '#/config', href: '' };
sandbox.window.history = { replaceState() {} };
sandbox.window.confirm = () => true;
sandbox.confirm = () => true;
const ctx = vm.createContext(sandbox);

const errors = [];
for (const f of ['data','config','qa','views','app']) {
  try { vm.runInContext(fs.readFileSync(DIR + f + '.js', 'utf8'), ctx, { filename: f + '.js' }); }
  catch (e) { errors.push(f + '.js: ' + e.message); }
}

const TS = ctx.window.TS;
const schema = JSON.parse(fs.readFileSync(SCHEMA, 'utf8'));
const cfg = {}; for (const [k, v] of Object.entries(schema)) cfg[k] = v.default;
TS.state.config = cfg;

const checks = [];
const pending = [];
const t = (n, fn) => {
  try {
    const r = fn();
    // 支持异步断言：收集起来在最后统一结算
    if (r && typeof r.then === 'function') {
      pending.push(r.then(() => checks.push([n, true, '']))
        .catch(e => checks.push([n, false, e.message])));
      return;
    }
    checks.push([n, true, '']);
  } catch (e) { checks.push([n, false, e.message]); }
};

t('渲染全部 schema 字段控件', () => {
  TS.config.render();
  const expected = Object.keys(schema).length;
  const fields = document.querySelectorAll('[data-field]');
  if (fields.length < expected) {
    throw new Error('只渲染出 ' + fields.length + ' 个控件，schema 共 ' + expected + ' 项');
  }
});
t('脏字段可被 collect 收集', () => {
  TS.state.dirty.clear(); TS.state.dirty.add('enable_plugin'); TS.state.dirty.add('max_chars');
  const patch = TS.config.collect();
  if (!('enable_plugin' in patch)) throw new Error('enable_plugin 未收集');
  if (!('max_chars' in patch)) throw new Error('max_chars 未收集');
});
t('多选控件 collect 返回数组', () => {
  TS.state.dirty.clear(); TS.state.dirty.add('allowed_reply_types');
  const patch = TS.config.collect();
  if (!Array.isArray(patch.allowed_reply_types)) throw new Error('不是数组: ' + typeof patch.allowed_reply_types);
});
t('列表控件 collect 按行拆分', () => {
  TS.state.dirty.clear(); TS.state.dirty.add('ignore_keywords');
  const el2 = document.querySelector('[data-field="ignore_keywords"]');
  if (!el2) throw new Error('找不到 ignore_keywords 控件');
  el2.value = 'a\nb\nc';
  const patch = TS.config.collect();
  if (JSON.stringify(patch.ignore_keywords) !== JSON.stringify(['a','b','c'])) throw new Error(JSON.stringify(patch.ignore_keywords));
});
t('fillDefaults() 不抛错', () => TS.config.fillDefaults());
t('renderStatus 空态 / 有数据', () => { TS.state.status = null; TS.views.renderStatus();
  TS.state.status = { api_configured: true, model: 'jev-latest', active_sessions: 3, rate_limit_used: 5,
    rate_limit_per_minute: 60, enable_plugin: true, enable_group: true, enable_private: false, failure_mode: 'silent',
    min_confidence: 'medium', reply_probability: 100, allowed_reply_types: ['明确提问'],
    use_qa_table: true, qa_mode: 'jev', qa_mode_label: 'Jev 话题模式（语义路由，LLM 围绕答案生成）',
    qa_summary: { global_entries: 3, groups: ['111'], privates: [], total_entries: 4 },
    reply_style: '自然', reply_length_mode: '简短', max_chars: 200, model_mode: 'follow_session', custom_provider_id: '',
    delay: { enabled: true, mode: 'random', min: 1, max: 3, fixed: 2 }, session_cooldown: 30, user_cooldown: 30,
    max_continuous_replies: 2, filter_mode: 'blacklist_only', context_message_count: 3, cache_enabled: false,
    cache_ttl: 60, cache_size: 0, debug_log: false, regex_ok: { force_trigger: true, ignore: true },
    force_trigger_regex: '', ignore_regex: '' };
  TS.views.renderStatus(); });
t('renderTryResult 三种分支', () => {
  TS.state.tryResult = null; TS.views.renderTryResult();
  TS.state.tryResult = { ok:true, text:'hi', model:'m', elapsed_ms:120, should_reply:true, reply_type:'explicit_question',
    reply_type_display:'明确提问', confidence_level:'high', confidence_score:0.93, urgency:'normal', reason:'r',
    is_fallback:false, type_allowed:true, confidence_ok:true, would_reply:true, verdicts:['ok'], gates:{min_confidence:'中'} };
  TS.views.renderTryResult();
  TS.state.tryResult = { ok:true, text:'hi', model:'m', elapsed_ms:90, should_reply:true, reply_type:'joke',
    reply_type_display:'玩笑', confidence_level:'low', confidence_score:0.2, urgency:'low', reason:'r', is_fallback:true,
    type_allowed:false, confidence_ok:false, would_reply:false, verdicts:['未包含'], gates:{min_confidence:'中'} };
  TS.views.renderTryResult();
});
t('renderRecent 增删', () => { TS.state.tryRecent = [{sender:'A',text:'hi'}]; TS.views.renderRecent(); TS.state.tryRecent = []; TS.views.renderRecent(); });

/* ── 固定问答表视图 ─────────────────────────────────── */
const qaFixture = () => ({
  tables: [
    { key: 'global', scope: 'global', scope_id: '', ids: [], name: '', label: '全局默认表',
      entries: [
        { question: '怎么安装%', answer: { text: '统一答案', images: [] }, enabled: true },
        { question: '%如何安装%', answer: { text: '统一答案', images: [] }, enabled: true }
      ]},
    { key: 'group:111', scope: 'group', scope_id: '111', ids: ['111', '222'], name: '技术群组',
      label: '技术群组',
      entries: [
        { question: '群专属问题', answer: { text: '群专属答案', images: [] }, enabled: true },
        { question: '图片问题', answer: { text: '', images: ['https://example.test/a.jpg'] }, enabled: true },
        { question: '多图问题', answer: { text: '', images: ['https://example.test/a.jpg', 'https://example.test/b.jpg'] }, enabled: true }
      ]},
    { key: 'group:333', scope: 'group', scope_id: '333', ids: ['333'], name: '',
      label: '群聊表 · 333',
      entries: [] }
  ],
  summary: { global_entries: 2, groups: ['111', '222', '333'], privates: [], total_entries: 3, table_count: 3 },
  import_candidates: [], configured_import_path: '', data_file: '/tmp/qa_tables.json',
  use_qa_table: true, enable_jev_topic: true, mode: 'jev', mode_label: 'Jev 话题模式',
  qa_min_confidence: '中', context_message_count: 3
});

t('qa.render 表列表与行渲染', () => {
  TS.state.qa = qaFixture();
  TS.state.qaTableKey = 'global';
  TS.qa.loadDraft();
  if (!hosts['#qa-rows'].children.length) throw new Error('未渲染任何问答分组');
  if (!hosts['#qa-scope-bar'].children.length) throw new Error('未渲染问答表列表');
  if (!hosts['#qa-mode-bar'].children.length) throw new Error('未渲染模式条');
});

t('qa 行默认折叠', () => {
  const items = [];
  const walk = n => { for (const c of n.children) { items.push(c); walk(c); } };
  walk(hosts['#qa-rows']);
  const details = items.filter(n => n.tagName === 'DETAILS');
  if (!details.length) throw new Error('未找到可折叠条目');
  if (details.some(d => d.open === true)) throw new Error('存在默认展开的条目');
});

t('qa 折叠态只展示 Q 与 A', () => {
  const items = [];
  const walk = n => { for (const c of n.children) { items.push(c); walk(c); } };
  walk(hosts['#qa-rows']);
  const d = items.filter(n => n.tagName === 'DETAILS')[0];
  const summary = d.children.find(c => c.tagName === 'SUMMARY');
  if (!summary) throw new Error('折叠条目缺少 summary');
  const textOf = n => {
    let out = n.textContent || '';
    for (const c of n.children) out += textOf(c);
    return out;
  };
  const label = textOf(summary);
  if (label.indexOf('怎么安装%') < 0) throw new Error('折叠摘要未显示问题：' + label);
  if (label.indexOf('统一答案') < 0) throw new Error('折叠摘要未显示答案：' + label);
});

t('qa 多 Q 一 A 归组', () => {
  const groups = hosts['#qa-rows'].children.filter(
    n => n.className && String(n.className).indexOf('qa-group') >= 0
  );
  if (groups.length !== 1) throw new Error('期望 1 个分组，实际 ' + groups.length);
});

t('qa 多群一域：一张表含多个 ID', () => {
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
  const t = TS.state.qa.tables.find(x => x.key === 'group:111');
  if (!t || t.ids.length !== 2) throw new Error('多 ID 表未保留 ids');
  if (!hosts['#qa-rows'].children.length) throw new Error('群表未渲染');
});

t('qa 打开群配置弹窗', () => {
  const modal = hosts['#qa-cfg-modal'];
  if (!modal) throw new Error('缺少弹窗容器');
  TS.qa.openTableConfig(TS.state.qa.tables.find(x => x.key === 'group:111'));
  if (modal.hasAttribute && modal.hasAttribute('hidden')) throw new Error('弹窗未打开');
  const idsHost = hosts['#qa-cfg-ids'];
  if (!idsHost.children.length) throw new Error('未渲染 ID 列表');
  TS.qa.closeTableConfig();
});

t('qa 图片答案渲染极小缩略图', () => {
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
  const nodes = [];
  const walk = n => { for (const c of n.children) { nodes.push(c); walk(c); } };
  walk(hosts['#qa-rows']);

  const thumbs = nodes.filter(n => String(n.className || '').indexOf('qa-thumb') >= 0);
  if (thumbs.length < 2) throw new Error('期望至少 2 个缩略图，实际 ' + thumbs.length);

  const first = thumbs[0];
  if (first.tagName !== 'A') throw new Error('缩略图应是链接，实际 ' + first.tagName);
  if (first.href !== 'https://example.test/a.jpg') throw new Error('缩略图 href 不正确：' + first.href);
  const img = first.children.find(c => c.tagName === 'IMG');
  if (!img) throw new Error('缩略图缺少 img 元素');
  if (img.src !== 'https://example.test/a.jpg') throw new Error('img.src 不正确：' + img.src);

  // 多图应有 +N 角标
  const more = thumbs.filter(t => t.children.some(c => String(c.className || '').indexOf('qa-thumb-more') >= 0));
  if (!more.length) throw new Error('多图未显示剩余数量角标');
});

t('qa 缩略图加载失败退化为链接文字', () => {
  const nodes = [];
  const walk = n => { for (const c of n.children) { nodes.push(c); walk(c); } };
  walk(hosts['#qa-rows']);
  const thumb = nodes.filter(n => String(n.className || '').indexOf('qa-thumb') >= 0)[0];
  const img = thumb.children.find(c => c.tagName === 'IMG');
  if (!img || !img._ev || !img._ev.error) throw new Error('缩略图未注册 error 回退处理');
  // 触发加载失败
  img._ev.error({});
  if (String(thumb.className).indexOf('is-broken') < 0) throw new Error('加载失败后未标记 is-broken');
  const fb = thumb.children.find(c => String(c.className || '').indexOf('qa-thumb-fallback') >= 0);
  if (!fb) throw new Error('加载失败后未显示链接文字回退');
  if (thumb.children.some(c => c.tagName === 'IMG')) throw new Error('加载失败后仍保留破图元素');
});

t('qa 无图答案不渲染缩略图', () => {
  TS.state.qaTableKey = 'global';
  TS.qa.loadDraft();
  const nodes = [];
  const walk = n => { for (const c of n.children) { nodes.push(c); walk(c); } };
  walk(hosts['#qa-rows']);
  const thumbs = nodes.filter(n => String(n.className || '').indexOf('qa-thumb') >= 0);
  if (thumbs.length) throw new Error('无图答案不应出现缩略图，实际 ' + thumbs.length);
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
});

/* ── 确认对话框（sandbox 下 window.confirm 失效）── */
t('confirmDialog 存在且返回 Promise', () => {
  if (typeof TS.confirmDialog !== 'function') throw new Error('缺少 TS.confirmDialog');
  const p = TS.confirmDialog('测试确认');
  if (!p || typeof p.then !== 'function') throw new Error('confirmDialog 未返回 Promise');
  // 清理：按取消，避免 Promise 悬空
  const modal = document.querySelector('#app-confirm');
  if (!modal) throw new Error('未创建确认对话框');
  const cancel = modal.querySelector('#app-confirm-cancel');
  if (!cancel) throw new Error('缺少取消按钮');
  cancel._ev.click();
});

t('confirmDialog 取消返回 false', async () => {
  const p = TS.confirmDialog('取消测试');
  const modal = document.querySelector('#app-confirm');
  modal.querySelector('#app-confirm-cancel')._ev.click();
  const r = await p;
  if (r !== false) throw new Error('取消应返回 false，实际 ' + r);
});

t('confirmDialog 确定返回 true', async () => {
  const p = TS.confirmDialog('确定测试');
  const modal = document.querySelector('#app-confirm');
  modal.querySelector('#app-confirm-ok')._ev.click();
  const r = await p;
  if (r !== true) throw new Error('确定应返回 true，实际 ' + r);
});

t('confirmDialog 显示传入的文案', async () => {
  const p = TS.confirmDialog('这段文案应当出现', { title: '自定义标题' });
  const modal = document.querySelector('#app-confirm');
  const msg = modal.querySelector('#app-confirm-message');
  if (String(msg.textContent).indexOf('这段文案应当出现') < 0) {
    throw new Error('未显示确认文案：' + msg.textContent);
  }
  // 用标题元素自身的文本校验（桩不支持后代选择器语法）
  const nodes = [];
  const walk = n => { for (const c of n.children) { nodes.push(c); walk(c); } };
  walk(modal);
  const h = nodes.filter(n => n.tagName === 'H2')[0];
  if (!h) throw new Error('对话框缺少标题元素');
  if (String(h.textContent) !== '自定义标题') throw new Error('标题未生效：' + h.textContent);
  modal.querySelector('#app-confirm-cancel')._ev.click();
  await p;
});

t('产品代码不再直接调用被沙箱屏蔽的 window.confirm', () => {
  const files = ['qa.js', 'config.js', 'app.js'];
  const bad = [];
  for (const f of files) {
    const src = fs.readFileSync(path.join(DIR, f), 'utf8');
    src.split('\n').forEach((line, i) => {
      if (/window\.confirm\s*\(/.test(line) && line.trim().indexOf('//') !== 0) {
        bad.push(f + ':' + (i + 1));
      }
    });
  }
  if (bad.length) throw new Error('仍在使用 window.confirm：' + bad.join(', '));
});

t('qa 表格子不是 label（否则点任意位置会触发 ⋯ 按钮）', () => {
  const chips = [];
  const walk = n => { for (const c of n.children) { if (String(c.className || '').indexOf('qa-scope-chip') >= 0) chips.push(c); walk(c); } };
  walk(hosts['#qa-scope-bar']);
  if (!chips.length) throw new Error('未找到问答表格子');
  const bad = chips.filter(c => c.tagName === 'LABEL');
  if (bad.length) throw new Error('有 ' + bad.length + ' 个格子仍是 <label>，点击正文会连带触发内部按钮');
});

t('qa 格子内的 ⋯ 按钮阻止冒泡且只弹配置', () => {
  const walks = [];
  const walk = n => { for (const c of n.children) { walks.push(c); walk(c); } };
  walk(hosts['#qa-scope-bar']);
  const cfgBtns = walks.filter(c => String(c.className || '').indexOf('qa-scope-cfg') >= 0);
  if (!cfgBtns.length) throw new Error('未找到 ⋯ 按钮');

  const chip = cfgBtns[0].parentNode;
  let chipClicks = 0;
  chip.addEventListener('click', () => { chipClicks++; });

  const ev = { stopped: false, stopPropagation() { this.stopped = true; }, preventDefault() {} };
  cfgBtns[0]._ev.click(ev);
  if (!ev.stopped) throw new Error('⋯ 按钮未调用 stopPropagation，会连带切换问答表');
  if (chipClicks !== 0) throw new Error('点击 ⋯ 触发了所在格子的切换逻辑');

  // 同时确认弹窗确实被打开
  const modal = hosts['#qa-cfg-modal'];
  if (modal && modal.hasAttribute && modal.hasAttribute('hidden')) throw new Error('⋯ 未打开群配置弹窗');
  TS.qa.closeTableConfig();
});

/* ── 样式守卫：DOM 桩没有布局引擎，关键布局规则只能查样式源 ── */
t('样式：.qa-row-head 保持 flex 行布局', () => {
  const css = fs.readFileSync(
    require('path').join(REPO, 'pages', 'typesafe-console', 'style.css'), 'utf8'
  );
  const m = css.match(/\.qa-row-head\s*\{([^}]*)\}/);
  if (!m) throw new Error('style.css 中缺少 .qa-row-head 规则');
  const body = m[1];
  if (body.indexOf('display: flex') < 0) {
    throw new Error('.qa-row-head 缺少 display:flex，展开区控件会重叠：' + body.trim());
  }
  if (body.indexOf('align-items') < 0) {
    throw new Error('.qa-row-head 缺少 align-items，开关与文字会错位');
  }
});

t('样式：问答表格子不是 label 选择器依赖', () => {
  const css = fs.readFileSync(
    require('path').join(REPO, 'pages', 'typesafe-console', 'style.css'), 'utf8'
  );
  // .qa-scope-cfg 必须有可点击尺寸，否则 ⋯ 点不到
  const m = css.match(/\.qa-scope-cfg\s*\{([^}]*)\}/);
  if (!m) throw new Error('style.css 中缺少 .qa-scope-cfg 规则');
  const body = m[1];
  if (!/width:\s*\d+px/.test(body) || !/height:\s*\d+px/.test(body)) {
    throw new Error('⋯ 按钮缺少明确尺寸，可能点不中：' + body.trim());
  }
});

t('qa 空表渲染占位', () => {
  TS.state.qaTableKey = 'group:333';
  TS.qa.loadDraft();
  if (!hosts['#qa-rows'].children.length) throw new Error('空表未渲染占位');
});

console.log('脚本加载错误: ' + (errors.length ? errors.join(' | ') : '(无)'));
Promise.all(pending).then(() => {
  console.log('');
  for (const [n, ok, msg] of checks) console.log((ok ? 'PASS  ' : 'FAIL  ') + n + (msg ? '  -> ' + msg : ''));
  const failed = checks.filter(c => !c[1]).length + errors.length;
  console.log('');
  console.log(failed === 0 ? 'SMOKE TEST OK (' + checks.length + ' 项)' : 'SMOKE TEST FAILED (' + failed + ')');
  process.exit(failed === 0 ? 0 : 1);
});