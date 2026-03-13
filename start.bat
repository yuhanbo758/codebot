@echo off
REM Codebot 快速启动脚本 (Windows)

echo ╔════════════════════════════════════════╗
echo ║        Codebot 启动中...               ║
echo ╚════════════════════════════════════════╝

REM 检查虚拟环境
if not exist venv (
    echo ❌ 虚拟环境不存在，请先运行安装脚本
    pause
    exit /b 1
)

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 启动后端
echo 正在启动后端服务器...
python backend\main.py

pause
