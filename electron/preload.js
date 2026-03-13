const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // 应用控制
  quit: () => ipcRenderer.send('app-quit'),
  
  // 对话管理
  newConversation: () => ipcRenderer.send('new-conversation'),
  onNewConversation: (callback) => ipcRenderer.on('new-conversation', callback),
  
  // 系统信息
  getPlatform: () => require('process').platform,
  getVersion: () => ipcRenderer.invoke('get-version'),
  copyText: (text) => ipcRenderer.invoke('clipboard-copy', text)
});
