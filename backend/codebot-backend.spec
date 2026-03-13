# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

block_cipher = None

# 收集必要包的数据文件和子模块
datas = []
hiddenimports = []
binaries = []

# chromadb
tmp_ret = collect_all('chromadb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# fastapi / starlette
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')

# pydantic
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# onnxruntime
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# tokenizers
tmp_ret = collect_all('tokenizers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# tornado
tmp_ret = collect_all('tornado')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# uvicorn
hiddenimports += collect_submodules('uvicorn')

# lark_oapi
tmp_ret = collect_all('lark_oapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# hnswlib — C++ 扩展，PyInstaller 无法自动发现，必须手动指定
# 直接硬编码路径，目标 '.' 表示放入 _internal/ 根目录
_hnswlib_src = r'D:\wenjian\python\.conda\Lib\site-packages\hnswlib.cp312-win_amd64.pyd'
binaries.append((_hnswlib_src, '.'))

# winrt — windows_toasts 依赖的 C++ 扩展 .pyd 文件
_winrt_dir = r'D:\wenjian\python\.conda\Lib\site-packages\winrt'
_winrt_pyds = [
    '_winrt.cp312-win_amd64.pyd',
    '_winrt_windows_data_xml_dom.cp312-win_amd64.pyd',
    '_winrt_windows_foundation.cp312-win_amd64.pyd',
    '_winrt_windows_foundation_collections.cp312-win_amd64.pyd',
    '_winrt_windows_ui_notifications.cp312-win_amd64.pyd',
]
for _pyd in _winrt_pyds:
    binaries.append((os.path.join(_winrt_dir, _pyd), 'winrt'))

# winrt msvcp140.dll (needed by the .pyd files)
binaries.append((os.path.join(_winrt_dir, 'msvcp140.dll'), 'winrt'))

# collect_all for document/data packages
for _pkg in ('docx', 'pptx', 'pypdf', 'pdfplumber', 'reportlab', 'plyer', 'windows_toasts', 'winrt'):
    try:
        tmp_ret = collect_all(_pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception as e:
        print(f"Warning: collect_all('{_pkg}') failed: {e}")

# skills/ — 内置技能目录打包进 _internal/skills/
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_skills_src = os.path.join(_spec_dir, '..', 'skills')
_skills_src = os.path.normpath(_skills_src)
if os.path.isdir(_skills_src):
    datas.append((_skills_src, 'skills'))
    print(f"[spec] bundling skills from: {_skills_src}")
else:
    print(f"[spec] WARNING: skills dir not found at: {_skills_src}")

# other hidden imports
hiddenimports += ['hnswlib']
hiddenimports += [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.middleware.proxy_headers',
    'email.mime.text',
    'email.mime.multipart',
    'multipart',
    'aiofiles',
    'httpx',
    'croniter',
    'loguru',
    'psutil',
    'sqlite3',
    'aiosmtplib',
    'jose',
    'passlib',
    'passlib.handlers.bcrypt',
    'bcrypt',
    'plyer',
    'plyer.platforms',
    'plyer.platforms.win',
    'plyer.platforms.win.notification',
    'windows_toasts',
    'winrt',
    'winrt._winrt',
    'winrt.windows',
    'winrt.windows.data',
    'winrt.windows.data.xml',
    'winrt.windows.data.xml.dom',
    'winrt.windows.foundation',
    'winrt.windows.foundation.collections',
    'winrt.windows.ui',
    'winrt.windows.ui.notifications',
    'openpyxl',
    'pandas',
    'xlrd',
    'docx',
    'pptx',
    'PIL',
    'pypdf',
    'pdfplumber',
    'reportlab',
    'requests',
    'bs4',
    'dotenv',
    'anyio',
    'anyio._backends._asyncio',
    'anyio._backends._trio',
    'sniffio',
    'onnxruntime',
    'onnxruntime.capi',
    'tokenizers',
    'tornado',
    'tornado.gen',
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'IPython',
        'jupyter',
        'notebook',
        'test',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='codebot-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='codebot-backend',
)
