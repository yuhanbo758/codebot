const { contextBridge, ipcRenderer } = require('electron');

function toPlainValue(value) {
  if (value == null) return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_) {
    return value;
  }
}

contextBridge.exposeInMainWorld('electronAPI', {
  // 应用控制
  quit: () => ipcRenderer.send('app-quit'),
  
  // 对话管理
  newConversation: () => ipcRenderer.send('new-conversation'),
  onNewConversation: (callback) => ipcRenderer.on('new-conversation', callback),
  
  // 外部链接
  openExternal: (url) => ipcRenderer.invoke('open-external', url),

  // 内置浏览器：在 Electron 内部新窗口中打开链接
  openBuiltin: (url) => ipcRenderer.invoke('open-builtin', url),

  // 通知主进程链接打开模式（'system' 或 'builtin'），让主进程的拦截器知道如何处理
  setLinkOpenMode: (mode) => ipcRenderer.invoke('set-link-open-mode', mode),
  
  // 选择文件夹（用于项目目录选择等）
  selectFolder: (options) => ipcRenderer.invoke('dialog:selectFolder', toPlainValue(options)),

  // 系统信息
  getPlatform: () => require('process').platform,
  getVersion: () => ipcRenderer.invoke('get-version'),
  copyText: (text) => ipcRenderer.invoke('clipboard-copy', text),

  // Account / membership
  accountLogin: (credentials) => ipcRenderer.invoke('account:login', toPlainValue(credentials)),
  accountLogout: () => ipcRenderer.invoke('account:logout'),
  accountMe: () => ipcRenderer.invoke('account:me'),
  accountAccess: () => ipcRenderer.invoke('account:access'),
  openMemberCenter: () => ipcRenderer.invoke('shop:open-member-center'),
  openCodebotStore: () => ipcRenderer.invoke('shop:open-codebot-store'),
  openSkillsFolder: () => ipcRenderer.invoke('skills:open-folder'),
  onAccountChanged: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('account:changed', listener)
    return () => ipcRenderer.removeListener('account:changed', listener)
  },
  onSkillDownload: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('skills:download', listener)
    return () => ipcRenderer.removeListener('skills:download', listener)
  },

  // Updates
  checkUpdate: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onUpdateStatus: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('update:status', listener);
    return () => ipcRenderer.removeListener('update:status', listener);
  }
});
