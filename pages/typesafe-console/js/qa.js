/* ============================================================
   TypeSafe 配置中心 · 固定问答表视图
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

  /* ── 草稿 ─────────────────────────────────────────────── */
  function loadDraft() {
    const table = currentTable();
    state.qaTableKey = table ? table.key : 'global';
    state.qaDraft = (table && table.entries ? table.entries : []).map(e => ({
      _id: nextId(),
      question: e.question || '',
      answerText: (e.answer && e.answer.text) || '',
      answerImages: ((e.answer && e.answer.images) || []).join('\n'),
      _hadAnswer: Boolean(e.answer && ((e.answer.text || '').trim() || (e.answer.images || []).length)),
      enabled: e.enabled !== false,
      answerKey: e.answer_key || ''
    }));
    state.qaDirty = false;
    render();
  }

  function collectDraft() {
    return state.qaDraft
      .filter(row => String(row.question || '').trim())
      .map(row => {
        const entry = {
          question: String(row.question).trim(),
          answer: {
            text: String(row.answerText || ''),
            images: format.lines(row.answerImages)
          },
          enabled: row.enabled !== false
        };
        // answer_key 由后端在保存时按答案指纹重新分组，这里不回传旧键
        return entry;
      });
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

    if (!state.qaDraft.length) {
      host.appendChild(el('div', 'empty', '该作用域还没有问答对。点下方「新增一条」开始，或从 KeyReply 导入。'));
      return;
    }

    // 按答案内容分组显示，直观呈现「多个 Q 指向同一个 A」
    const groups = new Map();
    state.qaDraft.forEach(row => {
      const sig = row.answerText + '\u0000' + row.answerImages;
      if (!groups.has(sig)) groups.set(sig, []);
      groups.get(sig).push(row);
    });

    let idx = 0;
    groups.forEach(rows => {
      const wrap = el('div', 'qa-group');
      // 仅当确实存在共享答案时才显示分组头，单条 Q 不额外占位
      if (rows.length > 1) {
        const head = el('div', 'qa-group-head');
        head.appendChild(el('span', 'pill pill-brand', '多 Q 一 A'));
        head.appendChild(el('span', 'qa-group-title', rows.length + ' 个问题共用同一答案'));
        wrap.appendChild(head);
      }
      rows.forEach(row => wrap.appendChild(buildRow(row, idx++)));
      host.appendChild(wrap);
    });
  }

  function buildRow(row, index) {
    // 默认折叠：折叠态只显示 Q 与 A，点击展开才出现全部编辑控件
    const item = el('details', 'qa-item' + (row.enabled === false ? ' is-disabled' : ''));
    item.open = Boolean(row._expand);

    /* ── 折叠摘要：只显示 Q 与 A ── */
    const summary = el('summary', 'qa-item-summary');
    summary.appendChild(el('span', 'qa-item-index', '#' + (index + 1)));
    if (row.enabled === false) summary.appendChild(el('span', 'pill pill-mute', '已停用'));

    const texts = el('span', 'qa-item-texts');
    const qSpan = el('span', 'qa-item-q', row.question || '（未填写问题）');
    const aSpan = el('span', 'qa-item-a', answerSummaryOf(row));
    texts.appendChild(qSpan);
    texts.appendChild(aSpan);
    summary.appendChild(texts);

    const thumb = answerThumb(row);
    if (thumb) summary.appendChild(thumb);

    summary.appendChild(el('span', 'qa-item-chevron'));
    item.appendChild(summary);

    /* ── 展开区：编辑控件 ── */
    const body = el('div', 'qa-item-body');

    const head = el('div', 'qa-row-head');
    const toggleWrap = el('label', 'switch');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = row.enabled !== false;
    cb.addEventListener('change', () => {
      row.enabled = cb.checked;
      state.qaDirty = true;
      item.classList.toggle('is-disabled', !cb.checked);
      updateDirty();
    });
    toggleWrap.appendChild(cb);
    toggleWrap.appendChild(el('span', 'switch-track'));
    head.appendChild(toggleWrap);
    head.appendChild(el('span', 'field-meta', '启用该条'));

    const del = el('button', 'btn btn-danger btn-sm', '删除');
    del.type = 'button';
    del.style.marginLeft = 'auto';
    del.addEventListener('click', (ev) => {
      ev.preventDefault();
      state.qaDraft = state.qaDraft.filter(r => r !== row);
      state.qaDirty = true;
      renderRows();
      updateDirty();
    });
    head.appendChild(del);
    body.appendChild(head);

    // Q
    const qField = el('div', 'field');
    const qHead = el('div', 'field-head');
    qHead.appendChild(el('label', 'field-label', '问题 Q（支持 % 通配）'));
    qHead.appendChild(el('span', 'field-hint', '例如 怎么安装% / %今天%天气%'));
    qField.appendChild(qHead);
    const qInput = el('input', 'input');
    qInput.type = 'text';
    qInput.value = row.question;
    qInput.placeholder = '用户可能怎么问';
    qInput.addEventListener('input', () => {
      row.question = qInput.value;
      qSpan.textContent = row.question || '（未填写问题）';
      state.qaDirty = true;
      updateDirty();
    });
    qField.appendChild(qInput);
    body.appendChild(qField);

    // A
    const aField = el('div', 'field');
    aField.appendChild(el('div', 'field-head')).appendChild(el('label', 'field-label', '答案 A'));
    const aArea = el('textarea', 'textarea');
    aArea.rows = 2;
    aArea.value = row.answerText;
    aArea.placeholder = '命中后由 LLM 围绕这段内容生成回复';
    aArea.addEventListener('input', () => {
      row.answerText = aArea.value;
      aSpan.textContent = answerSummaryOf(row);
      state.qaDirty = true;
      updateDirty();
    });
    aField.appendChild(aArea);

    const imgDetails = el('details', 'qa-images');
    imgDetails.appendChild(el('summary', null, '答案附带图片（可选）'));
    const imgArea = el('textarea', 'textarea');
    imgArea.rows = 2;
    imgArea.value = row.answerImages;
    imgArea.placeholder = '每行一个图片 URL';
    imgArea.addEventListener('input', () => {
      row.answerImages = imgArea.value;
      aSpan.textContent = answerSummaryOf(row);
      // 图片是答案的一部分，填写后自动展开该分组便于核对
      state.qaDirty = true;
      updateDirty();
    });
    imgDetails.appendChild(imgArea);
    aField.appendChild(imgDetails);
    body.appendChild(aField);

    item.appendChild(body);
    return item;
  }
  function updateDirty() {
    const btn = $('#qa-save-btn');
    if (btn) btn.disabled = !state.qaDirty;
    const note = $('#qa-note');
    if (note) {
      const n = state.qaDraft.length;
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
    host.appendChild(el('span', 'field-label', '当前回复模式：'));
    host.appendChild(el('span', 'pill ' + (d.use_qa_table ? 'pill-ok' : 'pill-mute'),
      d.use_qa_table ? (d.mode_label || '固定问答表') : '大模型自由回复'));
    if (d.use_qa_table) {
      host.appendChild(el('span', 'pill ' + (d.enable_jev_topic ? 'pill-brand' : 'pill-warn'),
        d.enable_jev_topic ? 'Jev 审核已开启' : 'Jev 关闭（KeyReply 原样）'));
      host.appendChild(el('span', 'field-meta', '最低置信度 ' + (d.qa_min_confidence || '中')
        + ' · 上下文 ' + format.num(d.context_message_count) + ' 条'));
    } else {
      const tip = el('span', 'field-meta', '问答表暂不生效，可在「配置中心 → 固定问答表」切换回复来源。');
      host.appendChild(tip);
    }
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
      const entries = editingKey
        ? ((currentTable() && currentTable().entries) || [])
        : [];
      const result = await TS.api.qaSave({
        key: editingKey || undefined,
        scope: 'group',
        ids: editingIds,
        name: name,
        entries: entries
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
        entries: collectDraft()
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
      state.qaDraft.push({ _id: nextId(), question: '', answerText: '', answerImages: '', enabled: true, _expand: true });
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