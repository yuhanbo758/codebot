@echo off
REM Codebot 项目结构检查

echo ╔════════════════════════════════════════╗
echo ║   Codebot 项目结构检查                 ║
echo ╚════════════════════════════════════════╝
echo.

echo [检查] 后端文件...
if exist backend\main.py (echo  ✅ backend/main.py) else (echo  ❌ backend/main.py)
if exist backend\config.py (echo  ✅ backend/config.py) else (echo  ❌ backend/config.py)
if exist backend\requirements.txt (echo  ✅ backend/requirements.txt) else (echo  ❌ backend/requirements.txt)

echo.
echo [检查] 核心模块...
if exist backend\core\opencode_ws.py (echo  ✅ opencode_ws.py) else (echo  ❌ opencode_ws.py)
if exist backend\core\memory_manager.py (echo  ✅ memory_manager.py) else (echo  ❌ memory_manager.py)
if exist backend\core\scheduler.py (echo  ✅ scheduler.py) else (echo  ❌ scheduler.py)

echo.
echo [检查] API 路由...
if exist backend\api\routes\chat.py (echo  ✅ chat.py) else (echo  ❌ chat.py)
if exist backend\api\routes\memory.py (echo  ✅ memory.py) else (echo  ❌ memory.py)
if exist backend\api\routes\scheduler.py (echo  ✅ scheduler.py) else (echo  ❌ scheduler.py)

echo.
echo [检查] 前端文件...
if exist frontend\package.json (echo  ✅ package.json) else (echo  ❌ package.json)
if exist frontend\src\main.js (echo  ✅ main.js) else (echo  ❌ main.js)
if exist frontend\src\App.vue (echo  ✅ App.vue) else (echo  ❌ App.vue)

echo.
echo [检查] 配置文件...
if exist .env.example (echo  ✅ .env.example) else (echo  ❌ .env.example)
if exist .gitignore (echo  ✅ .gitignore) else (echo  ❌ .gitignore)
if exist README.md (echo  ✅ README.md) else (echo  ❌ README.md)

echo.
echo ╔════════════════════════════════════════╗
echo ║   检查完成！                           ║
echo ╚════════════════════════════════════════╝
echo.
echo 下一步:
echo   1. 运行 test_backend.bat 测试后端
echo   2. 运行 run.bat 完整启动
echo.

pause
