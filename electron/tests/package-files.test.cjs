const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const electronRoot = path.resolve(__dirname, '..');
const packageJson = JSON.parse(fs.readFileSync(path.join(electronRoot, 'package.json'), 'utf8'));
const packagedFiles = new Set(packageJson.build.files);

/**
 * 解析 CommonJS 本地依赖，并确认它们都明确进入 electron-builder 的 app.asar。
 * 这里只处理相对路径；Electron、Node 内置模块与 node_modules 由打包器单独管理。
 */
function collectLocalRequires(fileName) {
  const source = fs.readFileSync(path.join(electronRoot, fileName), 'utf8');
  const dependencies = [];
  const requirePattern = /require\(\s*['"](\.\.?\/[^'"]+)['"]\s*\)/g;
  for (const match of source.matchAll(requirePattern)) {
    const dependency = path.posix.normalize(path.posix.join(path.posix.dirname(fileName), match[1]));
    dependencies.push(path.posix.extname(dependency) ? dependency : `${dependency}.js`);
  }
  return dependencies;
}

test('Electron 主进程的本地 CommonJS 依赖全部包含在 app.asar 打包清单中', () => {
  const pending = [packageJson.main];
  const visited = new Set();

  while (pending.length > 0) {
    const current = pending.pop();
    if (visited.has(current)) continue;
    visited.add(current);

    assert.equal(
      packagedFiles.has(current),
      true,
      `${current} 被 Electron 主进程依赖，但未列入 build.files，安装后会出现 Cannot find module`,
    );

    for (const dependency of collectLocalRequires(current)) {
      assert.equal(fs.existsSync(path.join(electronRoot, dependency)), true, `本地依赖不存在：${dependency}`);
      pending.push(dependency);
    }
  }
});
