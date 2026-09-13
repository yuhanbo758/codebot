"""检查待打包前端包含三执行器完全访问入口，防止发布旧 dist 或漏接设置页。"""
from pathlib import Path
import sys


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    if not (root / "index.html").is_file():
        raise RuntimeError("前端构建产物缺少 index.html")
    # 从真正交给 electron-builder 的 JS 产物读取，不以源码存在代替打包验证。
    content = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.js"))
    required = ("/api/config/opencode/access", "/api/config/codex", "/api/config/rakazo", "full_access", "完全访问")
    missing = [marker for marker in required if marker not in content]
    if missing:
        raise RuntimeError(f"前端产物缺少完全访问功能标记：{missing}")
    print("Three-executor full access frontend bundle verified")


if __name__ == "__main__":
    main()
