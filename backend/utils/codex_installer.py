"""为 Windows 打包版按需安装与 Codex SDK 匹配的官方 CLI。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config import settings


# 更新 SDK 时必须同步更新此版本；发布门禁会核对 backend/requirements.txt。
CODEX_RUNTIME_VERSION = "0.158.0"
_REGISTRY_HOST = "registry.npmjs.org"
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
ProgressCallback = Callable[[int, int], None]
_TARGETS = {
    "AMD64": ("win32-x64", "x86_64-pc-windows-msvc"),
    "X86_64": ("win32-x64", "x86_64-pc-windows-msvc"),
    "ARM64": ("win32-arm64", "aarch64-pc-windows-msvc"),
    "AARCH64": ("win32-arm64", "aarch64-pc-windows-msvc"),
}


class CodexInstallError(RuntimeError):
    """官方 CLI 下载或校验失败，可在检查网络后重试。"""


def uses_managed_runtime() -> bool:
    """源码开发仍使用 SDK 自带 CLI；仅 Windows 冻结程序需要按需下载。"""
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _target() -> tuple[str, str]:
    import platform

    target = _TARGETS.get(platform.machine().upper())
    if target is None:
        raise RuntimeError(f"当前 Windows 架构不支持 Codex CLI：{platform.machine()}")
    return target


def managed_codex_path(version: str = CODEX_RUNTIME_VERSION) -> Path:
    platform_name, triple = _target()
    return (settings.BASE_DIR / "codex-runtime" / version / "node_modules" / "@openai"
            / f"codex-{platform_name}" / "vendor" / triple / "bin" / "codex.exe")


def _existing_codex_path() -> Optional[Path]:
    """同时识别原有 npm 升级器可能生成的嵌套包布局。"""
    platform_name, triple = _target()
    package_dir = (settings.BASE_DIR / "codex-runtime" / CODEX_RUNTIME_VERSION
                   / "node_modules" / "@openai")
    direct = package_dir / f"codex-{platform_name}" / "vendor" / triple / "bin" / "codex.exe"
    nested = (package_dir / "codex" / "node_modules" / "@openai"
              / f"codex-{platform_name}" / "vendor" / triple / "bin" / "codex.exe")
    for candidate in (direct, nested):
        if candidate.is_file():
            return candidate
    return None


def managed_runtime_installed() -> bool:
    return _existing_codex_path() is not None


def _official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != _REGISTRY_HOST
            or parsed.port is not None or parsed.username or parsed.password):
        raise RuntimeError("Codex CLI 下载地址不是官方 npm registry")
    return url


def _open_official(url: str):
    response = urlopen(Request(_official_url(url), headers={"User-Agent": "Codebot-Codex-Installer"}), timeout=30)
    try:
        _official_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def _package_metadata(platform_name: str) -> tuple[str, bytes]:
    package_version = f"{CODEX_RUNTIME_VERSION}-{platform_name}"
    metadata_url = f"https://{_REGISTRY_HOST}/@openai%2fcodex/{package_version}"
    with _open_official(metadata_url) as response:
        raw = response.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise RuntimeError("Codex CLI 包元数据过大")
    metadata = json.loads(raw)
    if metadata.get("name") != "@openai/codex" or metadata.get("version") != package_version:
        raise RuntimeError("官方 registry 返回了不匹配的 Codex CLI 包")
    dist = metadata.get("dist") or {}
    tarball = _official_url(str(dist.get("tarball") or ""))
    expected_path = f"/@openai/codex/-/codex-{package_version}.tgz"
    if urlparse(tarball).path != expected_path:
        raise RuntimeError("Codex CLI 包地址与固定版本不一致")
    integrity = str(dist.get("integrity") or "")
    if not integrity.startswith("sha512-"):
        raise RuntimeError("Codex CLI 包缺少 SHA-512 完整性信息")
    expected_digest = base64.b64decode(integrity[7:], validate=True)
    if len(expected_digest) != hashlib.sha512().digest_size:
        raise RuntimeError("Codex CLI 包的 SHA-512 摘要无效")
    return tarball, expected_digest


def _download_verified(tarball: str, expected_digest: bytes, archive: Path,
                       progress: Optional[ProgressCallback] = None) -> None:
    digest = hashlib.sha512()
    total = 0
    started = time.monotonic()
    with _open_official(tarball) as response, archive.open("wb") as output:
        expected_size = int(response.headers.get("Content-Length") or 0)
        while chunk := response.read(64 * 1024):
            total += len(chunk)
            if total > _MAX_ARCHIVE_BYTES:
                raise RuntimeError("Codex CLI 下载超过大小限制")
            if time.monotonic() - started > 60 * 60:
                raise RuntimeError("Codex CLI 下载超时")
            digest.update(chunk)
            output.write(chunk)
            if progress:
                progress(total, expected_size)
    if total == 0 or digest.digest() != expected_digest:
        raise RuntimeError("Codex CLI 下载文件的 SHA-512 校验失败")


def _extract_archive(archive: Path, package_root: Path) -> None:
    total = 0
    count = 0
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            if "\\" in member.name or "\x00" in member.name:
                raise RuntimeError("Codex CLI 压缩包包含非法路径")
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != "package"
                    or any(part in {"", ".", ".."} or ":" in part for part in parts)):
                raise RuntimeError("Codex CLI 压缩包包含非法路径")
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError("Codex CLI 压缩包包含不支持的链接或特殊文件")
            if member.size < 0:
                raise RuntimeError("Codex CLI 压缩包包含非法文件大小")
            count += 1
            if count > 1000:
                raise RuntimeError("Codex CLI 压缩包文件数超过限制")
            total += member.size
            if total > _MAX_UNPACKED_BYTES:
                raise RuntimeError("Codex CLI 解压内容超过大小限制")
            relative = Path(*parts[1:])
            if not relative.parts:
                continue
            target = package_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError("Codex CLI 压缩包文件读取失败")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _verify_binary(binary: Path) -> None:
    if not binary.is_file():
        raise RuntimeError("官方 Codex CLI 包缺少 codex.exe")
    result = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=60,
        check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.stdout.strip() != f"codex-cli {CODEX_RUNTIME_VERSION}":
        raise RuntimeError(f"Codex CLI 版本校验失败：{result.stdout.strip()}")


def _verify_app_server(binary: Path) -> None:
    process = subprocess.Popen(
        [str(binary), "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        time.sleep(1.5)
        if process.poll() is not None:
            details = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"Codex App Server 启动失败：{details[-1000:]}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def ensure_managed_codex(progress: Optional[ProgressCallback] = None) -> Path:
    """校验已有程序，或下载到临时目录后原子切换到 Codebot 用户目录。"""
    try:
        return _ensure_managed_codex(progress)
    except Exception as exc:
        raise CodexInstallError(f"Codex CLI 安装失败：{exc}。请检查网络或到设置 → Codex 重试。") from exc


def _ensure_managed_codex(progress: Optional[ProgressCallback]) -> Path:
    installed = _existing_codex_path()
    if installed:
        _verify_binary(installed)
        return installed
    binary = managed_codex_path()
    platform_name, triple = _target()
    version_root = settings.BASE_DIR / "codex-runtime" / CODEX_RUNTIME_VERSION
    version_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-download-", dir=version_root.parent) as temporary:
        temporary_root = Path(temporary)
        tarball, expected_digest = _package_metadata(platform_name)
        archive = temporary_root / "codex.tgz"
        _download_verified(tarball, expected_digest, archive, progress)
        package_root = (temporary_root / "staged" / "node_modules" / "@openai"
                        / f"codex-{platform_name}")
        _extract_archive(archive, package_root)
        staged_binary = package_root / "vendor" / triple / "bin" / "codex.exe"
        _verify_binary(staged_binary)
        _verify_app_server(staged_binary)
        if version_root.exists():
            # 另一个进程可能已安装同版本；不要覆盖运行中的程序。
            installed = _existing_codex_path()
            if installed:
                _verify_binary(installed)
                return installed
            raise RuntimeError(f"版本目录 {version_root} 已存在但缺少 CLI，请在设置中选择自定义 CLI，或清理损坏的版本目录后重试")
        os.replace(temporary_root / "staged", version_root)
    return binary
