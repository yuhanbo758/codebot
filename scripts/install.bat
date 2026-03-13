@echo off
REM Codebot 安装脚本 (Windows)

echo ╔════════════════════════════════════════╗
echo ║        Codebot 安装程序                ║
echo ╚════════════════════════════════════════╝

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到 Python，请先安装 Python 3.11+
    exit /b 1
)

REM 检查 Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到 Node.js，请先安装 Node.js 18+
    exit /b 1
)

echo ✅ 环境检查通过

REM 创建虚拟环境
echo 正在创建 Python 虚拟环境...
python -m venv venv

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 安装 Python 依赖
echo 正在安装 Python 依赖...
pip install -r backend\requirements.txt

REM 安装前端依赖
echo 正在安装前端依赖...
cd frontend
call npm install
call npm run build
cd ..

REM 创建数据目录
echo 创建数据目录...
if not exist data\backups mkdir data\backups
if not exist logs mkdir logs
if not exist skills\custom mkdir skills\custom

REM 检查 OpenCode
echo 检查 OpenCode 安装...
where opencode >nul 2>&1
if errorlevel 1 (
    echo ⚠️  OpenCode 未安装
    echo 正在安装 OpenCode...
    
    REM 尝试使用 scoop
    where scoop >nul 2>&1
    if not errorlevel 1 (
        scoop install opencode
    ) else (
        REM 使用 npm 安装
        npm install -g opencode-ai
    )
)

echo.
echo ╔════════════════════════════════════════╗
echo ║        Codebot 安装完成！              ║
echo ╚════════════════════════════════════════╝
echo.
echo 启动方式:
echo   1. 直接启动后端：venv\Scripts\activate ^&^& python backend\main.py
echo   2. 使用 Electron: cd electron ^&^& npm start
echo.
echo 访问地址：
echo   本地：http://127.0.0.1:8080
echo.

pause
