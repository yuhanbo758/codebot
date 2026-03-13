@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ============================================================
echo  Codebot - One-click build script
echo ============================================================
echo.

set "ROOT=%~dp0"
set "VENV=%ROOT%venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"
set "PYINSTALLER=%VENV%\Scripts\pyinstaller.exe"

echo [0/5] Releasing build artifacts...
taskkill /F /IM codebot-backend.exe >nul 2>&1
taskkill /F /IM Codebot.exe >nul 2>&1
if exist "%ROOT%backend\dist\codebot-backend" rmdir /s /q "%ROOT%backend\dist\codebot-backend"
if exist "%ROOT%backend\build\codebot-backend" rmdir /s /q "%ROOT%backend\build\codebot-backend"
if exist "%ROOT%electron\dist\electron\win-unpacked" rmdir /s /q "%ROOT%electron\dist\electron\win-unpacked"

:: ---------------------------------------------------------------
:: Step 1 - Create venv (if not already present)
:: ---------------------------------------------------------------
echo [1/5] Checking Python virtual environment...
if not exist "%PYTHON%" (
    echo      venv not found, creating at %VENV% ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Make sure Python 3.10+ is on PATH.
        exit /b 1
    )
    echo      venv created.
) else (
    echo      venv already exists, skipping creation.
)

:: ---------------------------------------------------------------
:: Step 2 - Install / upgrade Python dependencies
:: ---------------------------------------------------------------
echo.
echo [2/5] Installing Python dependencies into venv...
"%PIP%" install --upgrade pip --quiet
"%PIP%" install pyinstaller --quiet
"%PIP%" install -r "%ROOT%backend\requirements.txt" --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check your network or requirements.txt.
    exit /b 1
)
echo      Dependencies installed.

:: ---------------------------------------------------------------
:: Step 3 - Build Python backend with PyInstaller
:: ---------------------------------------------------------------
echo.
echo [3/5] Building Python backend with PyInstaller...
pushd "%ROOT%backend"
if exist "%ROOT%backend\dist_build\codebot-backend" rmdir /s /q "%ROOT%backend\dist_build\codebot-backend"
if exist "%ROOT%backend\build_tmp2\codebot-backend" rmdir /s /q "%ROOT%backend\build_tmp2\codebot-backend"
"%PYINSTALLER%" codebot-backend.spec --clean --noconfirm --distpath "%ROOT%backend\dist_build" --workpath "%ROOT%backend\build_tmp2"
if errorlevel 1 (
    popd
    echo ERROR: PyInstaller build failed. See output above.
    exit /b 1
)
if exist "%ROOT%backend\dist\codebot-backend" rmdir /s /q "%ROOT%backend\dist\codebot-backend"
xcopy "%ROOT%backend\dist_build\codebot-backend" "%ROOT%backend\dist\codebot-backend" /E /I /Y >nul
if errorlevel 1 (
    popd
    echo ERROR: Copying backend build artifacts failed.
    exit /b 1
)
popd
echo      Backend built: backend\dist\codebot-backend\

:: ---------------------------------------------------------------
:: Step 4 - Build Vue frontend
:: ---------------------------------------------------------------
echo.
echo [4/5] Building Vue frontend...
pushd "%ROOT%frontend"
call npm install --silent
if errorlevel 1 (
    popd
    echo ERROR: npm install frontend failed.
    exit /b 1
)
call npm run build
if errorlevel 1 (
    popd
    echo ERROR: Vite build failed. See output above.
    exit /b 1
)
popd
echo      Frontend built: frontend\dist\

:: ---------------------------------------------------------------
:: Step 5 - Package Electron app with electron-builder
:: ---------------------------------------------------------------
echo.
echo [5/5] Packaging Electron app...
pushd "%ROOT%electron"
call npm install --silent
if errorlevel 1 (
    popd
    echo ERROR: npm install electron failed.
    exit /b 1
)
call npm run build
if errorlevel 1 (
    popd
    echo ERROR: electron-builder failed. See output above.
    exit /b 1
)
popd

echo.
echo ============================================================
echo  Build complete!
echo  Installer:  electron\dist\electron_new\
echo ============================================================
endlocal
