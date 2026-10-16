(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const csrfHeaders = { 'X-CSRF-Token': token };
  let taskId = null;
  let runId = null;
  let eventCursor = 0;
  let pollHandle = null;
  const selectedResults = new Set();

  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const element = byId(id);
    if (element) element.textContent = value ?? '';
  };
  const show = (element, visible = true) => element?.classList.toggle('hidden', !visible);

  async function api(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) Object.assign(headers, csrfHeaders);
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { ...options, method, headers });
    const type = response.headers.get('content-type') || '';
    const data = type.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof data === 'object' ? (data.detail?.missing?.join('；') || data.detail?.detail || data.detail || '请求未完成') : data;
      throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
    }
    return data;
  }

  function appendTextList(target, values, emptyText = '未指定') {
    if (!target) return;
    target.replaceChildren();
    const items = values?.length ? values : [emptyText];
    items.forEach((value) => {
      const li = document.createElement('li');
      li.textContent = typeof value === 'string' ? value : JSON.stringify(value);
      target.append(li);
    });
  }

  function renderPreview(task) {
    const panel = byId('task-preview');
    show(panel);
    if (taskId && taskId !== task.id) selectedResults.clear();
    taskId = task.id;
    const spec = task.spec;
    setText('preview-title', `第 ${task.version} 版 · ${spec.kind === 'list' ? '名单采集' : spec.kind === 'comparison' ? '比较任务' : spec.kind === 'fact' ? '事实查询' : '研究任务'}`);
    setText('preview-summary', spec.summary || spec.raw_input);
    setText('preview-search-query', task.search_query || spec.summary || spec.raw_input);
    appendTextList(byId('preview-fields'), (spec.fields || []).map((field) => `${field.name}${field.required ? '（必需）' : ''}`));
    appendTextList(byId('preview-conditions'), (spec.required_conditions || []).map((item) => item.text));
    appendTextList(byId('preview-assumptions'), [...(spec.assumptions || []), ...(spec.stop_conditions || []), ...(spec.region ? [`地区：${spec.region}`] : []), ...(spec.time_window ? [`时间范围：${spec.time_window.label || `${spec.time_window.start} 至 ${spec.time_window.end}`}`] : [])]);
    const capability = task.capability;
    const notes = [];
    if (capability.missing?.length) notes.push(`缺少配置：${capability.missing.join('；')}`);
    if (capability.available?.length) notes.push(`可用能力：${capability.available.join('、')}`);
    if (capability.local_fallbacks?.length) notes.push(`本地处理：${capability.local_fallbacks.join('；')}`);
    setText('capability-box', notes.join('\n') || '任务可以开始。');
    appendTextList(byId('data-flow-list'), [...(capability.data_flow || []), '不会登录网站，不会运行来源中的指令或下载可执行文件。']);
    const needsSearch = (capability.missing || []).some((item) => item.includes('搜索服务'));
    show(byId('search-settings-link'), needsSearch);
    const start = byId('start-task');
    if (start) start.disabled = !capability.ready;
    const link = byId('task-link');
    if (link) {
      link.href = `/tasks/${encodeURIComponent(task.id)}`;
      show(link);
    }
    panel?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderBudget(snapshot, targetId = 'budget-list') {
    const target = byId(targetId);
    if (!target) return;
    target.replaceChildren();
    const labels = {
      active_run: '运行轮次', document_fetch: '文档读取', browser_fetch: '动态页面',
      search_calls: '搜索请求', model_calls: '模型请求', input_tokens: '输入 Token', output_tokens: '输出 Token',
    };
    Object.keys(labels).forEach((key) => {
      const used = snapshot.used?.[key] || 0;
      const reserved = snapshot.reserved?.[key] || 0;
      const limit = snapshot.limits?.[key] || 0;
      const dt = document.createElement('dt');
      const dd = document.createElement('dd');
      dt.textContent = labels[key];
      dd.textContent = `${used} 已用${reserved ? `，${reserved} 在途` : ''} / ${limit}`;
      target.append(dt, dd);
    });
  }

  function renderResults(data, targetId = 'result-list') {
    const target = byId(targetId);
    if (!target) return;
    target.replaceChildren();
    const sources = data.sources || [];
    if (sources.length) {
      const heading = document.createElement('h3');
      heading.textContent = `已读取的网页（${sources.length}）`;
      const explanation = document.createElement('p');
      explanation.className = 'helper-text';
      explanation.textContent = '下面是实际读取的网页原文片段，方便你查看出处。片段保留来源表述，请结合原文判断。';
      target.append(heading, explanation);
      sources.forEach((source) => {
        const card = document.createElement('article');
        card.className = 'result-card source-card';
        const title = document.createElement('h3');
        const label = source.title || source.url || '来源原文';
        if (source.url?.startsWith('https://') || source.url?.startsWith('http://')) {
          const link = document.createElement('a');
          link.href = source.url;
          link.textContent = label;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          title.append(link);
        } else {
          title.textContent = label;
        }
        const excerpt = document.createElement('p');
        excerpt.className = 'source-excerpt';
        excerpt.textContent = source.excerpt || '此来源没有可展示的原文片段。';
        card.append(title, excerpt);
        if (source.fetched_at) {
          const date = document.createElement('p');
          date.className = 'helper-text';
          date.textContent = `读取时间：${source.fetched_at}`;
          card.append(date);
        }
        target.append(card);
      });
    }
    if (!data.items?.length) {
      const empty = document.createElement('p');
      empty.className = 'helper-text';
      empty.textContent = sources.length ? '暂未提取出符合字段要求的结构化结果，可以先查看上方网页原文。' : '当前视图还没有结果。采集失败时，失败原因会保留在进展记录中。';
      target.append(empty);
      setText('selected-count', `已选 ${selectedResults.size} 个对象`);
      return;
    }
    if (sources.length) {
      const heading = document.createElement('h3');
      heading.textContent = '提取出的字段';
      target.append(heading);
    }
    const sortOrder = byId('result-sort')?.value || 'name_asc';
    const statusOrder = { confirmed: 0, user_confirmed: 1, pending_review: 2, excluded: 3 };
    const items = [...data.items].sort((left, right) => {
      if (sortOrder === 'name_desc') return right.entity.localeCompare(left.entity, 'zh-CN');
      if (sortOrder === 'status') return (statusOrder[left.status] ?? 4) - (statusOrder[right.status] ?? 4) || left.entity.localeCompare(right.entity, 'zh-CN');
      return left.entity.localeCompare(right.entity, 'zh-CN');
    });
    items.forEach((entity) => {
      const card = document.createElement('article');
      card.className = 'result-card';
      const heading = document.createElement('div');
      heading.className = 'result-heading';
      const title = document.createElement('h3');
      title.textContent = entity.entity;
      const pick = document.createElement('label');
      pick.className = 'export-pick';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'export-selection';
      checkbox.value = entity.entity_id;
      checkbox.checked = selectedResults.has(entity.entity_id);
      checkbox.setAttribute('aria-label', `选择${entity.entity}用于比较或导出`);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) selectedResults.add(entity.entity_id);
        else selectedResults.delete(entity.entity_id);
        setText('selected-count', `已选 ${selectedResults.size} 个对象`);
      });
      const pickText = document.createElement('span');
      pickText.textContent = '选择对象';
      pick.append(checkbox, pickText);
      const status = document.createElement('span');
      status.className = `status-pill status-${entity.status}`;
      status.textContent = ({ confirmed: '有来源核对', user_confirmed: '用户已修正', pending_review: '待核查', excluded: '不符合条件' })[entity.status] || entity.status;
      heading.append(title, pick, status);
      card.append(heading);

      entity.observations.forEach((observation) => {
        const fact = document.createElement('div');
        fact.className = 'fact-row';
        const label = document.createElement('strong');
        label.textContent = `${observation.field}：`;
        const value = document.createElement('span');
        value.textContent = `${observation.value ?? '未知'}${observation.unit ? ` ${observation.unit}` : ''}${observation.period ? ` · ${observation.period}` : ''}`;
        fact.append(label, value);
        card.append(fact);
        if (observation.reasons?.length) {
          const reason = document.createElement('p');
          reason.className = 'reason-text';
          reason.textContent = observation.reasons.join('；');
          card.append(reason);
        }
        observation.evidence?.forEach((evidence) => {
          const source = document.createElement('blockquote');
          source.className = 'evidence-quote';
          const quote = document.createElement('p');
          quote.textContent = `“${evidence.quote}”`;
          source.append(quote);
          const citation = document.createElement('p');
          citation.className = 'citation';
          if (evidence.url?.startsWith('http://') || evidence.url?.startsWith('https://')) {
            const link = document.createElement('a');
            link.href = evidence.url;
            link.rel = 'noreferrer noopener';
            link.target = '_blank';
            link.textContent = evidence.title || evidence.url;
            citation.append(link);
          } else {
            citation.textContent = evidence.title || '本机粘贴材料';
          }
          if (!evidence.snapshot_available) citation.append(' · 原始快照已清理，摘录保留但无法重新定位');
          source.append(citation);
          card.append(source);
        });
        if (taskId && observation.field) {
          const correction = document.createElement('details');
          correction.className = 'correction-control';
          const summary = document.createElement('summary');
          summary.textContent = observation.origin === 'user_correction' ? '修改已保存' : '修正此字段';
          correction.append(summary);
          const input = document.createElement('input');
          input.value = observation.value || '';
          input.setAttribute('aria-label', `修正${entity.entity}的${observation.field}`);
          const save = document.createElement('button');
          save.type = 'button';
          save.className = 'secondary-button small-button';
          save.textContent = '保存修正';
          save.addEventListener('click', async () => {
            try {
              await api(`/api/entities/${encodeURIComponent(entity.entity_id)}`, { method: 'PATCH', body: JSON.stringify({ field: observation.field, value: input.value }) });
              await refreshResults();
            } catch (error) { setText('app-message', error.message); }
          });
          correction.append(input, save);
          if (observation.origin === 'user_correction') {
            const undo = document.createElement('button');
            undo.type = 'button';
            undo.className = 'quiet-button';
            undo.textContent = '撤销修正';
            undo.addEventListener('click', async () => {
              try {
                await api(`/api/entities/${encodeURIComponent(entity.entity_id)}/corrections/${encodeURIComponent(observation.field)}`, { method: 'DELETE' });
                await refreshResults();
              } catch (error) { setText('app-message', error.message); }
            });
            correction.append(undo);
          }
          card.append(correction);
        }
      });
      (entity.conflicts || []).forEach((group) => {
        const conflict = document.createElement('p');
        conflict.className = 'conflict-note';
        conflict.textContent = `${group.field}存在不同记载：${group.values.map((item) => `${item.value}（${item.independent_source_count} 个独立来源）`).join('；')}`;
        card.append(conflict);
      });
      target.append(card);
    });
    setText('selected-count', `已选 ${selectedResults.size} 个对象`);
  }

  async function refreshResults(targetId = 'result-list') {
    if (!taskId) return;
    const view = byId('result-filter')?.value || 'all';
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/results?view=${encodeURIComponent(view)}`);
    renderResults(data, targetId);
    return data;
  }

  function selectedEntityIds() {
    return Array.from(selectedResults);
  }

  async function compareSelected(targetTaskId) {
    const ids = selectedEntityIds();
    const panel = byId('compare-results');
    if (!panel) return;
    panel.replaceChildren();
    show(panel);
    if (ids.length < 2) {
      const message = document.createElement('p');
      message.className = 'notice';
      message.textContent = '至少选择两个对象，才能并排比较。';
      panel.append(message);
      return;
    }
    const allResults = await api(`/api/tasks/${encodeURIComponent(targetTaskId)}/results?view=all`);
    const selected = allResults.items.filter((item) => selectedResults.has(item.entity_id));
    const intro = document.createElement('p');
    intro.className = 'notice';
    intro.textContent = '并排展示选中对象的已有观察值和来源。缺失项保留为“未提供”；本表不自动换算币种、周期或规格，也不生成推荐排名。';
    panel.append(intro);
    if (selected.length < 2) {
      const message = document.createElement('p');
      message.className = 'helper-text';
      message.textContent = '所选对象在当前任务结果中不足两个。';
      panel.append(message);
      return;
    }

    const fields = Array.from(new Set(selected.flatMap((entity) => entity.observations.map((item) => item.field))))
      .sort((left, right) => left.localeCompare(right, 'zh-CN'));
    const table = document.createElement('table');
    table.className = 'comparison-table';
    const head = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['字段', ...selected.map((entity) => entity.entity)].forEach((label) => {
      const cell = document.createElement('th');
      cell.scope = 'col';
      cell.textContent = label;
      headerRow.append(cell);
    });
    head.append(headerRow);
    table.append(head);
    const body = document.createElement('tbody');
    const statusRow = document.createElement('tr');
    const statusHeading = document.createElement('th');
    statusHeading.scope = 'row';
    statusHeading.textContent = '核查状态';
    statusRow.append(statusHeading);
    selected.forEach((entity) => {
      const cell = document.createElement('td');
      cell.textContent = ({ confirmed: '有来源核对', user_confirmed: '用户已修正', pending_review: '待核查', excluded: '不符合条件' })[entity.status] || entity.status;
      statusRow.append(cell);
    });
    body.append(statusRow);

    fields.forEach((field) => {
      const row = document.createElement('tr');
      const heading = document.createElement('th');
      heading.scope = 'row';
      heading.textContent = field;
      row.append(heading);
      selected.forEach((entity) => {
        const cell = document.createElement('td');
        const observations = entity.observations.filter((item) => item.field === field);
        if (!observations.length) {
          cell.textContent = '未提供';
        } else {
          const values = Array.from(new Set(observations.map((item) => `${item.value ?? '未知'}${item.unit ? ` ${item.unit}` : ''}${item.period ? ` · ${item.period}` : ''}`)));
          const value = document.createElement('div');
          value.textContent = values.join('；');
          cell.append(value);
          observations.flatMap((item) => item.evidence || []).slice(0, 3).forEach((evidence) => {
            const source = document.createElement('a');
            source.className = 'compare-source';
            source.textContent = evidence.title || evidence.url || '来源摘录';
            if (evidence.url?.startsWith('http://') || evidence.url?.startsWith('https://')) {
              source.href = evidence.url;
              source.target = '_blank';
              source.rel = 'noreferrer noopener';
            } else {
              source.removeAttribute('href');
            }
            source.title = evidence.quote || '';
            cell.append(source);
          });
        }
        row.append(cell);
      });
      body.append(row);
    });
    table.append(body);
    panel.append(table);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function exportResults(targetTaskId, format, targetFilter = 'all', copy = false) {
    const entityIds = selectedEntityIds();
    const response = await fetch(`/api/tasks/${encodeURIComponent(targetTaskId)}/export`, {
      method: 'POST',
      headers: { ...csrfHeaders, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        format,
        view: targetFilter,
        entity_ids: entityIds.length ? entityIds : null,
      }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || '导出未完成');
    }
    if (copy) {
      const content = await response.text();
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content);
        setText('app-message', '文本已复制到剪贴板。');
      } else {
        downloadBlob(new Blob([content], { type: 'text/plain;charset=utf-8' }), 'information-results.txt');
        setText('app-message', '浏览器不支持剪贴板，已下载文本文件。');
      }
      return;
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `information-results.${format}`;
    downloadBlob(blob, filename);
    setText('app-message', `已导出 ${response.headers.get('X-Export-Row-Count') || 0} 条记录。`);
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function deleteTask(taskToDelete, keepResults) {
    const prompt = keepResults
      ? '将清理原始来源快照，并保留摘录和结果；来源状态会变成“证据已失效”。继续吗？'
      : '将永久删除此任务、结果、修正和来源快照。此操作不能撤销。继续吗？';
    if (!window.confirm(prompt)) return;
    await api(`/api/tasks/${encodeURIComponent(taskToDelete)}/data?keep_derived_results=${keepResults}`, { method: 'DELETE' });
    if (byId('task-page')) {
      if (keepResults) {
        setText('task-page-error', '原始快照已清理，结果和摘录已保留；摘录无法重新定位原文。');
        const data = await api(`/api/tasks/${encodeURIComponent(taskToDelete)}/results`);
        renderResults(data, 'task-page-results');
      } else {
        window.location.assign('/history');
      }
    } else {
      await loadHistory(byId('history-query')?.value || '');
      setText('history-message', '任务及其结果已删除。');
    }
  }

  async function refreshRun() {
    if (!runId) return;
    const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
    setText('run-status', ({ queued: '排队中', running: '正在采集', paused: '已暂停', interrupted: '中断，可恢复', completed: '采集完成', partial: '部分完成', blocked: '需要配置或额度不足', failed: '运行失败', cancelled: '已停止' })[run.status] || run.status);
    setText('run-reason', run.stop_reason || '');
    renderBudget(run.budget);
    const active = ['queued', 'running'].includes(run.status);
    show(byId('pause-run'), run.status === 'running');
    show(byId('resume-run'), ['paused', 'interrupted'].includes(run.status));
    show(byId('cancel-run'), active || run.status === 'paused');
    const events = await api(`/api/runs/${encodeURIComponent(runId)}/events?after=${eventCursor}`);
    const list = byId('event-list');
    events.items.forEach((event) => {
      const item = document.createElement('li');
      const message = document.createElement('span');
      message.textContent = event.message;
      const time = document.createElement('time');
      time.dateTime = event.created_at;
      time.textContent = new Date(event.created_at).toLocaleTimeString();
      item.append(time, message);
      list?.append(item);
      eventCursor = event.id || eventCursor;
    });
    await refreshResults();
    if (!active && !['paused', 'interrupted'].includes(run.status)) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }

  async function startRun() {
    const start = byId('start-task');
    if (start) start.disabled = true;
    try {
      const run = await api(`/api/tasks/${encodeURIComponent(taskId)}/runs`, { method: 'POST', body: '{}' });
      runId = run.id;
      eventCursor = 0;
      byId('event-list')?.replaceChildren();
      show(byId('run-panel'));
      await refreshRun();
      if (!pollHandle) pollHandle = setInterval(() => refreshRun().catch((error) => setText('app-message', error.message)), 1000);
      byId('run-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      setText('preview-error', error.message);
      if (start) start.disabled = false;
    }
  }

  const taskForm = byId('task-form');
  taskForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    setText('preview-error', '');
    const button = taskForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const task = await api('/api/tasks', {
        method: 'POST',
        body: JSON.stringify({ raw_input: byId('task-input').value, supplemental_material: byId('source-input').value }),
      });
      renderPreview(task);
    } catch (error) {
      setText('preview-error', error.message);
      show(byId('task-preview'));
    } finally { button.disabled = false; }
  });

  byId('edit-task')?.addEventListener('click', () => {
    show(byId('task-preview'), false);
    byId('task-input')?.focus();
  });
  byId('start-task')?.addEventListener('click', startRun);
  byId('pause-run')?.addEventListener('click', async () => {
    try { await api(`/api/runs/${encodeURIComponent(runId)}/pause`, { method: 'POST', body: '{}' }); await refreshRun(); }
    catch (error) { setText('app-message', error.message); }
  });
  byId('resume-run')?.addEventListener('click', async () => {
    try { await api(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST', body: '{}' }); await refreshRun(); }
    catch (error) { setText('app-message', error.message); }
  });
  byId('cancel-run')?.addEventListener('click', async () => {
    try { await api(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', body: '{}' }); await refreshRun(); }
    catch (error) { setText('app-message', error.message); }
  });
  byId('result-filter')?.addEventListener('change', () => refreshResults().catch((error) => setText('app-message', error.message)));
  byId('result-sort')?.addEventListener('change', async () => {
    try {
      if (byId('task-page')) {
        const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/results`);
        renderResults(data, 'task-page-results');
      } else {
        await refreshResults();
      }
    } catch (error) { setText('app-message', error.message); }
  });
  byId('compare-button')?.addEventListener('click', () => {
    compareSelected(taskId).catch((error) => setText('app-message', error.message));
  });
  byId('export-button')?.addEventListener('click', () => {
    exportResults(taskId, byId('export-format').value, byId('result-filter').value)
      .catch((error) => setText('app-message', error.message));
  });
  byId('copy-button')?.addEventListener('click', () => {
    exportResults(taskId, 'text', byId('result-filter').value, true)
      .catch((error) => setText('app-message', error.message));
  });
  byId('message-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = byId('message-input');
    if (!input.value.trim()) return;
    try {
      const result = await api(`/api/tasks/${encodeURIComponent(taskId)}/messages`, { method: 'POST', body: JSON.stringify({ message: input.value }) });
      input.value = '';
      if (result.action === 'view') {
        byId('result-filter').value = result.view_filter || 'confirmed';
        await refreshResults();
      } else if (result.action === 'continue') {
        runId = result.run.id;
        eventCursor = 0;
        await refreshRun();
      } else {
        setText('preview-title', `任务已更新为第 ${result.version.number} 版`);
        setText('preview-summary', result.version.spec.summary || result.version.spec.raw_input);
        setText('preview-search-query', result.search_query || result.version.spec.summary || result.version.spec.raw_input);
        const capability = result.capability;
        setText('capability-box', capability.missing?.join('；') || '新版本可开始采集。');
        show(byId('search-settings-link'), (capability.missing || []).some((item) => item.includes('搜索服务')));
        byId('start-task').disabled = !capability.ready;
        show(byId('task-preview'));
      }
    } catch (error) { setText('app-message', error.message); }
  });

  const providerForm = byId('provider-form');
  let configuredSearchProviders = new Set(JSON.parse(providerForm?.dataset.configuredSearchProviders || '[]'));
  let savedModelPreset = providerForm?.dataset.modelPreset || 'none';
  let savedModelConfigured = providerForm?.dataset.modelConfigured === 'true';
  const searchLabels = { baidu: '百度搜索', brave: 'Brave Search' };
  function renderProviderSelection() {
    if (!providerForm) return;
    const searchProvider = byId('search-provider').value;
    const modelPreset = byId('model-preset').value;
    const searchLabel = searchProvider === 'browser' ? (byId('browser-search-engine').value === 'baidu' ? '百度' : '必应') : searchLabels[searchProvider];
    setText('search-state', searchProvider === 'browser' ? `${searchLabel}公开网页搜索，无需密钥，可以开始搜索` : `${searchLabel}${configuredSearchProviders.has(searchProvider) ? '已配置，可以自动发现网页' : '尚未配置；也可以直接提供网址或粘贴文字'}`);
    setText('model-state', modelPreset === 'none' ? '本地读取，不使用云模型，无需密钥' : (savedModelConfigured && savedModelPreset === modelPreset ? '已配置' : '尚未配置；请填写该服务的密钥'));
    show(byId('browser-search-help'), searchProvider === 'browser');
    show(byId('search-key-fields'), searchProvider !== 'browser');
    show(byId('baidu-search-help'), searchProvider === 'baidu');
    show(byId('brave-search-help'), searchProvider === 'brave');
    show(byId('zhipu-model-help'), modelPreset === 'zhipu_free');
    show(byId('local-model-help'), modelPreset === 'none');
    show(byId('model-key-fields'), modelPreset !== 'none');
    show(byId('custom-model-fields'), modelPreset === 'custom');
    ['model-base-url', 'model-name', 'model-json-mode'].forEach((id) => { byId(id).disabled = modelPreset !== 'custom'; });
    setText('provider-data-flow', `搜索词会发送到${searchLabel}，并访问结果中的来源网站；${modelPreset === 'none' ? '网页正文在本机读取和保存，不发送给云模型' : `任务文字和选中的来源正文会发送到${modelPreset === 'zhipu_free' ? '智谱' : '你配置的模型服务'}`}。`);
  }
  byId('search-provider')?.addEventListener('change', () => {
    byId('search-secret').value = '';
    byId('clear-search').checked = false;
    setText('settings-message', '');
    renderProviderSelection();
  });
  byId('model-preset')?.addEventListener('change', () => {
    byId('model-secret').value = '';
    byId('clear-model').checked = false;
    setText('settings-message', '');
    renderProviderSelection();
  });
  byId('browser-search-engine')?.addEventListener('change', renderProviderSelection);
  renderProviderSelection();

  providerForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = { search_provider: byId('search-provider').value };
    if (body.search_provider === 'browser') {
      body.browser_search_engine = byId('browser-search-engine').value;
    } else {
      if (byId('search-secret').value) body.search_secret = byId('search-secret').value;
      if (byId('clear-search').checked) body.search_secret = '';
    }
    const baseUrl = byId('model-base-url').value.trim();
    const modelName = byId('model-name').value.trim();
    const preset = byId('model-preset').value;
    if (preset === 'none' || preset === 'zhipu_free') {
      body.model_preset = preset;
    } else if (baseUrl || modelName || byId('model-secret').value) {
      body.model_preset = preset;
      body.model_base_url = baseUrl;
      body.model_name = modelName;
      body.model_json_mode = byId('model-json-mode').value;
    }
    if (preset !== 'none' && byId('model-secret').value) body.model_secret = byId('model-secret').value;
    body.clear_model = preset === 'none' || byId('clear-model').checked;
    const message = byId('settings-message');
    try {
      const config = await api('/api/settings/providers', { method: 'PUT', body: JSON.stringify(body) });
      configuredSearchProviders = new Set(config.configured_search_providers || []);
      savedModelPreset = config.model_preset;
      savedModelConfigured = config.model_configured;
      byId('search-provider').value = config.search_provider;
      byId('browser-search-engine').value = config.browser_search_engine || 'bing';
      byId('model-preset').value = config.model_preset;
      byId('model-base-url').value = config.model_base_url;
      byId('model-name').value = config.model_name;
      byId('model-json-mode').value = config.model_json_mode;
      byId('search-secret').value = '';
      byId('model-secret').value = '';
      byId('clear-search').checked = false;
      byId('clear-model').checked = false;
      renderProviderSelection();
      message.textContent = '设置已保存。密钥不会再次显示。';
      message.className = 'success-text';
    } catch (error) {
      message.textContent = error.message;
      message.className = 'error-text';
    }
  });

  async function loadHistory(query = '') {
    const params = new URLSearchParams({ query });
    const data = await api(`/api/history?${params}`);
    const target = byId('history-list');
    target.replaceChildren();
    if (!data.items.length) {
      const empty = document.createElement('p');
      empty.className = 'helper-text';
      empty.textContent = '还没有匹配的任务记录。';
      target.append(empty);
    }
    data.items.forEach((task) => {
      const article = document.createElement('article');
      article.className = 'history-item';
      const link = document.createElement('a');
      link.href = `/tasks/${encodeURIComponent(task.id)}`;
      link.textContent = task.latest_version.spec.summary || task.latest_version.spec.raw_input;
      const info = document.createElement('span');
      info.textContent = `第 ${task.latest_version.number} 版 · ${task.latest_run_status || '尚未运行'} · ${new Date(task.updated_at).toLocaleString()}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'danger-button small-button history-delete';
      remove.textContent = '删除任务及结果';
      remove.addEventListener('click', () => deleteTask(task.id, false).catch((error) => setText('history-message', error.message)));
      article.append(link, info, remove);
      target.append(article);
    });
    setText('history-message', `共 ${data.total} 个任务`);
  }
  byId('history-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    loadHistory(byId('history-query').value).catch((error) => setText('history-message', error.message));
  });
  if (byId('history-list')) loadHistory().catch((error) => setText('history-message', error.message));

  const taskPage = byId('task-page');
  if (taskPage) {
    taskId = taskPage.dataset.taskId;
    const refreshTaskPage = async () => {
      const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      const capability = task.capability;
      setText('task-detail-capability', capability.missing?.join('；') || '任务当前可运行。');
      const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/results`);
      renderResults(data, 'task-page-results');
      const run = task.current_run;
      byId('task-page-start').disabled = !capability.ready || ['queued', 'running'].includes(run?.status);
      if (run?.id) {
        runId = run.id;
        const labels = { queued: '排队中', running: '正在采集', paused: '已暂停', interrupted: '中断，可恢复', completed: '采集完成', partial: '部分完成', blocked: '需要配置或额度不足', failed: '运行失败', cancelled: '已停止' };
        setText('task-page-run', `最近一轮状态：${labels[run.status] || run.status}${run.stop_reason ? ` · ${run.stop_reason}` : ''}`);
        show(byId('task-page-pause'), run.status === 'running');
        show(byId('task-page-resume'), ['paused', 'interrupted'].includes(run.status));
        show(byId('task-page-cancel'), ['queued', 'running', 'paused', 'interrupted'].includes(run.status));
        const runDetails = await api(`/api/runs/${encodeURIComponent(run.id)}`);
        renderBudget(runDetails.budget, 'task-page-budget');
        const events = await api(`/api/runs/${encodeURIComponent(run.id)}/events?after=${eventCursor}`);
        const eventList = byId('task-page-events');
        events.items.forEach((event) => {
          const item = document.createElement('li');
          const message = document.createElement('span');
          message.textContent = event.message;
          const time = document.createElement('time');
          time.dateTime = event.created_at;
          time.textContent = new Date(event.created_at).toLocaleTimeString();
          item.append(time, message);
          eventList?.append(item);
          eventCursor = event.id || eventCursor;
        });
      } else {
        runId = null;
        setText('task-page-run', '还没有运行记录。');
        show(byId('task-page-pause'), false);
        show(byId('task-page-resume'), false);
        show(byId('task-page-cancel'), false);
      }
      if (['queued', 'running'].includes(run?.status)) window.setTimeout(() => refreshTaskPage().catch((error) => setText('task-page-error', error.message)), 1000);
    };
    refreshTaskPage().catch((error) => setText('task-page-error', error.message));
    byId('task-page-start')?.addEventListener('click', async () => {
      try {
        const run = await api(`/api/tasks/${encodeURIComponent(taskId)}/runs`, { method: 'POST', body: '{}' });
        runId = run.id;
        eventCursor = 0;
        byId('task-page-events')?.replaceChildren();
        setText('task-page-run', `运行中：${run.id}`);
        window.setTimeout(() => refreshTaskPage().catch((error) => setText('task-page-error', error.message)), 300);
      } catch (error) { setText('task-page-error', error.message); }
    });
    byId('task-page-pause')?.addEventListener('click', async () => {
      try { await api(`/api/runs/${encodeURIComponent(runId)}/pause`, { method: 'POST', body: '{}' }); await refreshTaskPage(); }
      catch (error) { setText('task-page-error', error.message); }
    });
    byId('task-page-resume')?.addEventListener('click', async () => {
      try { await api(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST', body: '{}' }); await refreshTaskPage(); }
      catch (error) { setText('task-page-error', error.message); }
    });
    byId('task-page-cancel')?.addEventListener('click', async () => {
      try { await api(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', body: '{}' }); await refreshTaskPage(); }
      catch (error) { setText('task-page-error', error.message); }
    });
    byId('task-page-keep-results')?.addEventListener('click', () => deleteTask(taskId, true).catch((error) => setText('task-page-error', error.message)));
    byId('task-page-delete')?.addEventListener('click', () => deleteTask(taskId, false).catch((error) => setText('task-page-error', error.message)));
    byId('task-export-button')?.addEventListener('click', () => exportResults(taskId, byId('task-export-format').value, 'all').catch((error) => setText('task-page-error', error.message)));
    byId('task-copy-button')?.addEventListener('click', () => exportResults(taskId, 'text', 'all', true).catch((error) => setText('task-page-error', error.message)));
  }
})();
