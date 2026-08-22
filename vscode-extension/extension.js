const vscode = require('vscode');
const http = require('http');
const https = require('https');
const path = require('path');
const crypto = require('crypto');

/** Codebot HTTP 客户端。网络请求只在扩展宿主中执行，不向 Webview 暴露 Node 能力。 */
class CodebotClient {
  constructor() { this.baseUrl = ''; }

  configuredCandidates() {
    const configured = vscode.workspace.getConfiguration('codebot').get('backendUrl', '').trim();
    return [...new Set([configured, 'http://127.0.0.1:18080', 'http://127.0.0.1:15682'].filter(Boolean))];
  }

  async connect() {
    const errors = [];
    for (const candidate of this.configuredCandidates()) {
      try {
        await this.requestFrom(candidate, 'GET', '/api/health');
        this.baseUrl = candidate.replace(/\/$/, '');
        return this.baseUrl;
      } catch (error) {
        errors.push(`${candidate}: ${error.message}`);
      }
    }
    throw new Error(`无法连接 Codebot。请先启动桌面版，或配置 codebot.backendUrl。\n${errors.join('\n')}`);
  }

  async request(method, apiPath, body) {
    if (!this.baseUrl) await this.connect();
    return this.requestFrom(this.baseUrl, method, apiPath, body);
  }

  requestFrom(baseUrl, method, apiPath, body) {
    const target = new URL(apiPath, `${baseUrl.replace(/\/$/, '')}/`);
    const transport = target.protocol === 'https:' ? https : http;
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body), 'utf8');
    return new Promise((resolve, reject) => {
      const request = transport.request(target, {
        method,
        headers: payload ? {
          'Content-Type': 'application/json; charset=utf-8',
          'Content-Length': payload.length,
        } : {},
        timeout: 15000,
      }, (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          if ((response.statusCode || 500) >= 400) {
            reject(new Error(parseApiError(text, response.statusCode)));
            return;
          }
          try { resolve(text ? JSON.parse(text) : {}); }
          catch { resolve({ success: true, data: text }); }
        });
      });
      request.on('timeout', () => request.destroy(new Error('连接 Codebot 超时')));
      request.on('error', reject);
      if (payload) request.write(payload);
      request.end();
    });
  }

  /** 按 NDJSON 边界读取 Codebot 流式事件。 */
  async stream(apiPath, body, onEvent) {
    if (!this.baseUrl) await this.connect();
    const target = new URL(apiPath, `${this.baseUrl}/`);
    const transport = target.protocol === 'https:' ? https : http;
    const payload = Buffer.from(JSON.stringify(body), 'utf8');
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        if (error) reject(error); else resolve();
      };
      const request = transport.request(target, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Content-Length': payload.length,
        },
      }, (response) => {
        // 收到响应头后允许长时间流式执行；30 秒超时只约束“完全连不上后端”。
        request.setTimeout(0);
        if ((response.statusCode || 500) >= 400) {
          const chunks = [];
          response.on('data', (chunk) => chunks.push(chunk));
          response.on('end', () => finish(new Error(parseApiError(Buffer.concat(chunks).toString('utf8'), response.statusCode))));
          return;
        }
        response.setEncoding('utf8');
        let buffer = '';
        let ended = false;
        response.on('data', (chunk) => {
          buffer += chunk;
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) emitJsonLine(line, onEvent);
        });
        response.on('end', () => { ended = true; emitJsonLine(buffer, onEvent); finish(); });
        response.on('aborted', () => finish(new Error('Codebot 流连接被后端中断')));
        response.on('close', () => { if (!ended) finish(new Error('Codebot 流连接意外关闭')); });
        response.on('error', finish);
      });
      request.setTimeout(30000, () => request.destroy(new Error('建立 Codebot 流连接超时')));
      request.on('error', finish);
      request.write(payload);
      request.end();
    });
  }
}

function emitJsonLine(line, onEvent) {
  const value = String(line || '').trim();
  if (!value) return;
  try { onEvent(JSON.parse(value)); }
  catch { onEvent({ type: 'error', message: `无法解析流式事件：${value.slice(0, 200)}` }); }
}

function parseApiError(text, statusCode) {
  try {
    const data = JSON.parse(text || '{}');
    return typeof data.detail === 'string' ? data.detail : data.message || JSON.stringify(data.detail) || `HTTP ${statusCode}`;
  } catch { return text || `HTTP ${statusCode}`; }
}

function nonce() { return crypto.randomBytes(16).toString('base64'); }

function isTextFile(name, mimeType) {
  if (String(mimeType || '').startsWith('text/')) return true;
  return /\.(?:[cm]?[jt]sx?|vue|py|java|kt|go|rs|rb|php|cs|cpp|cc|c|h|hpp|swift|scala|sh|ps1|bat|cmd|json|ya?ml|toml|ini|env|md|txt|sql|html?|css|scss|less|xml|svg)$/i.test(name || '');
}

/**
 * 在用户完成编辑器选区后显示一个紧邻代码的 CodeLens 操作。
 *
 * VS Code 的稳定 API 能监听编辑器选区，但不会提供“鼠标松开”这一更底层的 DOM 事件。
 * 这里使用短延迟合并拖动过程中的连续事件：用户停止调整选区后再刷新 CodeLens，既接近
 * “松开后出现”，也避免拖动鼠标时反复重绘编辑器。
 */
class EditorSelectionCodeLensProvider {
  constructor() {
    this.selection = null;
    this.timer = null;
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeCodeLenses = this.emitter.event;
  }

  schedule(event) {
    if (this.timer) clearTimeout(this.timer);
    const selection = event?.selections?.[0];
    if (!event?.textEditor || !selection || selection.isEmpty) {
      this.clear();
      return;
    }
    const document = event.textEditor.document;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.selection = {
        uri: document.uri.toString(),
        start: { line: selection.start.line, character: selection.start.character },
        end: { line: selection.end.line, character: selection.end.character },
      };
      this.emitter.fire();
    }, 180);
  }

  clear() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (!this.selection) return;
    this.selection = null;
    this.emitter.fire();
  }

  provideCodeLenses(document) {
    if (!this.selection || document.uri.toString() !== this.selection.uri) return [];
    const start = new vscode.Position(this.selection.start.line, 0);
    return [new vscode.CodeLens(new vscode.Range(start, start), {
      title: '$(comment-discussion) 加入 Codebot 对话',
      tooltip: '将当前编辑器选区加入 Codebot 输入区',
      command: 'codebot.addEditorSelection',
      arguments: [this.selection],
    })];
  }

  dispose() {
    if (this.timer) clearTimeout(this.timer);
    this.emitter.dispose();
  }
}

/**
 * Codebot 侧栏中的原生文件拖放区。
 *
 * 该视图由 VS Code 自己渲染，不经过 Webview iframe，因此资源管理器拖拽不需要 Shift，
 * `text/uri-list` 会通过稳定的 TreeDragAndDropController API 直接送到扩展宿主。
 */
class CodebotFileDropTreeProvider {
  constructor(chatProvider) {
    this.chatProvider = chatProvider;
    this.dropMimeTypes = ['text/uri-list', 'files'];
    this.dragMimeTypes = [];
    this.dropTarget = { id: 'codebot-file-drop-target' };
  }

  getChildren(element) { return element ? [] : [this.dropTarget]; }

  getTreeItem() {
    const item = new vscode.TreeItem('拖放资源管理器文件到这里', vscode.TreeItemCollapsibleState.None);
    item.description = '加入当前对话';
    item.tooltip = '无需按键；也可以点击这里选择文件';
    item.iconPath = new vscode.ThemeIcon('files');
    item.command = { command: 'codebot.attachFiles', title: '选择文件加入 Codebot 对话' };
    return item;
  }

  async handleDrop(_target, dataTransfer, token) {
    if (token.isCancellationRequested) return;
    const uris = [];
    const uriListItem = dataTransfer.get('text/uri-list');
    if (uriListItem) {
      const raw = await uriListItem.asString();
      for (const value of String(raw || '').split(/\r?\n/)) {
        const text = value.trim();
        if (!text || text.startsWith('#')) continue;
        try { uris.push(vscode.Uri.parse(text)); }
        catch { /* 单个无效 URI 不影响同批其他文件。 */ }
      }
    }
    for (const [, item] of dataTransfer) {
      const file = item.asFile();
      if (file?.uri) uris.push(file.uri);
    }
    const unique = [...new Map(uris.filter((uri) => uri.scheme === 'file').map((uri) => [uri.toString().toLowerCase(), uri])).values()];
    if (!unique.length) throw new Error('拖放内容中没有可读取的本地文件。');
    await this.chatProvider.addResourceFiles(unique);
  }
}

class CodebotChatViewProvider {
  constructor(context) {
    this.context = context;
    this.client = new CodebotClient();
    this.view = null;
    this.conversationId = context.workspaceState.get('codebot.conversationId');
    this.running = false;
    this.pendingContexts = [];
    this.runtimePollTimer = null;
    this.runtimeLastSeq = 0;
    this.runtimePolling = false;
  }

  resolveWebviewView(webviewView) {
    this.view = webviewView;
    const mediaRoot = vscode.Uri.joinPath(this.context.extensionUri, 'media');
    webviewView.webview.options = { enableScripts: true, localResourceRoots: [mediaRoot] };
    webviewView.webview.html = this.html(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((message) => this.handleMessage(message));
    this.initialize();
  }

  post(message) { if (this.view) this.view.webview.postMessage(message); }
  workspaceFolder() { return vscode.workspace.workspaceFolders?.[0]; }
  workspacePath() { return this.workspaceFolder()?.uri.fsPath || ''; }

  conversationPreferences(id = this.conversationId) {
    const all = this.context.workspaceState.get('codebot.conversationPreferences', {});
    const stored = all[String(id || '')] || { mode: 'editor', target: 'codebot', model: '', reasoningEffort: '' };
    const target = this.normalizeTarget(stored.target);
    if (target !== stored.target) {
      all[String(id || '')] = { ...stored, target };
      // workspaceState 写入无需阻塞当前界面初始化；下一次读取即使用已迁移值。
      void this.context.workspaceState.update('codebot.conversationPreferences', all);
    }
    return { ...stored, target };
  }

  normalizeTarget(target) {
    return ({
      hermes: 'codex',
      hermes_cli: 'codex',
      hermes_agent: 'codex',
      hermes_obsidian: 'codex_obsidian',
    })[String(target || '').toLowerCase()] || target || 'codebot';
  }

  async saveConversationPreferences(preferences) {
    if (!this.conversationId) return;
    const all = this.context.workspaceState.get('codebot.conversationPreferences', {});
    all[String(this.conversationId)] = {
      mode: ['build', 'plan', 'agent', 'editor'].includes(preferences.mode) ? preferences.mode : 'editor',
      target: this.normalizeTarget(preferences.target),
      model: preferences.model || '',
      reasoningEffort: preferences.reasoningEffort || '',
    };
    await this.context.workspaceState.update('codebot.conversationPreferences', all);
  }

  async initialize() {
    this.post({ type: 'status', text: '正在连接 Codebot…' });
    try {
      const baseUrl = await this.client.connect();
      const preferences = this.conversationPreferences();
      const modelEndpoint = String(preferences.target || '').startsWith('codex') ? '/api/codex/models' : '/api/chat/models';
      const [models, skills, knowledge, commands, conversations] = await Promise.all([
        this.client.request('GET', modelEndpoint),
        this.client.request('GET', '/api/chat/skills/search?query=&limit=100'),
        this.client.request('GET', '/api/chat/knowledge/search?query=&limit=50'),
        this.client.request('GET', '/api/chat/commands'),
        this.client.request('GET', '/api/chat/conversations?limit=100'),
      ]);
      const items = (conversations?.data?.items || []).filter((item) => item.conversation_type !== 'multi_agent_hub');
      if (this.conversationId && !items.some((item) => Number(item.id) === Number(this.conversationId))) {
        this.conversationId = undefined;
        await this.context.workspaceState.update('codebot.conversationId', undefined);
      }
      this.post({
        type: 'ready',
        baseUrl,
        workspacePath: this.workspacePath(),
        models: Array.isArray(models?.data) ? models.data : models?.data?.models || [],
        skills: skills?.data?.skills || skills?.data?.items || [],
        knowledge: knowledge?.data?.items || [],
        commands: [...(commands?.data?.commands || []), ...(commands?.data?.skills || [])],
        conversations: items,
        conversationId: this.conversationId,
        preferences,
      });
      for (const item of this.pendingContexts.splice(0)) this.post({ type: 'contextAdded', item });
      if (this.conversationId) {
        await this.loadMessages();
        await this.syncRuntime();
      }
    } catch (error) {
      this.post({ type: 'fatal', message: error.message });
    }
  }

  async refreshConversations() {
    const response = await this.client.request('GET', '/api/chat/conversations?limit=100');
    const items = (response?.data?.items || []).filter((item) => item.conversation_type !== 'multi_agent_hub');
    this.post({ type: 'conversations', items, conversationId: this.conversationId });
  }

  async newConversation() {
    this.stopRuntimePolling();
    this.running = false;
    this.runtimeLastSeq = 0;
    const response = await this.client.request('POST', '/api/chat/conversations', {
      title: 'VS Code 新对话',
      project_dir: this.workspacePath() || null,
    });
    this.conversationId = response?.data?.id;
    await this.context.workspaceState.update('codebot.conversationId', this.conversationId);
    await this.saveConversationPreferences({ mode: 'editor', target: 'codebot', model: '', reasoningEffort: '' });
    await this.refreshConversations();
    this.post({ type: 'conversationSelected', id: this.conversationId, clear: true, preferences: this.conversationPreferences() });
    return this.conversationId;
  }

  async selectConversation(id) {
    const numericId = Number(id);
    if (!Number.isInteger(numericId) || numericId <= 0) return;
    this.conversationId = numericId;
    await this.context.workspaceState.update('codebot.conversationId', numericId);
    this.stopRuntimePolling();
    this.running = false;
    this.runtimeLastSeq = 0;
    const preferences = this.conversationPreferences(numericId);
    this.post({ type: 'conversationSelected', id: numericId, preferences });
    await this.loadModelsForTarget(preferences.target);
    await this.ensureConversationWorkspace();
    await this.loadMessages();
    await this.syncRuntime();
  }

  async loadMessages() {
    if (!this.conversationId) return;
    try {
      const response = await this.client.request('GET', `/api/chat/conversations/${this.conversationId}/messages?limit=500`);
      this.post({ type: 'history', items: response?.data?.items || [] });
    } catch (error) {
      this.post({ type: 'error', message: `加载历史对话失败：${error.message}` });
    }
  }

  async ensureConversationWorkspace() {
    const workspacePath = this.workspacePath();
    if (!this.conversationId || !workspacePath) return;
    await this.client.request('PATCH', `/api/chat/conversations/${this.conversationId}/project_dir`, {
      project_dir: workspacePath,
    });
  }

  async syncRuntime() {
    if (!this.conversationId || this.runtimePolling) return null;
    this.runtimePolling = true;
    try {
      const response = await this.client.request(
        'GET',
        `/api/chat/queue_status/${this.conversationId}?since_seq=${this.runtimeLastSeq}`,
      );
      const runtime = response?.data || {};
      this.runtimeLastSeq = Number(runtime.runtime_last_seq || this.runtimeLastSeq || 0);
      this.running = Boolean(runtime.running);
      this.post({
        type: 'runtimeState',
        running: this.running,
        queued: Number(runtime.queued || 0),
        content: runtime.runtime_content || '',
        events: runtime.runtime_events || [],
      });
      if (this.running) this.startRuntimePolling();
      else this.stopRuntimePolling();
      return runtime;
    } finally {
      this.runtimePolling = false;
    }
  }

  startRuntimePolling() {
    if (this.runtimePollTimer) return;
    this.runtimePollTimer = setInterval(async () => {
      try {
        const wasRunning = this.running;
        const runtime = await this.syncRuntime();
        if (wasRunning && runtime && !runtime.running) {
          await this.loadMessages();
          await this.refreshConversations();
          this.post({ type: 'streamFinished', recovered: true });
        }
      } catch (error) {
        this.post({ type: 'status', text: `运行状态同步失败：${error.message}` });
      }
    }, 1500);
  }

  stopRuntimePolling() {
    if (this.runtimePollTimer) clearInterval(this.runtimePollTimer);
    this.runtimePollTimer = null;
  }

  async abort() {
    if (!this.conversationId || !this.running) return;
    this.post({ type: 'status', text: '正在终止任务…' });
    await this.client.request('POST', '/api/chat/abort', { conversation_id: this.conversationId });
    await this.syncRuntime();
  }

  async undo(messageId) {
    if (!this.conversationId) return;
    if (this.running) throw new Error('请先停止当前任务，再执行撤销。');
    const numericMessageId = Number(messageId);
    if (!Number.isFinite(numericMessageId)) throw new Error('该消息尚未写入历史记录，请稍后再试。');
    const response = await this.client.request(
      'POST',
      `/api/chat/conversations/${this.conversationId}/undo`,
      { message_id: numericMessageId, conversation_id: this.conversationId },
    );
    await this.loadMessages();
    await this.refreshConversations();
    this.post({ type: 'undoComplete', data: response?.data || {}, message: response?.message || '已撤销消息' });
  }

  async handleMessage(message) {
    try {
      if (message.type === 'refresh') return this.initialize();
      if (message.type === 'newConversation') return this.newConversation();
      if (message.type === 'selectConversation') return this.selectConversation(message.id);
      if (message.type === 'preferences') return this.saveConversationPreferences(message.preferences || {});
      if (message.type === 'refreshModels') return this.loadModelsForTarget(message.target);
      if (message.type === 'openSettings') return vscode.commands.executeCommand('workbench.action.openSettings', 'codebot.backendUrl');
      if (message.type === 'addEditorSelection') return this.addEditorSelection();
      if (message.type === 'addTerminalSelection') return this.addTerminalSelection();
      if (message.type === 'attachFiles') return this.attachFiles();
      if (message.type === 'copyText') return vscode.env.clipboard.writeText(String(message.text || ''));
      if (message.type === 'abort') return this.abort();
      if (message.type === 'undo') return this.undo(message.messageId);
      if (message.type === 'send') return this.send(message.payload || {});
    } catch (error) {
      this.post({ type: 'error', message: error.message });
      // Webview 会在发消息前立即锁定输入区；流建立前失败也必须释放锁。
      if (message.type === 'send') {
        this.post({ type: 'sendRejected', message: String(message.payload?.message || '') });
        this.post({ type: 'streamFinished' });
      }
    }
  }

  async addEditorSelection(selectionContext) {
    let document;
    let selection;
    if (selectionContext?.uri && selectionContext?.start && selectionContext?.end) {
      document = await vscode.workspace.openTextDocument(vscode.Uri.parse(selectionContext.uri));
      selection = new vscode.Selection(
        selectionContext.start.line, selectionContext.start.character,
        selectionContext.end.line, selectionContext.end.character,
      );
    } else {
      const editor = vscode.window.activeTextEditor;
      document = editor?.document;
      selection = editor?.selection;
    }
    if (!document || !selection || selection.isEmpty) {
      vscode.window.showInformationMessage('请先在编辑器中选中要加入 Codebot 对话的代码。');
      return;
    }
    const folder = vscode.workspace.getWorkspaceFolder(document.uri);
    const filePath = folder ? path.relative(folder.uri.fsPath, document.uri.fsPath) : document.uri.fsPath;
    await this.addContext({
      id: crypto.randomUUID(),
      kind: 'editor',
      label: `${filePath}:${selection.start.line + 1}-${selection.end.line + 1}`,
      filePath,
      language: document.languageId,
      startLine: selection.start.line + 1,
      endLine: selection.end.line + 1,
      content: document.getText(selection),
    });
  }

  async loadModelsForTarget(target) {
    const normalized = this.normalizeTarget(target);
    const endpoint = normalized.startsWith('codex') ? '/api/codex/models' : '/api/chat/models';
    try {
      const response = await this.client.request('GET', endpoint);
      const models = Array.isArray(response?.data) ? response.data : response?.data?.models || [];
      this.post({ type: 'models', models, target: normalized });
    } catch (error) {
      this.post({ type: 'models', models: [], target: normalized, error: error.message });
    }
  }

  async addContext(item) {
    if (this.view) this.post({ type: 'contextAdded', item });
    else this.pendingContexts.push(item);
    await vscode.commands.executeCommand('codebot.chatView.focus');
  }

  async addTerminalSelection() {
    if (!vscode.window.activeTerminal) {
      vscode.window.showInformationMessage('请先打开终端并选中要加入对话的输出。');
      return;
    }
    const previousClipboard = await vscode.env.clipboard.readText();
    const sentinel = `__CODEBOT_NO_TERMINAL_SELECTION_${crypto.randomUUID()}__`;
    try {
      await vscode.env.clipboard.writeText(sentinel);
      await vscode.commands.executeCommand('workbench.action.terminal.copySelection');
      await new Promise((resolve) => setTimeout(resolve, 80));
      const content = await vscode.env.clipboard.readText();
      if (!content.trim() || content === sentinel) {
        vscode.window.showInformationMessage('没有读取到终端选区，请先选中终端输出。');
        return;
      }
      await this.addContext({
        id: crypto.randomUUID(), kind: 'terminal', label: '终端选区', content,
      });
    } finally {
      await vscode.env.clipboard.writeText(previousClipboard);
    }
  }

  async attachFiles() {
    const uris = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectMany: true,
      canSelectFolders: false,
      defaultUri: this.workspaceFolder()?.uri,
      openLabel: '加入 Codebot 对话',
    });
    if (!uris?.length) return;
    await this.addResourceFiles(uris);
  }

  /** 资源管理器右键菜单与原生文件选择器共用的可靠文件入口。 */
  async addResourceFiles(uris) {
    const candidates = (Array.isArray(uris) ? uris : [uris]).filter((uri) => uri instanceof vscode.Uri).slice(0, 10);
    if (!candidates.length) {
      vscode.window.showInformationMessage('没有可加入 Codebot 对话的文件。');
      return;
    }
    let addedCount = 0;
    for (const uri of candidates) {
      const stat = await vscode.workspace.fs.stat(uri);
      if ((stat.type & vscode.FileType.File) === 0) continue;
      await this.addUriFile(uri);
      addedCount += 1;
    }
    if (addedCount === 0) throw new Error('请选择文件；暂不支持把文件夹直接加入对话。');
    await vscode.commands.executeCommand('codebot.chatView.focus');
  }

  async addUriFile(uri, mimeType = '') {
    const bytes = await vscode.workspace.fs.readFile(uri);
    if (bytes.byteLength > 2 * 1024 * 1024) throw new Error(`文件超过 2 MB，未加入对话：${uri.fsPath}`);
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    const relativePath = folder ? path.relative(folder.uri.fsPath, uri.fsPath) : uri.fsPath;
    const name = path.basename(uri.fsPath);
    const text = isTextFile(name, mimeType);
    await this.addContext({
      id: crypto.randomUUID(), kind: 'file', label: relativePath, filePath: relativePath,
      name, mimeType: mimeType || 'application/octet-stream', isText: text,
      content: text ? Buffer.from(bytes).toString('utf8') : Buffer.from(bytes).toString('base64'),
    });
  }

  buildMessage(rawMessage, contexts) {
    const blocks = [];
    for (const item of contexts) {
      if (item.kind === 'editor') {
        blocks.push(`<editor_context file="${item.filePath}" lines="${item.startLine}-${item.endLine}" language="${item.language || ''}">\n${item.content}\n</editor_context>`);
      } else if (item.kind === 'terminal') {
        blocks.push(`<terminal_output untrusted="true">\n${item.content}\n</terminal_output>`);
      } else if (item.kind === 'file') {
        blocks.push(`【指定修改文件：${item.filePath || item.name}】`);
      }
    }
    return blocks.length ? `${rawMessage}\n\n${blocks.join('\n\n')}` : rawMessage;
  }

  buildAttachments(contexts) {
    return contexts.filter((item) => item.kind === 'file').map((item) => ({
      name: item.filePath || item.name,
      type: item.mimeType || 'application/octet-stream',
      content: item.content || '',
      is_text: Boolean(item.isText),
    }));
  }

  async send(payload) {
    if (this.running) throw new Error('当前回复尚未完成；可以点击“停止”后再发送。');
    const rawMessage = String(payload.message || '').trim();
    const contexts = Array.isArray(payload.contexts) ? payload.contexts : [];
    if (!rawMessage && contexts.length === 0) return;
    if (!this.conversationId) await this.newConversation();
    await this.ensureConversationWorkspace();

    // 扩展宿主重启或 Webview 重新加载后，本地 running 会丢失；发送前必须向后端
    // 查询真实运行态，避免同一句话在旧任务仍执行时被再次排队，形成重复用户消息。
    const runtimeResponse = await this.client.request('GET', `/api/chat/queue_status/${this.conversationId}?since_seq=0`);
    if (runtimeResponse?.data?.running) {
      this.runtimeLastSeq = 0;
      await this.syncRuntime();
      throw new Error('该对话仍在后台处理，已恢复运行状态；请等待或点击“停止”。');
    }
    await this.saveConversationPreferences(payload);

    const modelMessage = this.buildMessage(rawMessage || '请处理我附加的上下文。', contexts);
    const attachments = this.buildAttachments(contexts);
    await this.client.request('POST', `/api/chat/conversations/${this.conversationId}/messages`, { content: modelMessage });
    this.stopRuntimePolling();
    this.runtimeLastSeq = 0;
    this.running = true;
    const startedAt = Date.now();
    let lastVisibleEventAt = startedAt;
    const idleTimer = setInterval(() => {
      const silentSeconds = Math.floor((Date.now() - lastVisibleEventAt) / 1000);
      if (silentSeconds >= 8) {
        this.post({ type: 'streamIdle', elapsedSeconds: Math.floor((Date.now() - startedAt) / 1000), silentSeconds });
      }
    }, 4000);
    this.post({ type: 'streamStart', user: rawMessage || '请处理附加内容', contexts: contexts.map((item) => ({ kind: item.kind, label: item.label })) });
    try {
      await this.client.stream('/api/chat/send_stream', {
        conversation_id: this.conversationId,
        message: modelMessage,
        model: payload.model || null,
        reasoning_effort: String(payload.target || '').startsWith('codex') ? payload.reasoningEffort || null : null,
        mode: ['build', 'plan', 'agent', 'editor'].includes(payload.mode) ? payload.mode : 'editor',
        project_dir: this.workspacePath() || null,
        target: payload.target || 'codebot',
        knowledge_paths: Array.isArray(payload.knowledgePaths) ? payload.knowledgePaths : [],
        attached_files: attachments,
        user_already_saved: true,
      }, (event) => {
        lastVisibleEventAt = Date.now();
        this.post({ type: 'streamEvent', event });
      });
      await this.loadMessages();
      await this.refreshConversations();
    } finally {
      clearInterval(idleTimer);
      this.running = false;
      this.post({ type: 'streamFinished' });
    }
  }

  html(webview) {
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', 'main.js'));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', 'style.css'));
    const token = nonce();
    return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${token}';">
<meta name="viewport" content="width=device-width,initial-scale=1.0"><link rel="stylesheet" href="${styleUri}"></head>
<body><div id="app">
  <header class="header">
    <div class="history-row"><select id="conversation" aria-label="历史对话"><option value="">新对话</option></select><button id="copyConversation" title="复制整个对话">⧉</button><button id="new" title="新建对话">＋</button><button id="refresh" title="刷新">↻</button></div>
    <div id="status" class="status">正在初始化…</div>
  </header>
  <main id="messages" class="messages"><div class="empty">选择历史对话继续任务，或从下方开始新任务。</div></main>
  <section id="composer" class="composer">
    <div id="contextChips" class="chips"></div>
    <div id="suggestions" class="suggestions hidden"></div>
    <textarea id="input" rows="3" placeholder="输入消息…  / 命令 · @ Skill · # 知识库（Enter 发送，Ctrl+Enter 换行）"></textarea>
    <div class="composer-actions"><div class="settings-inline"><select id="mode" aria-label="模式"><option value="editor">Editor</option><option value="build">Build</option><option value="plan">Plan</option><option value="agent">Agent</option></select><select id="target" aria-label="执行目标"><option value="codebot">Codebot</option><option value="codex">Codex</option><option value="obsidian">Obsidian</option><option value="codex_obsidian">Codex + Obsidian</option></select><select id="model" aria-label="模型"><option value="">默认模型</option></select><select id="effort" aria-label="Codex 推理强度" class="hidden"><option value="">默认推理</option></select><button id="terminal" title="稳定 API 无法监听终端选区松开，请选中输出后点击此处">终端选区</button><button id="files" title="选择文件；也可拖到下方原生拖放区或使用资源管理器右键菜单">文件</button></div><button id="send" class="primary">发送</button></div>
  </section>
</div><script nonce="${token}" src="${scriptUri}"></script></body></html>`;
  }
}

function activate(context) {
  const provider = new CodebotChatViewProvider(context);
  const selectionCodeLensProvider = new EditorSelectionCodeLensProvider();
  const fileDropProvider = new CodebotFileDropTreeProvider(provider);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('codebot.chatView', provider, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.window.createTreeView('codebot.fileDropView', {
      treeDataProvider: fileDropProvider,
      dragAndDropController: fileDropProvider,
      showCollapseAll: false,
      canSelectMany: false,
    }),
    vscode.languages.registerCodeLensProvider([{ scheme: 'file' }, { scheme: 'untitled' }], selectionCodeLensProvider),
    vscode.window.onDidChangeTextEditorSelection((event) => selectionCodeLensProvider.schedule(event)),
    vscode.window.onDidChangeActiveTextEditor(() => selectionCodeLensProvider.clear()),
    selectionCodeLensProvider,
    vscode.commands.registerCommand('codebot.openChat', () => vscode.commands.executeCommand('codebot.chatView.focus')),
    vscode.commands.registerCommand('codebot.newConversation', async () => { await vscode.commands.executeCommand('codebot.chatView.focus'); await provider.newConversation(); }),
    vscode.commands.registerCommand('codebot.addEditorSelection', (selection) => provider.addEditorSelection(selection)),
    vscode.commands.registerCommand('codebot.addTerminalSelection', () => provider.addTerminalSelection()),
    vscode.commands.registerCommand('codebot.attachFiles', () => provider.attachFiles()),
    vscode.commands.registerCommand('codebot.addResourceFiles', (uri, selectedUris) => provider.addResourceFiles(selectedUris?.length ? selectedUris : uri)),
  );
}

function deactivate() {}
module.exports = { activate, deactivate, CodebotClient, CodebotChatViewProvider, EditorSelectionCodeLensProvider, CodebotFileDropTreeProvider };
