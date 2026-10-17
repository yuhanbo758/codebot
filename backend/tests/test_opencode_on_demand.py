"""Windows 瘦身包的下载完整性与本机 CLI 复用。"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-on-demand-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)

from api.routes import config as config_routes  # noqa: E402
from api.routes import chat  # noqa: E402
from utils import installer  # noqa: E402
from utils import opencode_installer  # noqa: E402


def _archive(path: Path, *, bad_link=False):
    with tarfile.open(path, "w:gz") as bundle:
        metadata = json.dumps({"name": "opencode-windows-x64", "version": opencode_installer.OPENCODE_VERSION}).encode()
        info = tarfile.TarInfo("package/package.json")
        info.size = len(metadata)
        bundle.addfile(info, io.BytesIO(metadata))
        info = tarfile.TarInfo("package/bin/opencode.exe")
        if bad_link:
            info.type = tarfile.SYMTYPE
            info.linkname = "../../other.exe"
            bundle.addfile(info)
        else:
            info.size = 6
            bundle.addfile(info, io.BytesIO(b"MZtest"))


class OnDemandInstallerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows managed CLI")
    def test_managed_cli_is_discovered_after_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "opencode.exe"
            binary.write_bytes(b"MZtest")
            with patch.object(opencode_installer, "managed_opencode_path", return_value=binary):
                self.assertIn([str(binary)], installer.collect_opencode_commands())

    def test_native_package_extracts_only_verified_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "opencode.tgz"
            target = Path(temporary) / "opencode.exe"
            _archive(archive)
            opencode_installer._extract_binary(archive, target, "opencode-windows-x64")
            self.assertEqual(target.read_bytes(), b"MZtest")
            _archive(archive, bad_link=True)
            with self.assertRaisesRegex(RuntimeError, "链接或特殊文件"):
                opencode_installer._extract_binary(archive, target, "opencode-windows-x64")

    def test_download_requires_matching_sha512(self):
        payload = b"fake archive"
        response = io.BytesIO(payload)
        response.headers = {"Content-Length": str(len(payload))}
        response.geturl = lambda: "https://registry.npmjs.org/opencode-windows-x64/-/pkg.tgz"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "download.tgz"
            with patch.object(opencode_installer, "_open", return_value=response):
                opencode_installer._download("https://registry.npmjs.org/pkg", hashlib.sha512(payload).digest(), target)
            self.assertEqual(target.read_bytes(), payload)
            response2 = io.BytesIO(payload)
            response2.headers = {"Content-Length": str(len(payload))}
            with patch.object(opencode_installer, "_open", return_value=response2):
                with self.assertRaisesRegex(RuntimeError, "SHA-512"):
                    opencode_installer._download("https://registry.npmjs.org/pkg", b"0" * 64, target)


class OnDemandRouteTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.name == "nt", "Windows one-click download")
    async def test_missing_cli_downloads_only_after_explicit_request(self):
        old_client = chat.opencode_ws
        chat.opencode_ws = SimpleNamespace(connected=True)
        try:
            with (
                patch.object(config_routes, "_probe_opencode_cli", return_value=None),
                patch.object(chat, "_ensure_opencode_client_connected", new=AsyncMock(return_value=True)),
                patch.object(opencode_installer, "install_managed_opencode", return_value=Path("managed.exe")) as download,
            ):
                await config_routes.opencode_runtime_status()
                download.assert_not_called()
                await config_routes.install_opencode_runtime()
                download.assert_called_once()
        finally:
            chat.opencode_ws = old_client

    async def test_existing_local_cli_starts_without_download(self):
        old_client = chat.opencode_ws
        chat.opencode_ws = SimpleNamespace(connected=True)
        try:
            with (
                patch.object(config_routes, "_probe_opencode_cli", return_value={"path": "local.exe", "version": "1.19.0", "source": "local"}),
                patch.object(chat, "_ensure_opencode_client_connected", new=AsyncMock(return_value=True)) as connect,
                patch.object(opencode_installer, "install_managed_opencode") as download,
            ):
                result = await config_routes.install_opencode_runtime()
            self.assertTrue(result["data"]["serverConnected"])
            connect.assert_awaited_once()
            download.assert_not_called()
        finally:
            chat.opencode_ws = old_client


if __name__ == "__main__":
    unittest.main()
