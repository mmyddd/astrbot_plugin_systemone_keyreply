/* ============================================================
   TypeSafe 配置中心 · 状态视图与试判视图
   ============================================================ */
(function () {
  'use strict';

  const TS = window.TS;
  const $ = TS.$, $$ = TS.$$, esc = TS.esc, el = TS.el, format = TS.format, state = TS.state;

  /* ══════════════════════ 运行状态 ══════════════════════ */
  const FAILURE_LABEL = {
    silent: '静默，不回复',
    rule_based: '基础规则判断',
    pass_to_astrbot: '直通 AstrBot'
  };

  const CONFIDENCE_LABEL = { high: '高', medium: '中', low: '低' };

  function pill(text, kind) {
    return '<span class="pill pill-' + (kind || 'mute') + '">' + esc(text) + '</span>';
  }

  function statCard(label, value, sub) {
    const box = el('div', 'stat');
    box.appendChild(el('div', 'stat-label', label));
    box.appendChild(el('div', 'stat-value', value));
    if (sub) box.appendChild(el('div', 'stat-sub', sub));
    return box;
  }

  function renderStatus() {
    const host = $('#status-body');
    if (!host) return;
    const s = state.status;
    if (!s) {
      host.innerHTML = '<div class="empty">尚未读取到运行状态</div>';
      return;
    }
    host.textContent = '';

    /* 指标卡 */
    const stats = el('div', 'stat-grid');
    stats.appendChild(statCard('TypeSafe API', s.api_configured ? '已配置' : '未配置', s.api_configured ? '密钥就绪' : '请填写 API Key'));
    stats.appendChild(statCard('生效模型', s.model || '—', s.custom_model ? '自定义模型' : '预设模型'));
    stats.appendChild(statCard('活跃会话', format.num(s.active_sessions), '内存中保留的上下文'));
    stats.appendChild(statCard('限流用量', format.num(s.rate_limit_used) + ' / ' + format.num(s.rate_limit_per_minute), '本分钟已发出请求'));
    host.appendChild(stats);

    /* 开关与策略 */
    const dl = el('dl', 'kv');
    const add = (key, value) => {
      dl.appendChild(el('dt', null, key));
      const dd = el('dd');
      dd.innerHTML = value;
      dl.appendChild(dd);
    };

    const on = s.enable_plugin;
    add('插件总开关', pill(on ? '已启用' : '已停用', on ? 'ok' : 'bad'));
    add('生效场景', [
      pill('群聊 ' + (s.enable_group ? '开' : '关'), s.enable_group ? 'ok' : 'mute'),
      pill('私聊 ' + (s.enable_private ? '开' : '关'), s.enable_private ? 'ok' : 'mute')
    ].join(' '));
    add('判定模型', esc(s.model || '—') + (s.custom_model ? ' <span class="field-meta">(自定义)</span>' : ''));
    // Base URL：只填根地址，/v1/systemone 由插件自动追加
    const baseNote = !s.base_url
      ? ' <span class="field-meta">官方默认地址，自动追加 /v1/systemone</span>'
      : (s.base_url_supported === false
        ? ' ' + pill('当前 typesafe-sdk 不支持自定义地址，已回退官方', 'bad')
        : ' <span class="field-meta">自定义根地址，自动追加 /v1/systemone</span>');
    add('API 地址', esc(s.base_url_effective || s.base_url || '—') + baseNote);
    add('失败处理策略', pill(FAILURE_LABEL[s.failure_mode] || s.failure_mode || '—', s.failure_mode === 'silent' ? 'mute' : 'brand'));
    add('回复来源', pill(s.qa_mode_label || '固定问答表', 'brand')
      + (s.qa_mode === 'jev'
        ? ' <span class="field-meta">正则召回 → Jev 判定真提问 → 回复答案</span>'
        : ' <span class="field-meta">正则命中直接发送答案</span>'));
    add('相关性判定门槛', pill(CONFIDENCE_LABEL[s.qa_min_confidence] || s.qa_min_confidence || '—', 'brand')
      + ' <span class="field-meta">Jev 判定低于该等级将保持静默</span>');
    if (s.qa_summary) {
      add('问答表规模', '全局 ' + format.num(s.qa_summary.global_entries) + ' 条 · 群表 '
        + format.num((s.qa_summary.groups || []).length) + ' 个 · 私聊表 '
        + format.num((s.qa_summary.privates || []).length) + ' 个');
    }
    add('回复风格 / 篇幅', esc(s.reply_style || '—') + ' · ' + esc(s.reply_length_mode || '—')
      + ' <span class="field-meta">上限 ' + format.num(s.max_chars) + ' 字</span>');
    add('回复模型', esc(s.model_mode === 'custom' ? '指定 Provider: ' + (s.custom_provider_id || '未填写') : '跟随当前会话模型'));

    const d = s.delay || {};
    add('模拟延时', d.enabled
      ? esc(d.mode === 'fixed' ? '固定 ' + d.fixed + ' 秒' : '随机 ' + d.min + '~' + d.max + ' 秒')
      : pill('关闭（秒回）', 'mute'));

    add('冷却设置', '会话 ' + format.num(s.session_cooldown) + ' 秒 · 用户 '
      + format.num(s.user_cooldown) + ' 秒 · 连续上限 ' + format.num(s.max_continuous_replies) + ' 轮');
    add('黑白名单模式', pill(s.filter_mode || '—', 'brand'));
    add('上下文条数', format.num(s.context_message_count) + ' 条');
    add('判定缓存', s.cache_enabled
      ? pill('开启 ' + format.num(s.cache_ttl) + ' 秒', 'ok') + ' <span class="field-meta">当前缓存 ' + format.num(s.cache_size) + ' 条</span>'
      : pill('关闭', 'mute'));
    add('调试日志', pill(s.debug_log ? '开启' : '关闭', s.debug_log ? 'warn' : 'mute'));

    const regex = s.regex_ok || {};
    const regexBad = [];
    if (s.force_trigger_regex && !regex.force_trigger) regexBad.push('强制触发正则');
    if (s.ignore_regex && !regex.ignore) regexBad.push('忽略正则');
    add('正则状态', regexBad.length
      ? pill(regexBad.join(' / ') + ' 无法编译', 'bad')
      : pill('正常', 'ok'));

    host.appendChild(dl);

    /* 限流用量条 */
    const meterWrap = el('div');
    meterWrap.style.marginTop = '16px';
    const pct = s.rate_limit_per_minute
      ? Math.min(100, Math.round((s.rate_limit_used / s.rate_limit_per_minute) * 100))
      : 0;
    meterWrap.innerHTML =
      '<div class="field-head"><span class="field-label">本分钟限流用量</span>'
      + '<span class="field-meta">' + format.num(s.rate_limit_used) + ' / ' + format.num(s.rate_limit_per_minute) + '</span></div>'
      + '<div class="meter"><span style="width:' + pct + '%"></span></div>';
    host.appendChild(meterWrap);
  }

  async function probe() {
    const button = $('#probe-btn');
    const box = $('#probe-result');
    if (button) button.classList.add('is-busy');
    TS.show(box, true);
    if (box) box.innerHTML = '<span class="pill pill-brand">正在请求 TypeSafe…</span>';
    try {
      const result = await TS.api.probe();
      if (box) {
        box.innerHTML = result.ok
          ? pill('连接正常', 'ok') + ' <span class="field-meta">模型 ' + esc(result.model || '') + ' · 耗时 ' + format.ms(result.elapsed_ms) + '</span>'
          : pill('连接失败', 'bad') + ' <span class="field-meta">' + esc(result.message || '未知错误') + '</span>';
      }
      TS.toast(result.ok ? 'TypeSafe 连接正常' : 'TypeSafe 连接失败', result.ok ? 'ok' : 'err');
    } catch (error) {
      if (box) box.innerHTML = pill('请求异常', 'bad') + ' <span class="field-meta">' + esc(error && error.message ? error.message : error) + '</span>';
      TS.toast('连通性测试失败', 'err');
    } finally {
      if (button) button.classList.remove('is-busy');
    }
  }

  /* ══════════════════════ 在线试判 ══════════════════════ */
  function renderRecent() {
    const host = $('#try-recent');
    if (!host) return;
    host.textContent = '';
    if (!state.tryRecent.length) {
      host.appendChild(el('div', 'field-meta', '未添加模拟上下文，本次试判仅依据当前消息。'));
      return;
    }
    state.tryRecent.forEach((item, index) => {
      const row = el('div', 'recent-row');
      const sender = el('input', 'input input-sender');
      sender.type = 'text';
      sender.value = item.sender || '';
      sender.placeholder = '群友';
      sender.addEventListener('input', () => { state.tryRecent[index].sender = sender.value; });

      const text = el('input', 'input');
      text.type = 'text';
      text.value = item.text || '';
      text.placeholder = '历史消息内容';
      text.addEventListener('input', () => { state.tryRecent[index].text = text.value; });

      const remove = el('button', 'btn btn-ghost btn-sm', '删除');
      remove.type = 'button';
      remove.addEventListener('click', () => {
        state.tryRecent.splice(index, 1);
        renderRecent();
      });

      row.appendChild(sender);
      row.appendChild(text);
      row.appendChild(remove);
      host.appendChild(row);
    });
  }

  function gateRow(name, pass, value) {
    const row = el('div', 'gate ' + (pass ? 'is-pass' : 'is-fail'));
    row.appendChild(el('span', 'gate-icon', pass ? '✓' : '✕'));
    row.appendChild(el('span', 'gate-name', name));
    row.appendChild(el('span', 'gate-val', value));
    return row;
  }

  function renderTryResult() {
    const host = $('#try-result');
    if (!host) return;
    const r = state.tryResult;
    if (!r) {
      host.innerHTML = '<div class="empty">输入一段消息后点击「开始试判」，这里会显示 TypeSafe 的结构化裁决结果。</div>';
      return;
    }
    host.textContent = '';

    /* 结论横幅 */
    const verdict = el('div', 'verdict ' + (r.would_reply ? 'is-yes' : 'is-no'));
    const icon = el('span', 'verdict-icon');
    icon.innerHTML = r.would_reply
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';
    verdict.appendChild(icon);
    const text = el('div', 'verdict-text');
    text.appendChild(el('b', null, r.would_reply ? '综合闸门通过：会主动回复' : '综合闸门未通过：保持静默'));
    text.appendChild(el('span', null,
      'TypeSafe 判定 ' + (r.should_reply ? '需要回复' : '无需回复')
      + ' · 意图「' + r.reply_type_display + '」'
      + ' · 置信度 ' + r.confidence_level + '（' + Number(r.confidence_score).toFixed(2) + '）'));
    verdict.appendChild(text);
    host.appendChild(verdict);

    /* 闸门明细 */
    const gates = el('div', 'gate-list');
    gates.style.marginTop = '14px';
    gates.appendChild(gateRow(
      'TypeSafe 建议回复',
      r.should_reply,
      r.should_reply ? '是' : '否'
    ));
    gates.appendChild(gateRow(
      '意图类型在允许列表内',
      r.type_allowed,
      r.type_allowed ? '是' : '否'
    ));
    gates.appendChild(gateRow(
      '置信度达到门槛（' + (r.gates ? r.gates.min_confidence : '') + '）',
      r.confidence_ok,
      r.confidence_level
    ));
    gates.appendChild(gateRow(
      '未触发故障降级',
      !r.is_fallback,
      r.is_fallback ? '已降级' : '正常'
    ));
    host.appendChild(gates);

    /* 明细 */
    const dl = el('dl', 'kv');
    dl.style.marginTop = '14px';
    const add = (key, value) => {
      dl.appendChild(el('dt', null, key));
      const dd = el('dd');
      dd.innerHTML = value;
      dl.appendChild(dd);
    };
    add('意图类型', esc(r.reply_type_display) + ' <span class="field-meta">' + esc(r.reply_type) + '</span>');
    add('置信度', pill(r.confidence_level, r.confidence_level === 'high' ? 'ok' : r.confidence_level === 'low' ? 'warn' : 'brand')
      + ' <span class="field-meta">' + Number(r.confidence_score).toFixed(2) + '</span>');
    add('紧迫程度', pill(r.urgency || '—', r.urgency === 'high' ? 'bad' : 'mute'));
    add('判定耗时', format.ms(r.elapsed_ms) + ' <span class="field-meta">模型 ' + esc(r.model || '') + '</span>');
    if (r.is_fallback) add('降级提示', pill('本次为降级结果', 'warn'));
    host.appendChild(dl);

    if (r.verdicts && r.verdicts.length) {
      const box = el('div', 'reason-box');
      box.style.marginTop = '12px';
      box.innerHTML = r.verdicts.map(v => '· ' + esc(v)).join('<br>');
      host.appendChild(box);
    }

    const reason = el('div', 'reason-box');
    reason.style.marginTop = '12px';
    reason.innerHTML = '<strong>判定理由</strong><br>' + esc(r.reason || '—');
    host.appendChild(reason);
  }

  async function runTry() {
    const input = $('#try-input');
    const text = input ? input.value.trim() : '';
    if (!text) {
      TS.toast('请先输入要试判的消息', 'err');
      return;
    }
    const button = $('#try-btn');
    if (button) button.classList.add('is-busy');
    try {
      const recent = state.tryRecent
        .filter(item => String(item.text || '').trim())
        .map(item => ({ sender: item.sender || '群友', text: item.text }));
      const result = await TS.api.tryMessage({ text: text, recent: recent });
      state.tryResult = result;
      renderTryResult();
    } catch (error) {
      state.tryResult = null;
      renderTryResult();
      TS.toast('试判失败：' + (error && error.message ? error.message : error), 'err');
    } finally {
      if (button) button.classList.remove('is-busy');
    }
  }

  Object.assign(TS, {
    views: {
      renderStatus: renderStatus,
      probe: probe,
      renderRecent: renderRecent,
      renderTryResult: renderTryResult,
      runTry: runTry
    }
  });
})();