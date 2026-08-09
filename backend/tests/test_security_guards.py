"""文件、附件和浏览器来源边界的回归测试。"""

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.routes.chat import AttachedFile, SendMessageRequest, _path_is_within_allowed_roots  # noqa: E402
from utils.secrets import MASKED_SECRET, merge_masked_secrets, redact_secrets  # noqa: E402


class SecurityGuardTests(unittest.TestCase):
    def test_sibling_path_does_not_bypass_root(self):
        root = Path("D:/workspace/project").resolve()
        sibling = Path("D:/workspace/project-secret/token.txt").resolve()
        child = Path("D:/workspace/project/src/main.py").resolve()

        self.assertFalse(_path_is_within_allowed_roots(sibling, [root]))
        self.assertTrue(_path_is_within_allowed_roots(child, [root]))

    def test_attachment_count_is_bounded(self):
        attachments = [AttachedFile(name=f"{index}.txt", type="text/plain", content="x") for index in range(11)]
        with self.assertRaises(ValidationError):
            SendMessageRequest(conversation_id=1, message="test", attached_files=attachments)

    def test_attachment_total_budget_is_bounded(self):
        attachments = [
            AttachedFile(name="a.txt", type="text/plain", content="x" * 26_000_000),
            AttachedFile(name="b.txt", type="text/plain", content="x" * 26_000_000),
        ]
        with self.assertRaises(ValidationError):
            SendMessageRequest(conversation_id=1, message="test", attached_files=attachments)

    def test_sensitive_config_is_redacted_recursively(self):
        source = {
            "name": "demo",
            "env": {"API_KEY": "real-key", "REGION": "cn"},
            "webhook_url": "https://example.invalid/private-hook",
        }

        redacted = redact_secrets(source)

        self.assertEqual(redacted["env"]["API_KEY"], MASKED_SECRET)
        self.assertEqual(redacted["env"]["REGION"], "cn")
        self.assertEqual(redacted["webhook_url"], MASKED_SECRET)
        self.assertEqual(source["env"]["API_KEY"], "real-key")

    def test_masked_secret_preserves_existing_value(self):
        existing = {"env": {"API_KEY": "real-key", "OPTION": "old"}}
        updates = {"env": {"API_KEY": MASKED_SECRET, "OPTION": "new"}}

        merged = merge_masked_secrets(existing, updates)

        self.assertEqual(merged["env"]["API_KEY"], "real-key")
        self.assertEqual(merged["env"]["OPTION"], "new")


if __name__ == "__main__":
    unittest.main()
