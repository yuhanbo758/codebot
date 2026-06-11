const { app, BrowserWindow, Menu, dialog, ipcMain, clipboard, shell, session, safeStorage } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');
const http = require('http');
const https = require('https');
const fs = require('fs');
let autoUpdater = null;
try {
  autoUpdater = require('electron-updater').autoUpdater;
} catch (error) {
  console.warn(`[update] electron-updater not available: ${error.message || error}`);
}

const isDevRuntime = !app.isPackaged;
const backendPort = process.env.CODEBOT_BACKEND_PORT || (isDevRuntime ? '18080' : '15682');
const opencodePort = process.env.CODEBOT_OPENCODE_PREFERRED_PORT || (isDevRuntime ? '11203' : '11200');
const BACKEND_URL = `http://127.0.0.1:${backendPort}`;
const FRONTEND_DEV_URL = 'http://127.0.0.1:3000';
const SHOP_BASE_URL = process.env.CODEBOT_SHOP_BASE_URL || 'https://shop.sanrenjz.com';
const SHOP_MEMBER_CENTER_URL = `${SHOP_BASE_URL}/member-center`;
const SHOP_CODEBOT_STORE_URL = `${SHOP_BASE_URL}/codebot`;
const SHOP_DOWNLOAD_HOSTNAME = 'xz.sanrenjz.com';
const CODEBOT_UPDATE_GENERIC_URL = process.env.CODEBOT_UPDATE_GENERIC_URL || 'https://xz.sanrenjz.com/Download/codebot/';
const CODEBOT_GITHUB_OWNER = 'yuhanbo758';
const CODEBOT_GITHUB_REPO = 'codebot';
const CODEBOT_GITHUB_RELEASES_API = `https://api.github.com/repos/${CODEBOT_GITHUB_OWNER}/${CODEBOT_GITHUB_REPO}/releases`;
const ACCOUNT_TOKEN_FILE = 'account-token.bin';
const BUILTIN_BROWSER_PARTITION = 'persist:builtin-browser';
const SHOP_HOSTNAME = new URL(SHOP_BASE_URL).hostname;
let cachedAccount = null;
let lastUpdateSource = 'github';
let builtinSessionEventsBound = false;
let pendingManualUpdate = null;
let lastCheckedUpdateInfo = null;

function toSerializable(value) {
  if (value == null) return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_) {
    if (value instanceof Error) {
      return {
        message: value.message || String(value),
        stack: value.stack || '',
      };
    }
    return { message: String(value) };
  }
}

function getBuiltinSession() {
  return session.fromPartition(BUILTIN_BROWSER_PARTITION);
}

function sendRendererEvent(channel, payload = {}) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, toSerializable(payload));
  }
}

function sendAccountChanged(payload = {}) {
  sendRendererEvent('account:changed', payload);
}

function sendSkillDownloadEvent(payload = {}) {
  sendRendererEvent('skills:download', payload);
}

function isShopDomain(domain) {
  const normalized = String(domain || '').replace(/^\./, '').toLowerCase();
  return normalized === SHOP_HOSTNAME || normalized.endsWith(`.${SHOP_HOSTNAME}`);
}

function isShopUrl(rawUrl) {
  try {
    return isShopDomain(new URL(rawUrl).hostname);
  } catch (_) {
    return false;
  }
}

function isSkillDownloadUrl(rawUrl) {
  try {
    const hostname = new URL(rawUrl).hostname.toLowerCase();
    return isShopDomain(hostname) || hostname === SHOP_DOWNLOAD_HOSTNAME;
  } catch (_) {
    return false;
  }
}

function isSupportedSkillDownload(fileName) {
  return /\.(zip|md|markdown|txt)$/i.test(String(fileName || ''));
}

function codebotSkillsDir() {
  return path.join(app.getPath('userData'), 'skills');
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

function sanitizeFileName(fileName) {
  return String(fileName || 'download')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
    .replace(/\s+/g, ' ')
    .trim() || 'download';
}

function slugifyName(value, fallback = 'skill') {
  const normalized = String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

function uniquePath(filePath) {
  if (!fs.existsSync(filePath)) return filePath;
  const parsed = path.parse(filePath);
  let counter = 1;
  while (true) {
    const candidate = path.join(parsed.dir, `${parsed.name}_${counter}${parsed.ext}`);
    if (!fs.existsSync(candidate)) return candidate;
    counter += 1;
  }
}

function uniqueAutoSkillDir(baseName) {
  const skillsDir = ensureDir(codebotSkillsDir());
  const slugBase = `auto_${slugifyName(baseName, 'downloaded_skill')}`;
  let candidate = path.join(skillsDir, slugBase);
  let counter = 1;
  while (fs.existsSync(candidate)) {
    candidate = path.join(skillsDir, `${slugBase}_${counter}`);
    counter += 1;
  }
  return candidate;
}

function parseDownloadedSkillContent(content, fallbackName, slug) {
  const trimmed = String(content || '').trim();
  if (trimmed.startsWith('---')) {
    return trimmed.endsWith('\n') ? trimmed : `${trimmed}\n`;
  }
  const name = fallbackName || slug || '自动生成技能';
  return `---\nname: "${name.replace(/"/g, '\\"')}"\nslug: "${slug}"\ndescription: "从程序小店下载并自动安装"\nversion: "1.0.0"\nsource: auto_generated\ncompatibility:\n  - codebot\ncreated_at: "${new Date().toISOString()}"\n---\n\n# ${name}\n\n${trimmed || '从程序小店下载的技能内容。'}\n`;
}

function findFilesRecursive(rootDir, matcher) {
  const matches = [];
  const queue = [rootDir];
  while (queue.length) {
    const current = queue.shift();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.name === '.git' || entry.name === 'node_modules' || entry.name === '__pycache__') continue;
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        queue.push(entryPath);
      } else if (matcher(entryPath, entry.name)) {
        matches.push(entryPath);
      }
    }
  }
  return matches;
}

function removePath(targetPath) {
  try {
    fs.rmSync(targetPath, { recursive: true, force: true });
  } catch (_) {}
}

function copyDirectoryContents(sourceDir, targetDir) {
  fs.cpSync(sourceDir, targetDir, { recursive: true, force: true });
}

function extractArchive(archivePath, destinationDir) {
  ensureDir(destinationDir);
  return new Promise((resolve, reject) => {
    const isWindows = process.platform === 'win32';
    const command = isWindows ? 'powershell.exe' : 'tar';
    const args = isWindows
      ? ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', `Expand-Archive -LiteralPath '${archivePath.replace(/'/g, "''")}' -DestinationPath '${destinationDir.replace(/'/g, "''")}' -Force`]
      : ['-xf', archivePath, '-C', destinationDir];
    const child = spawn(command, args, { windowsHide: true });
    let stderr = '';
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', reject);
    child.on('exit', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(stderr.trim() || '解压技能包失败'));
      }
    });
  });
}

async function installDownloadedSkill(downloadPath) {
  const fileName = path.basename(downloadPath);
  const ext = path.extname(fileName).toLowerCase();
  const parsed = path.parse(fileName);
  const targetDir = uniqueAutoSkillDir(parsed.name || 'downloaded_skill');
  ensureDir(targetDir);

  if (ext === '.md' || ext === '.markdown' || ext === '.txt') {
    const content = fs.readFileSync(downloadPath, 'utf8');
    fs.writeFileSync(path.join(targetDir, 'SKILL.md'), parseDownloadedSkillContent(content, parsed.name, path.basename(targetDir)), 'utf8');
    removePath(downloadPath);
    return { slug: path.basename(targetDir), path: targetDir };
  }

  if (ext === '.zip') {
    const extractDir = uniquePath(path.join(ensureDir(path.join(codebotSkillsDir(), '.downloads')), `${parsed.name}_unzipped`));
    try {
      await extractArchive(downloadPath, extractDir);
      const skillMdFiles = findFilesRecursive(extractDir, (entryPath, entryName) => entryName.toLowerCase() === 'skill.md');
      if (skillMdFiles.length > 0) {
        copyDirectoryContents(path.dirname(skillMdFiles[0]), targetDir);
      } else {
        const markdownFiles = findFilesRecursive(extractDir, (_entryPath, entryName) => /\.(md|markdown|txt)$/i.test(entryName));
        if (markdownFiles.length === 0) {
          throw new Error('下载内容中没有找到可安装的 SKILL.md');
        }
        const content = fs.readFileSync(markdownFiles[0], 'utf8');
        fs.writeFileSync(path.join(targetDir, 'SKILL.md'), parseDownloadedSkillContent(content, parsed.name, path.basename(targetDir)), 'utf8');
      }
    } finally {
      removePath(extractDir);
      removePath(downloadPath);
    }
    return { slug: path.basename(targetDir), path: targetDir };
  }

  removePath(targetDir);
  throw new Error(`暂不支持自动安装 ${ext || '该类型'} 文件，请下载 SKILL.md 或 zip 技能包`);
}

async function readTokenFromWebContents(webContents) {
  if (!webContents || webContents.isDestroyed()) return '';
  try {
    const url = webContents.getURL();
    if (!isShopUrl(url)) return '';
    const token = await webContents.executeJavaScript(`(() => {
      try {
        return String(window.localStorage.getItem('token') || '');
      } catch (_) {
        return '';
      }
    })()`, true);
    return String(token || '').trim();
  } catch (_) {
    return '';
  }
}

async function syncAccountFromWebContents(webContents) {
  const token = await readTokenFromWebContents(webContents);
  if (token) {
    saveAccountToken(token);
  } else {
    clearAccountToken();
  }
  cachedAccount = await loadAccountSnapshot();
  sendAccountChanged(cachedAccount || { authenticated: false, canDownloadUpdates: false, user: null });
  return cachedAccount;
}

async function clearShopSession() {
  const builtinSession = getBuiltinSession();
  try {
    await builtinSession.clearStorageData({ origin: SHOP_BASE_URL, storages: ['localstorage'] });
  } catch (_) {}
  const cookies = await builtinSession.cookies.get({ url: SHOP_BASE_URL });
  for (const cookie of cookies) {
    const domain = String(cookie.domain || '').replace(/^\./, '');
    const protocol = cookie.secure ? 'https://' : 'http://';
    const cookieUrl = `${protocol}${domain}${cookie.path || '/'}`;
    try {
      await builtinSession.cookies.remove(cookieUrl, cookie.name);
    } catch (_) {}
  }
}

async function shopSessionRequest(apiPath, options = {}) {
  const builtinSession = getBuiltinSession();
  if (typeof builtinSession.fetch !== 'function') {
    throw new Error('当前 Electron 版本不支持共享浏览器会话请求');
  }
  const token = String(options.token || readAccountToken() || '').trim();
  if (!token) {
    throw new Error('未登录');
  }
  const url = `${SHOP_BASE_URL}${apiPath}`;
  const headers = {
    Accept: 'application/json, text/plain, */*',
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    Authorization: `Bearer ${token}`,
    ...(options.headers || {}),
  };
  const response = await builtinSession.fetch(url, {
    method: options.method || 'GET',
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
    credentials: 'include',
  });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; }
  if (!response.ok) {
    throw new Error(payload?.message || payload?.error || payload?.detail || `请求失败: ${response.status}`);
  }
  return payload;
}

async function loadAccountSnapshotFromSession() {
  try {
    const token = readAccountToken();
    if (!token) {
      return { authenticated: false, canDownloadUpdates: false };
    }
    const [me, access] = await Promise.all([
      shopSessionRequest('/api/users/electron/me?typeCategory=codebot', { token }),
      shopSessionRequest('/api/users/electron/access?typeCategory=codebot', { token }),
    ]);
    return {
      authenticated: true,
      user: toSerializable(me?.user || me?.data?.user || me?.data || me),
      canDownloadUpdates: isCodebotMember(access),
    };
  } catch (error) {
    return { authenticated: false, canDownloadUpdates: false, error: error.message || String(error) };
  }
}

function bindBuiltinSessionEvents() {
  if (builtinSessionEventsBound) return;
  builtinSessionEventsBound = true;
  const builtinSession = getBuiltinSession();

  builtinSession.on('will-download', (event, item, webContents) => {
    const sourceUrl = item.getURL() || webContents?.getURL() || '';
    const fileName = sanitizeFileName(item.getFilename());
    if (!isSkillDownloadUrl(sourceUrl) && !isSupportedSkillDownload(fileName)) return;
    const downloadsDir = ensureDir(path.join(codebotSkillsDir(), '.downloads'));
    const savePath = uniquePath(path.join(downloadsDir, fileName));
    item.setSavePath(savePath);
    sendSkillDownloadEvent({ type: 'started', fileName: path.basename(savePath) });
    item.on('done', async (_downloadEvent, state) => {
      if (state !== 'completed') {
        sendSkillDownloadEvent({ type: 'error', fileName: path.basename(savePath), message: `下载未完成：${state}` });
        return;
      }
      try {
        const installed = await installDownloadedSkill(savePath);
        sendSkillDownloadEvent({ type: 'completed', fileName: path.basename(savePath), installed });
      } catch (error) {
        sendSkillDownloadEvent({ type: 'error', fileName: path.basename(savePath), message: error.message || String(error) });
      }
    });
  });
}

function isInternalAppUrl(url) {
  return url.startsWith(BACKEND_URL)
    || url.startsWith(`http://localhost:${backendPort}`)
    || url.startsWith(FRONTEND_DEV_URL)
    || url.startsWith('http://localhost:3000');
}

// 显式隔离 Electron 的会话数据目录，避免开发态和打包态混用缓存数据。
const codebotProfileName = app.isPackaged ? 'codebot' : 'codebot-dev';
const codebotUserDataDir = path.join(app.getPath('appData'), codebotProfileName);
const codebotSessionDataDir = path.join(codebotUserDataDir, 'session');
fs.mkdirSync(codebotUserDataDir, { recursive: true });
fs.mkdirSync(codebotSessionDataDir, { recursive: true });
app.setPath('userData', codebotUserDataDir);
app.setPath('sessionData', codebotSessionDataDir);

let mainWindow;
let backendProcess;
// 链接打开模式：'system'（默认，使用系统浏览器）或 'builtin'（应用内置浏览器窗口）
let linkOpenMode = 'system';

app.commandLine.appendSwitch('disable-http-cache');
// 防止与其他 Electron 应用（如 OpenCode 桌面端）同时运行时
// Chromium 出现 "Network service crashed, restarting service" 错误
app.commandLine.appendSwitch('disable-gpu-sandbox');

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

// 选择文件夹对话框（用于项目目录选择等）
ipcMain.handle('dialog:selectFolder', async (_event, options) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: (options && options.title) || '选择文件夹',
    properties: ['openDirectory'],
    defaultPath: (options && options.defaultPath) || undefined,
  });
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

ipcMain.handle('get-version', () => app.getVersion());

ipcMain.handle('account:login', async (_event, credentials) => {
  openBuiltinBrowserWindow(SHOP_MEMBER_CENTER_URL);
  return { success: true, loginMode: 'browser', url: SHOP_MEMBER_CENTER_URL };
});

ipcMain.handle('account:logout', async () => {
  await clearShopSession();
  clearAccountToken();
  cachedAccount = null;
  sendAccountChanged({ authenticated: false, reason: 'logout' });
  return { authenticated: false };
});

ipcMain.handle('account:me', async () => {
  cachedAccount = await loadAccountSnapshot();
  return toSerializable(cachedAccount);
});

ipcMain.handle('account:access', async () => {
  return toSerializable(await fetchCodebotAccess());
});

ipcMain.handle('shop:open-member-center', async () => {
  openBuiltinBrowserWindow(SHOP_MEMBER_CENTER_URL);
  return { success: true };
});

ipcMain.handle('shop:open-codebot-store', async () => {
  openBuiltinBrowserWindow(SHOP_CODEBOT_STORE_URL);
  return { success: true };
});

ipcMain.handle('skills:open-folder', async () => {
  const folder = ensureDir(codebotSkillsDir());
  const error = await shell.openPath(folder);
  if (error) throw new Error(error);
  return { success: true, path: folder };
});

ipcMain.handle('update:check', async () => {
  return checkForCodebotUpdates();
});

ipcMain.handle('update:download', async () => {
  if (!autoUpdater) throw new Error('自动更新模块不可用');
  if (lastUpdateSource === 'github') {
    const version = String(lastCheckedUpdateInfo?.version || '').trim();
    if (!version) {
      throw new Error('尚未获得可下载的新版本，请先检查更新');
    }
    return downloadGithubReleaseInstaller(version);
  }
  pendingManualUpdate = null;
  await autoUpdater.downloadUpdate();
  return { success: true, mode: 'electron-updater' };
});

ipcMain.handle('update:install', async () => {
  if (pendingManualUpdate?.installerPath) {
    const installerPath = pendingManualUpdate.installerPath;
    if (!fs.existsSync(installerPath)) {
      pendingManualUpdate = null;
      throw new Error('已下载的安装包不存在，请重新下载更新');
    }

    // GitHub 公共更新走手动下载安装器流程，启动安装程序后退出当前应用，
    // 避免继续依赖 electron-updater 对错误资产名的自动解析。
    const child = spawn(installerPath, [], {
      detached: true,
      stdio: 'ignore',
      windowsHide: false,
    });
    child.unref();
    pendingManualUpdate = null;
    app.quit();
    return { success: true, mode: 'manual-github' };
  }

  if (!autoUpdater) throw new Error('自动更新模块不可用');
  autoUpdater.quitAndInstall(false, true);
  return { success: true };
});

// 创建内置浏览器窗口的辅助函数
// 使用 persist:builtin-browser 命名持久化 session，确保 Cookie 跨窗口关闭后保留
function openBuiltinBrowserWindow(url) {
  const builtinSession = getBuiltinSession();
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
  const syncAccountState = () => {
    syncAccountFromWebContents(browserWin.webContents).catch(() => {});
  };
  browserWin.webContents.on('did-finish-load', syncAccountState);
  browserWin.webContents.on('did-navigate-in-page', syncAccountState);
  browserWin.webContents.on('did-navigate', syncAccountState);
  browserWin.webContents.on('will-navigate', (event, targetUrl) => {
    if (!isSkillDownloadUrl(targetUrl)) return;
    event.preventDefault();
    browserWin.webContents.downloadURL(targetUrl);
  });
  // 内置浏览器窗口内部的新窗口也在同一内置浏览器中打开（同一 session，复用 cookie）
  browserWin.webContents.setWindowOpenHandler(({ url: newUrl }) => {
    if (isSkillDownloadUrl(newUrl)) {
      browserWin.webContents.downloadURL(newUrl);
      return { action: 'deny' };
    }
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

function tokenPath() {
  return path.join(app.getPath('userData'), ACCOUNT_TOKEN_FILE);
}

function saveAccountToken(token) {
  const file = tokenPath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const buffer = Buffer.from(String(token), 'utf8');
  const encrypted = safeStorage && safeStorage.isEncryptionAvailable()
    ? safeStorage.encryptString(String(token))
    : buffer;
  fs.writeFileSync(file, encrypted);
}

function readAccountToken() {
  const file = tokenPath();
  if (!fs.existsSync(file)) return '';
  const data = fs.readFileSync(file);
  try {
    if (safeStorage && safeStorage.isEncryptionAvailable()) {
      return safeStorage.decryptString(data);
    }
  } catch (_) {}
  return data.toString('utf8');
}

function clearAccountToken() {
  try { fs.unlinkSync(tokenPath()); } catch (_) {}
}

async function shopRequest(apiPath, options = {}) {
  const url = `${SHOP_BASE_URL}${apiPath}`;
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };
  const token = options.token || '';
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const fetchImpl = global.fetch;
  if (typeof fetchImpl !== 'function') {
    throw new Error('当前 Electron 运行时不支持 fetch');
  }
  const response = await fetchImpl(url, {
    method: options.method || 'GET',
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; }
  if (!response.ok) {
    throw new Error(payload?.message || payload?.error || payload?.detail || `请求失败: ${response.status}`);
  }
  return payload;
}

function isCodebotMember(accessPayload) {
  const payload = accessPayload?.data || accessPayload || {};
  if (payload.canDownloadUpdates === true) return true;
  if (payload.entitlements?.canDownloadUpdates === true) return true;
  const memberships = payload.entitlements?.activeMemberships || payload.activeMemberships || [];
  return memberships.some((item) => {
    const key = item?.planKey || item?.typeCategory || item?.category || item;
    return key === 'codebot' || key === 'all';
  });
}

async function fetchCodebotAccess() {
  const sessionAccess = await loadAccountSnapshotFromSession();
  if (sessionAccess.authenticated) {
    return {
      authenticated: true,
      canDownloadUpdates: Boolean(sessionAccess.canDownloadUpdates),
    };
  }
  const token = readAccountToken();
  if (!token) return { authenticated: false, canDownloadUpdates: false };
  try {
    const payload = await shopRequest('/api/users/electron/access?typeCategory=codebot', { token });
    return {
      authenticated: true,
      canDownloadUpdates: isCodebotMember(payload),
    };
  } catch (error) {
    return { authenticated: false, canDownloadUpdates: false, error: error.message || String(error) };
  }
}

async function loadAccountSnapshot() {
  const sessionSnapshot = await loadAccountSnapshotFromSession();
  if (sessionSnapshot.authenticated) {
    return sessionSnapshot;
  }
  const token = readAccountToken();
  if (!token) return { authenticated: false, canDownloadUpdates: false };
  try {
    const [me, access] = await Promise.all([
      shopRequest('/api/users/electron/me?typeCategory=codebot', { token }),
      fetchCodebotAccess(),
    ]);
    return {
      authenticated: true,
      user: toSerializable(me?.user || me?.data?.user || me?.data || me),
      canDownloadUpdates: Boolean(access.canDownloadUpdates),
    };
  } catch (error) {
    return { authenticated: false, canDownloadUpdates: false, error: error.message || String(error) };
  }
}

function sendUpdateEvent(type, payload = {}) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('update:status', toSerializable({ type, source: lastUpdateSource, ...payload }));
  }
}

function normalizeAssetName(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '');
}

function parseVersionParts(version) {
  return String(version || '')
    .trim()
    .replace(/^v/i, '')
    .split('.')
    .map((item) => Number.parseInt(item, 10))
    .map((item) => (Number.isFinite(item) ? item : 0));
}

function isVersionGreater(nextVersion, currentVersion) {
  const next = parseVersionParts(nextVersion);
  const current = parseVersionParts(currentVersion);
  const maxLength = Math.max(next.length, current.length, 3);
  for (let index = 0; index < maxLength; index += 1) {
    const left = next[index] || 0;
    const right = current[index] || 0;
    if (left > right) return true;
    if (left < right) return false;
  }
  return false;
}

async function fetchLatestGithubRelease() {
  const fetchImpl = global.fetch;
  if (typeof fetchImpl !== 'function') {
    throw new Error('当前 Electron 运行时不支持 GitHub Release 查询');
  }
  const response = await fetchImpl(`${CODEBOT_GITHUB_RELEASES_API}/latest`, {
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'Codebot-Updater',
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload?.message || `查询 GitHub 最新 Release 失败: ${response.status}`);
  }
  return payload;
}

async function fetchGithubReleaseByVersion(version) {
  const fetchImpl = global.fetch;
  if (typeof fetchImpl !== 'function') {
    throw new Error('当前 Electron 运行时不支持 GitHub Release 查询');
  }

  const tag = String(version || '').trim().replace(/^v/i, '');
  if (!tag) {
    throw new Error('缺少可下载的版本号，请先重新检查更新');
  }

  const response = await fetchImpl(`${CODEBOT_GITHUB_RELEASES_API}/tags/v${encodeURIComponent(tag)}`, {
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'Codebot-Updater',
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload?.message || `查询 GitHub Release 失败: ${response.status}`);
  }
  return payload;
}

function resolveGithubInstallerAsset(releasePayload, version) {
  const assets = Array.isArray(releasePayload?.assets) ? releasePayload.assets : [];
  const normalizedVersion = String(version || '').trim().replace(/^v/i, '');
  const expectedNames = [
    `Codebot-Setup-${normalizedVersion}.exe`,
    `Codebot.Setup.${normalizedVersion}.exe`,
    `Codebot Setup ${normalizedVersion}.exe`,
  ];
  const expectedNameSet = new Set(expectedNames.map((item) => normalizeAssetName(item)));

  // GitHub Release 资产名可能因为上传方式不同出现空格/点号/连字符差异，
  // 这里统一归一化后做匹配，优先锁定 setup 安装包，避免 portable 包被误选。
  let asset = assets.find((item) => expectedNameSet.has(normalizeAssetName(item?.name)));
  if (!asset) {
    asset = assets.find((item) => {
      const name = String(item?.name || '');
      return /\.exe$/i.test(name) && /setup/i.test(name) && name.includes(normalizedVersion);
    });
  }

  if (!asset?.browser_download_url) {
    throw new Error(`未找到 ${normalizedVersion} 对应的安装包资源`);
  }
  return asset;
}

function downloadFileWithRedirects(url, destinationPath, onProgress, redirectCount = 0) {
  if (redirectCount > 5) {
    return Promise.reject(new Error('下载重定向次数过多'));
  }

  return new Promise((resolve, reject) => {
    const transport = String(url).startsWith('https:') ? https : http;
    const request = transport.get(url, {
      headers: {
        'User-Agent': 'Codebot-Updater',
        Accept: 'application/octet-stream',
      },
    }, (response) => {
      const statusCode = Number(response.statusCode || 0);
      const redirectLocation = response.headers.location;

      if ([301, 302, 303, 307, 308].includes(statusCode) && redirectLocation) {
        response.resume();
        const nextUrl = new URL(redirectLocation, url).toString();
        resolve(downloadFileWithRedirects(nextUrl, destinationPath, onProgress, redirectCount + 1));
        return;
      }

      if (statusCode !== 200) {
        response.resume();
        reject(new Error(`Cannot download "${url}", status ${statusCode}`));
        return;
      }

      fs.mkdirSync(path.dirname(destinationPath), { recursive: true });
      const fileStream = fs.createWriteStream(destinationPath);
      const totalBytes = Number(response.headers['content-length'] || 0);
      let receivedBytes = 0;
      let settled = false;

      const fail = (error) => {
        if (settled) return;
        settled = true;
        try { fileStream.destroy(); } catch (_) {}
        try { fs.unlinkSync(destinationPath); } catch (_) {}
        reject(error);
      };

      response.on('data', (chunk) => {
        receivedBytes += chunk.length;
        if (typeof onProgress === 'function') {
          onProgress({
            percent: totalBytes > 0 ? (receivedBytes / totalBytes) * 100 : 0,
            transferred: receivedBytes,
            total: totalBytes,
          });
        }
      });
      response.on('error', fail);
      fileStream.on('error', fail);

      fileStream.on('finish', () => {
        if (settled) return;
        settled = true;
        fileStream.close(() => resolve(destinationPath));
      });

      response.pipe(fileStream);
    });

    request.on('error', (error) => {
      try { fs.unlinkSync(destinationPath); } catch (_) {}
      reject(error);
    });
  });
}

async function downloadGithubReleaseInstaller(version) {
  const releasePayload = await fetchGithubReleaseByVersion(version);
  const installerAsset = resolveGithubInstallerAsset(releasePayload, version);
  const updatesDir = ensureDir(path.join(app.getPath('userData'), 'updates'));
  const targetFileName = sanitizeFileName(installerAsset.name || `Codebot-Setup-${version}.exe`);
  const targetPath = path.join(updatesDir, targetFileName);

  pendingManualUpdate = null;
  sendUpdateEvent('download-progress', {
    progress: { percent: 0, transferred: 0, total: Number(installerAsset.size || 0) },
  });

  await downloadFileWithRedirects(installerAsset.browser_download_url, targetPath, (progress) => {
    sendUpdateEvent('download-progress', { progress });
  });

  pendingManualUpdate = {
    installerPath: targetPath,
    version: String(version || '').trim(),
    source: 'github',
  };
  sendUpdateEvent('downloaded', {
    info: lastCheckedUpdateInfo,
    message: '安装包已下载，点击“安装重启”启动安装程序',
  });
  return { success: true, path: targetPath, mode: 'manual-github' };
}

function configureAutoUpdater(source, token = '') {
  if (!autoUpdater) return;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  const useGenericFeed = source === 'object-storage';
  autoUpdater.requestHeaders = useGenericFeed && token ? { Authorization: `Bearer ${token}` } : {};
  lastUpdateSource = useGenericFeed ? 'object-storage' : 'github';
  if (useGenericFeed) {
    autoUpdater.setFeedURL({ provider: 'generic', url: CODEBOT_UPDATE_GENERIC_URL });
  } else {
    autoUpdater.setFeedURL({ provider: 'github', owner: CODEBOT_GITHUB_OWNER, repo: CODEBOT_GITHUB_REPO });
  }
}

async function checkForCodebotUpdates() {
  if (!autoUpdater) throw new Error('自动更新模块不可用，请确认 electron-updater 已安装');
  const token = readAccountToken();
  const access = await fetchCodebotAccess();
  const preferredSource = access.authenticated && access.canDownloadUpdates ? 'object-storage' : 'github';
  const currentVersion = app.getVersion();

  const runCheck = async (source) => {
    configureAutoUpdater(source, token);
    sendUpdateEvent('checking', { source: lastUpdateSource });
    const result = await autoUpdater.checkForUpdates();
    lastCheckedUpdateInfo = toSerializable(result?.updateInfo || null);
    pendingManualUpdate = null;
    return {
      source: lastUpdateSource,
      updateInfo: lastCheckedUpdateInfo,
      hasUpdate: isVersionGreater(lastCheckedUpdateInfo?.version, currentVersion),
    };
  };

  if (preferredSource === 'object-storage') {
    try {
      const objectStorageResult = await runCheck('object-storage');
      if (objectStorageResult.hasUpdate) {
        return {
          success: true,
          source: objectStorageResult.source,
          canDownloadUpdates: Boolean(access.canDownloadUpdates),
          updateInfo: objectStorageResult.updateInfo,
        };
      }

      const githubRelease = await fetchLatestGithubRelease();
      const githubVersion = String(githubRelease?.tag_name || githubRelease?.name || '').replace(/^v/i, '').trim();
      if (isVersionGreater(githubVersion, currentVersion)) {
        lastUpdateSource = 'github';
        lastCheckedUpdateInfo = toSerializable({
          version: githubVersion,
          releaseName: githubRelease?.name || '',
          releaseNotes: githubRelease?.body || '',
          publishedAt: githubRelease?.published_at || '',
        });
        pendingManualUpdate = null;
        sendUpdateEvent('available', { info: lastCheckedUpdateInfo, source: 'github' });
        return {
          success: true,
          source: 'github',
          canDownloadUpdates: Boolean(access.canDownloadUpdates),
          updateInfo: lastCheckedUpdateInfo,
        };
      }

      sendUpdateEvent('not-available', { info: objectStorageResult.updateInfo, source: objectStorageResult.source });
      return {
        success: true,
        source: objectStorageResult.source,
        canDownloadUpdates: Boolean(access.canDownloadUpdates),
        updateInfo: objectStorageResult.updateInfo,
      };
    } catch (error) {
      const fallbackResult = await runCheck('github');
      return {
        success: true,
        source: fallbackResult.source,
        canDownloadUpdates: Boolean(access.canDownloadUpdates),
        updateInfo: fallbackResult.updateInfo,
        fallbackReason: error.message || String(error),
      };
    }
  }

  const result = await runCheck('github');
  return {
    success: true,
    source: result.source,
    canDownloadUpdates: Boolean(access.canDownloadUpdates),
    updateInfo: result.updateInfo,
  };
}

function bindAutoUpdaterEvents() {
  if (!autoUpdater) return;
  autoUpdater.on('checking-for-update', () => sendUpdateEvent('checking'));
  autoUpdater.on('update-available', (info) => sendUpdateEvent('available', { info }));
  autoUpdater.on('update-not-available', (info) => sendUpdateEvent('not-available', { info }));
  autoUpdater.on('download-progress', (progress) => sendUpdateEvent('download-progress', { progress }));
  autoUpdater.on('update-downloaded', (info) => sendUpdateEvent('downloaded', { info }));
  autoUpdater.on('error', (error) => sendUpdateEvent('error', { message: error.message || String(error) }));
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

  mainWindow.loadFile(path.join(__dirname, 'loading.html')).catch((error) => {
    console.error(`[window] 加载启动页失败: ${error.message || error}`);
  });

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error(`[window] 页面加载失败: ${validatedURL} (${errorCode}) ${errorDescription}`);
  });

  startBackend()
    .then(() => waitForBackendReady())
    .then(async () => {
      const localIP = getLocalIP();
      const url = await resolveRendererUrl();
      await mainWindow.loadURL(url);
      
      console.log(`
╔════════════════════════════════════════╗
║        Codebot 已启动！                ║
╠════════════════════════════════════════╣
║  本地访问：http://127.0.0.1:${backendPort}      ║
║  局域网访问：http://${localIP}:${backendPort}    ║
║  移动端：使用手机浏览器访问局域网地址  ║
╚════════════════════════════════════════╝
      `);
    })
    .catch((error) => {
      console.error(`[window] 后端启动链路失败: ${error.message || error}`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.executeJavaScript(`
          document.title = 'Codebot 启动失败';
          document.body.innerHTML = ` + JSON.stringify(`
            <main style="font-family:Segoe UI,PingFang SC,Microsoft YaHei,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#0f172a;color:#e2e8f0;">
              <section style="width:min(560px,calc(100vw - 48px));padding:32px;border-radius:20px;background:rgba(15,23,42,.88);border:1px solid rgba(248,113,113,.28);box-shadow:0 20px 60px rgba(0,0,0,.35);">
                <h1 style="margin:0 0 12px;font-size:28px;color:#f8fafc;">Codebot 启动失败</h1>
                <p style="margin:0 0 14px;line-height:1.7;color:#cbd5e1;">后端服务没有在预期时间内就绪，请查看日志后重试。</p>
                <pre style="margin:0;padding:16px;border-radius:14px;background:#111827;color:#fca5a5;white-space:pre-wrap;word-break:break-word;">${escapeHtml(error.message || String(error))}</pre>
              </section>
            </main>
          `) + `;
        `).catch((renderError) => {
          console.error(`[window] 渲染启动失败页失败: ${renderError.message || renderError}`);
        });
      }
      dialog.showErrorBox('服务启动失败', error.message || '无法连接到后端服务');
    });

  createMenu();

  // 拦截窗口内导航：根据用户设置选择在内置窗口或系统浏览器中打开外部链接
  mainWindow.webContents.on('will-navigate', (event, url) => {
    // 允许导航回本地应用的地址
    if (isInternalAppUrl(url)) {
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
  const isDev = isDevRuntime;
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
        console.log('[backend] 开发模式检测到源码后端，准备重启以加载最新代码');
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
  const docsSource = path.join(repoRoot, 'README.md');

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
    CODEBOT_DOCS_SOURCE: docsSource,
    CODEBOT_OPENCODE_PATH: opencodePath,
    CODEBOT_FORCE_OPENCODE_AUTOSTART: '1',
    CODEBOT_BACKEND_PORT: backendPort,
    CODEBOT_OPENCODE_SERVER_URL: `http://127.0.0.1:${opencodePort}`,
    CODEBOT_OPENCODE_PREFERRED_PORT: opencodePort,
    CODEBOT_OPENCODE_FALLBACK_PORT: opencodePort,
  };
  // 清除可能污染 PyInstaller 运行时的 Python 环境变量
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;

  // 确定要执行的命令和参数
  let cmd, args, cwd;

  // 开发模式（npm start / electron . ）始终使用 Python 源码，确保加载最新修改

  if (isDev) {
    const devServerReady = await checkFrontendDevServer().then(() => true).catch(() => false);
    if (!devServerReady) {
      await ensureFrontendDist(repoRoot);
    }
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
        const runningMsg = `[backend] 新进程退出，但已有后端正在监听 ${backendPort}，继续使用现有实例\n`;
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
    const req = http.get(`${BACKEND_URL}/api/health`, (res) => {
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

function checkFrontendDevServer() {
  return new Promise((resolve, reject) => {
    const req = http.get(FRONTEND_DEV_URL, (res) => {
      if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
        resolve(true);
        res.resume();
        return;
      }
      reject(new Error(`前端开发服务不可用: ${res.statusCode}`));
    });
    req.on('error', (err) => reject(err));
    req.setTimeout(1000, () => {
      req.destroy(new Error('前端开发服务超时'));
    });
  });
}

async function resolveRendererUrl() {
  if (!app.isPackaged) {
    try {
      await checkFrontendDevServer();
      return FRONTEND_DEV_URL;
    } catch (_) {}
  }
  return BACKEND_URL;
}

function ensureFrontendDist(repoRoot) {
  return new Promise((resolve, reject) => {
    const buildCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
    const buildArgs = ['run', 'build'];
    const buildCwd = path.join(repoRoot, 'frontend');
    const useShell = process.platform === 'win32';
    console.log('[frontend] 开始构建前端资源...');
    const buildProcess = spawn(buildCmd, buildArgs, {
      cwd: buildCwd,
      env: { ...process.env },
      stdio: 'pipe',
      detached: false,
      shell: useShell,
    });
    let stderr = '';
    buildProcess.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    buildProcess.on('error', (error) => {
      reject(error);
    });
    buildProcess.on('exit', (code) => {
      if (code === 0) {
        console.log('[frontend] 前端资源构建完成');
        resolve();
        return;
      }
      reject(new Error(stderr.trim() || `前端构建失败，退出码 ${code}`));
    });
  });
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
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
    message: `Codebot v${app.getVersion()}`,
    detail: '基于 OpenCode 的个人 AI 助手\n\n© 2024'
  });
}

app.whenReady().then(() => {
  console.log(`[electron] userData: ${app.getPath('userData')}`);
  console.log(`[electron] sessionData: ${app.getPath('sessionData')}`);
  bindBuiltinSessionEvents();
  bindAutoUpdaterEvents();
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
