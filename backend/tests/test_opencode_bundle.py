"""OpenCode 发布文件要按 npm 的跨平台目录合同收集。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import stage_opencode_binary as stage  # noqa: E402


class OpenCodeBundleTests(unittest.TestCase):
    def test_npm_postinstall_source_is_exe_on_every_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "native"

            def install(command, **_kwargs):
                prefix = Path(command[command.index("--prefix") + 1])
                source = prefix / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
                source.parent.mkdir(parents=True)
                source.write_bytes(b"native-binary")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(stage, "TARGET_DIR", target),
                patch.object(stage, "EXECUTABLE", "opencode"),
                patch.object(stage.subprocess, "run", side_effect=install),
                patch.object(stage, "verify_binary"),
            ):
                result = stage.stage_binary()
            self.assertEqual(result.name, "opencode")
            self.assertEqual(result.read_bytes(), b"native-binary")

    def test_packaged_path_matches_each_platform_layout(self):
        layouts = {
            "win32": "win-unpacked/resources/opencode/opencode.exe",
            "darwin": "mac-arm64/Codebot.app/Contents/Resources/opencode/opencode",
            "linux": "linux-unpacked/resources/opencode/opencode",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for platform_name, relative in layouts.items():
                candidate = root / relative
                candidate.parent.mkdir(parents=True)
                candidate.write_bytes(b"binary")
                self.assertEqual(stage.find_packaged_binary(root, platform_name), candidate)

    def test_shell_script_cannot_pass_native_binary_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "opencode.exe"
            fake.write_bytes(b"#!/bin/sh\n")
            with self.assertRaisesRegex(RuntimeError, "not native"):
                stage.verify_binary(fake)


if __name__ == "__main__":
    unittest.main()
