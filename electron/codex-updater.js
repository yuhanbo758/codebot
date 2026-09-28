const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const TARGETS = {
  'win32-x64': ['codex-win32-x64', 'x86_64-pc-windows-msvc'],
  'win32-arm64': ['codex-win32-arm64', 'aarch64-pc-windows-msvc'],
  'darwin-x64': ['codex-darwin-x64', 'x86_64-apple-darwin'],
  'darwin-arm64': ['codex-darwin-arm64', 'aarch64-apple-darwin'],
  'linux-x64': ['codex-linux-x64', 'x86_64-unknown-linux-musl'],
  'linux-arm64': ['codex-linux-arm64', 'aarch64-unknown-linux-musl'],
};

function npmInvocation() {
  if (process.platform !== 'win32') return { command: 'npm', prefixArgs: [] };
  // Windows 的 npm.cmd 需要 shell 才能 spawn；直接运行相邻 npm-cli.js，
  // 避免把用户目录拼进 cmd.exe 命令行。
  for (const directory of String(process.env.PATH || '').split(path.delimiter)) {
    if (!directory || !fs.existsSync(path.join(directory, 'npm.cmd'))) continue;
    const script = path.join(directory, 'node_modules', 'npm', 'bin', 'npm-cli.js');
    if (!fs.existsSync(script)) continue;
    const adjacentNode = path.join(directory, 'node.exe');
    return { command: fs.existsSync(adjacentNode) ? adjacentNode : 'node.exe', prefixArgs: [script] };
  }
  throw new Error('未找到 npm，请先安装 Node.js/npm');
}

function runCommand(command, args, { timeoutMs = 600000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      windowsHide: true,
      env: { ...process.env, npm_config_update_notifier: 'false' },
    });
    let output = '';
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; child.kill(); }, timeoutMs);
    child.stdout.on('data', (chunk) => { output = (output + chunk.toString()).slice(-4096); });
    child.stderr.on('data', () => {});
    child.on('error', (error) => { clearTimeout(timer); reject(error); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (timedOut) reject(new Error('Codex CLI 升级超时'));
      else if (code !== 0) reject(new Error(`Codex CLI 命令失败（退出码 ${code}）`));
      else resolve(output.trim());
    });
  });
}

function managedBinary(prefix, platform = process.platform, arch = process.arch) {
  const target = TARGETS[`${platform}-${arch}`];
  if (!target) throw new Error(`当前平台不支持 Codex CLI：${platform}/${arch}`);
  const [packageName, triple] = target;
  const binary = platform === 'win32' ? 'codex.exe' : 'codex';
  const packageRoot = path.join(prefix, 'node_modules', '@openai', 'codex');
  for (const root of [path.join(packageRoot, 'node_modules'), path.join(prefix, 'node_modules')]) {
    const candidate = path.join(root, '@openai', packageName, 'vendor', triple, 'bin', binary);
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error('官方 npm 包已安装，但未找到当前平台的 Codex CLI 原生程序');
}

async function upgradeManagedCodex(userDataDir) {
  const { command: npm, prefixArgs } = npmInvocation();
  const registry = '--registry=https://registry.npmjs.org/';
  let version;
  try {
    version = JSON.parse(await runCommand(npm, [...prefixArgs, 'view', '@openai/codex', 'version', '--json', registry], { timeoutMs: 30000 }));
  } catch (error) {
    throw new Error(`无法查询官方 Codex CLI 版本，请确认 npm 和网络可用：${error.message}`);
  }
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version)) {
    throw new Error('npm 返回的 Codex CLI 版本无效');
  }
  // 安装到版本目录；运行中的旧 CLI 和 Codebot 内置 runtime 均不会被覆盖。
  const prefix = path.join(userDataDir, 'codex-runtime', version);
  let binary;
  try { binary = managedBinary(prefix); } catch (_) { binary = ''; }
  if (!binary) {
    await runCommand(npm, [...prefixArgs,
      'install', '--prefix', prefix, `@openai/codex@${version}`,
      '--ignore-scripts', '--no-audit', '--no-fund', registry,
    ]);
    binary = managedBinary(prefix);
  }
  const reported = await runCommand(binary, ['--version'], { timeoutMs: 15000 });
  if (reported !== `codex-cli ${version}`) throw new Error(`Codex CLI 版本校验失败：${reported}`);
  return { version, codexBin: fs.realpathSync(binary) };
}

module.exports = { managedBinary, upgradeManagedCodex };
