const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  classifyDockerRuntimeSocketFailure,
  dockerCliCandidates,
  dockerDesktopCandidates,
  dockerLogLineTimestamp,
  dockerRuntimeRecoveryPaths,
  isDockerIngestSocketFailure,
  isOfficialDockerDownloadUrl,
  isRecentInstallerCache,
  parseRegistryStringValue,
  quarantineDockerRuntimeDirectory,
  quarantineDockerRuntimeEndpointDirectories,
  readRecentDockerBackendError,
  resolveStorageLayout,
  startInstalledDockerDesktop,
  verifyDockerAuthenticode,
} = require('../docker-installer');

test('Docker 安装器只接受官方 HTTPS 下载域名', () => {
  assert.equal(
    isOfficialDockerDownloadUrl('https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'),
    true,
  );
  assert.equal(isOfficialDockerDownloadUrl('http://desktop.docker.com/installer.exe'), false);
  assert.equal(isOfficialDockerDownloadUrl('https://docker.com.evil.example/installer.exe'), false);
  assert.equal(isOfficialDockerDownloadUrl('https://example.com/installer.exe'), false);
});

test('用户选择的磁盘只生成固定 Codebot Docker 子目录', () => {
  const layout = resolveStorageLayout('D:\\AI-Runtime');
  assert.equal(layout.selectedRoot, 'D:\\AI-Runtime');
  assert.equal(layout.applicationDir, 'D:\\AI-Runtime\\DockerDesktop\\app');
  assert.equal(layout.wslDataDir, 'D:\\AI-Runtime\\DockerDesktop\\wsl');
  assert.equal(layout.cacheDir, 'D:\\AI-Runtime\\.codebot-installer-cache');
});

test('Docker 安装目录拒绝相对路径、UNC 和 Windows 系统目录', () => {
  assert.throws(() => resolveStorageLayout('runtime'), /绝对路径/);
  assert.throws(() => resolveStorageLayout('\\\\server\\share'), /绝对路径/);
  assert.throws(() => resolveStorageLayout(`${process.env.WINDIR || 'C:\\Windows'}\\Temp`), /系统目录/);
});

test('状态检测同时扫描用户自选的 Desktop 与 CLI 路径', () => {
  const layout = resolveStorageLayout('D:\\AI-Runtime');
  assert.equal(
    dockerDesktopCandidates(layout).includes('D:\\AI-Runtime\\DockerDesktop\\app\\Docker Desktop.exe'),
    true,
  );
  assert.equal(
    dockerCliCandidates(layout).includes('D:\\AI-Runtime\\DockerDesktop\\app\\resources\\bin\\docker.exe'),
    true,
  );
});

test('已安装未运行时只启动现有 Docker Desktop，不进入安装流程', async () => {
  const calls = [];
  let statusReadCount = 0;
  const result = await startInstalledDockerDesktop({}, (progress) => calls.push({ progress }), {
    getDockerInstallerStatus: async () => {
      statusReadCount += 1;
      return statusReadCount === 1
        ? {
          supported: true,
          installed: true,
          ready: false,
          desktopExecutable: 'D:\\Docker\\Docker Desktop.exe',
          installLocation: 'D:\\Docker',
        }
        : { supported: true, installed: true, ready: true };
    },
    startDockerDesktop: async (layout, onProgress, executable) => {
      calls.push({ layout, executable });
      onProgress({ phase: 'starting', percent: 92, message: '正在启动 Docker Desktop' });
      return { ready: true, executable: 'D:\\Docker\\resources\\bin\\docker.exe' };
    },
  });

  assert.equal(result.success, true);
  assert.equal(result.alreadyInstalled, true);
  assert.equal(calls.find((item) => item.executable)?.executable, 'D:\\Docker\\Docker Desktop.exe');
  assert.equal(calls.some((item) => item.progress?.phase === 'install'), false);
  assert.equal(calls.some((item) => item.progress?.phase === 'download'), false);
});

test('启动接口在 Docker Desktop 未安装时拒绝伪装成安装操作', async () => {
  await assert.rejects(
    startInstalledDockerDesktop({}, null, {
      getDockerInstallerStatus: async () => ({
        supported: true,
        installed: false,
        ready: false,
        desktopExecutable: '',
      }),
    }),
    /尚未检测到 Docker Desktop/,
  );
});

test('可以从 Windows 卸载注册表输出解析 Docker 自定义安装位置', () => {
  const output = [
    'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Docker Desktop',
    '    InstallLocation    REG_SZ    D:\\RJ\\docker\\DockerDesktop\\app',
  ].join('\r\n');
  assert.equal(
    parseRegistryStringValue(output, 'InstallLocation'),
    'D:\\RJ\\docker\\DockerDesktop\\app',
  );
});

test('Docker 临时 socket 恢复路径被固定在 LOCALAPPDATA 的两组运行目录', () => {
  const paths = dockerRuntimeRecoveryPaths('C:\\Users\\tester\\AppData\\Local');
  assert.equal(paths.runtimeDir, 'C:\\Users\\tester\\AppData\\Local\\Docker\\run');
  assert.equal(
    paths.ingestSocket,
    'C:\\Users\\tester\\AppData\\Local\\Docker\\run\\sailor-ingest.sock',
  );
  assert.equal(
    paths.backendLog,
    'C:\\Users\\tester\\AppData\\Local\\Docker\\log\\host\\com.docker.backend.exe.log',
  );
  assert.equal(
    paths.secretsEngineSocket,
    'C:\\Users\\tester\\AppData\\Local\\docker-secrets-engine\\engine.sock',
  );
  assert.equal(dockerRuntimeRecoveryPaths('relative\\path'), null);
});

test('仅识别 Docker 后端明确报告的 sailor ingest socket 故障', () => {
  const matching = 'starting services: initializing Ingest server: listening on unix://C:/Users/tester/AppData/Local/Docker/run/sailor-ingest.sock: remove failed: The file cannot be accessed by the system.';
  assert.equal(isDockerIngestSocketFailure(matching), true);
  assert.equal(isDockerIngestSocketFailure('Docker Engine timed out'), false);
  assert.equal(
    isDockerIngestSocketFailure('initializing Ingest server: unrelated.sock: The file cannot be accessed by the system.'),
    false,
  );
});

test('运行时故障分类只接受固定 Ingest 与 Secrets Engine 端点', () => {
  const paths = dockerRuntimeRecoveryPaths('C:\\Users\\tester\\AppData\\Local');
  const ingest = 'initializing Ingest server: listening on unix://C:/Users/tester/AppData/Local/Docker/run/sailor-ingest.sock: The file cannot be accessed by the system.';
  const secrets = 'initializing Secrets Engine: listening on unix://C:/Users/tester/AppData/Local/docker-secrets-engine/engine.sock: The file cannot be accessed by the system.';
  assert.equal(classifyDockerRuntimeSocketFailure(ingest, paths)?.kind, 'docker-run');
  assert.equal(classifyDockerRuntimeSocketFailure(secrets, paths)?.kind, 'docker-secrets-engine');
  assert.equal(
    classifyDockerRuntimeSocketFailure(
      'initializing Secrets Engine: unix://C:/Users/attacker/engine.sock: The file cannot be accessed by the system.',
      paths,
    ),
    null,
  );
});

test('Docker 后端日志时间只解析行首 ISO 时间戳', () => {
  assert.equal(
    dockerLogLineTimestamp('[2026-08-31T01:33:32.342334800Z][com.docker.backend.exe] backend crashed'),
    Date.parse('2026-08-31T01:33:32.342334800Z'),
  );
  assert.equal(Number.isNaN(dockerLogLineTimestamp('backend crashed without timestamp')), true);
});

test('当前启动不会复用同一日志文件里的历史崩溃行', () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codebot-docker-log-'));
  const logPath = path.join(temporaryRoot, 'backend.log');
  const startedAt = Date.now();
  try {
    const oldTimestamp = new Date(startedAt - 5_000).toISOString();
    const currentTimestamp = new Date(startedAt + 10).toISOString();
    fs.writeFileSync(logPath, [
      `[${oldTimestamp}][com.docker.backend.exe] backend crashed: sailor-ingest.sock`,
      `[${currentTimestamp}][com.docker.backend.exe] normal startup progress`,
    ].join('\n'));
    assert.match(readRecentDockerBackendError(logPath, startedAt), /normal startup progress/);
    assert.doesNotMatch(readRecentDockerBackendError(logPath, startedAt), /backend crashed/);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test('运行目录隔离仅接受白名单端点并保留旧目录', () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codebot-docker-runtime-'));
  try {
    const paths = dockerRuntimeRecoveryPaths(temporaryRoot);
    fs.mkdirSync(paths.runtimeDir, { recursive: true });
    fs.writeFileSync(path.join(paths.runtimeDir, 'sailor-ingest.sock'), '');
    const failure = classifyDockerRuntimeSocketFailure(
      `initializing Ingest server: ${paths.ingestSocket}: The file cannot be accessed by the system.`,
      paths,
    );
    const result = quarantineDockerRuntimeDirectory(failure, paths);
    assert.equal(fs.existsSync(paths.runtimeDir), true);
    assert.deepEqual(fs.readdirSync(paths.runtimeDir), []);
    assert.equal(fs.existsSync(path.join(result.quarantine, 'sailor-ingest.sock')), true);

    fs.writeFileSync(path.join(paths.runtimeDir, 'unexpected.db'), 'data');
    assert.throws(
      () => quarantineDockerRuntimeDirectory(failure, paths),
      /未识别项目/,
    );
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test('一次 Docker 崩溃会同时隔离 Ingest 与 Secrets Engine 两组临时端点', () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codebot-docker-endpoints-'));
  try {
    const runtimePaths = dockerRuntimeRecoveryPaths(temporaryRoot);
    fs.mkdirSync(runtimePaths.runtimeDir, { recursive: true });
    fs.mkdirSync(runtimePaths.secretsEngineDir, { recursive: true });
    fs.writeFileSync(path.join(runtimePaths.runtimeDir, 'sailor-ingest.sock'), '');
    fs.writeFileSync(path.join(runtimePaths.runtimeDir, 'dockerInference'), '');
    fs.writeFileSync(path.join(runtimePaths.secretsEngineDir, 'engine.sock'), '');

    const quarantined = quarantineDockerRuntimeEndpointDirectories(runtimePaths);
    assert.deepEqual(quarantined.map((item) => item.kind), ['docker-run', 'docker-secrets-engine']);
    assert.deepEqual(fs.readdirSync(runtimePaths.runtimeDir), []);
    assert.deepEqual(fs.readdirSync(runtimePaths.secretsEngineDir), []);
    assert.equal(quarantined.every((item) => fs.existsSync(item.quarantine)), true);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test('任一 Docker 临时目录含未知数据时两组目录都不移动', () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codebot-docker-endpoints-guard-'));
  try {
    const runtimePaths = dockerRuntimeRecoveryPaths(temporaryRoot);
    fs.mkdirSync(runtimePaths.runtimeDir, { recursive: true });
    fs.mkdirSync(runtimePaths.secretsEngineDir, { recursive: true });
    fs.writeFileSync(path.join(runtimePaths.runtimeDir, 'sailor-ingest.sock'), '');
    fs.writeFileSync(path.join(runtimePaths.secretsEngineDir, 'unexpected.db'), 'user-data');

    assert.throws(
      () => quarantineDockerRuntimeEndpointDirectories(runtimePaths),
      /未识别项目/,
    );
    assert.equal(fs.existsSync(path.join(runtimePaths.runtimeDir, 'sailor-ingest.sock')), true);
    assert.equal(fs.existsSync(path.join(runtimePaths.secretsEngineDir, 'unexpected.db')), true);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test('只在 24 小时内复用已下载的安装包缓存', () => {
  // 不能使用当前测试文件自身的 mtime：源码从其他机器、压缩包或 Git 检出时，
  // mtime 可能晚于本机时钟，导致“刚下载的缓存”被错误判成未来文件。改用本次
  // 测试创建并显式设置时间戳的临时文件，才能稳定验证 24 小时窗口逻辑。
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codebot-installer-cache-'));
  const installerCache = path.join(temporaryRoot, 'DockerDesktopInstaller.exe');
  const now = Date.now();
  try {
    fs.writeFileSync(installerCache, 'installer-cache');
    fs.utimesSync(installerCache, now / 1000, now / 1000);
    assert.equal(isRecentInstallerCache(installerCache, now), true);
    assert.equal(isRecentInstallerCache(installerCache, now + (25 * 60 * 60 * 1000)), false);
    assert.equal(isRecentInstallerCache(`${installerCache}.missing`, now), false);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test('签名校验通过进程环境变量传递路径，不再依赖 powershell -Command 的 $args', async () => {
  const calls = [];
  const signature = await verifyDockerAuthenticode(__filename, {
    runProcess: async (command, args, options) => {
      calls.push({ command, args, options });
      return {
        code: 0,
        stdout: JSON.stringify({
          Status: 'Valid',
          StatusMessage: 'Signature verified.',
          Subject: 'CN=Docker Inc, O=Docker Inc, C=US',
          Issuer: 'CN=DigiCert Test CA',
          Thumbprint: 'TEST-THUMBPRINT',
        }),
        stderr: '',
      };
    },
  });

  assert.equal(calls.length, 1);
  assert.match(calls[0].command, /WindowsPowerShell[\\/]v1\.0[\\/]powershell\.exe$/i);
  assert.equal(calls[0].args.includes(__filename), false);
  assert.equal(calls[0].args.at(-1).includes('$args[0]'), false);
  assert.match(calls[0].args.at(-1), /Import-Module Microsoft\.PowerShell\.Security/);
  assert.equal(calls[0].options.env.CODEBOT_DOCKER_INSTALLER_PATH, __filename);
  assert.equal(
    Object.keys(calls[0].options.env).some((key) => key.toLowerCase() === 'psmodulepath'),
    false,
  );
  assert.equal(signature.status, 'Valid');
  assert.match(signature.subject, /Docker Inc/);
});

test('签名有效但发布者不是 Docker Inc 时仍然失败关闭', async () => {
  await assert.rejects(
    verifyDockerAuthenticode(__filename, {
      runProcess: async () => ({
        code: 0,
        stdout: JSON.stringify({
          Status: 'Valid',
          StatusMessage: 'Signature verified.',
          Subject: 'CN=Unrelated Publisher, O=Unrelated Publisher, C=US',
        }),
        stderr: '',
      }),
    }),
    /签名者不是 Docker Inc/,
  );
});
