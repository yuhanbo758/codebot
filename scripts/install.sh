#!/bin/bash
# Codebot 安装脚本 (Linux/macOS)

set -e

echo "╔════════════════════════════════════════╗"
echo "║        Codebot 安装程序                ║"
echo "╚════════════════════════════════════════╝"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未检测到 Python 3，请先安装 Python 3.11+"
    exit 1
fi

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "❌ 未检测到 Node.js，请先安装 Node.js 18+"
    exit 1
fi

echo "✅ 环境检查通过"

# 创建虚拟环境
echo "正在创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 安装 Python 依赖
echo "正在安装 Python 依赖..."
pip install -r backend/requirements.txt

# 安装前端依赖
echo "正在安装前端依赖..."
cd frontend
npm install
npm run build
cd ..

# 创建数据目录
echo "创建数据目录..."
mkdir -p data/backups
mkdir -p logs
mkdir -p skills/custom

# 检查 OpenCode
echo "检查 OpenCode 安装..."
if ! command -v opencode &> /dev/null; then
    echo "⚠️  OpenCode 未安装"
    echo "正在安装 OpenCode..."
    
    if command -v brew &> /dev/null; then
        brew install anomalyco/tap/opencode
    else
        curl -fsSL https://opencode.ai/install | bash
    fi
fi

echo ""
echo "╔════════════════════════════════════════╗"
echo "║        Codebot 安装完成！              ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "启动方式:"
echo "  1. 直接启动后端：source venv/bin/activate && python backend/main.py"
echo "  2. 使用 Electron: cd electron && npm start"
echo ""
echo "访问地址："
echo "  本地：http://127.0.0.1:8080"
echo ""
