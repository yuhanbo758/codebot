const vscode = acquireVsCodeApi();
const el = (id) => document.getElementById(id);
const state = {
  models: [], skills: [], knowledge: [], commands: [], conversations: [], contexts: [],
  knowledgePaths: [], assistant: null, assistantHasContent: false, progressRoot: null, lastRole: '', streamFailed: false,
  running: false, suggestionItems: [], suggestionIndex: 0,
};

function post(type, extra) { vscode.postMessage(Object.assign({ type: type }, extra || {})); }
function setStatus(text, error) { el('status').textContent = text || ''; el('status').className = error ? 'status error' : 'status'; }
function option(select, value, label, disabled) { const item = document.createElement('option'); item.value = value; item.textContent = label; item.disabled = Boolean(disabled); select.appendChild(item); }
function nearBottom() { return el('messages').scrollHeight - el('messages').scrollTop - el('messages').clientHeight < 80; }
function scrollBottom(force) { if (force || nearBottom()) requestAnimationFrame(() => { el('messages').scrollTop = el('messages').scrollHeight; }); }

function renderConversations(items, selectedId) {
  state.conversations = items || [];
  const select = el('conversation'); select.innerHTML = '';
  option(select, '', '选择历史对话…');
  for (const item of state.conversations) option(select, String(item.id), item.title || ('对话 ' + item.id));
  if (selectedId) select.value = String(selectedId);
}

function applyPreferences(preferences) {
  const value = preferences || {};
  el('mode').value = value.mode || 'editor';
  el('target').value = value.target || 'codebot';
  const requestedModel = value.model || '';
  // 旧版本可能持久化过只能走 Chat Completions 的 OpenCode 模型。
  // Codex 目标的模型接口不会返回它们，因此这里必须清空而不是让一个不可见旧值继续随请求发送。
  el('model').value = Array.from(el('model').options).some((item) => item.value === requestedModel) ? requestedModel : '';
  el('effort').value = value.reasoningEffort || '';
  renderEfforts();
}

function savePreferences() {
  post('preferences', { preferences: { mode: el('mode').value, target: el('target').value, model: el('model').value, reasoningEffort: el('effort').value } });
}

function renderModels(models) {
  state.models = models || [];
  const selected = el('model').value;
  const model = el('model'); model.innerHTML = ''; option(model, '', '默认模型');
  for (const item of state.models) {
    const id = typeof item === 'string' ? item : (item.id || item.model || item.name);
    if (id) {
      const baseLabel = typeof item === 'string' ? item : (item.displayName || item.display_name || item.name || id);
      const sourceLabel = typeof item === 'object'
        && item.source === 'opencode'
        && !String(baseLabel).includes('OpenCode')
        ? ' · OpenCode 兼容模型'
        : '';
      option(model, id, baseLabel + sourceLabel, typeof item === 'object' && item.runnable === false);
    }
  }
  if (selected && Array.from(model.options).some((item) => item.value === selected)) model.value = selected;
  renderEfforts();
}

function renderEfforts() {
  const select = el('effort');
  const codex = String(el('target').value || '').startsWith('codex');
  select.classList.toggle('hidden', !codex);
  const selectedEffort = select.value;
  const selectedModel = state.models.find((item) => typeof item === 'object' && (item.id || item.model || item.name) === el('model').value);
  const raw = selectedModel?.supportedReasoningEfforts || selectedModel?.supported_reasoning_efforts || [];
  const efforts = raw.map((item) => typeof item === 'string' ? item : item?.effort || item?.value || item?.reasoningEffort).filter(Boolean);
  // OpenCode 兼容模型只展示上游明确声明的档位；没有元数据时交给 Codex/provider 使用默认值。
  // Codex 原生旧格式模型仍保留历史兜底列表，避免兼容旧版 App Server 返回结构。
  const values = efforts.length
    ? [...new Set(efforts)]
    : selectedModel?.source === 'opencode'
      ? []
      : ['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
  select.innerHTML = ''; option(select, '', '默认推理');
  for (const effort of values) option(select, effort, ({ minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '超高', max: '最大', ultra: '自动委派' })[effort] || effort);
  if (values.includes(selectedEffort)) select.value = selectedEffort;
}

function addMessage(role, text, contexts, messageId) {
  const empty = el('messages').querySelector('.empty'); if (empty) empty.remove();
  const wrapper = document.createElement('article'); wrapper.className = 'message ' + role;
  if (messageId !== undefined && messageId !== null) wrapper.dataset.messageId = String(messageId);
  const head = document.createElement('div'); head.className = 'message-head';
  const label = document.createElement('span'); label.textContent = role === 'user' ? '你' : 'Codebot';
  const actions = document.createElement('span'); actions.className = 'message-actions';
  const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'message-action'; copy.textContent = '复制'; copy.title = '复制这条消息';
  copy.onclick = () => post('copyText', { text: bubble.textContent || '' });
  actions.appendChild(copy);
  if (role === 'user' && messageId !== undefined && messageId !== null) {
    const undo = document.createElement('button'); undo.type = 'button'; undo.className = 'message-action danger'; undo.textContent = '撤销';
    undo.title = '撤销这轮及之后的对话，并恢复该轮修改前的项目文件';
    undo.onclick = () => {
      if (state.running) { setStatus('请先停止当前任务，再执行撤销。', true); return; }
      if (confirm('撤销这轮及之后的对话，并将项目文件恢复到这轮修改前？')) post('undo', { messageId: messageId });
    };
    actions.appendChild(undo);
  }
  head.append(label, actions);
  const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = text || '';
  wrapper.append(head, bubble);
  if (contexts && contexts.length) {
    const summary = document.createElement('div'); summary.className = 'context-summary';
    summary.textContent = contexts.map((item) => item.label).join(' · '); wrapper.appendChild(summary);
  }
  el('messages').appendChild(wrapper); state.lastRole = role; scrollBottom(true); return bubble;
}

function renderHistory(items) {
  el('messages').innerHTML = ''; state.assistant = null; state.assistantHasContent = false; state.progressRoot = null; state.lastRole = ''; state.streamFailed = false;
  if (!items || !items.length) {
    el('messages').innerHTML = '<div class="empty">该对话暂无消息，可从下方继续任务。</div>'; return;
  }
  for (const item of items) addMessage(item.role === 'user' ? 'user' : 'assistant', item.content || '', null, item.id);
  scrollBottom(true);
}

function setRunning(running) {
  state.running = Boolean(running);
  el('send').disabled = false;
  el('send').textContent = state.running ? '停止' : '发送';
  el('send').classList.toggle('stop', state.running);
  // 单个 Webview 只维护一个活动流；运行中锁定会话和执行参数，避免切换后把
  // 当前流的完成事件、历史刷新或 Git 撤销错误地应用到另一个对话。
  for (const id of ['conversation', 'new', 'refresh', 'mode', 'target', 'model', 'effort']) {
    el(id).disabled = state.running;
  }
}

function ensureStreamingAssistant() {
  if (!state.assistant) {
    state.assistant = addMessage('assistant', '正在等待模型输出…');
    state.assistant.classList.add('thinking');
    state.assistantHasContent = false;
  }
  return state.assistant;
}

function eventLabel(event) {
  const part = event.part || {};
  return event.summary || event.message || part.title || part.name || part.tool || event.event_type || event.phase || event.type || '正在处理';
}

function addProgressEvent(event) {
  ensureStreamingAssistant();
  if (!state.progressRoot) {
    state.progressRoot = document.createElement('div'); state.progressRoot.className = 'progress-events';
    state.assistant.parentElement.appendChild(state.progressRoot);
  }
  const text = String(eventLabel(event) || '').trim();
  if (!text) return;
  const last = state.progressRoot.lastElementChild;
  if (last && last.textContent === text) return;
  const row = document.createElement('div'); row.className = 'progress-event'; row.textContent = text;
  state.progressRoot.appendChild(row);
  while (state.progressRoot.children.length > 30) state.progressRoot.firstElementChild.remove();
  scrollBottom(false);
}

function copyConversation() {
  const lines = [];
  for (const message of el('messages').querySelectorAll('.message')) {
    const role = message.classList.contains('user') ? '你' : 'Codebot';
    const text = message.querySelector('.bubble')?.textContent || '';
    if (text.trim()) lines.push(role + '：\n' + text.trim());
  }
  if (!lines.length) { setStatus('当前没有可复制的对话。'); return; }
  post('copyText', { text: lines.join('\n\n') }); setStatus('已复制整个对话');
}

function renderContexts() {
  const root = el('contextChips'); root.innerHTML = '';
  for (const item of state.contexts) {
    const chip = document.createElement('div'); chip.className = 'chip';
    const text = document.createElement('span'); text.textContent = (item.kind === 'editor' ? '选区 ' : item.kind === 'terminal' ? '终端 ' : '文件 ') + item.label;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.title = '移除';
    remove.onclick = () => { state.contexts = state.contexts.filter((entry) => entry.id !== item.id); renderContexts(); };
    chip.append(text, remove); root.appendChild(chip);
  }
  for (const item of state.knowledgePaths) {
    const chip = document.createElement('div'); chip.className = 'chip';
    const text = document.createElement('span'); text.textContent = '# ' + item.name;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.onclick = () => { state.knowledgePaths = state.knowledgePaths.filter((entry) => entry.path !== item.path); renderContexts(); };
    chip.append(text, remove); root.appendChild(chip);
  }
}

function currentTrigger() {
  const input = el('input'); const before = input.value.slice(0, input.selectionStart);
  const match = before.match(/(?:^|\s)([/@#])([^\s\n]*)$/); return match ? { symbol: match[1], query: match[2], start: input.selectionStart - match[2].length - 1 } : null;
}

function suggestionData(trigger) {
  const query = trigger.query.toLowerCase();
  const includes = function () { const values = Array.from(arguments); return values.join(' ').toLowerCase().includes(query); };
  if (trigger.symbol === '/') return state.commands.filter((item) => includes(item.label, item.name, item.description)).slice(0, 12).map((item) => ({ kind: 'command', item: item, title: item.label, detail: item.description || '' }));
  if (trigger.symbol === '@') return state.skills.filter((item) => includes(item.name, item.slug, item.description, item.sourceLabel)).slice(0, 12).map((item) => ({ kind: 'skill', item: item, title: '@' + (item.name || item.slug), detail: item.description || '' }));
  return state.knowledge.filter((item) => includes(item.name, item.path, item.description)).slice(0, 12).map((item) => ({ kind: 'knowledge', item: item, title: '#' + (item.name || item.path), detail: item.path || '' }));
}

function updateSuggestions() {
  const trigger = currentTrigger();
  if (!trigger) return hideSuggestions();
  state.suggestionItems = suggestionData(trigger); state.suggestionIndex = 0;
  const root = el('suggestions'); root.innerHTML = '';
  if (!state.suggestionItems.length) return hideSuggestions();
  state.suggestionItems.forEach((entry, index) => {
    const row = document.createElement('div'); row.className = 'suggestion' + (index === 0 ? ' active' : '');
    const title = document.createElement('div'); title.className = 'suggestion-title'; title.textContent = entry.title;
    const detail = document.createElement('div'); detail.className = 'suggestion-detail'; detail.textContent = entry.detail;
    row.append(title, detail); row.onmousedown = (event) => { event.preventDefault(); chooseSuggestion(index); }; root.appendChild(row);
  });
  root.classList.remove('hidden');
}

function hideSuggestions() { state.suggestionItems = []; el('suggestions').classList.add('hidden'); }
function replaceTrigger(replacement) {
  const input = el('input'); const trigger = currentTrigger(); if (!trigger) return;
  const end = input.selectionStart; input.value = input.value.slice(0, trigger.start) + replacement + input.value.slice(end);
  const cursor = trigger.start + replacement.length; input.setSelectionRange(cursor, cursor); input.focus(); hideSuggestions();
}

function chooseSuggestion(index) {
  const entry = state.suggestionItems[index]; if (!entry) return;
  if (entry.kind === 'skill') {
    const item = entry.item; replaceTrigger('使用技能 @[' + item.id + '] ' + (item.name || item.slug) + ' '); return;
  }
  if (entry.kind === 'knowledge') {
    const item = entry.item; const path = item.path || item.id;
    if (!state.knowledgePaths.some((entry) => entry.path === path)) state.knowledgePaths.push({ name: item.name || path, path: path });
    el('target').value = el('target').value === 'codex' ? 'codex_obsidian' : 'obsidian'; savePreferences(); post('refreshModels', { target: el('target').value }); renderContexts(); replaceTrigger(''); return;
  }
  const command = entry.item;
  if (command.type === 'skill' && command.skill_id) { replaceTrigger('使用技能 @[' + command.skill_id + '] ' + command.skill_name + ' '); return; }
  if (['build', 'plan', 'agent', 'editor'].includes(command.name)) { el('mode').value = command.name; savePreferences(); replaceTrigger(''); setStatus('已切换到 ' + command.name.toUpperCase() + ' 模式'); return; }
  if (command.name === 'clear') { el('input').value = ''; state.contexts = []; state.knowledgePaths = []; renderContexts(); hideSuggestions(); return; }
  if (command.name === 'memory') { replaceTrigger('显示与当前话题相关的记忆 '); return; }
  replaceTrigger(command.label + ' ');
}

function moveSuggestion(delta) {
  if (!state.suggestionItems.length) return;
  state.suggestionIndex = Math.max(0, Math.min(state.suggestionItems.length - 1, state.suggestionIndex + delta));
  Array.from(el('suggestions').children).forEach((row, index) => row.classList.toggle('active', index === state.suggestionIndex));
  el('suggestions').children[state.suggestionIndex]?.scrollIntoView({ block: 'nearest' });
}

function send() {
  if (state.running) { post('abort'); setStatus('正在终止任务…'); return; }
  const message = el('input').value.trim(); if (!message && !state.contexts.length) return;
  setRunning(true);
  post('send', { payload: {
    message: message, mode: el('mode').value, target: el('target').value, model: el('model').value, reasoningEffort: el('effort').value,
    contexts: state.contexts, knowledgePaths: state.knowledgePaths.map((item) => item.path),
  } });
  el('input').value = ''; hideSuggestions();
}

function handleStreamEvent(event) {
  const type = event.type;
  if (type === 'content_delta' || type === 'stdout') {
    const assistant = ensureStreamingAssistant();
    const next = event.content || ((state.assistantHasContent ? assistant.textContent : '') + (event.delta || ''));
    assistant.textContent = next; assistant.classList.remove('thinking'); state.assistantHasContent = Boolean(next);
    setStatus('正在输出…'); scrollBottom(false); return;
  }
  if (type === 'meta_event' && event.event_type === 'session.error') {
    const message = event.data?.error?.data?.message || event.data?.error?.message || event.summary || 'OpenCode 会话错误';
    const assistant = ensureStreamingAssistant(); assistant.textContent = '错误：' + message;
    assistant.classList.remove('thinking'); state.assistantHasContent = true; state.streamFailed = true;
    addProgressEvent(event); setStatus(message, true); return;
  }
  if (type === 'tool_event' || type === 'meta_event') {
    addProgressEvent(event); setStatus(eventLabel(event)); return;
  }
  if (type === 'error') {
    const message = event.message || event.error || '执行失败'; const assistant = ensureStreamingAssistant();
    if (!state.assistantHasContent) assistant.textContent = '错误：' + message;
    assistant.classList.remove('thinking'); state.streamFailed = true; setStatus(message, true); return;
  }
  if (type === 'queued') { setStatus(event.message || '任务已排队'); addProgressEvent(event); return; }
  if (type === 'done') {
    const assistant = ensureStreamingAssistant();
    if (event.content) { assistant.textContent = event.content; state.assistantHasContent = true; }
    if (!state.assistantHasContent) assistant.textContent = '模型已结束处理，但没有返回可见文本。';
    assistant.classList.remove('thinking'); setStatus(state.streamFailed ? '执行失败' : '已完成', state.streamFailed); scrollBottom(false); return;
  }
  if (type === 'status') {
    const text = event.message || (event.phase === 'started' ? '正在处理…' : event.phase || '正在处理…');
    setStatus(text); if (event.phase !== 'started') addProgressEvent(event); return;
  }
}

el('new').onclick = () => post('newConversation');
el('refresh').onclick = () => post('refresh');
el('copyConversation').onclick = copyConversation;
el('terminal').onclick = () => post('addTerminalSelection');
el('files').onclick = () => post('attachFiles');
el('send').onclick = send;
el('conversation').onchange = () => { if (el('conversation').value) post('selectConversation', { id: el('conversation').value }); };
el('mode').onchange = savePreferences;
el('target').onchange = () => { el('model').value = ''; el('effort').value = ''; savePreferences(); post('refreshModels', { target: el('target').value }); renderEfforts(); };
el('model').onchange = () => { renderEfforts(); savePreferences(); };
el('effort').onchange = savePreferences;
el('input').oninput = updateSuggestions;
el('input').onkeydown = (event) => {
  if (event.isComposing) return;
  if (!el('suggestions').classList.contains('hidden')) {
    if (event.key === 'ArrowDown') { event.preventDefault(); moveSuggestion(1); return; }
    if (event.key === 'ArrowUp') { event.preventDefault(); moveSuggestion(-1); return; }
    if (event.key === 'Enter' && !event.ctrlKey) { event.preventDefault(); chooseSuggestion(state.suggestionIndex); return; }
    if (event.key === 'Escape') { event.preventDefault(); hideSuggestions(); return; }
  }
  if (event.key === 'Enter' && !event.ctrlKey) { event.preventDefault(); send(); }
};

window.addEventListener('message', ({ data }) => {
  if (data.type === 'status') setStatus(data.text);
  if (data.type === 'fatal' || data.type === 'error') {
    setStatus(data.message, true);
    if (state.running && state.assistant && !state.assistantHasContent) {
      state.assistant.textContent = '错误：' + data.message; state.assistant.classList.remove('thinking');
    }
  }
  if (data.type === 'ready') {
    state.skills = data.skills || []; state.knowledge = data.knowledge || []; state.commands = data.commands || [];
    renderModels(data.models || []);
    renderConversations(data.conversations, data.conversationId); applyPreferences(data.preferences);
    setRunning(false); setStatus('已连接 ' + data.baseUrl + (data.workspacePath ? ' · ' + data.workspacePath : ''));
  }
  if (data.type === 'conversations') renderConversations(data.items, data.conversationId);
  if (data.type === 'models') {
    renderModels(data.models || []);
    if (data.error) setStatus('模型列表加载失败：' + data.error, true);
  }
  if (data.type === 'conversationSelected') {
    el('conversation').value = String(data.id || ''); applyPreferences(data.preferences);
    state.contexts = []; state.knowledgePaths = []; state.assistant = null; state.progressRoot = null;
    renderContexts(); if (data.clear) renderHistory([]);
  }
  if (data.type === 'history') renderHistory(data.items);
  if (data.type === 'contextAdded') { state.contexts.push(data.item); renderContexts(); setStatus('已加入：' + data.item.label); el('input').focus(); }
  if (data.type === 'streamStart') {
    addMessage('user', data.user, data.contexts); state.assistant = null; state.progressRoot = null; state.streamFailed = false;
    ensureStreamingAssistant(); state.contexts = []; state.knowledgePaths = []; renderContexts();
    setRunning(true); setStatus('正在处理…');
  }
  if (data.type === 'streamEvent') handleStreamEvent(data.event || {});
  if (data.type === 'sendRejected') {
    if (!el('input').value && data.message) el('input').value = data.message;
  }
  if (data.type === 'streamIdle') {
    const text = '模型仍在处理，已用时 ' + data.elapsedSeconds + ' 秒（' + data.silentSeconds + ' 秒无新事件）';
    setStatus(text); addProgressEvent({ summary: text });
  }
  if (data.type === 'runtimeState') {
    if (data.running) {
      if (state.lastRole === 'user' && !state.assistant) ensureStreamingAssistant();
      for (const event of data.events || []) handleStreamEvent(event);
      if (data.content) handleStreamEvent({ type: 'content_delta', content: data.content });
      setRunning(true); setStatus(data.queued ? '后台任务处理中，另有 ' + data.queued + ' 个任务排队' : '已恢复后台任务，正在处理…');
    } else if (state.lastRole === 'user' && (data.events || []).length) {
      // 扩展关闭期间任务可能已经结束，但终止性错误不会写入 assistant 历史。
      // 回放最后一次运行事件，确保旧对话也能显示真实错误而不是永久空白。
      ensureStreamingAssistant();
      for (const event of data.events || []) handleStreamEvent(event);
      if (data.content) handleStreamEvent({ type: 'content_delta', content: data.content });
      setRunning(false);
    } else if (state.running) {
      setRunning(false); setStatus('已完成');
    }
  }
  if (data.type === 'undoComplete') {
    const restored = Boolean(data.data && data.data.project_restored);
    setStatus(restored ? '已撤销对话并恢复项目文件' : (data.message || '已撤销对话'));
  }
  if (data.type === 'streamFinished') { setRunning(false); if (!el('status').classList.contains('error')) setStatus('已完成'); el('input').focus(); }
});
