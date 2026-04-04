const { app, BrowserWindow, Menu, dialog, ipcMain, clipboard, shell, session } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');
const http = require('http');
const fs = require('fs');

let mainWindow;
let backendProcess;
// 链接打开模式：'system'（默认，使用系统浏览器）或 'builtin'（应用内置浏览器窗口）
let linkOpenMode = 'system';

app.commandLine.appendSwitch('disable-http-cache');

ipcMain.handle('clipboard-copy', (_event, text) => {
  if (typeof text !== 'string') {
    return false;
  }
  clipboard.writeText(text);
  return true;
});

// 用系统默认浏览器打开外部链接
ipcMain.handle('open-external', async (_event, url) => {
  if (typeof url !== 'string') return false;
  try {
    await shell.openExternal(url);
    return true;
  } catch {
    return false;
  }
});

// 设置链接打开模式（由前端在设置变更时调用）
ipcMain.handle('set-link-open-mode', (_event, mode) => {
  if (mode === 'builtin' || mode === 'system') {
    linkOpenMode = mode;
  }
  return linkOpenMode;
});

// 在 Electron 内置浏览器窗口中打开链接
ipcMain.handle('open-builtin', (_event, url) => {
  if (typeof url !== 'string') return false;
  try {
    openBuiltinBrowserWindow(url);
    return true;
  } catch {
    return false;
  }
});

// 创建内置浏览器窗口的辅助函数
// 使用 persist:builtin-browser 命名持久化 session，确保 Cookie 跨窗口关闭后保留
function openBuiltinBrowserWindow(url) {
  const builtinSession = session.fromPartition('persist:builtin-browser');
  const browserWin = new BrowserWindow({
    width: 1100,
    height: 750,
    minWidth: 640,
    minHeight: 480,
    title: url,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      session: builtinSession,
    },
  });
  browserWin.loadURL(url);
  // 内置浏览器窗口内部的新窗口也在同一内置浏览器中打开（同一 session，复用 cookie）
  browserWin.webContents.setWindowOpenHandler(({ url: newUrl }) => {
    openBuiltinBrowserWindow(newUrl);
    return { action: 'deny' };
  });
  // 同步标题
  browserWin.webContents.on('page-title-updated', (_e, title) => {
    browserWin.setTitle(title || url);
  });
  return browserWin;
}

// 获取局域网 IP
function getLocalIP() {
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        return iface.address;
      }
    }
  }
  return '127.0.0.1';
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 768,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
    icon: path.join(__dirname, '..', 'logo.ico'),
  });

  startBackend()
    .then(() => waitForBackendReady())
    .then(async () => {
      const localIP = getLocalIP();
      const url = 'http://127.0.0.1:8080';
      await mainWindow.webContents.session.clearCache();
      await mainWindow.loadURL(url);
      
      console.log(`
╔════════════════════════════════════════╗
║        Codebot 已启动！                ║
╠════════════════════════════════════════╣
║  本地访问：http://127.0.0.1:8080      ║
║  局域网访问：http://${localIP}:8080    ║
║  移动端：使用手机浏览器访问局域网地址  ║
╚════════════════════════════════════════╝
      `);
    })
    .catch((error) => {
      dialog.showErrorBox('服务启动失败', error.message || '无法连接到后端服务');
    });

  createMenu();

  // 拦截窗口内导航：根据用户设置选择在内置窗口或系统浏览器中打开外部链接
  mainWindow.webContents.on('will-navigate', (event, url) => {
    // 允许导航回本地应用的地址
    if (url.startsWith('http://127.0.0.1:8080') || url.startsWith('http://localhost:8080')) {
      return;
    }
    event.preventDefault();
    if (linkOpenMode === 'builtin') {
      openBuiltinBrowserWindow(url);
    } else {
      shell.openExternal(url);
    }
  });

  // 拦截 window.open() 和 target="_blank" 链接
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (linkOpenMode === 'builtin') {
      openBuiltinBrowserWindow(url);
    } else {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function createMenu() {
  Menu.setApplicationMenu(null);
}

async function startBackend() {
  const isDev = !app.isPackaged;
  try {
    const payload = await checkHealth();
    const opencodeConnected = Boolean(payload?.opencode_connected);
    const runtimeSource = payload?.runtime_source;
    if (opencodeConnected) {
      if (!isDev) {
        console.log('[backend] 已检测到运行中的服务且 OpenCode 已连接，跳过启动');
        return;
      }
      if (runtimeSource === 'source') {
        console.log('[backend] 开发模式检测到源码后端且 OpenCode 已连接，跳过启动');
        return;
      }
    }
    if (payload?.pid) {
      try {
        process.kill(payload.pid);
        console.log(`[backend] 已终止已有后端进程（准备重启并拉起 OpenCode） pid=${payload.pid}`);
      } catch (e) {
        console.log(`[backend] 无法终止已有后端 pid=${payload.pid}: ${e.message}`);
        if (opencodeConnected) {
          return;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 600));
    } else if (payload?.status === 'healthy' && opencodeConnected) {
      return;
    }
  } catch (_) {}

  const repoRoot = path.join(__dirname, '..');
  const packagedBackend = path.join(process.resourcesPath || '', 'backend', 'codebot-backend', 'codebot-backend.exe');
  const devBackend = path.join(repoRoot, 'backend', 'dist', 'codebot-backend', 'codebot-backend.exe');
  const frontendDist = app.isPackaged
    ? path.join(process.resourcesPath || '', 'frontend-dist')
    : path.join(repoRoot, 'frontend', 'dist');

  // userData = %APPDATA%\codebot，始终可写
  const userDataDir = app.getPath('userData');

  // 日志文件路径：写到 userData 目录，方便排查
  const logDir = path.join(userDataDir, 'logs');
  try { fs.mkdirSync(logDir, { recursive: true }); } catch (_) {}
  const logFile = path.join(logDir, 'backend.log');
  const logStream = fs.createWriteStream(logFile, { flags: 'a', encoding: 'utf8' });

  const resourcesDir = process.resourcesPath || path.join(repoRoot, 'electron', 'resources');
  const opencodeCandidates = [
    path.join(process.resourcesPath || '', 'opencode', 'opencode.exe'),
    path.join(process.resourcesPath || '', 'opencode', 'opencode'),
    path.join(repoRoot, 'electron', 'vendor', 'opencode', 'opencode.exe'),
    path.join(repoRoot, 'electron', 'vendor', 'opencode', 'opencode'),
    path.join(repoRoot, 'vendor', 'opencode', 'opencode.exe'),
    path.join(repoRoot, 'vendor', 'opencode', 'opencode'),
  ];
  const opencodePath = opencodeCandidates.find((p) => p && fs.existsSync(p)) || '';

  const env = {
    ...process.env,
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    CODEBOT_FRONTEND_DIST: frontendDist,
    CODEBOT_DATA_DIR: userDataDir,
    CODEBOT_RESOURCES_DIR: resourcesDir,
    CODEBOT_OPENCODE_PATH: opencodePath,
    CODEBOT_FORCE_OPENCODE_AUTOSTART: '1',
    CODEBOT_OPENCODE_PREFERRED_PORT: '1120',
    CODEBOT_OPENCODE_FALLBACK_PORT: '4096',
  };
  // 清除可能污染 PyInstaller 运行时的 Python 环境变量
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;

  // 确定要执行的命令和参数
  let cmd, args, cwd;

  // 开发模式（npm start / electron . ）始终使用 Python 源码，确保加载最新修改

  if (isDev) {
    const venvPython = path.join(repoRoot, 'venv', 'Scripts', 'python.exe');
    const { spawnSync } = require('child_process');
    const venvOk = fs.existsSync(venvPython) &&
      spawnSync(venvPython, ['-c', 'import fastapi'], { encoding: 'utf8' }).status === 0;
    cmd = venvOk ? venvPython : 'python';
    args = [path.join(repoRoot, 'backend', 'main.py')];
    cwd = repoRoot;
  } else if (fs.existsSync(packagedBackend)) {
    cmd = packagedBackend;
    args = [];
    cwd = path.dirname(packagedBackend);
  } else if (fs.existsSync(devBackend)) {
    cmd = devBackend;
    args = [];
    cwd = path.dirname(devBackend);
  } else {
    dialog.showErrorBox('后端缺失', `未找到后端可执行文件：\n${packagedBackend}`);
    throw new Error('后端可执行文件不存在');
  }

  console.log(`[backend] 启动: ${cmd}`);
  console.log(`[backend] cwd: ${cwd}`);
  console.log(`[backend] CODEBOT_DATA_DIR: ${userDataDir}`);
  console.log(`[backend] CODEBOT_FRONTEND_DIST: ${frontendDist}`);

  // 使用 pipe 将后端输出写入日志文件，避免 inherit 在无控制台时挂起
  // 当 cmd 是裸命令名（非绝对路径）时，Windows 需要 shell:true 才能通过 PATH 解析
  const useShell = process.platform === 'win32' && !path.isAbsolute(cmd);
  backendProcess = spawn(cmd, args, {
    cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    env,
    detached: false,
    shell: useShell,
  });

  backendProcess.stdout.pipe(logStream);
  backendProcess.stderr.pipe(logStream);

  backendProcess.on('error', (err) => {
    const msg = `[backend] 启动失败: ${err.message}\n`;
    console.error(msg);
    logStream.write(msg);
  });

  backendProcess.on('exit', async (code, signal) => {
    if (!isDev && code === 1 && signal === null) {
      try {
        await checkHealth();
        const runningMsg = `[backend] 新进程退出，但已有后端正在监听 8080，继续使用现有实例\n`;
        console.log(runningMsg);
        logStream.write(runningMsg);
        return;
      } catch (_) {}
    }
    const msg = `[backend] 退出，code=${code} signal=${signal}\n`;
    console.log(msg);
    logStream.write(msg);
  });
}

function checkHealth() {
  return new Promise((resolve, reject) => {
    const req = http.get('http://127.0.0.1:8080/api/health', (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        if (res.statusCode !== 200) {
          reject(new Error(`健康检查失败: ${res.statusCode}`));
          return;
        }
        try {
          const payload = JSON.parse(data);
          if (payload.status === 'healthy') {
            resolve(payload);
          } else {
            reject(new Error('后端未就绪'));
          }
        } catch (err) {
          reject(new Error('健康检查响应解析失败'));
        }
      });
    });
    req.on('error', (err) => reject(err));
    req.setTimeout(1000, () => {
      req.destroy(new Error('健康检查超时'));
    });
  });
}

async function waitForBackendReady() {
  const maxRetries = 60;
  for (let i = 0; i < maxRetries; i += 1) {
    try {
      await checkHealth();
      return true;
    } catch (err) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  throw new Error('后端启动超时，请确认后端可执行文件与 OpenCode 环境可用');
}

function showAbout() {
  const { dialog } = require('electron');
  dialog.showMessageBox({
    title: '关于 Codebot',
    message: 'Codebot v2.1.0',
    detail: '基于 OpenCode 的个人 AI 助手\n\n© 2024'
  });
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (backendProcess) {
    backendProcess.kill();
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('quit', () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
