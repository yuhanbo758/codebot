"""为 Windows 打包版按需安装与 Codex SDK 匹配的官方 CLI。"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.error import URLError
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


def _local_codex_candidates() -> list[Path]:
    """只选原生 exe；npm 的 cmd/ps1 启动脚本不能直接交给 SDK。"""
    platform_name, triple = _target()
    prefixes = []
    if os.environ.get("APPDATA"):
        prefixes.append(Path(os.environ["APPDATA"]) / "npm")
    if os.environ.get("NPM_CONFIG_PREFIX"):
        prefixes.append(Path(os.environ["NPM_CONFIG_PREFIX"]))
    prefixes.extend(Path(entry.strip().strip('"')) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry.strip())
    package_suffix = Path("vendor") / triple / "bin" / "codex.exe"
    candidates = []
    for prefix in prefixes:
        package_dir = prefix / "node_modules" / "@openai"
        candidates.extend((
            package_dir / "codex" / "node_modules" / "@openai" / f"codex-{platform_name}" / package_suffix,
            package_dir / f"codex-{platform_name}" / package_suffix,
        ))
    candidates.extend(prefix / "codex.exe" for prefix in prefixes)
    if os.environ.get("LOCALAPPDATA"):
        desktop_bin = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin"
        candidates.extend(desktop_bin.glob("*/codex.exe"))
    seen = set()
    found = []
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen and candidate.is_file():
            seen.add(key)
            found.append(candidate)
    return found


def local_runtime_detected() -> bool:
    """状态接口只探测文件，不在页面刷新时启动外部程序。"""
    return bool(_local_codex_candidates())


def _verify_local_candidate(binary: Path) -> None:
    result = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=15,
        check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    match = re.fullmatch(r"codex-cli ([^\s]+)", result.stdout.strip())
    if match is None:
        raise RuntimeError("本机 Codex CLI 未返回可识别的版本")
    from openai_codex._runtime_requirements import require_runtime_version
    from openai_codex.client import CodexClient, CodexConfig

    require_runtime_version(match.group(1))
    # SDK 握手是兼容性门禁；仅能执行 --version 的旧 CLI 不能直接启用。
    client = CodexClient(config=CodexConfig(codex_bin=str(binary), experimental_api=True))
    try:
        client.start()
        client.initialize()
    finally:
        client.close()


def _official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != _REGISTRY_HOST
            or parsed.port is not None or parsed.username or parsed.password):
        raise RuntimeError("Codex CLI 下载地址不是官方 npm registry")
    return url


def _open_official(url: str, *, range_start: int = 0):
    headers = {"User-Agent": "Codebot-Codex-Installer"}
    if range_start:
        headers["Range"] = f"bytes={range_start}-"
    response = urlopen(Request(_official_url(url), headers=headers), timeout=30)
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
    expected_size = 0
    started = time.monotonic()
    with archive.open("w+b") as output:
        for attempt in range(12):
            try:
                with _open_official(tarball, range_start=total) as response:
                    status = getattr(response, "status", 200)
                    if total and status == 206:
                        content_range = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)",
                            str(response.headers.get("Content-Range") or ""),
                        )
                        if (not content_range or int(content_range.group(1)) != total
                                or int(content_range.group(2)) < total
                                or int(content_range.group(2)) >= int(content_range.group(3))
                                or (expected_size and int(content_range.group(3)) != expected_size)):
                            raise RuntimeError("Codex CLI 续传响应的起始位置不匹配")
                        expected_size = int(content_range.group(3))
                    elif total and status == 200:
                        # 官方源未接受 Range 时从头重算哈希，绝不拼接两份响应。
                        output.seek(0)
                        output.truncate()
                        digest = hashlib.sha512()
                        total = 0
                        expected_size = int(response.headers.get("Content-Length") or 0)
                    elif status == 200:
                        expected_size = int(response.headers.get("Content-Length") or 0)
                    else:
                        raise RuntimeError(f"Codex CLI 下载返回异常状态：{status}")
                    if expected_size > _MAX_ARCHIVE_BYTES:
                        raise RuntimeError("Codex CLI 下载超过大小限制")
                    while chunk := response.read(64 * 1024):
                        if total + len(chunk) > _MAX_ARCHIVE_BYTES or (expected_size and total + len(chunk) > expected_size):
                            raise RuntimeError("Codex CLI 下载超过大小限制")
                        output.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                        if time.monotonic() - started > 60 * 60:
                            raise RuntimeError("Codex CLI 下载超时")
                        if progress:
                            progress(total, expected_size)
                    if expected_size and total != expected_size:
                        raise http.client.IncompleteRead(b"", expected_size - total)
                break
            except (OSError, URLError, http.client.HTTPException) as exc:
                if expected_size and total == expected_size:
                    output.flush()
                    if archive.stat().st_size == expected_size and digest.digest() == expected_digest:
                        break
                if attempt == 11 or time.monotonic() - started > 60 * 60:
                    raise RuntimeError(f"Codex CLI 下载多次中断：{exc}") from exc
                time.sleep(min(2 ** attempt, 8))
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


def resolve_codex_runtime() -> Path:
    """只复用已有的兼容 CLI；普通 Codex 请求绝不触发下载。"""
    installed = _existing_codex_path()
    failure = ""
    if installed:
        try:
            _verify_binary(installed)
            return installed
        except Exception as exc:
            # 受管安装损坏时仍可尝试用户本机其他 CLI。
            failure = str(exc)
    for candidate in _local_codex_candidates():
        try:
            _verify_local_candidate(candidate)
            return candidate
        except Exception as exc:
            # 下一个本机候选仍可能可用。
            failure = str(exc)
            continue
    if failure:
        raise CodexInstallError(
            f"检测到 Codex CLI，但版本或 App Server 不兼容：{failure}。"
            "请更新本机 CLI，或到设置 → Codex 点击一键下载。"
        )
    raise CodexInstallError("未找到兼容的本机 Codex CLI。请到设置 → Codex 点击一键下载，或指定自定义 CLI。")


def install_managed_codex(progress: Optional[ProgressCallback] = None) -> Path:
    """仅供用户主动操作：校验已有受管程序或下载官方固定版本。"""
    try:
        return _install_managed_codex(progress)
    except Exception as exc:
        raise CodexInstallError(f"Codex CLI 安装失败：{exc}。请检查网络并在设置 → Codex 重试。") from exc


def _install_managed_codex(progress: Optional[ProgressCallback]) -> Path:
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
