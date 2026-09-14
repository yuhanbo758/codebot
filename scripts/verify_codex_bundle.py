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
    # 仅确认 codex 子进程存活不足以证明 Codebot 的第三方模型链路被打包。
    # 直接检查最终可执行文件的 PYZ，而不是源码目录或开发环境的导入结果。
    from PyInstaller.archive.readers import CArchiveReader
    backend_bin = root / ("codebot-backend.exe" if os.name == "nt" else "codebot-backend")
    archive = CArchiveReader(str(backend_bin))
    pyz_name = next((name for name in archive.toc if name.endswith(".pyz")), None)
    if pyz_name is None:
        raise RuntimeError("后端产物缺少 PYZ 模块归档")
    bundled_modules = archive.open_embedded_archive(pyz_name).toc
    required_modules = {
        "core.codex_runtime", "core.codex_model_bridge", "core.model_route_registry",
        "core.rakazo_runtime", "api.routes.codex", "api.routes.rakazo",
        # 三种执行器的完全访问依赖共享配置、配置 API 和 OpenCode 事件流。
        "config", "api.routes.config", "core.opencode_ws",
        # 聊天提示词去重和日志隐私必须进入三平台正式后端，不能只在源码测试通过。
        "api.routes.chat", "api.routes.logs", "core.prompt_optimizer",
    }
    missing = required_modules.difference(bundled_modules)
    if missing:
        raise RuntimeError(f"后端产物缺少模型路由模块：{sorted(missing)}")
    print("Codebot bundled model routing modules verified")
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
