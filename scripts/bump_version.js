const fs = require('fs');
const path = require('path');

const rootDir = path.resolve(__dirname, '..');

const files = [
  {
    path: 'backend/__init__.py',
    update: (content, version) => content.replace(/__version__\s*=\s*"[^"]+"/, `__version__ = "${version}"`),
  },
  {
    path: 'backend/config.py',
    update: (content, version) => content.replace(/version:\s*str\s*=\s*"[^"]+"/, `version: str = "${version}"`),
  },
  {
    path: 'frontend/package.json',
    update: (content, version) => updateJsonVersion(content, version),
  },
  {
    path: 'frontend/package-lock.json',
    update: (content, version) => updateJsonVersion(content, version),
  },
  {
    path: 'electron/package.json',
    update: (content, version) => updateJsonVersion(content, version),
  },
  {
    path: 'electron/package-lock.json',
    update: (content, version) => updateJsonVersion(content, version),
  },
];

function updateJsonVersion(content, version) {
  const data = JSON.parse(content);
  data.version = version;
  if (data.packages && data.packages['']) {
    data.packages[''].version = version;
  }
  return `${JSON.stringify(data, null, 2)}\n`;
}

function bumpMinor(version) {
  const parts = String(version || '').trim().split('.').map((part) => Number(part));
  if (parts.length !== 3 || parts.some((part) => !Number.isInteger(part) || part < 0)) {
    throw new Error(`Invalid semver version: ${version}`);
  }
  const [major, minor] = parts;
  if (minor >= 9) {
    return `${major + 1}.0.0`;
  }
  return `${major}.${minor + 1}.0`;
}

function resolveTargetVersion() {
  const explicitVersion = process.argv[2];
  const electronPackagePath = path.join(rootDir, 'electron', 'package.json');
  const currentVersion = JSON.parse(fs.readFileSync(electronPackagePath, 'utf8')).version;
  return explicitVersion ? String(explicitVersion).trim() : bumpMinor(currentVersion);
}

function main() {
  const version = resolveTargetVersion();

  for (const file of files) {
    const filePath = path.join(rootDir, file.path);
    const original = fs.readFileSync(filePath, 'utf8');
    const updated = file.update(original, version);
    if (updated === original) {
      throw new Error(`Version marker not updated in ${file.path}`);
    }
    fs.writeFileSync(filePath, updated, 'utf8');
  }

  process.stdout.write(`${version}\n`);
}

main();
