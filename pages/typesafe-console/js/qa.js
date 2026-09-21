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

  /* ── 作用域 ───────────────────────────────────────────── */
  function scopeKey(s) { return s.scope_id ? s.scope + ':' + s.scope_id : s.scope; }

  function currentScope() { return state.qaScope || { scope: 'global', scope_id: '' }; }

  function tableFor(scope, scopeId) {
    const data = state.qa;
    if (!data) return null;
    const key = scopeId ? scope + ':' + scopeId : scope;
    return (data.tables || []).find(t => t.key === key) || null;
  }

  /* ── 草稿 ─────────────────────────────────────────────── */
  function loadDraft() {
    const s = currentScope();
    const table = tableFor(s.scope, s.scope_id);
    state.qaDraft = (table && table.entries ? table.entries : []).map(e => ({
      _id: nextId(),
      question: e.question || '',
      answerText: (e.answer && e.answer.text) || '',
      answerImages: ((e.answer && e.answer.images) || []).join('\n'),
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
    const summary = data.summary || {};
    const cur = currentScope();

    const list = el('div', 'chip-group');
    const options = [{ scope: 'global', scope_id: '', label: '全局默认表', count: summary.global_entries || 0 }];
    (summary.groups || []).forEach(g => {
      const t = tableFor('group', g);
      options.push({ scope: 'group', scope_id: g, label: '群 ' + g, count: t ? t.entries.length : 0 });
    });
    (summary.privates || []).forEach(p => {
      const t = tableFor('private', p);
      options.push({ scope: 'private', scope_id: p, label: '私聊 ' + p, count: t ? t.entries.length : 0 });
    });

    options.forEach(opt => {
      const active = opt.scope === cur.scope && String(opt.scope_id) === String(cur.scope_id);
      const chip = el('label', 'chip' + (active ? ' is-checked' : ''));
      chip.appendChild(el('span', null, opt.label + ' · ' + opt.count + ' 条'));
      chip.addEventListener('click', () => {
        if (state.qaDirty && !window.confirm('当前改动尚未保存，切换作用域将丢弃这些改动。继续？')) return;
        state.qaScope = { scope: opt.scope, scope_id: opt.scope_id };
        loadDraft();
      });
      list.appendChild(chip);
    });
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
      const head = el('div', 'qa-group-head');
      head.appendChild(el('span', 'qa-group-title',
        rows.length > 1 ? rows.length + ' 个问题共用同一答案' : '1 个问题'));
      if (rows.length > 1) head.appendChild(el('span', 'pill pill-brand', '多 Q 一 A'));
      wrap.appendChild(head);

      rows.forEach(row => wrap.appendChild(buildRow(row, idx++)));
      host.appendChild(wrap);
    });
  }

  function buildRow(row, index) {
    const box = el('div', 'qa-row' + (row.enabled === false ? ' is-disabled' : ''));

    const head = el('div', 'qa-row-head');
    head.appendChild(el('span', 'qa-row-index', '#' + (index + 1)));

    const toggle = el('label', 'switch');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = row.enabled !== false;
    cb.addEventListener('change', () => {
      row.enabled = cb.checked;
      state.qaDirty = true;
      box.classList.toggle('is-disabled', !cb.checked);
      updateDirty();
    });
    toggle.appendChild(cb);
    toggle.appendChild(el('span', 'switch-track'));
    head.appendChild(toggle);

    const del = el('button', 'btn btn-ghost btn-sm', '删除');
    del.type = 'button';
    del.addEventListener('click', () => {
      state.qaDraft = state.qaDraft.filter(r => r !== row);
      state.qaDirty = true;
      renderRows();
      updateDirty();
    });
    head.appendChild(del);
    box.appendChild(head);

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
    qInput.addEventListener('input', () => { row.question = qInput.value; state.qaDirty = true; updateDirty(); });
    qField.appendChild(qInput);
    box.appendChild(qField);

    // A
    const aField = el('div', 'field');
    aField.appendChild(el('div', 'field-head')).appendChild(el('label', 'field-label', '答案 A'));
    const aArea = el('textarea', 'textarea');
    aArea.rows = 2;
    aArea.value = row.answerText;
    aArea.placeholder = '命中后由 LLM 围绕这段内容生成回复';
    aArea.addEventListener('input', () => { row.answerText = aArea.value; state.qaDirty = true; updateDirty(); });
    aField.appendChild(aArea);

    const imgDetails = el('details', 'qa-images');
    imgDetails.appendChild(el('summary', null, '答案附带图片（可选）'));
    const imgArea = el('textarea', 'textarea');
    imgArea.rows = 2;
    imgArea.value = row.answerImages;
    imgArea.placeholder = '每行一个图片 URL';
    imgArea.addEventListener('input', () => { row.answerImages = imgArea.value; state.qaDirty = true; updateDirty(); });
    imgDetails.appendChild(imgArea);
    aField.appendChild(imgDetails);
    box.appendChild(aField);

    return box;
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
    const input = $('#qa-import-path');
    if (input && !input.value) input.value = state.qa.configured_import_path || (cands[0] || '');
  }

  /* ── 操作 ─────────────────────────────────────────────── */
  async function save() {
    const btn = $('#qa-save-btn');
    if (btn) btn.classList.add('is-busy');
    try {
      const s = currentScope();
      await TS.api.qaSave({
        scope: s.scope,
        scope_id: s.scope_id,
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

    if (replace && !window.confirm('覆盖模式会用 KeyReply 的问答表整体替换当前作用域的内容，确定继续？')) {
      return;
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

  function addScope() {
    const kind = $('#qa-new-kind') ? $('#qa-new-kind').value : 'group';
    const idInput = $('#qa-new-id');
    const id = idInput ? idInput.value.trim() : '';
    if (!id) { TS.toast('请填写群号或用户 QQ', 'err'); return; }
    state.qaScope = { scope: kind, scope_id: id };
    state.qaDraft = [];
    state.qaDirty = true;
    // 先把空表登记到本地视图，保存时后端会创建
    if (state.qa && !tableFor(kind, id)) {
      state.qa.tables = (state.qa.tables || []).concat([{ key: kind + ':' + id, scope: kind, scope_id: id, entries: [] }]);
      const summary = state.qa.summary = state.qa.summary || {};
      if (kind === 'group') summary.groups = (summary.groups || []).concat([id]).sort();
      else summary.privates = (summary.privates || []).concat([id]).sort();
    }
    if (idInput) idInput.value = '';
    render();
    TS.toast('已切换到新作用域，添加问答对后点保存即可创建', 'ok');
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
          rows.push(['正则召回', '<span class="pill pill-ok">命中</span> ' + esc(r.classic.question)
            + '<br><span class="field-meta">来源 ' + esc(r.classic.table_label || '') + ' · 答案：'
            + esc(String(r.classic.answer || '').slice(0, 60)) + '</span>']);
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
            if (r.jev.answer) rows.push(['将围绕此答案生成', esc(String(r.jev.answer).slice(0, 80))]);
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
      state.qaDraft.push({ _id: nextId(), question: '', answerText: '', answerImages: '', enabled: true });
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
    $('#qa-add-scope')?.addEventListener('click', () => addScope());
    $('#qa-test-btn')?.addEventListener('click', () => runTest());
  }

  Object.assign(TS, {
    qa: { load: load, render: render, bind: bind, loadDraft: loadDraft }
  });
})();
