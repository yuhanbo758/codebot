"""验证 PyInstaller 产物内 Codex binary 存在且 App Server 可以保持运行。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_codex_bundle.py <pyinstaller-dist-dir>")
    root = Path(sys.argv[1]).resolve()
    candidates = [
        path
        for path in root.rglob("codex.exe" if os.name == "nt" else "codex")
        if path.is_file() and "codex_cli_bin" in {part.lower() for part in path.parts}
    ]
    if not candidates:
        raise RuntimeError(f"打包产物内未找到 Codex runtime：{root}")
    codex_bin = candidates[0]

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(codex_bin), "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )
    try:
        time.sleep(1.5)
        if process.poll() is not None:
            stderr = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"Codex App Server 提前退出，code={process.returncode}：{stderr[-2000:]}")
        print(f"Codex bundled runtime verified: {codex_bin}")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
