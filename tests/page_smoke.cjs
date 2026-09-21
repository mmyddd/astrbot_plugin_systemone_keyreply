/* ============================================================
   插件页冒烟测试
   在最小 DOM 桩下真实执行 pages/typesafe-console/js/*.js，
   捕获 ReferenceError（如曾经的 "$$ is not defined"）与渲染期异常。

   运行： node tests/page_smoke.cjs
   ============================================================ */
/* 页面冒烟测试 v2：更忠实的 DOM 桩（支持子树查询与 window.setTimeout）。 */
const vm = require('vm');
const fs = require('fs');
const REPO = require('path').resolve(__dirname, '..');
const DIR = require('path').join(REPO, 'pages', 'typesafe-console', 'js') + require('path').sep;
const SCHEMA = require('path').join(REPO, '_conf_schema.json');

function matchSel(n, sel) {
  sel = String(sel).trim();
  if (sel.includes(',')) return sel.split(',').some(s => matchSel(n, s));
  if (sel.startsWith('#')) return n.id === sel.slice(1);
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
    textContent: '', innerHTML: '', value: '', checked: false, type: '',
    id: '', className: '', placeholder: '', rows: 0, disabled: false, parentNode: null,
    classList: {
      _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, f) { if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (f) { this._s.add(c); } else { this._s.delete(c); } }
    },
    appendChild(c) { this.children.push(c); if (c) c.parentNode = this; return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    focus() {}, click() {},
    setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') this.id = v; },
    removeAttribute(k) { delete this.attrs[k]; }, getAttribute(k) { return this.attrs[k]; },
    addEventListener(ev, fn) { (this._ev = this._ev || {})[ev] = fn; },
    removeEventListener() {},
    querySelector(sel) { return descendants(this).find(n => matchSel(n, sel)) || null; },
    querySelectorAll(sel) { return descendants(this).filter(n => matchSel(n, sel)); }
  };
  function descendants(n) { const out = []; const walk = x => { for (const c of x.children) { out.push(c); walk(c); } }; walk(n); return out; }
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
const t = (n, fn) => { try { fn(); checks.push([n, true, '']); } catch (e) { checks.push([n, false, e.message]); } };

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
      entries: [{ question: '群专属问题', answer: { text: '群专属答案', images: [] }, enabled: true }]},
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

t('qa 空表渲染占位', () => {
  TS.state.qaTableKey = 'group:333';
  TS.qa.loadDraft();
  if (!hosts['#qa-rows'].children.length) throw new Error('空表未渲染占位');
});

console.log('脚本加载错误: ' + (errors.length ? errors.join(' | ') : '(无)'));
console.log('');
for (const [n, ok, msg] of checks) console.log((ok ? 'PASS  ' : 'FAIL  ') + n + (msg ? '  -> ' + msg : ''));
const failed = checks.filter(c => !c[1]).length + errors.length;
console.log('');
console.log(failed === 0 ? 'SMOKE TEST OK (' + checks.length + ' 项)' : 'SMOKE TEST FAILED (' + failed + ')');
process.exit(failed === 0 ? 0 : 1);