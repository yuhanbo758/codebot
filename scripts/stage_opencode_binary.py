"""为 Electron 发布包准备并验证当前平台的 OpenCode 原生可执行文件。"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


OPENCODE_VERSION = "1.18.32"
ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "electron" / "vendor" / "opencode" / "native"
EXECUTABLE = "opencode.exe" if os.name == "nt" else "opencode"
# opencode-ai 的 postinstall 在所有平台都写入 bin/opencode.exe；只有发布目标名按平台区分。
SOURCE_EXECUTABLE = "opencode.exe"


def verify_binary(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"OpenCode binary missing: {path}")
    with path.open("rb") as binary:
        signature = binary.read(4)
    if os.name == "nt":
        expected = b"MZ"
    elif sys.platform == "darwin":
        expected = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
    else:
        expected = b"\x7fELF"
    valid = signature.startswith(expected) if isinstance(expected, bytes) else signature in expected
    if not valid:
        raise RuntimeError(f"OpenCode binary is not native for this platform: {path}")
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=20, check=True)
    actual = result.stdout.strip()
    if actual != OPENCODE_VERSION:
        raise RuntimeError(f"OpenCode version mismatch: expected {OPENCODE_VERSION}, got {actual}")


def stage_binary() -> Path:
    # 固定运行时版本；模型目录由用户手动刷新时从 models.dev 更新。
    with tempfile.TemporaryDirectory(prefix="codebot-opencode-") as temporary:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        subprocess.run(
            [npm, "install", "--prefix", temporary, f"opencode-ai@{OPENCODE_VERSION}",
             "--no-save", "--no-package-lock", "--no-audit", "--no-fund"],
            check=True,
            timeout=300,
        )
        source = Path(temporary) / "node_modules" / "opencode-ai" / "bin" / SOURCE_EXECUTABLE
        verify_binary(source)
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        target = TARGET_DIR / EXECUTABLE
        shutil.copy2(source, target)
        verify_binary(target)
        return target


def find_packaged_binary(root: Path, platform_name: str) -> Path:
    patterns = {
        "win32": "win*-unpacked/resources/opencode/opencode.exe",
        "darwin": "mac*/Codebot.app/Contents/Resources/opencode/opencode",
        "linux": "linux*-unpacked/resources/opencode/opencode",
    }
    pattern = patterns.get(platform_name)
    if not pattern:
        raise RuntimeError(f"Unsupported packaging platform: {platform_name}")
    matches = list(root.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one packaged OpenCode binary matching {pattern}, found {len(matches)}")
    return matches[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--verify-path", type=Path)
    parser.add_argument("--verify-packaged-root", type=Path)
    options = parser.parse_args()
    if options.verify_packaged_root:
        binary = find_packaged_binary(options.verify_packaged_root, sys.platform)
    else:
        binary = options.verify_path or (TARGET_DIR / EXECUTABLE if options.verify_only else stage_binary())
    verify_binary(binary)
    print(f"OpenCode {OPENCODE_VERSION} binary verified: {ascii(str(binary))}")
