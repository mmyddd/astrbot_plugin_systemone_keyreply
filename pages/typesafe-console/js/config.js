/* ============================================================
   TypeSafe 配置中心 · 配置视图
   字段定义（分组/类型/选项/说明） + 渲染 + 收集 + 脏标记
   ============================================================ */
(function () {
  'use strict';

  const TS = window.TS;
  const $ = TS.$, $$ = TS.$$, esc = TS.esc, el = TS.el, format = TS.format, state = TS.state;

  /* ── 字段定义：与 _conf_schema.json 一一对应 ─────────────
     type: bool | int | text | textarea | select | list | multiselect
     gate: 该字段所属的运行闸门（用于状态页与试判页提示）
     ──────────────────────────────────────────────────────── */
  const GROUPS = [
    {
      id: 'basic',
      title: '基础开关',
      desc: '插件总开关与生效场景。关闭总开关后所有消息直接短路，不产生任何判定开销。',
      fields: [
        { key: 'enable_plugin', type: 'bool', label: '启用插件', desc: '插件总开关。关闭后不处理任何消息。' },
        { key: 'enable_group', type: 'bool', label: '启用群聊自动回复', desc: '是否在群聊中判定并主动回复。' },
        { key: 'enable_private', type: 'bool', label: '启用私聊自动回复', desc: '是否在私聊中判定并主动回复。' },
        { key: 'ignore_bots', type: 'bool', label: '忽略机器人自己的消息', desc: '避免把其他机器人的发言当作聊天内容。' },
        { key: 'ignore_commands', type: 'bool', label: '忽略 AstrBot 命令', desc: '以指令前缀开头的消息不参与判定。' },
        { key: 'ignore_pure_media', type: 'bool', label: '忽略纯媒体/图片表情消息', desc: '纯图片、表情包、CQ 码占位符直接丢弃，节省判定开销。' },
        { key: 'context_message_count', type: 'select', label: 'TypeSafe 上下文消息数量', options: [0, 3, 5, 10], desc: '作为判定依据的最近聊天消息条数。', hint: '0 表示不带上下文，仅看当前消息' }
      ]
    },
    {
      id: 'typesafe',
      title: 'TypeSafe 判定',
      desc: '决策层：由 TypeSafe AI 判定消息是否值得主动回复、属于哪种意图。',
      fields: [
        { key: 'typesafe_api_key', type: 'secret', label: 'TypeSafe API Key', desc: '在 console.typesafe.ai 获取。已保存的密钥以掩码显示，保持掩码即为不修改。', hint: 'ts_...' },
        { key: 'typesafe_model', type: 'select', label: 'TypeSafe 判定模型', options: ['jev-latest (推荐最新旗舰)', 'jev', '自定义模型'], desc: '用于结构化裁决的模型。' },
        { key: 'typesafe_custom_model', type: 'text', label: 'TypeSafe 自定义模型名称', desc: '当判定模型选择「自定义模型」时生效。', hint: '例如 jev-1.13.0', dependsOn: { key: 'typesafe_model', value: '自定义模型' } },
        { key: 'typesafe_timeout', type: 'int', label: 'TypeSafe API 超时时间 (秒)', desc: '单次裁决请求的超时时间，超时按失败处理策略降级。', min: 1, max: 120 },
        { key: 'failure_mode', type: 'select', label: 'TypeSafe 失败处理策略', options: ['静默，不回复', '基础规则判断 (问号回复)', '直通 AstrBot'], desc: '超时、限流或网络异常时的降级方式。' },
        { key: 'rate_limit_per_minute', type: 'int', label: 'TypeSafe 每分钟最大请求数', desc: '本地滑动窗口限流，防止超出服务端额度。', min: 1, max: 600 },
        { key: 'enable_cache', type: 'bool', label: '启用 TypeSafe 判断缓存', desc: '相同消息在缓存有效期内复用判定结果，减少请求。' },
        { key: 'cache_ttl', type: 'int', label: '缓存有效期 (秒)', desc: '判定结果的复用时长。', min: 1, max: 3600 },
      ]
    },
    {
      id: 'reply',
      title: '回复生成',
      desc: '生成层：仅在判定通过后，才调用 AstrBot 已配置的大语言模型生成回复。',
      fields: [
        { key: 'model_mode', type: 'select', label: '回复模型选择模式', options: ['跟随当前会话模型', '指定已配置 Provider'], desc: '生成回复时使用的模型来源。' },
        { key: 'custom_provider_id', type: 'text', label: '指定 AstrBot Provider ID', desc: '选择「指定已配置 Provider」时填入 Provider ID。', dependsOn: { key: 'model_mode', value: '指定已配置 Provider' } },
        { key: 'reply_style', type: 'select', label: '回复风格', options: ['自然', '简洁', '专业', '友好', '幽默', '活泼', '严谨', '群友', '自定义'], desc: '影响生成回复的语气与措辞。' },
        { key: 'reply_length_mode', type: 'select', label: '回复长度模式', options: ['极短 (20~50字)', '简短 (50~100字)', '正常 (100~250字)', '详细 (250~500字)', '自然/精炼'], desc: '控制生成回答的篇幅。' },
        { key: 'max_chars', type: 'int', label: '最大回复字数保护', desc: '超出该字数的回复会被合理截断。', min: 20, max: 5000 },
        { key: 'custom_prompt', type: 'textarea', label: '回复附加提示词', wide: true, desc: '追加到生成提示词中，用于约束语气与行为。' }
      ]
    },
    {
      id: 'qa',
      title: '固定问答表（KeyReply）',
      desc: '回复来源选择「固定问答表」时生效：先由本地正则召回，命中才触发 Jev 审核与 LLM 生成；未命中一律静默。',
      fields: [
        { key: 'qa_min_confidence', type: 'select', label: '相关性判定最低置信度', options: ['高', '中', '低'], desc: 'Jev 判定为真提问的置信度低于该等级时视为未命中，保持静默。' },
      ]
    },
    {
      id: 'delay',
      title: '模拟真人延时',
      desc: '发送前的思考与打字延时，避免「秒回」显得过于机械。',
      fields: [
        { key: 'enable_reply_delay', type: 'bool', label: '启用回复延时', desc: '开启后在发送前等待一小段时间。' },
        { key: 'reply_delay_mode', type: 'select', label: '回复延时模式', options: ['随机延时 (推荐)', '固定延时'], desc: '随机延时在区间内取值，更接近真人节奏。' },
        { key: 'reply_delay_min', type: 'int', label: '最小随机延时 (秒)', desc: '随机延时下界。', min: 0, max: 60, dependsOn: { key: 'reply_delay_mode', value: '随机延时 (推荐)' } },
        { key: 'reply_delay_max', type: 'int', label: '最大随机延时 (秒)', desc: '随机延时上界，需不小于下界。', min: 0, max: 120, dependsOn: { key: 'reply_delay_mode', value: '随机延时 (推荐)' } },
        { key: 'reply_delay_fixed', type: 'int', label: '固定延时时长 (秒)', desc: '固定延时模式下的等待秒数。', min: 0, max: 120, dependsOn: { key: 'reply_delay_mode', value: '固定延时' } }
      ]
    },
    {
      id: 'throttle',
      title: '防刷屏与频控',
      desc: '多级闸门叠加，避免机器人在群里自问自答式刷屏。',
      fields: [
        { key: 'session_cooldown', type: 'int', label: '同一会话回复冷却 (秒)', desc: '主动回复后，同会话普通消息在此时间内不再触发。', min: 0, max: 3600 },
        { key: 'user_cooldown', type: 'int', label: '同一用户主动回复冷却 (秒)', desc: '同一用户在此时间内不会再次主动触发机器人。', min: 0, max: 3600 },
        { key: 'max_continuous_replies', type: 'int', label: '最大连续主动回复次数', desc: '连续介入达到该轮数后暂停主动发言，直到被 @ 或其他用户交流。', min: 0, max: 20 },
      ]
    },
    {
      id: 'scope',
      title: '黑白名单',
      desc: '黑名单优先级高于白名单。支持群号、会话 ID 与 unified_msg_origin 多候选匹配。',
      fields: [
        { key: 'filter_mode', type: 'select', label: '黑白名单模式', options: ['全部允许 (仅黑名单拦截)', '仅白名单', '仅黑名单', '黑名单与白名单并用'], desc: '选择名单的生效方式。' },
        { key: 'session_whitelist', type: 'list', label: '会话白名单', desc: '每行一个群号或会话 ID。' },
        { key: 'session_blacklist', type: 'list', label: '会话黑名单', desc: '每行一个群号或会话 ID，优先于白名单。' },
        { key: 'user_whitelist', type: 'list', label: '用户白名单', desc: '每行一个用户 QQ 号。' },
        { key: 'user_blacklist', type: 'list', label: '用户黑名单', desc: '每行一个用户 QQ 号，优先于白名单。' }
      ]
    },
    {
      id: 'filters',
      title: '消息卫生过滤',
      desc: '调用 Jev 之前的长度检查，命中即短路、零 API 消耗。关键词与正则过滤已移除：问答表本身就是白名单式召回，未命中即静默，无需再维护第二套规则。',
      fields: [
        { key: 'min_message_length', type: 'int', label: '最短检测长度', desc: '短于该长度的消息不参与判定。', min: 0, max: 100 },
        { key: 'max_message_length', type: 'int', label: '最长检测长度', desc: '长于该长度的消息不参与判定。', min: 10, max: 20000 }
      ]
    },
    {
      id: 'debug',
      title: '调试',
      desc: '排查判定链路时使用，日常运行建议关闭。',
      fields: [
        { key: 'debug_log', type: 'bool', label: '启用调试日志', desc: '输出规则过滤等细节日志，便于定位「为什么没回复」。' }
      ]
    }
  ];

  const FIELD_MAP = {};
  GROUPS.forEach(group => {
    group.fields.forEach(field => { FIELD_MAP[field.key] = field; });
  });

  function fieldValue(key) {
    const cfg = state.config || {};
    return cfg[key];
  }

  /* ── 渲染 ─────────────────────────────────────────────── */
  function renderBool(field) {
    const wrap = el('label', 'switch-row');
    const text = el('span', 'switch-text');
    text.appendChild(el('b', null, field.label));
    if (field.desc) text.appendChild(el('span', null, field.desc));
    wrap.appendChild(text);

    const sw = el('span', 'switch');
    const input = el('input');
    input.type = 'checkbox';
    input.checked = Boolean(fieldValue(field.key));
    input.addEventListener('change', () => markDirty(field.key));
    const track = el('span', 'switch-track');
    sw.appendChild(input);
    sw.appendChild(track);
    wrap.appendChild(sw);
    wrap.dataset.field = field.key;
    return wrap;
  }

  function renderSelect(field) {
    const box = el('div', 'field');
    const head = el('div', 'field-head');
    head.appendChild(el('label', 'field-label', field.label));
    if (field.hint) head.appendChild(el('span', 'field-hint', field.hint));
    box.appendChild(head);

    const select = el('select', 'select');
    select.dataset.field = field.key;
    (field.options || []).forEach(option => {
      const opt = el('option', null, String(option));
      opt.value = String(option);
      select.appendChild(opt);
    });
    const current = fieldValue(field.key);
    select.value = current === undefined || current === null ? String(field.options[0]) : String(current);
    if (!select.value && field.options && field.options.length) select.value = String(field.options[0]);
    select.addEventListener('change', () => markDirty(field.key));
    box.appendChild(select);

    if (field.desc) box.appendChild(el('p', 'field-desc', field.desc));
    return box;
  }

  function renderInput(field) {
    const box = el('div', 'field');
    const head = el('div', 'field-head');
    head.appendChild(el('label', 'field-label', field.label));
    if (field.hint) head.appendChild(el('span', 'field-hint', field.hint));
    box.appendChild(head);

    const isNumber = field.type === 'int';
    const isSecret = field.type === 'secret';
    let input;
    if (isNumber) {
      input = el('input', 'input input-sm');
      input.type = 'number';
      if (field.min !== undefined) input.min = String(field.min);
      if (field.max !== undefined) input.max = String(field.max);
    } else {
      input = el('input', 'input' + (field.mono || isSecret ? ' input-mono' : ''));
      input.type = isSecret ? 'password' : 'text';
      input.autocomplete = isSecret ? 'new-password' : 'off';
      input.spellcheck = false;
    }
    input.dataset.field = field.key;
    input.placeholder = field.hint || '';
    const value = fieldValue(field.key);
    input.value = value === undefined || value === null ? '' : String(value);
    input.addEventListener('input', () => markDirty(field.key));
    box.appendChild(input);

    if (field.desc) box.appendChild(el('p', 'field-desc', field.desc));
    if (isSecret) {
      const toggle = el('button', 'btn btn-ghost btn-sm', '显示');
      toggle.type = 'button';
      toggle.style.alignSelf = 'flex-start';
      toggle.addEventListener('click', () => {
        const hidden = input.type === 'password';
        input.type = hidden ? 'text' : 'password';
        toggle.textContent = hidden ? '隐藏' : '显示';
      });
      box.appendChild(toggle);
    }
    return box;
  }

  function renderTextarea(field) {
    const box = el('div', 'field');
    const head = el('div', 'field-head');
    head.appendChild(el('label', 'field-label', field.label));
    box.appendChild(head);

    const area = el('textarea', 'textarea');
    area.dataset.field = field.key;
    area.rows = 4;
    const value = fieldValue(field.key);
    area.value = value === undefined || value === null ? '' : String(value);
    area.addEventListener('input', () => markDirty(field.key));
    box.appendChild(area);

    if (field.desc) box.appendChild(el('p', 'field-desc', field.desc));
    return box;
  }

  function renderList(field) {
    const box = el('div', 'field');
    const head = el('div', 'field-head');
    head.appendChild(el('label', 'field-label', field.label));
    const count = el('span', 'field-meta');
    count.dataset.countFor = field.key;
    head.appendChild(count);
    box.appendChild(head);

    const area = el('textarea', 'textarea');
    area.dataset.field = field.key;
    area.rows = 3;
    area.placeholder = '每行一项';
    area.value = format.list(fieldValue(field.key)).join('\n');
    const sync = () => {
      const n = format.lines(area.value).length;
      count.textContent = n + ' 项';
      markDirty(field.key);
    };
    area.addEventListener('input', sync);
    count.textContent = format.lines(area.value).length + ' 项';
    box.appendChild(area);

    if (field.desc) box.appendChild(el('p', 'field-desc', field.desc));
    return box;
  }

  function renderMultiSelect(field) {
    const box = el('div', 'field');
    const head = el('div', 'field-head');
    head.appendChild(el('label', 'field-label', field.label));
    const count = el('span', 'field-meta');
    count.dataset.countFor = field.key;
    box.appendChild(head);

    const selected = new Set(format.list(fieldValue(field.key)));
    const group = el('div', 'chip-group');
    group.dataset.field = field.key;

    (field.options || []).forEach(option => {
      const chip = el('label', 'chip' + (selected.has(option) ? ' is-checked' : ''));
      const input = el('input');
      input.type = 'checkbox';
      input.value = option;
      input.checked = selected.has(option);
      input.addEventListener('change', () => {
        chip.classList.toggle('is-checked', input.checked);
        updateCount();
        markDirty(field.key);
      });
      chip.appendChild(input);
      chip.appendChild(el('span', null, option));
      group.appendChild(chip);
    });

    function updateCount() {
      const n = $$('input:checked', group).length;
      count.textContent = '已选 ' + n + ' / ' + (field.options || []).length;
    }
    updateCount();
    box.appendChild(group);

    const actions = el('div', 'chip-actions');
    const all = el('button', 'btn btn-ghost btn-sm', '全选');
    all.type = 'button';
    all.addEventListener('click', () => {
      $$('input', group).forEach(input => {
        input.checked = true;
        input.parentNode.classList.add('is-checked');
      });
      updateCount();
      markDirty(field.key);
    });
    const none = el('button', 'btn btn-ghost btn-sm', '清空');
    none.type = 'button';
    none.addEventListener('click', () => {
      $$('input', group).forEach(input => {
        input.checked = false;
        input.parentNode.classList.remove('is-checked');
      });
      updateCount();
      markDirty(field.key);
    });
    actions.appendChild(all);
    actions.appendChild(none);
    box.appendChild(actions);

    if (field.desc) box.appendChild(el('p', 'field-desc', field.desc));
    return box;
  }

  const RENDERERS = {
    bool: renderBool,
    select: renderSelect,
    int: renderInput,
    text: renderInput,
    secret: renderInput,
    textarea: renderTextarea,
    list: renderList,
    multiselect: renderMultiSelect
  };

  function buildSection(group) {
    const card = el('section', 'card');
    card.id = 'section-' + group.id;

    const head = el('div', 'card-head');
    const titles = el('div');
    titles.appendChild(el('h2', null, group.title));
    if (group.desc) titles.appendChild(el('p', null, group.desc));
    head.appendChild(titles);

    const right = el('div', 'card-head-right');
    const reset = el('button', 'btn btn-ghost btn-sm', '恢复默认');
    reset.type = 'button';
    reset.addEventListener('click', () => resetGroup(group));
    right.appendChild(reset);
    head.appendChild(right);
    card.appendChild(head);

    const body = el('div', 'card-body');
    const grid = el('div', group.fields.length > 2 ? 'grid-2' : 'grid-2');
    group.fields.forEach(field => {
      const node = (RENDERERS[field.type] || renderInput)(field);
      if (field.wide) node.classList.add('field-wide');
      node.dataset.fieldWrap = field.key;
      grid.appendChild(node);
    });
    body.appendChild(grid);
    card.appendChild(body);
    return card;
  }

  function render() {
    const host = $('#config-sections');
    if (!host) return;
    host.textContent = '';
    GROUPS.forEach(group => host.appendChild(buildSection(group)));
    applyDependencies();
    state.dirty.clear();
    updateDirtyUI();
  }

  /* ── 依赖显隐（例如「自定义模型名」仅在选中自定义时可用）── */
  function applyDependencies() {
    GROUPS.forEach(group => {
      group.fields.forEach(field => {
        if (!field.dependsOn) return;
        const wrap = $('[data-field-wrap="' + field.key + '"]');
        const driver = $('[data-field="' + field.dependsOn.key + '"]');
        if (!wrap || !driver) return;
        const active = String(driver.value) === String(field.dependsOn.value);
        wrap.style.opacity = active ? '' : '.45';
        $$('input, select, textarea', wrap).forEach(input => { input.disabled = !active; });
      });
    });
  }

  /* ── 收集当前表单值 ───────────────────────────────────── */
  function collect() {
    const patch = {};
    GROUPS.forEach(group => {
      group.fields.forEach(field => {
        if (!state.dirty.has(field.key)) return;
        const node = $('[data-field="' + field.key + '"]');
        if (!node) return;
        switch (field.type) {
          case 'bool':
            patch[field.key] = Boolean(node.checked);
            break;
          case 'int':
            patch[field.key] = Number(node.value === '' ? 0 : node.value);
            break;
          case 'list':
            patch[field.key] = format.lines(node.value);
            break;
          case 'multiselect':
            patch[field.key] = $$('input:checked', node).map(input => input.value);
            break;
          default:
            patch[field.key] = node.value;
        }
      });
    });
    return patch;
  }

  /* ── 脏标记 ───────────────────────────────────────────── */
  function markDirty(key) {
    state.dirty.add(key);
    updateDirtyUI();
    applyDependencies();
  }

  function updateDirtyUI() {
    const badge = $('#nav-dirty');
    if (badge) TS.show(badge, state.dirty.size > 0);
    const note = $('#action-note');
    if (note) {
      note.textContent = state.dirty.size
        ? '有 ' + state.dirty.size + ' 项改动尚未保存'
        : '所有配置已与后端同步';
    }
    const save = $('#save-btn');
    if (save) save.disabled = state.dirty.size === 0 || state.saving;
  }

  async function save() {
    if (state.saving) return;
    const patch = collect();
    if (!Object.keys(patch).length) {
      TS.toast('没有需要保存的改动');
      return;
    }
    state.saving = true;
    const button = $('#save-btn');
    if (button) button.classList.add('is-busy');
    updateDirtyUI();
    try {
      const result = await TS.api.configUpdate(patch);
      const reloaded = result && result.reloaded;
      TS.toast(
        reloaded
          ? '已保存 ' + (result.updated || []).length + ' 项并热重载插件'
          : '已保存 ' + ((result && result.updated) || []).length + ' 项，需手动重载插件生效',
        'ok'
      );
      state.dirty.clear();
      await TS.app.loadAll(true);
    } catch (error) {
      TS.toast('保存失败：' + (error && error.message ? error.message : error), 'err');
    } finally {
      state.saving = false;
      if (button) button.classList.remove('is-busy');
      updateDirtyUI();
    }
  }

  async function resetGroup(group) {
    const go = await TS.confirmDialog(
      '将「' + group.title + '」中的配置恢复为默认值？',
      { title: '恢复默认值', okLabel: '恢复', danger: true }
    );
    if (!go) return;
    try {
      const result = await TS.api.configReset(group.fields.map(field => field.key));
      TS.toast('已恢复 ' + ((result && result.updated) || []).length + ' 项默认值', 'ok');
      state.dirty.clear();
      await TS.app.loadAll(true);
    } catch (error) {
      TS.toast('恢复默认失败：' + (error && error.message ? error.message : error), 'err');
    }
  }

  function fillDefaults() {
    if (!state.config) return;
    GROUPS.forEach(group => {
      group.fields.forEach(field => {
        if (field.type === 'secret') return;
        const node = $('[data-field="' + field.key + '"]');
        if (!node) return;
        if (node.type === 'checkbox') {
          node.checked = Boolean(field.default);
        } else if (field.type === 'multiselect') {
          const set = new Set(format.list(field.default));
          $$('input', node).forEach(input => {
            input.checked = set.has(input.value);
            input.parentNode.classList.toggle('is-checked', input.checked);
          });
        } else if (Array.isArray(field.default) || field.type === 'list') {
          node.value = format.list(field.default).join('\n');
        } else {
          node.value = field.default === undefined ? '' : String(field.default);
        }
        markDirty(field.key);
      });
    });
    TS.toast('已填入内置默认值，确认后点击保存');
  }

  Object.assign(TS, {
    config: {
      GROUPS: GROUPS,
      FIELD_MAP: FIELD_MAP,
      render: render,
      collect: collect,
      save: save,
      fillDefaults: fillDefaults,
      updateDirtyUI: updateDirtyUI
    }
  });
})();