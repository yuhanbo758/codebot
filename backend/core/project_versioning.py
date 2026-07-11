"""项目对话的独立 Git 快照管理，不修改用户项目自身的 Git 历史。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class ProjectVersionManager:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "project_versions"
        self.root.mkdir(parents=True, exist_ok=True)

    def _repo_dir(self, project_dir: str) -> Path:
        key = hashlib.sha256(str(Path(project_dir).resolve()).encode("utf-8")).hexdigest()[:20]
        return self.root / key

    def _run(self, repo: Path, worktree: Path, *args: str) -> str:
        env = os.environ.copy()
        env.update({"GIT_AUTHOR_NAME": "Codebot", "GIT_AUTHOR_EMAIL": "codebot@local",
                    "GIT_COMMITTER_NAME": "Codebot", "GIT_COMMITTER_EMAIL": "codebot@local"})
        result = subprocess.run(["git", f"--git-dir={repo}", f"--work-tree={worktree}", *args],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Git 快照操作失败")
        return result.stdout.strip()

    def snapshot(self, project_dir: str, conversation_id: int, message_id: int, phase: str) -> str:
        worktree = Path(project_dir).resolve()
        if not worktree.is_dir():
            raise ValueError(f"项目目录不存在：{worktree}")
        repo = self._repo_dir(project_dir)
        if not (repo / "HEAD").exists():
            repo.mkdir(parents=True, exist_ok=True)
            self._run(repo, worktree, "init")
        self._run(repo, worktree, "add", "-A")
        self._run(repo, worktree, "commit", "--allow-empty", "-m", f"conversation={conversation_id} message={message_id} phase={phase}")
        commit = self._run(repo, worktree, "rev-parse", "HEAD")
        meta_path = repo / "codebot-snapshots.json"
        meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
        meta.setdefault(str(conversation_id), {}).setdefault(str(message_id), {})[phase] = commit
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        return commit

    def restore_before(self, project_dir: str, conversation_id: int, message_id: int) -> Optional[str]:
        worktree, repo = Path(project_dir).resolve(), self._repo_dir(project_dir)
        meta_path = repo / "codebot-snapshots.json"
        if not meta_path.exists():
            return None
        item = json.loads(meta_path.read_text("utf-8")).get(str(conversation_id), {}).get(str(message_id), {})
        before, after = item.get("before"), item.get("after")
        if not before:
            return None
        if after:
            for relative in self._run(repo, worktree, "diff", "--name-only", "--diff-filter=A", before, after).splitlines():
                target = (worktree / relative).resolve()
                if worktree != target and worktree in target.parents:
                    if target.is_file() or target.is_symlink():
                        target.unlink(missing_ok=True)
                    elif target.is_dir():
                        shutil.rmtree(target)
        self._run(repo, worktree, "checkout", "-f", before, "--", ".")
        return before
