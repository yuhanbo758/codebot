"""Windows Codex 按需安装的来源、校验、解压与原子切换。"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("CODEBOT_DATA_DIR", tempfile.mkdtemp(prefix="codebot-codex-installer-tests-"))

from config import app_config, settings  # noqa: E402
from api.routes import codex as codex_routes  # noqa: E402
from core.codex_runtime import CodexRuntime  # noqa: E402
from utils import codex_installer as installer  # noqa: E402


class _Response(io.BytesIO):
    def __init__(self, content: bytes, url: str):
        super().__init__(content)
        self._url = url
        self.headers = {}

    def geturl(self):
        return self._url


class CodexInstallerTests(unittest.TestCase):
    def test_official_package_metadata_requires_matching_version_and_integrity(self):
        version = f"{installer.CODEX_RUNTIME_VERSION}-win32-x64"
        tarball = f"https://registry.npmjs.org/@openai/codex/-/codex-{version}.tgz"
        expected = hashlib.sha512(b"official archive").digest()
        metadata = {"name": "@openai/codex", "version": version, "dist": {
            "tarball": tarball, "integrity": "sha512-" + base64.b64encode(expected).decode(),
        }}
        with patch.object(installer, "_open_official", return_value=_Response(json.dumps(metadata).encode(), tarball)):
            self.assertEqual(installer._package_metadata("win32-x64"), (tarball, expected))
        metadata["dist"]["tarball"] = "https://example.com/codex.tgz"
        with patch.object(installer, "_open_official", return_value=_Response(json.dumps(metadata).encode(), tarball)):
            with self.assertRaisesRegex(RuntimeError, "官方 npm registry"):
                installer._package_metadata("win32-x64")

    def test_download_rejects_bad_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "codex.tgz"
            with patch.object(installer, "_open_official", return_value=_Response(b"wrong", "https://registry.npmjs.org/test")):
                with self.assertRaisesRegex(RuntimeError, "SHA-512"):
                    installer._download_verified("https://registry.npmjs.org/test", b"0" * 64, archive)

    def test_download_reports_progress_after_integrity_checked_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "codex.tgz"
            response = _Response(b"archive", "https://registry.npmjs.org/test")
            response.headers["Content-Length"] = "7"
            updates = []
            with patch.object(installer, "_open_official", return_value=response):
                installer._download_verified(
                    "https://registry.npmjs.org/test", hashlib.sha512(b"archive").digest(),
                    archive, lambda downloaded, total: updates.append((downloaded, total)),
                )
            self.assertEqual(archive.read_bytes(), b"archive")
            self.assertEqual(updates, [(7, 7)])

    def test_archive_rejects_path_escape_and_extracts_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "codex.tgz"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("package/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
                member.size = 3
                bundle.addfile(member, io.BytesIO(b"exe"))
            installer._extract_archive(archive, root / "safe")
            self.assertEqual((root / "safe/vendor/x86_64-pc-windows-msvc/bin/codex.exe").read_bytes(), b"exe")

            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("package/../outside")
                member.size = 3
                bundle.addfile(member, io.BytesIO(b"bad"))
            with self.assertRaisesRegex(RuntimeError, "非法路径"):
                installer._extract_archive(archive, root / "safe")
            self.assertFalse((root / "outside").exists())

    def test_install_promotes_verified_package_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            def fake_extract(_archive, package_root):
                binary = package_root / "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"verified")

            with (
                patch.object(settings, "BASE_DIR", Path(temporary)),
                patch.object(installer, "_target", return_value=("win32-x64", "x86_64-pc-windows-msvc")),
                patch.object(installer, "_package_metadata", return_value=("https://registry.npmjs.org/test", b"0" * 64)) as metadata,
                patch.object(installer, "_download_verified"),
                patch.object(installer, "_extract_archive", side_effect=fake_extract),
                patch.object(installer, "_verify_binary") as verify,
                patch.object(installer, "_verify_app_server") as verify_server,
            ):
                first = installer.ensure_managed_codex()
                self.assertEqual(first.read_bytes(), b"verified")
                self.assertEqual(installer.ensure_managed_codex(), first)
                self.assertEqual(metadata.call_count, 1)
                self.assertEqual(verify.call_count, 2)
                verify_server.assert_called_once()

    def test_reuses_existing_npm_upgrade_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            nested = (Path(temporary) / "codex-runtime" / installer.CODEX_RUNTIME_VERSION
                      / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai"
                      / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe")
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"existing")
            with (
                patch.object(settings, "BASE_DIR", Path(temporary)),
                patch.object(installer, "_target", return_value=("win32-x64", "x86_64-pc-windows-msvc")),
                patch.object(installer, "_verify_binary") as verify,
                patch.object(installer, "_package_metadata") as metadata,
            ):
                self.assertTrue(installer.managed_runtime_installed())
                self.assertEqual(installer.ensure_managed_codex(), nested)
                verify.assert_called_once_with(nested)
                metadata.assert_not_called()

    def test_packaged_runtime_uses_managed_binary_and_skips_first_launch_prewarm(self):
        runtime = object.__new__(CodexRuntime)
        runtime._opencode_models = {}
        runtime._bridge_token = "test-token"
        runtime._install_progress = {"installing": False, "downloadedBytes": 0, "totalBytes": 0}
        expected = Path("C:/Codebot/codex.exe")
        with (
            patch.object(CodexRuntime, "_sdk_imports", return_value={"CodexConfig": lambda **options: options}),
            patch.object(installer, "uses_managed_runtime", return_value=True),
            patch.object(installer, "ensure_managed_codex", return_value=expected) as ensure,
        ):
            config = runtime._build_sdk_config()
        self.assertEqual(config["codex_bin"], str(expected))
        ensure.assert_called_once()

        async def verify_prewarm():
            with (
                patch.object(app_config.codex, "enabled", True),
                patch.object(app_config.codex, "auto_start", True),
                patch.object(codex_routes.codex_runtime, "status", return_value={"runtimeInstallRequired": True}),
                patch.object(codex_routes.codex_runtime, "start") as start,
            ):
                self.assertFalse(await codex_routes.prepare_if_enabled())
                start.assert_not_called()

        asyncio.run(verify_prewarm())


if __name__ == "__main__":
    unittest.main()
