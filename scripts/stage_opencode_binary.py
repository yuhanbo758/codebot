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


def verify_binary(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"OpenCode 发布文件不存在：{path}")
    with path.open("rb") as binary:
        signature = binary.read(4)
    if os.name == "nt":
        expected = b"MZ"
    elif sys.platform == "darwin":
        expected = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
    else:
        expected = b"\x7fELF"
    valid = signature.startswith(expected) if isinstance(expected, bytes) else signature in expected
    if not valid:
        raise RuntimeError(f"OpenCode 发布文件不是当前平台的原生可执行文件：{path}")
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=20, check=True)
    actual = result.stdout.strip()
    if actual != OPENCODE_VERSION:
        raise RuntimeError(f"OpenCode 发布版本不匹配：期望 {OPENCODE_VERSION}，实际 {actual}")


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
        source = Path(temporary) / "node_modules" / "opencode-ai" / "bin" / EXECUTABLE
        verify_binary(source)
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        target = TARGET_DIR / EXECUTABLE
        shutil.copy2(source, target)
        verify_binary(target)
        return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--verify-path", type=Path)
    options = parser.parse_args()
    binary = options.verify_path or (TARGET_DIR / EXECUTABLE if options.verify_only else stage_binary())
    verify_binary(binary)
    print(f"OpenCode {OPENCODE_VERSION} 发布文件已验证：{binary}")
