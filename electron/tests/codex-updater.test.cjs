const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { managedBinary } = require('../codex-updater');

test('managedBinary finds the official platform package under the Codex npm package', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codebot-codex-updater-'));
  try {
    const binary = path.join(root, 'node_modules', '@openai', 'codex', 'node_modules',
      '@openai', 'codex-win32-x64', 'vendor', 'x86_64-pc-windows-msvc', 'bin', 'codex.exe');
    fs.mkdirSync(path.dirname(binary), { recursive: true });
    fs.writeFileSync(binary, 'test');
    assert.equal(managedBinary(root, 'win32', 'x64'), binary);
    assert.throws(() => managedBinary(root, 'plan9', 'x64'), /不支持/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
