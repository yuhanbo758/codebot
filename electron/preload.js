const { contextBridge, ipcRenderer } = require('electron');

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
  
  // 系统信息
  getPlatform: () => require('process').platform,
  getVersion: () => ipcRenderer.invoke('get-version'),
  copyText: (text) => ipcRenderer.invoke('clipboard-copy', text)
});

