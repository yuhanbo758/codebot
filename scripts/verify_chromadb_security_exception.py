"""验证 ChromaDB 漏洞豁免仍符合 Codebot 的本地嵌入式使用边界。

PYSEC-2026-3813 与 PYSEC-2026-3814 都需要攻击者访问 ChromaDB 的
HTTP 服务端接口。Codebot 当前只创建进程内 ``PersistentClient``，因此在
上游尚无修复版本时可以精确豁免这两个公告。若以后引入 Chroma HTTP 客户端、
服务端命令或 v2 API 路径，本脚本会让发布流水线失败，要求重新进行安全评估。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

# 这些模式对应两个公告成立所需的网络服务面。保持模式具体，避免把普通的
# ``PersistentClient``、说明文档或变量名误判为远程 Chroma 服务。
FORBIDDEN_PATTERNS = {
    "Chroma HTTP 客户端": re.compile(
        r"\b(?:chromadb\s*\.\s*)?(?:AsyncHttpClient|HttpClient)\s*\("
    ),
    "Chroma v2 HTTP API": re.compile(r"/api/v2/tenants/"),
    "Chroma 服务端命令": re.compile(
        r"\b(?:chroma|chromadb)\s+(?:run|serve)\b", re.IGNORECASE
    ),
    "Chroma 服务端参数数组": re.compile(
        r"[\"'](?:chroma|chromadb)[\"']\s*,\s*[\"'](?:run|serve)[\"']",
        re.IGNORECASE,
    ),
}

# 本地开发环境可能保留 PyInstaller 产物，其中会复制 ChromaDB 自身源码；
# 这些目录不是 Codebot 源码，扫描它们会把依赖包提供的 HttpClient 定义误报
# 为 Codebot 启用了网络服务面。CI 只需检查可维护的后端源码。
IGNORED_PATH_PARTS = {
    "__pycache__",
    ".pytest_cache",
    "dist",
    "dist_build",
    "venv",
}


def find_forbidden_surfaces() -> list[str]:
    """返回会使当前漏洞豁免失效的源码位置。"""

    findings: list[str] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        relative_to_backend = path.relative_to(BACKEND_ROOT)
        if any(
            part in IGNORED_PATH_PARTS or part.startswith("build_tmp")
            for part in relative_to_backend.parts
        ):
            continue
        relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
        content = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(content.splitlines(), start=1):
            for description, pattern in FORBIDDEN_PATTERNS.items():
                if pattern.search(line):
                    findings.append(
                        f"{relative_path}:{line_number}: {description}: {line.strip()}"
                    )
    return findings


def main() -> int:
    """执行边界检查，并提供便于 Actions 定位的明确结果。"""

    findings = find_forbidden_surfaces()
    if findings:
        print("ChromaDB 安全豁免已失效：检测到网络服务面，请重新评估漏洞：")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("ChromaDB 安全豁免边界有效：仅允许本地进程内 PersistentClient。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
