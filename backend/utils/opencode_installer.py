"""按需安装 Windows OpenCode 原生 CLI，不依赖全局 npm。"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import platform
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config import settings


OPENCODE_VERSION = "1.18.32"
_REGISTRY = "registry.npmjs.org"
_ARCHIVE_LIMIT = 256 * 1024 * 1024
_BINARY_LIMIT = 512 * 1024 * 1024
_PACKAGES = {
    "AMD64": "opencode-windows-x64",
    "X86_64": "opencode-windows-x64",
    "ARM64": "opencode-windows-arm64",
    "AARCH64": "opencode-windows-arm64",
}


class OpenCodeInstallError(RuntimeError):
    """下载、校验或启动失败。"""


def _package_name() -> str:
    name = _PACKAGES.get(platform.machine().upper())
    if os.name != "nt" or not name:
        raise RuntimeError("当前平台不支持按需安装 Windows OpenCode CLI")
    return name


def managed_opencode_path() -> Path:
    _package_name()
    return settings.BASE_DIR / "opencode-runtime" / OPENCODE_VERSION / "opencode.exe"


def _official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != _REGISTRY or parsed.port is not None
            or parsed.username or parsed.password or parsed.fragment):
        raise RuntimeError("OpenCode 下载地址不是官方 npm registry")
    return url


def _open(url: str, *, range_start: int = 0):
    headers = {"User-Agent": "Codebot-OpenCode-Installer"}
    if range_start:
        headers["Range"] = f"bytes={range_start}-"
    response = urlopen(Request(_official_url(url), headers=headers), timeout=30)
    try:
        _official_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def _metadata(package: str) -> tuple[str, bytes]:
    url = f"https://{_REGISTRY}/{package}/{OPENCODE_VERSION}"
    with _open(url) as response:
        raw = response.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise RuntimeError("OpenCode 包元数据过大")
    data = json.loads(raw)
    if data.get("name") != package or data.get("version") != OPENCODE_VERSION:
        raise RuntimeError("OpenCode 包名或版本不匹配")
    dist = data.get("dist") or {}
    tarball = _official_url(str(dist.get("tarball") or ""))
    if urlparse(tarball).path != f"/{package}/-/{package}-{OPENCODE_VERSION}.tgz":
        raise RuntimeError("OpenCode 包地址与固定版本不一致")
    integrity = str(dist.get("integrity") or "")
    if not integrity.startswith("sha512-"):
        raise RuntimeError("OpenCode 包缺少 SHA-512 校验值")
    digest = base64.b64decode(integrity[7:], validate=True)
    if len(digest) != hashlib.sha512().digest_size:
        raise RuntimeError("OpenCode 包校验值无效")
    return tarball, digest


def _download(url: str, expected_digest: bytes, archive: Path, progress=None) -> None:
    actual = hashlib.sha512()
    total = 0
    expected_size = 0
    started = time.monotonic()
    with archive.open("w+b") as output:
        for attempt in range(12):
            try:
                with _open(url, range_start=total) as response:
                    status = getattr(response, "status", 200)
                    if total and status == 206:
                        matched = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", str(response.headers.get("Content-Range") or ""))
                        if (not matched or int(matched.group(1)) != total
                                or int(matched.group(2)) < total
                                or int(matched.group(2)) >= int(matched.group(3))
                                or (expected_size and int(matched.group(3)) != expected_size)):
                            raise RuntimeError("OpenCode 续传响应不匹配")
                        expected_size = int(matched.group(3))
                    elif total and status == 200:
                        output.seek(0)
                        output.truncate()
                        actual = hashlib.sha512()
                        total = 0
                        expected_size = int(response.headers.get("Content-Length") or 0)
                    elif status == 200:
                        expected_size = int(response.headers.get("Content-Length") or 0)
                    else:
                        raise RuntimeError(f"OpenCode 下载返回异常状态：{status}")
                    if expected_size > _ARCHIVE_LIMIT:
                        raise RuntimeError("OpenCode 下载文件超过大小限制")
                    while chunk := response.read(64 * 1024):
                        total += len(chunk)
                        if total > _ARCHIVE_LIMIT or (expected_size and total > expected_size):
                            raise RuntimeError("OpenCode 下载文件超过大小限制")
                        output.write(chunk)
                        actual.update(chunk)
                        if time.monotonic() - started > 60 * 60:
                            raise RuntimeError("OpenCode 下载超时")
                        if progress:
                            progress(total, expected_size)
                    if expected_size and total != expected_size:
                        raise http.client.IncompleteRead(b"", expected_size - total)
                break
            except (OSError, http.client.HTTPException) as exc:
                if expected_size and total == expected_size:
                    output.flush()
                    if archive.stat().st_size == expected_size and actual.digest() == expected_digest:
                        break
                if attempt == 11 or time.monotonic() - started > 60 * 60:
                    raise RuntimeError(f"OpenCode 下载多次中断：{exc}") from exc
                time.sleep(min(2 ** attempt, 8))
    if total == 0 or actual.digest() != expected_digest:
        raise RuntimeError("OpenCode 下载文件 SHA-512 校验失败")


def _extract_binary(archive: Path, target: Path, package: str) -> None:
    metadata = None
    binary_found = False
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            if member.isdir() and member.name.rstrip("/") in {"package", "package/bin"}:
                continue
            if member.name not in {"package/package.json", "package/bin/opencode.exe"}:
                raise RuntimeError("OpenCode 包包含意外文件")
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError("OpenCode 包包含链接或特殊文件")
            if member.name == "package/package.json":
                if member.size > 64 * 1024:
                    raise RuntimeError("OpenCode 包元数据过大")
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("OpenCode 包元数据读取失败")
                with source:
                    metadata = json.loads(source.read(64 * 1024))
            else:
                if binary_found or member.size > _BINARY_LIMIT:
                    raise RuntimeError("OpenCode 程序重复或超过大小限制")
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("OpenCode 程序读取失败")
                with source, target.open("wb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(64 * 1024, remaining))
                        if not chunk:
                            raise RuntimeError("OpenCode 程序解压不完整")
                        output.write(chunk)
                        remaining -= len(chunk)
                binary_found = True
    if not binary_found or not isinstance(metadata, dict) or metadata.get("name") != package or metadata.get("version") != OPENCODE_VERSION:
        raise RuntimeError("OpenCode 原生包内容与固定版本不匹配")


def _verify_binary(binary: Path) -> None:
    if not binary.is_file():
        raise RuntimeError("OpenCode 原生程序缺失或格式错误")
    with binary.open("rb") as source:
        signature = source.read(2)
    if signature != b"MZ":
        raise RuntimeError("OpenCode 原生程序缺失或格式错误")
    result = subprocess.run(
        [str(binary), "--version"], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=30, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.stdout.strip() != OPENCODE_VERSION:
        raise RuntimeError(f"OpenCode 版本校验失败：{result.stdout.strip()}")


def install_managed_opencode(progress=None) -> Path:
    """只供设置页主动调用；完整校验成功后才公开新运行时。"""
    try:
        binary = managed_opencode_path()
        if binary.is_file():
            _verify_binary(binary)
            return binary
        package = _package_name()
        version_root = binary.parent
        version_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="opencode-download-", dir=version_root.parent) as temporary:
            temporary_root = Path(temporary)
            tarball, digest = _metadata(package)
            archive = temporary_root / "opencode.tgz"
            _download(tarball, digest, archive, progress)
            staged = temporary_root / "staged"
            staged.mkdir()
            staged_binary = staged / "opencode.exe"
            _extract_binary(archive, staged_binary, package)
            _verify_binary(staged_binary)
            if version_root.exists():
                # 并行请求不能覆盖正在使用的 CLI。
                _verify_binary(binary)
                return binary
            os.replace(staged, version_root)
        return binary
    except Exception as exc:
        raise OpenCodeInstallError(f"OpenCode CLI 安装失败：{exc}。请检查网络后在设置 → OpenCode 重试。") from exc
