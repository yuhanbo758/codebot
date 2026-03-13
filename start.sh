#!/bin/bash
# Codebot 快速启动脚本 (Linux/macOS)

echo "╔════════════════════════════════════════╗"
echo "║        Codebot 启动中...               ║"
echo "╚════════════════════════════════════════╝"

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行安装脚本"
    exit 1
fi

# 激活虚拟环境
source venv/bin/activate

# 启动后端
echo "正在启动后端服务器..."
python backend/main.py
