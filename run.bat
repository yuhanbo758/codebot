@echo off
REM Codebot 快速测试启动 (Windows)

echo ╔════════════════════════════════════════╗
echo ║     Codebot 快速测试启动               ║
echo ╚════════════════════════════════════════╝

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Python，请安装 Python 3.11+
    pause
    exit /b 1
)

REM 检查 Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Node.js，请安装 Node.js 18+
    pause
    exit /b 1
)

echo ✅ Python 和 Node.js 已安装

REM 安装 Python 依赖
echo.
echo [1/3] 安装 Python 依赖...
pip install -r backend\requirements.txt

REM 安装前端依赖
echo.
echo [2/3] 安装前端依赖...
cd frontend
call npm install
cd ..

REM 构建前端
echo.
echo [3/3] 构建前端...
cd frontend
call npm run build
cd ..

REM 创建数据目录
if not exist data\backups mkdir data\backups
if not exist logs mkdir logs

echo.
echo ╔════════════════════════════════════════╗
echo ║     准备启动...                        ║
echo ╚════════════════════════════════════════╝
echo.
echo 正在启动后端服务器...
echo 访问地址：http://127.0.0.1:8080
echo.

REM 启动后端
python backend\main.py

pause
