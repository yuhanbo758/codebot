#!/bin/bash
# Codebot 快速测试启动 (Linux/macOS)

echo "╔════════════════════════════════════════╗"
echo "║     Codebot 快速测试启动               ║"
echo "╚════════════════════════════════════════╝"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python，请安装 Python 3.11+"
    exit 1
fi

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "❌ 未找到 Node.js，请安装 Node.js 18+"
    exit 1
fi

echo "✅ Python 和 Node.js 已安装"

# 安装 Python 依赖
echo ""
echo "[1/3] 安装 Python 依赖..."
pip3 install -r backend/requirements.txt

# 安装前端依赖
echo ""
echo "[2/3] 安装前端依赖..."
cd frontend
npm install
cd ..

# 构建前端
echo ""
echo "[3/3] 构建前端..."
cd frontend
npm run build
cd ..

# 创建数据目录
mkdir -p data/backups
mkdir -p logs

echo ""
echo "╔════════════════════════════════════════╗"
echo "║     准备启动...                        ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "正在启动后端服务器..."
echo "访问地址：http://127.0.0.1:8080"
echo ""

# 启动后端
python3 backend/main.py
