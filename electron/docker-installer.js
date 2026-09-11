const fs = require('fs');
const path = require('path');
const os = require('os');
const https = require('https');
const { spawn } = require('child_process');

const DOCKER_DESKTOP_DOWNLOADS = {
  x64: 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe',
};
const MINIMUM_FREE_BYTES = 12 * 1024 * 1024 * 1024;
const RECOMMENDED_FREE_BYTES = 20 * 1024 * 1024 * 1024;
const MAX_REDIRECTS = 5;
const INSTALLER_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const DOCKER_UNINSTALL_REGISTRY_KEY = 'HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Docker Desktop';
const DOCKER_BACKEND_LOG_RELATIVE_PATH = path.win32.join('Docker', 'log', 'host', 'com.docker.backend.exe.log');
const DOCKER_RUNTIME_DIR_RELATIVE_PATH = path.win32.join('Docker', 'run');
const DOCKER_INGEST_SOCKET_NAME = 'sailor-ingest.sock';
const DOCKER_SECRETS_ENGINE_DIR_NAME = 'docker-secrets-engine';
const DOCKER_SECRETS_ENGINE_SOCKET_NAME = 'engine.sock';
const DOCKER_PROCESS_ROOT_ENV = 'CODEBOT_DOCKER_PROCESS_ROOT';
const ALLOWED_DOCKER_RUN_ENDPOINTS = new Set([
  'sailor-ingest.sock',
  'dockerInference',
  'dockerEthernetVfkit',
  'userAnalyticsOtlpHttp.sock',
]);
const ALLOWED_DOCKER_SECRETS_ENDPOINTS = new Set([DOCKER_SECRETS_ENGINE_SOCKET_NAME]);
// 使用进程级环境变量向 Windows PowerShell 传递安装包路径。
// `powershell.exe -Command <script> <path>` 不会像普通脚本那样把 `<path>` 放进 `$args`，
// 路径反而可能被追加到脚本最后一个命令，既会导致签名校验失败，也容易产生参数歧义。
const DOCKER_INSTALLER_PATH_ENV = 'CODEBOT_DOCKER_INSTALLER_PATH';

function serializableError(error, fallback = 'Docker Desktop 操作失败') {
  const message = error instanceof Error ? error.message : String(error || fallback);
  return message.replace(/[\r\n]+/g, ' ').slice(0, 2000) || fallback;
}

function isOfficialDockerDownloadUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    const hostname = parsed.hostname.toLowerCase();
    return parsed.protocol === 'https:' && (
      hostname === 'docker.com'
      || hostname.endsWith('.docker.com')
    );
  } catch (_) {
    return false;
  }
}

function runProcess(command, args, options = {}) {
  const timeoutMs = Number(options.timeoutMs || 60_000);
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      shell: false,
      windowsHide: options.windowsHide !== false,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error(`命令执行超时：${path.basename(command)}`));
    }, timeoutMs);
    child.stdout.on('data', (chunk) => {
      if (stdout.length < 256 * 1024) stdout += chunk.toString('utf8');
    });
    child.stderr.on('data', (chunk) => {
      if (stderr.length < 256 * 1024) stderr += chunk.toString('utf8');
    });
    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on('exit', (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code: Number(code ?? -1), signal: signal || '', stdout, stderr });
    });
  });
}

function dockerDesktopCandidates(layout, discoveredInstallDir = '') {
  const candidates = [];
  const add = (value) => {
    if (value && !candidates.includes(value)) candidates.push(value);
  };
  if (layout?.applicationDir) add(path.join(layout.applicationDir, 'Docker Desktop.exe'));
  if (discoveredInstallDir) add(path.join(discoveredInstallDir, 'Docker Desktop.exe'));
  if (process.env.ProgramFiles) add(path.join(process.env.ProgramFiles, 'Docker', 'Docker', 'Docker Desktop.exe'));
  if (process.env.LOCALAPPDATA) add(path.join(process.env.LOCALAPPDATA, 'Docker', 'Docker Desktop.exe'));
  return candidates;
}

function dockerCliCandidates(layout, discoveredInstallDir = '') {
  const candidates = ['docker.exe'];
  if (layout?.applicationDir) {
    candidates.push(path.join(layout.applicationDir, 'resources', 'bin', 'docker.exe'));
  }
  if (discoveredInstallDir) {
    candidates.push(path.join(discoveredInstallDir, 'resources', 'bin', 'docker.exe'));
  }
  if (process.env.ProgramFiles) {
    candidates.push(path.join(process.env.ProgramFiles, 'Docker', 'Docker', 'resources', 'bin', 'docker.exe'));
  }
  if (process.env.LOCALAPPDATA) {
    candidates.push(path.join(process.env.LOCALAPPDATA, 'Docker', 'resources', 'bin', 'docker.exe'));
  }
  return candidates;
}

async function firstWorkingDockerCli(layout, discoveredInstallDir = '') {
  for (const candidate of dockerCliCandidates(layout, discoveredInstallDir)) {
    if (path.win32.isAbsolute(candidate) && !fs.existsSync(candidate)) continue;
    try {
      const result = await runProcess(candidate, ['version', '--format', '{{json .Server}}'], { timeoutMs: 15_000 });
      if (result.code === 0) return { executable: candidate, result };
    } catch (_) {}
  }
  return null;
}

function parseRegistryStringValue(output, valueName) {
  const escapedName = String(valueName || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`^\\s*${escapedName}\\s+REG_\\w+\\s+(.+?)\\s*$`, 'im');
  return String(output || '').match(pattern)?.[1]?.trim() || '';
}

async function discoverDockerInstallLocation() {
  if (process.platform !== 'win32') return '';
  const systemRoot = process.env.SystemRoot || process.env.WINDIR || 'C:\\Windows';
  const regExecutable = path.win32.join(systemRoot, 'System32', 'reg.exe');
  try {
    const result = await runProcess(regExecutable, [
      'query',
      DOCKER_UNINSTALL_REGISTRY_KEY,
      '/v',
      'InstallLocation',
    ], { timeoutMs: 10_000 });
    if (result.code !== 0) return '';
    const installLocation = parseRegistryStringValue(result.stdout, 'InstallLocation');
    if (!path.win32.isAbsolute(installLocation)) return '';
    return path.win32.normalize(installLocation);
  } catch (_) {
    return '';
  }
}

function resolveStorageLayout(selectedRoot) {
  const raw = String(selectedRoot || '').trim();
  if (!raw || !path.win32.isAbsolute(raw) || raw.startsWith('\\\\')) {
    throw new Error('Docker 存储目录必须是本机磁盘上的绝对路径');
  }
  const root = path.win32.normalize(raw);
  const windowsDir = path.win32.normalize(process.env.WINDIR || 'C:\\Windows').toLowerCase();
  const normalizedLower = root.toLowerCase();
  if (normalizedLower === windowsDir || normalizedLower.startsWith(`${windowsDir}\\`)) {
    throw new Error('Docker 存储目录不能位于 Windows 系统目录内');
  }
  const dockerRoot = path.join(root, 'DockerDesktop');
  return {
    selectedRoot: root,
    dockerRoot,
    applicationDir: path.join(dockerRoot, 'app'),
    wslDataDir: path.join(dockerRoot, 'wsl'),
    hyperVDataDir: path.join(dockerRoot, 'hyper-v'),
    windowsContainersDataDir: path.join(dockerRoot, 'windows-containers'),
    cacheDir: path.join(root, '.codebot-installer-cache'),
  };
}

function ensureWritableStorage(layout) {
  fs.mkdirSync(layout.selectedRoot, { recursive: true });
  const probe = path.join(layout.selectedRoot, `.codebot-write-probe-${process.pid}-${Date.now()}`);
  fs.writeFileSync(probe, 'ok', { encoding: 'utf8', flag: 'wx' });
  fs.unlinkSync(probe);
  const stat = fs.statfsSync(layout.selectedRoot);
  const freeBytes = Number(stat.bavail) * Number(stat.bsize);
  if (!Number.isFinite(freeBytes) || freeBytes < MINIMUM_FREE_BYTES) {
    throw new Error('所选磁盘可用空间不足 12 GiB，无法安全安装 Docker Desktop 与 Rakazo');
  }
  return {
    freeBytes,
    recommended: freeBytes >= RECOMMENDED_FREE_BYTES,
  };
}

function isRecentInstallerCache(installerPath, now = Date.now()) {
  try {
    const stat = fs.statSync(installerPath);
    const ageMs = Number(now) - Number(stat.mtimeMs);
    // 只短期复用刚刚由一键安装流程下载的完整文件；最终仍必须重新通过 Authenticode 校验。
    return stat.isFile()
      && stat.size > 0
      && ageMs >= 0
      && ageMs <= INSTALLER_CACHE_MAX_AGE_MS;
  } catch (_) {
    return false;
  }
}

function downloadOfficialInstaller(url, destination, onProgress, redirectCount = 0) {
  if (!isOfficialDockerDownloadUrl(url)) {
    return Promise.reject(new Error('Docker 安装包地址不属于官方 docker.com 域名'));
  }
  if (redirectCount > MAX_REDIRECTS) {
    return Promise.reject(new Error('Docker 安装包重定向次数过多'));
  }
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { 'User-Agent': 'Codebot-Docker-Installer/1.0' } }, (response) => {
      const status = Number(response.statusCode || 0);
      if ([301, 302, 303, 307, 308].includes(status)) {
        const location = response.headers.location;
        response.resume();
        if (!location) {
          reject(new Error('Docker 官方下载返回了无目标重定向'));
          return;
        }
        const nextUrl = new URL(location, url).toString();
        downloadOfficialInstaller(nextUrl, destination, onProgress, redirectCount + 1).then(resolve, reject);
        return;
      }
      if (status !== 200) {
        response.resume();
        reject(new Error(`Docker 官方下载返回 HTTP ${status}`));
        return;
      }
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      const temporary = `${destination}.part`;
      const output = fs.createWriteStream(temporary, { flags: 'w' });
      const total = Number(response.headers['content-length'] || 0);
      let received = 0;
      let completed = false;
      const fail = (error) => {
        if (completed) return;
        completed = true;
        output.destroy();
        try { fs.unlinkSync(temporary); } catch (_) {}
        reject(error);
      };
      response.on('data', (chunk) => {
        received += chunk.length;
        if (typeof onProgress === 'function') onProgress({ received, total });
      });
      response.on('error', fail);
      output.on('error', fail);
      output.on('finish', () => {
        if (completed) return;
        completed = true;
        output.close(() => {
          fs.renameSync(temporary, destination);
          resolve(destination);
        });
      });
      response.pipe(output);
    });
    request.setTimeout(60_000, () => request.destroy(new Error('Docker 安装包下载连接超时')));
    request.on('error', reject);
  });
}

async function verifyDockerAuthenticode(installerPath, dependencies = {}) {
  const resolvedInstallerPath = path.resolve(String(installerPath || ''));
  if (!installerPath || !fs.existsSync(resolvedInstallerPath)) {
    throw new Error('Docker Desktop 安装包不存在，无法验证数字签名');
  }

  // 文件路径通过子进程专用环境变量传入，绝不拼接进 PowerShell 命令字符串。
  // 这里不能使用 `$args[0]`：powershell.exe 的 `-Command` 会消费后续原生参数，
  // 实测路径会被绑定到 `ConvertTo-Json`，从而让 `$args` 为空并以退出码 1 失败。
  const script = [
    '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
    '$ErrorActionPreference = "Stop"',
    // 显式加载 Windows PowerShell 自带安全模块，避免从 PowerShell 7/Codex 继承的模块路径抢先解析。
    'Import-Module Microsoft.PowerShell.Security -ErrorAction Stop',
    `$installerPath = [Environment]::GetEnvironmentVariable("${DOCKER_INSTALLER_PATH_ENV}", "Process")`,
    'if ([string]::IsNullOrWhiteSpace($installerPath)) { throw "未收到 Docker Desktop 安装包路径" }',
    '$signature = Get-AuthenticodeSignature -LiteralPath $installerPath',
    '$certificate = $signature.SignerCertificate',
    '$subject = if ($certificate) { [string]$certificate.Subject } else { "" }',
    '$issuer = if ($certificate) { [string]$certificate.Issuer } else { "" }',
    '$thumbprint = if ($certificate) { [string]$certificate.Thumbprint } else { "" }',
    '[PSCustomObject]@{ Status = [string]$signature.Status; StatusMessage = [string]$signature.StatusMessage; Subject = $subject; Issuer = $issuer; Thumbprint = $thumbprint } | ConvertTo-Json -Compress',
  ].join('; ');
  const processRunner = dependencies.runProcess || runProcess;
  const systemRoot = process.env.SystemRoot || process.env.WINDIR || 'C:\\Windows';
  const powershellExecutable = dependencies.powershellExecutable || path.win32.join(
    systemRoot,
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe',
  );
  const powershellEnv = {
    ...process.env,
    [DOCKER_INSTALLER_PATH_ENV]: resolvedInstallerPath,
  };
  // Electron 可能由 PowerShell 7、Codex 或开发终端启动，并继承其 PSModulePath。
  // Windows PowerShell 收到这类外部模块目录后可能无法自动加载
  // Microsoft.PowerShell.Security。删除继承值后，Windows PowerShell 会重建系统默认模块路径。
  for (const key of Object.keys(powershellEnv)) {
    if (key.toLowerCase() === 'psmodulepath') delete powershellEnv[key];
  }
  const result = await processRunner(powershellExecutable, [
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-Command', script,
  ], {
    timeoutMs: 60_000,
    env: powershellEnv,
  });
  if (result.code !== 0) {
    const detail = serializableError(
      String(result.stderr || result.stdout || '').trim(),
      '',
    );
    throw new Error(
      detail
        ? `无法验证 Docker Desktop 安装包的 Windows 数字签名：${detail}`
        : '无法验证 Docker Desktop 安装包的 Windows 数字签名',
    );
  }
  let payload;
  try { payload = JSON.parse(result.stdout.trim()); } catch (_) { payload = null; }
  if (!payload || typeof payload !== 'object') {
    throw new Error('Windows 数字签名校验未返回可解析的结果，已拒绝执行安装包');
  }
  const subject = String(payload?.Subject || '');
  const status = String(payload?.Status || '');
  const statusMessage = serializableError(String(payload?.StatusMessage || '').trim(), '');
  // 只接受 Windows 信任链验证为 Valid 且证书主体明确属于 Docker Inc 的安装包。
  // 不因网络、证书链或解析错误而降级放行，继续保持失败关闭。
  if (status !== 'Valid' || !/(?:^|,\s*)(?:CN|O)=Docker Inc(?:,|$)/i.test(subject)) {
    const detail = [status, statusMessage].filter(Boolean).join('：');
    throw new Error(
      `Docker Desktop 安装包签名无效或签名者不是 Docker Inc${detail ? `（${detail}）` : ''}，已拒绝执行`,
    );
  }
  return {
    status,
    statusMessage,
    subject,
    issuer: String(payload?.Issuer || ''),
    thumbprint: String(payload?.Thumbprint || ''),
  };
}

async function wslStatus() {
  if (process.platform !== 'win32') return { available: false, ready: false, reason: '仅 Windows 支持 Docker Desktop 一键安装' };
  try {
    const result = await runProcess('wsl.exe', ['--status'], { timeoutMs: 20_000 });
    return {
      available: true,
      ready: result.code === 0,
      output: `${result.stdout}\n${result.stderr}`.trim().slice(0, 2000),
    };
  } catch (error) {
    return { available: false, ready: false, reason: serializableError(error, '未检测到 WSL') };
  }
}

async function installWslIfNeeded(onProgress) {
  const current = await wslStatus();
  if (current.ready) return { changed: false, rebootRequired: false };
  onProgress?.({ phase: 'wsl', percent: 5, message: '正在启用或更新 WSL 2（Windows 可能弹出管理员确认）' });
  // wsl.exe 会按 Windows 官方流程申请所需权限；不借助 cmd/shell，也不安装 Linux 发行版。
  const result = await runProcess('wsl.exe', ['--install', '--no-distribution'], {
    timeoutMs: 15 * 60_000,
    windowsHide: false,
  });
  const output = `${result.stdout}\n${result.stderr}`.trim();
  if (result.code !== 0) throw new Error(`WSL 2 准备失败：${output.slice(0, 1200)}`);
  return { changed: true, rebootRequired: /restart|reboot|重新启动|重启/i.test(output) };
}

async function getDockerInstallerStatus(options = {}) {
  const supported = process.platform === 'win32' && process.arch === 'x64';
  const layout = options.storageRoot ? resolveStorageLayout(options.storageRoot) : null;
  // 注册表是跨重启的权威安装位置；用户选择目录用于安装刚完成但注册表尚未刷新的窗口。
  const discoveredInstallDir = await discoverDockerInstallLocation();
  const cli = await firstWorkingDockerCli(layout, discoveredInstallDir);
  const installedDesktop = dockerDesktopCandidates(layout, discoveredInstallDir)
    .find((candidate) => fs.existsSync(candidate)) || '';
  const release = os.release();
  const build = Number(String(release).split('.')[2] || 0);
  return {
    supported,
    platform: process.platform,
    arch: process.arch,
    windowsBuild: build,
    requirementsMet: supported && build >= 19045,
    installed: Boolean(cli || installedDesktop),
    ready: Boolean(cli),
    dockerExecutable: cli?.executable || '',
    desktopExecutable: installedDesktop,
    installLocation: installedDesktop ? path.dirname(installedDesktop) : discoveredInstallDir,
    wsl: await wslStatus(),
    message: cli
      ? 'Docker Engine 已就绪'
      : installedDesktop
        ? 'Docker Desktop 已安装但 Engine 尚未就绪'
        : supported
          ? '可由 Codebot 一键安装 Docker Desktop'
          : '当前系统不支持 Codebot 的 Docker Desktop 一键安装',
  };
}

function dockerRuntimeRecoveryPaths(localAppData = process.env.LOCALAPPDATA || '') {
  const normalizedRoot = String(localAppData || '').trim();
  if (!normalizedRoot || !path.win32.isAbsolute(normalizedRoot)) return null;
  const runtimeDir = path.win32.resolve(normalizedRoot, DOCKER_RUNTIME_DIR_RELATIVE_PATH);
  const secretsEngineDir = path.win32.resolve(normalizedRoot, DOCKER_SECRETS_ENGINE_DIR_NAME);
  return {
    localAppData: path.win32.resolve(normalizedRoot),
    runtimeDir,
    ingestSocket: path.win32.join(runtimeDir, DOCKER_INGEST_SOCKET_NAME),
    secretsEngineDir,
    secretsEngineSocket: path.win32.join(secretsEngineDir, DOCKER_SECRETS_ENGINE_SOCKET_NAME),
    backendLog: path.win32.resolve(normalizedRoot, DOCKER_BACKEND_LOG_RELATIVE_PATH),
  };
}

function isDockerIngestSocketFailure(logText) {
  const text = String(logText || '');
  return /initializing Ingest server/i.test(text)
    && /sailor-ingest\.sock/i.test(text)
    && /file cannot be accessed by the system/i.test(text);
}

function classifyDockerRuntimeSocketFailure(logText, runtimePaths = dockerRuntimeRecoveryPaths()) {
  if (!runtimePaths) return null;
  const text = String(logText || '');
  if (!/file cannot be accessed by the system/i.test(text)) return null;
  const normalizedText = text.replace(/\\/g, '/').toLowerCase();
  const normalizedIngestSocket = runtimePaths.ingestSocket.replace(/\\/g, '/').toLowerCase();
  const normalizedSecretsSocket = runtimePaths.secretsEngineSocket.replace(/\\/g, '/').toLowerCase();
  if (/initializing Ingest server/i.test(text) && normalizedText.includes(normalizedIngestSocket)) {
    return {
      kind: 'docker-run',
      directory: runtimePaths.runtimeDir,
      endpoint: runtimePaths.ingestSocket,
      allowedEntries: ALLOWED_DOCKER_RUN_ENDPOINTS,
    };
  }
  if (/initializing Secrets Engine/i.test(text) && normalizedText.includes(normalizedSecretsSocket)) {
    return {
      kind: 'docker-secrets-engine',
      directory: runtimePaths.secretsEngineDir,
      endpoint: runtimePaths.secretsEngineSocket,
      allowedEntries: ALLOWED_DOCKER_SECRETS_ENDPOINTS,
    };
  }
  return null;
}

function dockerLogLineTimestamp(logLine) {
  const match = String(logLine || '').match(/^\[([^\]]+)\]/);
  if (!match) return NaN;
  return Date.parse(match[1]);
}

function readRecentDockerBackendError(backendLog, startedAtMs) {
  try {
    const stat = fs.statSync(backendLog);
    // 只接受本轮启动附近产生的新日志，避免用历史故障触发恢复动作。
    if (Number(stat.mtimeMs) < Number(startedAtMs) - 2_000) return '';
    const size = Math.min(Number(stat.size) || 0, 256 * 1024);
    if (size <= 0) return '';
    const fd = fs.openSync(backendLog, 'r');
    try {
      const buffer = Buffer.alloc(size);
      fs.readSync(fd, buffer, 0, size, Math.max(0, Number(stat.size) - size));
      const lines = buffer.toString('utf8').split(/\r?\n/).filter(Boolean);
      // 同一个日志长期追加；正常的新启动也会刷新文件 mtime。必须再按每行的 ISO 时间
      // 过滤，绝不能因为文件刚更新就误用更早一次启动留下的崩溃行。
      const recentLines = lines.filter((line) => {
        const timestamp = dockerLogLineTimestamp(line);
        // launchedAt 在 spawn 之前记录，Docker 与 Codebot 使用同一台 Windows 主机时钟；
        // 不留负向容差，避免快速重试时把上一轮刚写入的崩溃行再次归入新一轮。
        return Number.isFinite(timestamp) && timestamp >= Number(startedAtMs);
      });
      const crashLine = [...recentLines].reverse().find((line) => (
        /backend crashed|reporting error to user/i.test(line)
      ));
      return serializableError(crashLine || recentLines.at(-1) || '', '');
    } finally {
      fs.closeSync(fd);
    }
  } catch (_) {
    return '';
  }
}

async function stopFailedDockerDesktopProcesses(applicationDir) {
  if (process.platform !== 'win32') return { stopped: 0 };
  const normalizedApplicationDir = path.win32.resolve(String(applicationDir || ''));
  if (!path.win32.isAbsolute(normalizedApplicationDir) || !fs.existsSync(normalizedApplicationDir)) {
    throw new Error('无法确认 Docker Desktop 安装目录，拒绝自动关闭进程');
  }
  const systemRoot = process.env.SystemRoot || process.env.WINDIR || 'C:\\Windows';
  const powershellExecutable = path.win32.join(
    systemRoot,
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe',
  );
  const script = [
    '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
    '$ErrorActionPreference = "Stop"',
    `$root = [Environment]::GetEnvironmentVariable("${DOCKER_PROCESS_ROOT_ENV}", "Process")`,
    '$root = [System.IO.Path]::GetFullPath($root).TrimEnd("\\")',
    '$prefix = $root + "\\"',
    '$targets = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) })',
    '$stopped = 0',
    'foreach ($target in $targets) { Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue; $stopped += 1 }',
    'Start-Sleep -Milliseconds 500',
    '$remaining = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) })',
    'if ($remaining.Count -gt 0) { exit 43 }',
    '[PSCustomObject]@{ stopped = $stopped } | ConvertTo-Json -Compress',
  ].join('; ');
  const childEnv = {
    ...process.env,
    [DOCKER_PROCESS_ROOT_ENV]: normalizedApplicationDir,
  };
  for (const key of Object.keys(childEnv)) {
    if (key.toLowerCase() === 'psmodulepath') delete childEnv[key];
  }
  const result = await runProcess(powershellExecutable, [
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-Command', script,
  ], {
    timeoutMs: 30_000,
    windowsHide: true,
    env: childEnv,
  });
  if (result.code !== 0) throw new Error(`无法关闭已崩溃的 Docker Desktop 进程（退出码 ${result.code}）`);
  try { return JSON.parse(result.stdout.trim()); } catch (_) { return { stopped: 0 }; }
}

function inspectDockerRuntimeDirectory(failure, runtimePaths = dockerRuntimeRecoveryPaths()) {
  const directory = path.win32.resolve(String(failure?.directory || ''));
  const allowedDirectories = new Set([
    runtimePaths?.runtimeDir?.toLowerCase(),
    runtimePaths?.secretsEngineDir?.toLowerCase(),
  ].filter(Boolean));
  if (!allowedDirectories.has(directory.toLowerCase())) {
    throw new Error('Docker 临时运行目录不在固定白名单内，已拒绝自动隔离');
  }
  if (!fs.existsSync(directory)) return { directory, exists: false, entries: [] };
  const entries = fs.readdirSync(directory, { withFileTypes: true });
  const unexpected = entries.filter((entry) => (
    entry.isDirectory() || !failure.allowedEntries.has(entry.name)
  ));
  if (unexpected.length > 0) {
    throw new Error(`Docker 临时运行目录包含未识别项目，拒绝自动移动：${unexpected.map((item) => item.name).join(', ')}`);
  }
  return { directory, exists: true, entries };
}

function quarantineDockerRuntimeDirectory(failure, runtimePaths = dockerRuntimeRecoveryPaths()) {
  const inspection = inspectDockerRuntimeDirectory(failure, runtimePaths);
  const { directory, entries } = inspection;
  if (!inspection.exists) {
    fs.mkdirSync(directory, { recursive: true });
    return { directory, quarantine: '', entries: [] };
  }
  let quarantine = `${directory}.stale-codebot-${Date.now()}-${process.pid}`;
  let suffix = 0;
  while (fs.existsSync(quarantine)) {
    suffix += 1;
    quarantine = `${directory}.stale-codebot-${Date.now()}-${process.pid}-${suffix}`;
  }
  // Windows 对损坏 AF_UNIX 重解析点本身会返回 EACCES，但其父目录仍可原子改名。
  // 这里保留完整旧目录以便回退，随后只新建空运行目录，不删除任何用户数据。
  fs.renameSync(directory, quarantine);
  fs.mkdirSync(directory, { recursive: false });
  return { directory, quarantine, entries: entries.map((entry) => entry.name) };
}

function dockerRuntimeEndpointFailures(runtimePaths = dockerRuntimeRecoveryPaths()) {
  if (!runtimePaths) return [];
  return [
    {
      kind: 'docker-run',
      directory: runtimePaths.runtimeDir,
      endpoint: runtimePaths.ingestSocket,
      allowedEntries: ALLOWED_DOCKER_RUN_ENDPOINTS,
    },
    {
      kind: 'docker-secrets-engine',
      directory: runtimePaths.secretsEngineDir,
      endpoint: runtimePaths.secretsEngineSocket,
      allowedEntries: ALLOWED_DOCKER_SECRETS_ENDPOINTS,
    },
  ];
}

/**
 * 一次失败的 Docker backend 会同时创建 Ingest 与 Secrets Engine 两组临时端点。
 * 若只移动日志中首先报错的一组，另一组会在自动重试时变成新的失效 socket，形成
 * `docker-run -> secrets-engine -> docker-run` 的交替崩溃。因此必须先验证两组目录
 * 都只含白名单运行端点，再一起隔离；任一目录出现未知文件时整次操作失败关闭。
 */
function quarantineDockerRuntimeEndpointDirectories(runtimePaths = dockerRuntimeRecoveryPaths()) {
  const failures = dockerRuntimeEndpointFailures(runtimePaths);
  // 先完成全部只读预检，避免移动第一组后才发现第二组含未知数据而造成半完成状态。
  for (const failure of failures) inspectDockerRuntimeDirectory(failure, runtimePaths);
  return failures.map((failure) => ({
    kind: failure.kind,
    ...quarantineDockerRuntimeDirectory(failure, runtimePaths),
  }));
}

async function recoverStaleDockerRuntime(startedAtMs, onProgress, applicationDir, recoveredKinds = new Set()) {
  const runtimePaths = dockerRuntimeRecoveryPaths();
  if (!runtimePaths) return { recovered: false, reason: 'missing-local-app-data' };
  const recentError = readRecentDockerBackendError(runtimePaths.backendLog, startedAtMs);
  const failure = classifyDockerRuntimeSocketFailure(recentError, runtimePaths);
  if (!failure) {
    return { recovered: false, reason: 'no-matching-current-failure', recentError };
  }
  if (recoveredKinds.has(failure.kind)) {
    return {
      recovered: false,
      attempted: true,
      reason: 'runtime-failure-recurred',
      recentError,
      error: `同一 Docker 临时端点故障在恢复后再次出现：${failure.kind}`,
    };
  }
  try {
    onProgress?.({
      phase: 'recovering',
      percent: 94,
      message: '检测到 Docker 异常退出遗留的临时端点，正在安全关闭崩溃进程',
    });
    const stopped = await stopFailedDockerDesktopProcesses(applicationDir);
    const quarantined = quarantineDockerRuntimeEndpointDirectories(runtimePaths);
    onProgress?.({
      phase: 'recovering',
      percent: 95,
      message: '已同时隔离 Docker 两组临时端点，旧目录保留可回退，正在自动重试',
    });
    return {
      recovered: true,
      kind: failure.kind,
      kinds: quarantined.map((item) => item.kind),
      recentError,
      stopped,
      quarantined,
    };
  } catch (error) {
    return {
      recovered: false,
      reason: 'runtime-directory-quarantine-failed',
      recentError,
      error: serializableError(error),
      attempted: true,
    };
  }
}

function launchDockerDesktop(executable) {
  const child = spawn(executable, [], {
    detached: true,
    shell: false,
    windowsHide: true,
    stdio: 'ignore',
  });
  child.unref();
}

async function startDockerDesktop(layout, onProgress, preferredExecutable = '') {
  const discoveredInstallDir = preferredExecutable ? path.dirname(preferredExecutable) : await discoverDockerInstallLocation();
  const executable = [preferredExecutable, ...dockerDesktopCandidates(layout, discoveredInstallDir)]
    .find((candidate) => candidate && fs.existsSync(candidate));
  if (!executable) throw new Error('Docker Desktop 已安装但没有找到启动程序');
  onProgress?.({ phase: 'starting', percent: 92, message: '正在启动 Docker Desktop' });
  let launchedAt = Date.now();
  const recoveredKinds = new Set();
  launchDockerDesktop(executable);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const cli = await firstWorkingDockerCli(layout, path.dirname(executable));
    if (cli) return { ready: true, executable: cli.executable };

    // Docker Desktop 异常退出时可能留下两类失效 Windows Unix socket。只有本轮新日志
    // 命中固定路径与固定错误，且目录内容完全落在运行端点白名单内时，才隔离并重试。
    if (recoveredKinds.size < 2 && attempt >= 1) {
      const recovery = await recoverStaleDockerRuntime(
        launchedAt,
        onProgress,
        path.dirname(executable),
        recoveredKinds,
      );
      if (recovery.recovered) {
        for (const kind of recovery.kinds || [recovery.kind]) recoveredKinds.add(kind);
        launchedAt = Date.now();
        launchDockerDesktop(executable);
        continue;
      }
      if (recovery.attempted) {
        throw new Error(
          `Docker Desktop 启动失败，且无法安全清理遗留的临时通信文件${recovery.error ? `：${recovery.error}` : ''}`,
        );
      }
    }
    onProgress?.({
      phase: 'starting',
      percent: Math.min(99, 92 + Math.floor(attempt / 16)),
      message: `等待 Docker Engine 就绪（${attempt + 1}/120）`,
    });
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  const runtimePaths = dockerRuntimeRecoveryPaths();
  const backendError = runtimePaths
    ? readRecentDockerBackendError(runtimePaths.backendLog, launchedAt)
    : '';
  throw new Error(
    `Docker Desktop 已启动，但 Docker Engine 在 4 分钟内未就绪${backendError ? `：${backendError}` : ''}`,
  );
}

/**
 * 只启动已经安装的 Docker Desktop。
 *
 * 该入口与安装流程严格分开：不会创建安装目录、检查安装磁盘空间、下载或执行
 * Docker 安装包，也不会调用 `wsl --install`。用户已经安装 Docker 但 Engine 未运行时，
 * Codebot 只能走这个入口，避免“启动”按钮重新弹出安装确认甚至重复执行安装器。
 */
async function startInstalledDockerDesktop(options = {}, onProgress, dependencies = {}) {
  const layout = options.storageRoot ? resolveStorageLayout(options.storageRoot) : null;
  const readStatus = dependencies.getDockerInstallerStatus || getDockerInstallerStatus;
  const startExisting = dependencies.startDockerDesktop || startDockerDesktop;
  const statusOptions = layout ? { storageRoot: layout.selectedRoot } : {};
  const status = await readStatus(statusOptions);

  if (!status.supported) {
    throw new Error('Codebot 启动 Docker Desktop 仅支持 Windows x64 桌面版');
  }
  if (status.ready) {
    onProgress?.({ phase: 'done', percent: 100, message: 'Docker Engine 已经就绪，无需重复启动' });
    return { success: true, alreadyReady: true, alreadyInstalled: true, status };
  }
  if (!status.installed || !status.desktopExecutable) {
    throw new Error('尚未检测到 Docker Desktop，请先使用“一键安装 Docker”');
  }

  onProgress?.({
    phase: 'starting',
    percent: 2,
    message: `已识别现有 Docker Desktop：${status.installLocation}，正在启动 Engine`,
  });
  const started = await startExisting(layout, onProgress, status.desktopExecutable);
  const finalStatus = await readStatus(statusOptions);
  if (!finalStatus.ready) throw new Error('Docker Desktop 已启动，但 Docker Engine 验证未通过');
  onProgress?.({ phase: 'done', percent: 100, message: 'Docker Desktop 已启动，Engine 已就绪' });
  return {
    success: true,
    alreadyReady: false,
    alreadyInstalled: true,
    started,
    status: finalStatus,
    message: 'Docker Desktop 已启动，Engine 已就绪',
  };
}

async function installDockerDesktop(options = {}, onProgress) {
  const layout = resolveStorageLayout(options.storageRoot);
  const disk = ensureWritableStorage(layout);
  const status = await getDockerInstallerStatus({ storageRoot: layout.selectedRoot });
  if (!status.supported || !status.requirementsMet) {
    throw new Error('一键安装要求 Windows 10 22H2（build 19045）或更高版本的 x64 系统');
  }
  if (status.ready) return { success: true, alreadyReady: true, status };
  onProgress?.({ phase: 'preflight', percent: 2, message: '安装目录和磁盘空间检查通过', disk });

  const wsl = await installWslIfNeeded(onProgress);
  if (wsl.rebootRequired) {
    return {
      success: false,
      rebootRequired: true,
      message: 'WSL 2 已启用，需要重启 Windows 后再点击一次“一键安装 Docker”',
      layout,
    };
  }

  if (status.installed && status.desktopExecutable) {
    onProgress?.({
      phase: 'starting',
      percent: 85,
      message: `已识别现有 Docker Desktop：${status.installLocation}，跳过重复安装`,
    });
    const started = await startDockerDesktop(layout, onProgress, status.desktopExecutable);
    const finalStatus = await getDockerInstallerStatus({ storageRoot: layout.selectedRoot });
    if (!finalStatus.ready) throw new Error('Docker Desktop 已启动，但 Docker Engine 验证未通过');
    onProgress?.({ phase: 'done', percent: 100, message: '现有 Docker Desktop 已启动，Engine 已就绪' });
    return {
      success: true,
      alreadyInstalled: true,
      alreadyReady: false,
      layout,
      disk,
      started,
      status: finalStatus,
      message: 'Docker Desktop 已安装，Codebot 已直接启动并验证 Engine',
    };
  }

  fs.mkdirSync(layout.cacheDir, { recursive: true });
  const installerPath = path.join(layout.cacheDir, 'DockerDesktopInstaller.exe');
  let signature = null;
  if (isRecentInstallerCache(installerPath)) {
    onProgress?.({ phase: 'verify', percent: 72, message: '发现刚下载的安装包，正在重新验证 Docker Inc 数字签名' });
    try {
      signature = await verifyDockerAuthenticode(installerPath);
      onProgress?.({ phase: 'verify', percent: 78, message: '缓存安装包签名有效，将直接继续安装' });
    } catch (_) {
      // 缓存无效时不降级执行；删除后重新从官方地址下载并再次验签。
      signature = null;
      onProgress?.({ phase: 'download', percent: 8, message: '缓存校验未通过，正在重新下载官方安装包' });
    }
  }
  if (!signature) {
    if (fs.existsSync(installerPath)) {
      try {
        fs.unlinkSync(installerPath);
      } catch (error) {
        throw new Error(`无法替换 Docker Desktop 安装包缓存：${serializableError(error)}`);
      }
    }
    onProgress?.({ phase: 'download', percent: 8, message: '正在从 Docker 官方地址下载安装包' });
    await downloadOfficialInstaller(DOCKER_DESKTOP_DOWNLOADS.x64, installerPath, ({ received, total }) => {
      const ratio = total > 0 ? received / total : 0;
      onProgress?.({
        phase: 'download',
        percent: Math.min(72, 8 + Math.round(ratio * 64)),
        message: total > 0
          ? `正在下载 Docker Desktop：${Math.round(ratio * 100)}%`
          : `正在下载 Docker Desktop：${Math.round(received / 1024 / 1024)} MiB`,
        received,
        total,
      });
    });

    onProgress?.({ phase: 'verify', percent: 75, message: '正在验证 Docker Inc 数字签名' });
    signature = await verifyDockerAuthenticode(installerPath);
  }
  for (const directory of [layout.applicationDir, layout.wslDataDir, layout.hyperVDataDir, layout.windowsContainersDataDir]) {
    fs.mkdirSync(directory, { recursive: true });
  }
  onProgress?.({ phase: 'install', percent: 80, message: '正在安装 Docker Desktop（Windows 可能弹出管理员确认）' });
  const installArgs = [
    'install', '--quiet', '--accept-license', '--backend=wsl-2', '--no-windows-containers',
    `--installation-dir=${layout.applicationDir}`,
    `--wsl-default-data-root=${layout.wslDataDir}`,
    `--hyper-v-default-data-root=${layout.hyperVDataDir}`,
    `--windows-containers-default-data-root=${layout.windowsContainersDataDir}`,
  ];
  const installed = await runProcess(installerPath, installArgs, {
    timeoutMs: 30 * 60_000,
    windowsHide: false,
  });
  if (installed.code !== 0) {
    // Docker 安装器会把“现有版本已是最新”作为退出码 3。只有再次从注册表/磁盘
    // 确认产品已安装时才把它转为“直接启动”，其他退出码或不完整安装继续失败关闭。
    const afterInstallStatus = await getDockerInstallerStatus({ storageRoot: layout.selectedRoot });
    if (installed.code === 3 && afterInstallStatus.installed && afterInstallStatus.desktopExecutable) {
      onProgress?.({ phase: 'starting', percent: 88, message: 'Docker Desktop 已是最新版本，正在直接启动 Engine' });
      const started = await startDockerDesktop(layout, onProgress, afterInstallStatus.desktopExecutable);
      const finalStatus = await getDockerInstallerStatus({ storageRoot: layout.selectedRoot });
      if (!finalStatus.ready) throw new Error('Docker Desktop 已安装，但 Docker Engine 验证未通过');
      return {
        success: true,
        alreadyInstalled: true,
        alreadyReady: false,
        layout,
        disk,
        signature,
        started,
        status: finalStatus,
        message: 'Docker Desktop 已是最新版本，Codebot 已直接启动并验证 Engine',
      };
    }
    const detail = (installed.stderr || installed.stdout).trim().slice(0, 1200);
    throw new Error(`Docker Desktop 安装失败（退出码 ${installed.code}）${detail ? `：${detail}` : ''}`);
  }
  try { fs.unlinkSync(installerPath); } catch (_) {}
  const started = await startDockerDesktop(layout, onProgress, path.join(layout.applicationDir, 'Docker Desktop.exe'));
  onProgress?.({ phase: 'done', percent: 100, message: 'Docker Desktop 已安装并就绪' });
  return {
    success: true,
    alreadyReady: false,
    layout,
    disk,
    signature,
    started,
    status: await getDockerInstallerStatus({ storageRoot: layout.selectedRoot }),
  };
}

module.exports = {
  DOCKER_DESKTOP_DOWNLOADS,
  isOfficialDockerDownloadUrl,
  dockerDesktopCandidates,
  dockerCliCandidates,
  parseRegistryStringValue,
  resolveStorageLayout,
  isRecentInstallerCache,
  dockerRuntimeRecoveryPaths,
  isDockerIngestSocketFailure,
  classifyDockerRuntimeSocketFailure,
  dockerLogLineTimestamp,
  readRecentDockerBackendError,
  quarantineDockerRuntimeDirectory,
  quarantineDockerRuntimeEndpointDirectories,
  recoverStaleDockerRuntime,
  getDockerInstallerStatus,
  startInstalledDockerDesktop,
  installDockerDesktop,
  verifyDockerAuthenticode,
};
