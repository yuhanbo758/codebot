#!/usr/bin/env python
"""Codebot 项目结构检查脚本"""
import os
import sys
from pathlib import Path

# 设置 UTF-8 编码
sys.stdout.reconfigure(encoding='utf-8')

def check_file(path, name):
    exists = os.path.exists(path)
    status = "OK" if exists else "MISSING"
    print(f"  [{status}] {name}")
    return exists

def main():
    base_dir = Path(__file__).parent
    all_ok = True
    
    print("=" * 50)
    print("   Codebot 项目结构检查")
    print("=" * 50)
    print()
    
    print("[检查] 后端文件...")
    all_ok &= check_file(base_dir / "backend" / "main.py", "backend/main.py")
    all_ok &= check_file(base_dir / "backend" / "config.py", "backend/config.py")
    all_ok &= check_file(base_dir / "backend" / "requirements.txt", "backend/requirements.txt")
    
    print()
    print("[检查] 核心模块...")
    all_ok &= check_file(base_dir / "backend" / "core" / "opencode_ws.py", "opencode_ws.py")
    all_ok &= check_file(base_dir / "backend" / "core" / "memory_manager.py", "memory_manager.py")
    all_ok &= check_file(base_dir / "backend" / "core" / "scheduler.py", "scheduler.py")
    
    print()
    print("[检查] API 路由...")
    all_ok &= check_file(base_dir / "backend" / "api" / "routes" / "chat.py", "chat.py")
    all_ok &= check_file(base_dir / "backend" / "api" / "routes" / "memory.py", "memory.py")
    all_ok &= check_file(base_dir / "backend" / "api" / "routes" / "scheduler.py", "scheduler.py")
    
    print()
    print("[检查] 前端文件...")
    all_ok &= check_file(base_dir / "frontend" / "package.json", "package.json")
    all_ok &= check_file(base_dir / "frontend" / "src" / "main.js", "main.js")
    all_ok &= check_file(base_dir / "frontend" / "src" / "App.vue", "App.vue")
    
    print()
    print("[检查] 配置文件...")
    all_ok &= check_file(base_dir / ".env.example", ".env.example")
    all_ok &= check_file(base_dir / ".gitignore", ".gitignore")
    all_ok &= check_file(base_dir / "README.md", "README.md")
    
    print()
    print("=" * 50)
    if all_ok:
        print("   OK - 所有文件都已创建！")
    else:
        print("   WARNING - 部分文件缺失")
    print("=" * 50)
    print()
    print("下一步:")
    print("  1. 安装依赖：pip install -r backend/requirements.txt")
    print("  2. 测试后端：python backend/main.py")
    print("  3. 或使用：run.bat (完整启动)")
    print()

if __name__ == "__main__":
    main()
