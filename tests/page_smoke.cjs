/* ============================================================
   插件页冒烟测试
   在最小 DOM 桩下真实执行 pages/systemone-console/js/*.js，
   捕获 ReferenceError（如曾经的 "$$ is not defined"）与渲染期异常。

   运行： node tests/page_smoke.cjs
   ============================================================ */
/* 页面冒烟测试 v2：更忠实的 DOM 桩（支持子树查询与 window.setTimeout）。 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const REPO = require('path').resolve(__dirname, '..');
const DIR = require('path').join(REPO, 'pages', 'systemone-console', 'js') + require('path').sep;
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
t('渲染覆盖全部 schema 字段控件', () => {
  TS.config.render();
  const rendered = document.querySelectorAll('[data-field]').length;
  const expected = Object.keys(schema).length;
  if (rendered < expected) throw new Error('渲染字段数不足: ' + rendered + '/' + expected);
});
t('列表控件 collect 按行拆分', () => {
  // 用一个仍然存在的列表字段作为样本（关键词过滤字段已随「消息过滤与触发」移除）
  TS.state.dirty.clear(); TS.state.dirty.add('session_blacklist');
  const el2 = document.querySelector('[data-field="session_blacklist"]');
  if (!el2) throw new Error('找不到 session_blacklist 控件');
  el2.value = 'a\nb\nc';
  const patch = TS.config.collect();
  if (JSON.stringify(patch.session_blacklist) !== JSON.stringify(['a','b','c'])) throw new Error(JSON.stringify(patch.session_blacklist));
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
    force_trigger_regex: '', ignore_regex: '',
    base_url: '', base_url_effective: 'https://api.typesafe.ai', base_url_supported: true };
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

/* ── 固定问答表视图（答案为中心的分组卡片）──────────── */
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
        { question: '多图问题', answer: { text: '', images: ['https://example.test/a.jpg', 'https://example.test/b.jpg'] }, enabled: true },
        { question: '带说明的问题', answer: { text: '有说明的答案', images: [] }, hint: '该答案适用于询问语境的场景', enabled: true }
      ]},
    { key: 'group:333', scope: 'group', scope_id: '333', ids: ['333'], name: '', label: '群聊表 · 333', entries: [] }
  ],
  summary: { global_entries: 2, groups: ['111', '222', '333'], privates: [], total_entries: 3, table_count: 3 },
  import_candidates: [], data_file: '/tmp/qa_tables.json',
  enable_jev_topic: true, mode: 'jev', mode_label: 'Jev 话题模式',
  qa_min_confidence: '中', context_message_count: 3
});

const walkAll = root => { const out = []; const w = n => { for (const c of n.children) { out.push(c); w(c); } }; w(root); return out; };
// 状态页的 <dd> 用 innerHTML 赋值，DOM 桩里 textContent 不含它，因此单独收集
const dsOf = root => walkAll(root).filter(n => n.tagName === 'DD');
// 精确类名匹配：避免 'qa-group-card' 误匹配 'qa-group-card-head'
const hasClass = (n, cls) => String(n.className || '').split(/\s+/).indexOf(cls) >= 0;

t('qa.render 表列表与分组卡片', () => {
  TS.state.qa = qaFixture();
  TS.state.qaTableKey = 'global';
  TS.qa.loadDraft();
  if (!hosts['#qa-rows'].children.length) throw new Error('未渲染任何分组卡片');
  if (!hosts['#qa-scope-bar'].children.length) throw new Error('未渲染问答表列表');
  if (!hosts['#qa-mode-bar'].children.length) throw new Error('未渲染模式条');
});

t('qa 多 Q 一 A 合并为一个答案卡片', () => {
  const cards = walkAll(hosts['#qa-rows']).filter(n => hasClass(n, 'qa-group-card'));
  if (cards.length !== 1) throw new Error('两条同答案 Q 应合并为 1 张卡片，实际 ' + cards.length);
  const qRows = walkAll(cards[0]).filter(n => hasClass(n, 'qa-q-row'));
  if (qRows.length !== 2) throw new Error('卡片下应有 2 个问题输入行，实际 ' + qRows.length);
});

t('qa 答案与辅助说明只渲染一次', () => {
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
  const cards = walkAll(hosts['#qa-rows']).filter(n => hasClass(n, 'qa-group-card'));
  // 4 条 Q，其中「群专属问题」与「带说明的问题」答案不同 -> 至少 3 个答案卡片
  if (cards.length < 3) throw new Error('答案分组数不足: ' + cards.length);
  const textareasPerCard = cards.map(c => walkAll(c).filter(n => n.tagName === 'TEXTAREA').length);
  // 每张卡片固定 3 个 textarea（答案 A、图片、辅助说明），不随 Q 数增长
  const bad = textareasPerCard.filter(n => n !== 3);
  if (bad.length) throw new Error('答案级输入框数量异常（应为 3）: ' + textareasPerCard.join(','));
});

t('qa 每条问答对渲染 Jev 判定开关', () => {
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
  const rows = walkAll(hosts['#qa-rows']).filter(n => hasClass(n, 'qa-q-row'));
  if (!rows.length) throw new Error('未渲染问答对行');
  const switches = rows.map(r => walkAll(r).find(n => hasClass(n, 'qa-q-jev')));
  if (switches.some(s => !s)) throw new Error('存在缺少 Jev 开关的问答对行');
  // 全部默认开启
  const offs = switches.filter(s => hasClass(s, 'is-off'));
  if (offs.length) throw new Error('默认应全部开启，实际有 ' + offs.length + ' 条为关闭');
  const labels = switches.map(s => String(s.textContent || '').trim());
  if (labels.some(l => l.indexOf('Jev 判定') < 0)) throw new Error('开关文案异常: ' + labels.join(','));
});

t('qa jev=False 的条目显示为直接回复', () => {
  TS.state.qa.tables.find(x => x.key === 'group:111').entries.push(
    { question: '直接回复条目', answer: { text: '直答', images: [] }, jev: false, enabled: true }
  );
  TS.qa.loadDraft();
  const switches = walkAll(hosts['#qa-rows'])
    .filter(n => hasClass(n, 'qa-q-row'))
    .map(r => walkAll(r).find(n => hasClass(n, 'qa-q-jev')));
  const off = switches.filter(s => hasClass(s, 'is-off'));
  if (off.length !== 1) throw new Error('应恰好有 1 条为关闭，实际 ' + off.length);
  if (String(off[0].textContent).indexOf('直接回复') < 0) {
    throw new Error('关闭态文案应为「直接回复」: ' + off[0].textContent);
  }
});

t('qa 辅助说明随答案保留', () => {
  const groups = TS.state.qaGroups || [];
  const withHint = groups.filter(g => String(g.hint || '').indexOf('询问语境') >= 0);
  if (withHint.length !== 1) throw new Error('未正确载入辅助说明，命中 ' + withHint.length + ' 组');
});

t('qa 图片答案渲染极小缩略图', () => {
  const nodes = walkAll(hosts['#qa-rows']);
  const thumbs = nodes.filter(n => hasClass(n, 'qa-thumb'));
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
  const after = walkAll(hosts['#qa-rows']).filter(n => hasClass(n, 'qa-thumb'));
  if (!after.length) throw new Error('图片答案未渲染缩略图');
});

t('qa 缩略图加载失败退化为链接文字', () => {
  const thumbs = walkAll(hosts['#qa-rows']).filter(n => hasClass(n, 'qa-thumb'));
  const thumb = thumbs[0];
  const img = thumb.children.find(c => c.tagName === 'IMG');
  if (!img || !img._ev || !img._ev.error) throw new Error('缩略图未注册 error 回退');
  img._ev.error({});
  if (String(thumb.className).indexOf('is-broken') < 0) throw new Error('加载失败后未标记 is-broken');
});

t('qa 表格子不是 label（否则点任意位置会触发 ⋯ 按钮）', () => {
  const chips = walkAll(hosts['#qa-scope-bar']).filter(n => hasClass(n, 'qa-scope-chip'));
  if (!chips.length) throw new Error('未找到问答表格子');
  if (chips.some(c => c.tagName === 'LABEL')) throw new Error('格子仍是 <label>，点击正文会连带触发内部按钮');
});

t('qa 格子内的 ⋯ 按钮阻止冒泡且只弹配置', () => {
  const cfgBtns = walkAll(hosts['#qa-scope-bar']).filter(n => hasClass(n, 'qa-scope-cfg'));
  if (!cfgBtns.length) throw new Error('未找到 ⋯ 按钮');
  const chip = cfgBtns[0].parentNode;
  let chipClicks = 0;
  chip.addEventListener('click', () => { chipClicks++; });
  const ev = { stopped: false, stopPropagation() { this.stopped = true; }, preventDefault() {} };
  cfgBtns[0]._ev.click(ev);
  if (!ev.stopped) throw new Error('⋯ 未调用 stopPropagation');
  if (chipClicks !== 0) throw new Error('点击 ⋯ 触发了所在格子的切换');
  TS.qa.closeTableConfig();
});

t('qa 打开群配置弹窗', () => {
  const modal = hosts['#qa-cfg-modal'];
  if (!modal) throw new Error('缺少弹窗容器');
  TS.qa.openTableConfig(TS.state.qa.tables.find(x => x.key === 'group:111'));
  if (modal.hasAttribute && modal.hasAttribute('hidden')) throw new Error('弹窗未打开');
  if (!hosts['#qa-cfg-ids'].children.length) throw new Error('未渲染 ID 列表');
  TS.qa.closeTableConfig();
});

t('qa 群配置保存不提交问答对（防误覆盖其它表）', async () => {
  TS.state.qa = qaFixture();
  TS.state.qaTableKey = 'global';   // 事故条件：当前选中的是没有问答对的全局表
  TS.qa.loadDraft();
  TS.qa.bind();
  const saved = [];
  const origSave = TS.api.qaSave, origList = TS.api.qaList;
  TS.api.qaSave = payload => { saved.push(payload); return Promise.resolve({ table: { key: 'group:111' } }); };
  TS.api.qaList = () => Promise.resolve(qaFixture());
  try {
    TS.qa.openTableConfig(TS.state.qa.tables.find(x => x.key === 'group:111'));
    hosts['#qa-cfg-id'].value = '999';
    TS.qa.addIdFromInput();
    await hosts['#qa-cfg-save']._ev.click();
    if (saved.length !== 1) throw new Error('未发出保存请求: ' + saved.length);
    if ('entries' in saved[0]) {
      throw new Error('群配置保存不应提交 entries，实际提交了 '
        + JSON.stringify(saved[0].entries).slice(0, 120));
    }
    if (saved[0].key !== 'group:111') throw new Error('保存目标表不正确: ' + saved[0].key);
    const ids = saved[0].ids || [];
    if (ids.length !== 3 || ids.indexOf('999') < 0) throw new Error('ids 不正确: ' + JSON.stringify(ids));
  } finally {
    TS.api.qaSave = origSave; TS.api.qaList = origList;
    TS.qa.closeTableConfig();
  }
});

t('qa 编辑页保存提交 entries 并显式允许清空', async () => {
  TS.state.qa = qaFixture();
  TS.state.qaTableKey = 'group:111';
  TS.qa.loadDraft();
  TS.qa.bind();
  const saved = [];
  const origSave = TS.api.qaSave, origList = TS.api.qaList;
  TS.api.qaSave = payload => { saved.push(payload); return Promise.resolve({ table: { key: 'group:111' } }); };
  TS.api.qaList = () => Promise.resolve(qaFixture());
  try {
    await hosts['#qa-save-btn']._ev.click();
    if (saved.length !== 1) throw new Error('未发出保存请求: ' + saved.length);
    if (!Array.isArray(saved[0].entries)) throw new Error('编辑页保存必须提交 entries 数组');
    if (saved[0].entries.length !== 4) throw new Error('提交的问答对数量异常: ' + saved[0].entries.length);
    if (saved[0].allow_empty !== true) throw new Error('编辑页保存应显式声明 allow_empty');
  } finally {
    TS.api.qaSave = origSave; TS.api.qaList = origList;
  }
});

t('qa 空表渲染占位', () => {
  TS.state.qaTableKey = 'group:333';
  TS.qa.loadDraft();
  if (!hosts['#qa-rows'].children.length) throw new Error('空表未渲染占位');
});

t('renderStatus 展示 API 地址默认分支', () => {
  TS.views.renderStatus();
  const dl = dsOf(hosts['#status-body']);
  const hit = dl.find(n => String(n.innerHTML).indexOf('https://api.typesafe.ai') >= 0);
  if (!hit) throw new Error('未展示 API 地址');
  if (String(hit.innerHTML).indexOf('/v1/systemone') < 0) {
    throw new Error('未说明 SDK 会自动追加 /v1/systemone');
  }
});

t('renderStatus 展示自定义 Base URL 与后缀说明', () => {
  TS.state.status = Object.assign({}, TS.state.status, {
    base_url: 'https://gw.example.com/ts',
    base_url_effective: 'https://gw.example.com/ts',
    base_url_supported: true
  });
  TS.views.renderStatus();
  const hit = dsOf(hosts['#status-body'])
    .find(n => String(n.innerHTML).indexOf('https://gw.example.com/ts') >= 0);
  if (!hit) throw new Error('未展示自定义 Base URL');
  if (String(hit.innerHTML).indexOf('/v1/systemone') < 0) {
    throw new Error('未说明自动追加 /v1/systemone');
  }
});

t('renderStatus 对旧版 SDK 回退给出提示', () => {
  TS.state.status = Object.assign({}, TS.state.status, {
    base_url: 'https://gw.example.com/ts', base_url_supported: false
  });
  TS.views.renderStatus();
  const all = walkAll(hosts['#status-body'])
    .map(n => String(n.innerHTML) + String(n.textContent)).join(' ');
  if (all.indexOf('不支持自定义') < 0) throw new Error('回退未给出提示');
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