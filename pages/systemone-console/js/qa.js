/* ============================================================
   SystemOne 配置中心 · 固定问答表视图
   作用域切换（全局/群/私聊） / 问答对增删改 / 多 Q 一 A 分组 / KeyReply 导入 / 命中测试
   ============================================================ */
(function () {
  'use strict';

  const TS = window.TS;
  const $ = TS.$, $$ = TS.$$, esc = TS.esc, el = TS.el, format = TS.format, state = TS.state;

  let uid = 0;
  const nextId = () => 'qa' + (++uid);

  /* ── 表选择 ───────────────────────────────────────────── */
  function currentTable() {
    const data = state.qa;
    if (!data) return null;
    const key = state.qaTableKey;
    const list = data.tables || [];
    return list.find(t => t.key === key) || list.find(t => t.key === 'global') || list[0] || null;
  }

  function currentScope() {
    const t = currentTable();
    if (!t) return { scope: 'global', scope_id: '' };
    return { scope: t.scope, scope_id: t.scope_id, key: t.key };
  }

  /** 折叠态展示的答案摘要：图片由缩略图承载，这里只描述文本部分 */
  function answerSummaryOf(row) {
    const text = String(row.answerText || '').trim();
    const images = format.lines(row.answerImages);
    if (text) return text;
    if (images.length) return '（图片答案）';
    return '（未填写答案）';
  }

  /**
   * 折叠态的极小缩略图。
   * 图片无法加载时（外链失效或页面 CSP 限制）退化为可点击的链接文字，
   * 避免留下一张破图；多条图片时角标显示剩余数量。
   */
  function answerThumb(row) {
    const images = format.lines(row.answerImages);
    if (!images.length) return null;
    const url = images[0];

    const link = el('a', 'qa-thumb');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noreferrer noopener';
    link.title = images.length > 1 ? url + ' （共 ' + images.length + ' 张）' : url;

    const img = el('img', 'qa-thumb-img');
    img.src = url;
    img.alt = '图片答案';
    img.loading = 'lazy';
    img.referrerPolicy = 'no-referrer';
    img.addEventListener('error', () => {
      link.textContent = '';
      link.classList.add('is-broken');
      link.appendChild(el('span', 'qa-thumb-fallback', '图片链接'));
    });
    link.appendChild(img);

    if (images.length > 1) {
      link.appendChild(el('span', 'qa-thumb-more', '+' + (images.length - 1)));
    }
    return link;
  }

  function tableIcon(t) {
    if (!t || t.scope === 'global') return '全局';
    return t.scope === 'group' ? '群' : '私聊';
  }

  function tableTitle(t) {
    if (!t) return '—';
    if (t.scope === 'global') return '全局默认表';
    if (t.name) return t.name;
    const base = t.scope === 'group' ? '群聊表' : '私聊表';
    if (t.ids.length === 1) return base + ' · ' + t.ids[0];
    return base + ' · ' + t.ids[0] + ' 等 ' + t.ids.length + ' 个会话';
  }

  /* ── 草稿：以「答案」为中心组织 ─────────────────────────
     数据结构仍然是一组问答对（每条 Q 带 answer_key），
     但编辑时按答案分组：一个 A 下挂若干 Q，A 与 hint 只写一次。
     ─────────────────────────────────────────────────────── */
  function loadDraft() {
    const table = currentTable();
    state.qaTableKey = table ? table.key : 'global';
    const entries = (table && table.entries) ? table.entries : [];

    // 按「答案内容 + hint」聚合为组；同组共享一份 A 与 hint
    const groups = [];
    const bySig = new Map();
    entries.forEach(en => {
      const ans = en.answer || {};
      const images = (ans.images || []).join('\n');
      const hint = en.hint || '';
      const sig = (ans.text || '') + '\u0000' + images + '\u0000' + hint;
      let g = bySig.get(sig);
      if (!g) {
        g = {
          _id: nextId(),
          answerText: ans.text || '',
          answerImages: images,
          hint: hint,
          questions: [],
          enabled: en.enabled !== false,
          answerKey: en.answer_key || ''
        };
        bySig.set(sig, g);
        groups.push(g);
      }
      g.questions.push({
        _id: nextId(),
        text: en.question || '',
        enabled: en.enabled !== false,
        jev: en.jev !== false
      });
      if (en.enabled === false) g.enabled = false;
    });

    if (!groups.length) {
      groups.push({ _id: nextId(), answerText: '', answerImages: '', hint: '', questions: [], enabled: true, answerKey: '' });
    }
    state.qaGroups = groups;
    state.qaDirty = false;
    render();
  }

  function collectDraft() {
    const out = [];
    (state.qaGroups || []).forEach(g => {
      const answerText = String(g.answerText || '');
      const images = format.lines(g.answerImages);
      const hint = String(g.hint || '').trim();
      const qs = (g.questions || []).filter(q => String(q.text || '').trim());
      // 没有 Q 的组不保存（避免留下无引用的空答案）
      qs.forEach(q => {
        const answer = { text: answerText, images: images };
        const entry = {
          question: String(q.text).trim(),
          answer: answer,
          enabled: q.enabled !== false && g.enabled !== false,
          // 条目级：是否交给 Jev 做相关性判定（默认开启）
          jev: q.jev !== false
        };
        // hint 是答案级的：只写在每个 Q 上，保存后由后端归入同一个答案池
        if (hint) entry.hint = hint;
        // 复用原有分组键，保证「一 A 多 Q」保存后仍是一组
        if (g.answerKey) entry.answer_key = g.answerKey;
        out.push(entry);
      });
    });
    return out;
  }

  /* ── 渲染 ─────────────────────────────────────────────── */
  function renderScopeBar() {
    const host = $('#qa-scope-bar');
    if (!host) return;
    host.textContent = '';
    const data = state.qa || {};
    const tables = data.tables || [];
    const cur = currentTable();

    const list = el('div', 'chip-group');
    tables.forEach(t => {
      const active = cur && t.key === cur.key;
      // 必须是 div 而非 label：label 会把内部按钮当作 labelable 控件，
      // 导致点击格子任意位置都会连带触发 ⋯ 按钮
      const chip = el('div', 'chip qa-scope-chip' + (active ? ' is-checked' : ''));
      chip.setAttribute('role', 'button');
      chip.tabIndex = 0;
      chip.appendChild(el('span', 'qa-scope-kind', tableIcon(t)));
      chip.appendChild(el('span', 'qa-scope-name', tableTitle(t)));
      chip.appendChild(el('span', 'qa-scope-count', t.entries.length + ' 条'));
      const selectThis = async () => {
        if (state.qaDirty) {
          const go = await TS.confirmDialog(
            '当前改动尚未保存，切换问答表将丢弃这些改动。',
            { title: '放弃未保存的改动', okLabel: '继续切换', danger: true }
          );
          if (!go) return;
        }
        state.qaTableKey = t.key;
        loadDraft();
      };
      chip.addEventListener('click', selectThis);
      chip.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectThis(); }
      });

      // 每张非全局表都提供一个「配置群」入口，用于编辑服务范围与名称
      if (t.scope !== 'global') {
        const cfg = el('button', 'qa-scope-cfg', '⋯');
        cfg.type = 'button';
        cfg.title = '打开群配置';
        cfg.addEventListener('click', (ev) => {
          // 只弹群配置，且不得冒泡去触发所在格子的「切换问答表」
          ev.stopPropagation();
          openTableConfig(t);
        });
        cfg.addEventListener('keydown', (ev) => ev.stopPropagation());
        chip.appendChild(cfg);
      }
      list.appendChild(chip);
    });

    // 多群一域入口
    const add = el('button', 'btn btn-ghost btn-sm', '+ 新建群配置');
    add.type = 'button';
    add.addEventListener('click', () => openTableConfig(null));
    list.appendChild(add);

    host.appendChild(list);
  }

  function renderRows() {
    const host = $('#qa-rows');
    if (!host) return;
    host.textContent = '';

    const groups = state.qaGroups || [];
    if (!groups.length) {
      host.appendChild(el('div', 'empty', '该作用域还没有问答对。点下方「新增一组」开始，或从 KeyReply 导入。'));
      return;
    }

    groups.forEach((g, gi) => host.appendChild(buildGroup(g, gi)));
  }

  /** 一个答案组：A + hint 只写一次，下面挂若干 Q。 */
  function buildGroup(group, gi) {
    const card = el('div', 'qa-group-card' + (group.enabled === false ? ' is-disabled' : ''));

    /* ── 组头：序号 + 联动的 Q 数 + 启用 + 删除 ── */
    const head = el('div', 'qa-group-card-head');
    head.appendChild(el('span', 'qa-group-card-index', '答案 #' + (gi + 1)));

    const qCount = (group.questions || []).length;
    const badge = el('span', 'pill ' + (qCount > 1 ? 'pill-brand' : 'pill-mute'),
      qCount > 1 ? qCount + ' 个问题共用' : (qCount === 1 ? '1 个问题' : '尚未添加问题'));
    head.appendChild(badge);

    const toggleWrap = el('label', 'switch');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = group.enabled !== false;
    cb.title = '停用整组';
    cb.addEventListener('change', () => {
      group.enabled = cb.checked;
      card.classList.toggle('is-disabled', !cb.checked);
      state.qaDirty = true;
      updateDirty();
    });
    toggleWrap.appendChild(cb);
    toggleWrap.appendChild(el('span', 'switch-track'));
    head.appendChild(toggleWrap);

    const del = el('button', 'btn btn-danger btn-sm', '删除整组');
    del.type = 'button';
    del.style.marginLeft = 'auto';
    del.addEventListener('click', () => {
      state.qaGroups = state.qaGroups.filter(x => x !== group);
      state.qaDirty = true;
      renderRows();
      updateDirty();
    });
    head.appendChild(del);
    card.appendChild(head);

    const body = el('div', 'qa-group-card-body');

    /* ── 答案 A（只写一次）── */
    const aField = el('div', 'field');
    const aHead = el('div', 'field-head');
    aHead.appendChild(el('label', 'field-label', '答案 A'));
    aHead.appendChild(el('span', 'field-hint', '被下面所有问题共用'));
    // 图片缩略图：加载失败自动退化为链接文字
    const thumb = answerThumb({ answerImages: group.answerImages });
    if (thumb) {
      thumb.classList.add('qa-thumb-inline');
      aHead.appendChild(thumb);
    }
    aField.appendChild(aHead);
    const aArea = el('textarea', 'textarea');
    aArea.rows = 3;
    aArea.value = group.answerText;
    aArea.placeholder = '命中后由 LLM 围绕这段内容生成回复；Jev 也会看到它';
    aArea.addEventListener('input', () => {
      group.answerText = aArea.value;
      state.qaDirty = true;
      updateDirty();
    });
    aField.appendChild(aArea);

    const imgDetails = el('details', 'qa-images');
    if (format.lines(group.answerImages).length) imgDetails.open = true;
    imgDetails.appendChild(el('summary', null, '答案附带图片（可选）'));
    const imgArea = el('textarea', 'textarea');
    imgArea.rows = 2;
    imgArea.value = group.answerImages;
    imgArea.placeholder = '每行一个图片 URL';
    imgArea.addEventListener('input', () => {
      group.answerImages = imgArea.value;
      state.qaDirty = true;
      updateDirty();
    });
    imgDetails.appendChild(imgArea);
    aField.appendChild(imgDetails);
    body.appendChild(aField);

    /* ── Jev 判定辅助说明（答案级，只写一次）── */
    const hField = el('div', 'field');
    const hHead = el('div', 'field-head');
    hHead.appendChild(el('label', 'field-label', 'Jev 判定辅助说明（可选）'));
    hHead.appendChild(el('span', 'field-hint', '不会发给用户，仅用于判定'));
    hField.appendChild(hHead);
    const hArea = el('textarea', 'textarea');
    hArea.rows = 2;
    hArea.value = group.hint;
    hArea.placeholder = '例如：该回答适合用户询问金锭怎么做的语境';
    hArea.addEventListener('input', () => {
      group.hint = hArea.value;
      state.qaDirty = true;
      updateDirty();
    });
    hField.appendChild(hArea);
    hField.appendChild(el('p', 'field-desc',
      'Jev 会同时看到这条答案、下面每个问题与这段说明，据此判断消息是真提问还是假命中。'));
    body.appendChild(hField);

    /* ── 问题列表：挂在答案下 ── */
    const qWrap = el('div', 'qa-q-list');
    const qHead = el('div', 'qa-q-list-head');
    qHead.appendChild(el('span', 'field-label', '问题 Q（支持 % 通配）'));
    qHead.appendChild(el('span', 'field-hint', '这些问法都指向上面这条答案'));
    qWrap.appendChild(qHead);

    (group.questions || []).forEach((q, qi) => {
      const row = el('div', 'qa-q-row');
      row.appendChild(el('span', 'qa-q-row-index', 'Q' + (qi + 1)));

      const qInput = el('input', 'input');
      qInput.type = 'text';
      qInput.value = q.text;
      qInput.placeholder = '用户可能怎么问，例如 金锭怎么做';
      qInput.addEventListener('input', () => {
        q.text = qInput.value;
        state.qaDirty = true;
        updateDirty();
      });
      row.appendChild(qInput);

      // 条目级 Jev 判定开关：关闭则命中即直接回复该答案
      const jevWrap = el('label', 'qa-q-jev' + (q.jev === false ? ' is-off' : ''));
      const jcb = el('input');
      jcb.type = 'checkbox';
      jcb.checked = q.jev !== false;
      jcb.title = '是否交给 Jev 判断真提问/假命中；关闭则命中即直接回复';
      jcb.addEventListener('change', () => {
        q.jev = jcb.checked;
        jevWrap.classList.toggle('is-off', !jcb.checked);
        label.textContent = jcb.checked ? 'Jev 判定' : '直接回复';
        state.qaDirty = true;
        updateDirty();
      });
      jevWrap.appendChild(jcb);
      const label = el('span', 'qa-q-jev-label', q.jev === false ? '直接回复' : 'Jev 判定');
      jevWrap.appendChild(label);
      row.appendChild(jevWrap);

      const qToggle = el('label', 'switch');
      const qcb = el('input');
      qcb.type = 'checkbox';
      qcb.checked = q.enabled !== false;
      qcb.title = '停用这条问题';
      qcb.addEventListener('change', () => {
        q.enabled = qcb.checked;
        state.qaDirty = true;
        updateDirty();
      });
      qToggle.appendChild(qcb);
      qToggle.appendChild(el('span', 'switch-track'));
      row.appendChild(qToggle);

      const qDel = el('button', 'btn btn-ghost btn-sm', '移除');
      qDel.type = 'button';
      qDel.addEventListener('click', () => {
        group.questions = group.questions.filter(x => x !== q);
        state.qaDirty = true;
        renderRows();
        updateDirty();
      });
      row.appendChild(qDel);
      qWrap.appendChild(row);
    });

    const addQ = el('button', 'btn btn-ghost btn-sm', '+ 加一个问法');
    addQ.type = 'button';
    addQ.addEventListener('click', () => {
      group.questions = group.questions || [];
      group.questions.push({ _id: nextId(), text: '', enabled: true, jev: true });
      state.qaDirty = true;
      renderRows();
      updateDirty();
    });
    qWrap.appendChild(addQ);
    body.appendChild(qWrap);

    card.appendChild(body);
    return card;
  }

  function updateDirty() {
    const btn = $('#qa-save-btn');
    if (btn) btn.disabled = !state.qaDirty;
    const note = $('#qa-note');
    if (note) {
      const n = (state.qaGroups || []).reduce((acc, g) => acc + (g.questions || []).length, 0);
      note.textContent = state.qaDirty
        ? '有未保存的改动（当前 ' + n + ' 条）'
        : '当前 ' + n + ' 条问答对，已与后端同步';
    }
    const badge = $('#qa-nav-dirty');
    if (badge) TS.show(badge, state.qaDirty);
  }

  function renderModeBar() {
    const host = $('#qa-mode-bar');
    if (!host) return;
    const d = state.qa || {};
    host.textContent = '';
    host.appendChild(el('span', 'field-label', '当前流程：'));
    host.appendChild(el('span', 'pill pill-ok', '固定问答表'));
    host.appendChild(el('span', 'pill pill-brand', 'Jev 相关性判定'));
    host.appendChild(el('span', 'field-meta',
      '是否判定由每条问答对单独控制 · 最低置信度 ' + (d.qa_min_confidence || '中')
      + ' · 上下文 ' + format.num(d.context_message_count) + ' 条'));
  }

  /* ── 群配置弹窗（多群一域）────────────────────────────── */
  let editingKey = null;
  let editingIds = [];
  let editingName = '';

  function openTableConfig(table) {
    editingKey = table ? table.key : null;
    editingIds = table ? table.ids.slice() : [];
    editingName = table ? (table.name || '') : '';
    const modal = $('#qa-cfg-modal');
    if (!modal) return;
    modal.removeAttribute('hidden');
    document.body.style.overflow = 'hidden';
    const title = $('#qa-cfg-title');
    if (title) title.textContent = table ? '编辑群配置' : '新建群配置';
    const delBtn = $('#qa-cfg-del');
    if (delBtn) TS.show(delBtn, Boolean(table));
    const nameInput = $('#qa-cfg-name');
    if (nameInput) nameInput.value = editingName;
    const idInput = $('#qa-cfg-id');
    if (idInput) { idInput.value = ''; idInput.focus(); }
    renderIdChips();
  }

  function closeTableConfig() {
    const modal = $('#qa-cfg-modal');
    if (modal) modal.setAttribute('hidden', '');
    document.body.style.overflow = '';
  }

  function renderIdChips() {
    const host = $('#qa-cfg-ids');
    if (!host) return;
    host.textContent = '';
    if (!editingIds.length) {
      host.appendChild(el('span', 'field-meta', '尚未添加任何群号，至少添加一个才能保存'));
      return;
    }
    editingIds.forEach(id => {
      const chip = el('span', 'chip is-checked qa-id-chip');
      chip.appendChild(el('span', null, id));
      const x = el('button', 'qa-id-remove', '×');
      x.type = 'button';
      x.addEventListener('click', () => {
        editingIds = editingIds.filter(v => v !== id);
        renderIdChips();
      });
      chip.appendChild(x);
      host.appendChild(chip);
    });
  }

  function addIdFromInput() {
    const input = $('#qa-cfg-id');
    if (!input) return;
    const value = input.value.trim();
    if (!value) return;
    if (editingIds.indexOf(value) >= 0) {
      TS.toast('该 ID 已在列表中', 'err');
      return;
    }
    // 支持一次粘贴多个（换行/逗号分隔），便于批量配置多群
    format.lines(value).forEach(v => {
      if (editingIds.indexOf(v) < 0) editingIds.push(v);
    });
    input.value = '';
    renderIdChips();
  }

  async function saveTableConfig() {
    const nameInput = $('#qa-cfg-name');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!editingIds.length) {
      TS.toast('请至少添加一个群号', 'err');
      return;
    }

    const btn = $('#qa-cfg-save');
    if (btn) btn.classList.add('is-busy');
    try {
      /* 这里【刻意不提交 entries】：弹窗只负责「服务哪些群号 + 名称」，
         问答对由问答表编辑页维护，后端在缺省 entries 时原样保留。
         曾经把 currentTable() 的问答对一并提交，当选中表不是本表时
         （默认就是全局表，且很可能没有问答对）会把本表的问答对整体覆盖掉。 */
      const result = await TS.api.qaSave({
        key: editingKey || undefined,
        scope: 'group',
        ids: editingIds,
        name: name
      });
      state.qaTableKey = (result && result.table && result.table.key) || editingKey;
      state.qaDirty = false;
      closeTableConfig();
      TS.toast(editingKey ? '群配置已更新' : '群配置已创建', 'ok');
      await load();
    } catch (error) {
      TS.toast('保存失败：' + (error && error.message ? error.message : error), 'err');
    } finally {
      if (btn) btn.classList.remove('is-busy');
    }
  }

  async function deleteTable() {
    if (!editingKey) return;
    const okDelete = await TS.confirmDialog(
      '删除这张问答表？其中的问答对将一并移除，且无法撤销。',
      { title: '删除问答表', okLabel: '删除', danger: true }
    );
    if (!okDelete) return;
    try {
      await TS.api.qaDelete({ key: editingKey });
      state.qaTableKey = 'global';
      state.qaDirty = false;
      closeTableConfig();
      TS.toast('问答表已删除', 'ok');
      await load();
    } catch (error) {
      TS.toast('删除失败：' + (error && error.message ? error.message : error), 'err');
    }
  }

  function render() {
    if (!state.qa) return;
    renderModeBar();
    renderScopeBar();
    renderRows();
    updateDirty();
  }

  /* ── 加载 ─────────────────────────────────────────────── */
  async function load() {
    const host = $('#qa-rows');
    if (host && !state.qa) host.innerHTML = '<div class="skeleton" style="height:90px"></div>';
    try {
      state.qa = await TS.api.qaList();
    } catch (error) {
      TS.toast('读取问答表失败：' + (error && error.message ? error.message : error), 'err');
      state.qa = { tables: [], summary: {} };
    }
    const summary = (state.qa && state.qa.summary) || {};
    const s = currentScope();
    // 当前作用域若已不存在（被删除或首次进入），回退到全局表
    if (s.scope_id) {
      const exists = (s.scope === 'group' ? summary.groups : summary.privates) || [];
      if (exists.indexOf(s.scope_id) < 0) state.qaScope = { scope: 'global', scope_id: '' };
    }
    loadDraft();
    renderImportHint();
  }

  function renderImportHint() {
    const host = $('#qa-import-hint');
    if (!host || !state.qa) return;
    host.textContent = '';
    const cands = state.qa.import_candidates || [];
    if (cands.length) {
      host.appendChild(el('span', null, '已探测到 ' + cands[0]));
    } else {
      const warn = el('span', null, '未探测到 KeyReply 数据文件，可点「手动指定路径」填写 triggers.yml 的位置');
      warn.style.color = 'var(--warning)';
      host.appendChild(warn);
    }
    if (state.qa.data_file) {
      host.appendChild(el('br'));
      host.appendChild(el('span', 'field-meta', '本插件数据文件：' + state.qa.data_file));
    }
    // 路径不再来自插件配置，仅用探测结果预填，用户也可在「手动指定路径」里改
    const input = $('#qa-import-path');
    if (input && !input.value && cands.length) input.value = cands[0];
  }

  /* ── 操作 ─────────────────────────────────────────────── */
  async function save() {
    const btn = $('#qa-save-btn');
    if (btn) btn.classList.add('is-busy');
    try {
      const s = currentScope();
      const t = currentTable();
      await TS.api.qaSave({
        key: s.key,
        scope: s.scope,
        scope_id: s.scope_id,
        ids: t ? t.ids : (s.scope_id ? [s.scope_id] : []),
        name: t ? t.name : '',
        entries: collectDraft(),
        // 编辑页是问答对的唯一来源：这里明确允许用空列表清空
        // （后端默认拒绝空列表覆盖非空表，防止误清空）
        allow_empty: true
      });
      state.qaDirty = false;
      TS.toast('问答表已保存', 'ok');
      await load();
    } catch (error) {
      TS.toast('保存失败：' + (error && error.message ? error.message : error), 'err');
    } finally {
      if (btn) btn.classList.remove('is-busy');
      updateDirty();
    }
  }

  async function importFromKeyReply() {
    const pathInput = $('#qa-import-path');
    const replaceBox = $('#qa-import-replace');
    const path = pathInput ? pathInput.value.trim() : '';
    const replace = Boolean(replaceBox && replaceBox.checked);

    if (replace) {
      const go = await TS.confirmDialog(
        '覆盖模式会用 KeyReply 的问答表整体替换当前作用域的内容，原有问答对将被移除。',
        { title: '覆盖导入', okLabel: '覆盖', danger: true }
      );
      if (!go) return;
    }

    const btn = $('#qa-import-btn');
    if (btn) btn.classList.add('is-busy');
    try {
      const s = currentScope();
      const result = await TS.api.qaImport({
        scope: s.scope,
        scope_id: s.scope_id,
        path: path,
        replace: replace
      });
      if (result && result.ok) {
        state.qaDirty = false;
        TS.toast(result.message || '复制完成', 'ok');
        await load();
        renderImportResult(result);
      } else {
        TS.toast((result && result.message) || '复制失败', 'err');
        renderImportResult(result || {});
      }
    } catch (error) {
      TS.toast('复制失败：' + (error && error.message ? error.message : error), 'err');
    } finally {
      if (btn) btn.classList.remove('is-busy');
    }
  }

  /* 复制结果明细：把来源与目标路径显示出来，便于排查 */
  function renderImportResult(result) {
    const host = $('#qa-import-hint');
    if (!host) return;
    host.textContent = '';
    if (result && result.source_path) {
      host.appendChild(el('span', null, '来源：' + result.source_path));
      host.appendChild(el('br'));
    }
    if (result && result.target_path) {
      const target = el('span', null, '已存入：' + result.target_path);
      target.style.color = 'var(--success)';
      host.appendChild(target);
    }
    if (result && result.message && !result.ok) {
      host.appendChild(el('span', null, result.message));
    }
    // 探测失败时列出找过的位置
    if (result && Array.isArray(result.searched) && result.searched.length) {
      const det = el('details', 'qa-images');
      det.appendChild(el('summary', null, '已尝试查找这些位置'));
      const ul = el('div', 'field-meta');
      ul.innerHTML = result.searched.map(x => '· ' + esc(x)).join('<br>');
      det.appendChild(ul);
      host.appendChild(det);
    }
  }

  /* ── 命中测试 ─────────────────────────────────────────── */
  async function runTest() {
    const input = $('#qa-test-input');
    const text = input ? input.value.trim() : '';
    if (!text) { TS.toast('请先输入要测试的消息', 'err'); return; }
    const host = $('#qa-test-result');
    try {
      const s = currentScope();
      const r = await TS.api.qaTest({ text: text, scope: s.scope, scope_id: s.scope_id });
      state.qaTestResult = r;
      if (host) {
        host.textContent = '';
        const rows = [];
        rows.push(['最终结果', r.would_reply
          ? '<span class="pill pill-ok">会回复</span>'
          : '<span class="pill pill-mute">保持静默</span>']);
        rows.push(['当前模式', esc(r.mode_label || r.mode || '—')]);
        if (r.classic) {
          const cImgs = r.classic.images || [];
          const cAns = String(r.classic.answer || '').trim()
            ? esc(String(r.classic.answer).slice(0, 60))
            : (cImgs.length ? '（纯图片答案 · ' + cImgs.length + ' 张）' : '（空答案）');
          rows.push(['正则召回', '<span class="pill pill-ok">命中</span> ' + esc(r.classic.question)
            + '<br><span class="field-meta">来源 ' + esc(r.classic.table_label || '') + ' · 答案：'
            + cAns + '</span>']);
        } else {
          rows.push(['正则召回', '<span class="pill pill-mute">未命中</span> <span class="field-meta">不会触发 Jev</span>']);
        }
        if (r.jev) {
          rows.push(['Jev 审核', r.jev.matched
            ? '<span class="pill pill-brand">确认话题</span> ' + esc(r.jev.question || '')
            : '<span class="pill pill-mute">未确认</span> <span class="field-meta">' + esc(r.jev.reason || '') + '</span>']);
          if (r.jev.matched) {
            rows.push(['置信度', esc(r.jev.confidence_level || '') + ' (' + Number(r.jev.confidence_score || 0).toFixed(2) + ')'
              + (r.jev.confidence_ok ? ' <span class="pill pill-ok">达标</span>' : ' <span class="pill pill-bad">低于门槛</span>')]);
            const jImgs = r.jev.images || [];
            if (r.jev.answer) {
              rows.push(['将围绕此答案生成', esc(String(r.jev.answer).slice(0, 80))]);
            } else if (jImgs.length) {
              rows.push(['将直接发送图片', esc(jImgs.join(' , ').slice(0, 80))]);
            }
          }
          rows.push(['耗时', format.ms(r.jev.elapsed_ms)]);
        }
        rows.push(['候选话题数', format.num(r.candidate_count)]);
        const kv = el('dl', 'kv');
        rows.forEach(([k, v]) => {
          kv.appendChild(el('dt', null, k));
          const dd = el('dd'); dd.innerHTML = v;
          kv.appendChild(dd);
        });
        host.appendChild(kv);
      }
    } catch (error) {
      TS.toast('测试失败：' + (error && error.message ? error.message : error), 'err');
    }
  }

  function bind() {
    $('#qa-save-btn')?.addEventListener('click', () => save());
    $('#qa-add-row')?.addEventListener('click', () => {
      state.qaGroups = state.qaGroups || [];
      state.qaGroups.push({
        _id: nextId(), answerText: '', answerImages: '', hint: '',
        questions: [{ _id: nextId(), text: '', enabled: true, jev: true }],
        enabled: true, answerKey: ''
      });
      state.qaDirty = true;
      renderRows();
      updateDirty();
    });
    $('#qa-import-btn')?.addEventListener('click', () => importFromKeyReply());
    $('#qa-import-toggle')?.addEventListener('click', () => {
      const row = $('#qa-import-path-row');
      if (row) {
        const hidden = row.hasAttribute('hidden');
        if (hidden) row.removeAttribute('hidden');
        else row.setAttribute('hidden', '');
      }
    });
    $('#qa-test-btn')?.addEventListener('click', () => runTest());
    $('#qa-add-scope')?.addEventListener('click', () => openTableConfig(null));
    $('#qa-cfg-close')?.addEventListener('click', () => closeTableConfig());
    $('#qa-cfg-cancel')?.addEventListener('click', () => closeTableConfig());
    $('#qa-cfg-save')?.addEventListener('click', () => saveTableConfig());
    $('#qa-cfg-del')?.addEventListener('click', () => deleteTable());
    $('#qa-cfg-add')?.addEventListener('click', () => addIdFromInput());
    $('#qa-cfg-id')?.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); addIdFromInput(); }
    });
  }

  Object.assign(TS, {
    qa: {
      load: load, render: render, bind: bind, loadDraft: loadDraft,
      openTableConfig: openTableConfig, closeTableConfig: closeTableConfig,
      addIdFromInput: addIdFromInput
    }
  });
})();