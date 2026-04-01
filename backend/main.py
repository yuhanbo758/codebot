"""
Codebot Backend Main Entry
基于 OpenCode 的个人 AI 助手
"""
import asyncio
import socket
import os
import sys
import shutil
from typing import Optional
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from loguru import logger

from config import settings, app_config
from database.init_db import conversations_db, tasks_db
from core.opencode_ws import OpenCodeClient
from core.memory_manager import MemoryManager
from core.memory_organizer import run_organize_loop
from core.lark_ws_bot import LarkWsBot
from core.scheduler import TaskScheduler
from core.sandbox import SandboxManager
from services.notification import NotificationService
from utils.installer import check_and_install_opencode, start_opencode_server, stop_opencode_server

# 导入 API 路由
from api.routes import chat, memory, scheduler as scheduler_router, skills, notifications, logs, lark, mcp as mcp_router, config as config_router, sandbox as sandbox_router


# 全局组件实例
opencode_ws: OpenCodeClient = None
memory_manager: MemoryManager = None
notification_service: NotificationService = None
lark_ws_bot: LarkWsBot = None
sandbox_manager: SandboxManager = None
_organize_loop_task = None

def _configure_console_encoding():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    stream = getattr(sys.stdout, "reconfigure", None)
    if callable(stream):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    stream = getattr(sys.stderr, "reconfigure", None)
    if callable(stream):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


_configure_console_encoding()


def _seed_builtin_skills(skills_dir: Path):
    """从打包资源或源码树复制内置技能到用户数据目录（仅复制缺失的技能）。"""
    # 确定内置技能的源目录（按优先级）
    builtin_src: Optional[Path] = None

    # 1. PyInstaller 打包：技能在 _MEIPASS/skills/
    if hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "skills"
        if candidate.is_dir():
            builtin_src = candidate

    # 2. Electron extraResources：CODEBOT_RESOURCES_DIR/skills/
    if builtin_src is None:
        resources_dir = os.environ.get("CODEBOT_RESOURCES_DIR", "").strip()
        if resources_dir:
            candidate = Path(resources_dir) / "skills"
            if candidate.is_dir():
                builtin_src = candidate

    # 3. 开发模式：repo_root/skills/
    if builtin_src is None:
        candidate = Path(__file__).parent.parent / "skills"
        if candidate.is_dir():
            builtin_src = candidate

    if builtin_src is None:
        logger.warning("未找到内置技能源目录，跳过技能种子复制")
        return

    logger.info(f"内置技能源目录：{builtin_src}")
    copied = 0
    for skill_dir in builtin_src.iterdir():
        if not skill_dir.is_dir():
            continue
        dest = skills_dir / skill_dir.name
        if dest.exists():
            # 已存在，跳过（不覆盖用户自定义内容）
            continue
        try:
            shutil.copytree(str(skill_dir), str(dest))
            copied += 1
            logger.info(f"已复制内置技能：{skill_dir.name}")
        except Exception as e:
            logger.warning(f"复制技能 {skill_dir.name} 失败：{e}")
    if copied:
        logger.info(f"共复制 {copied} 个内置技能到 {skills_dir}")


def _migrate_auto_json_skills(skills_dir: Path):
    """将旧格式的 auto_*.json 自动技能迁移为目录格式（builtin: 兼容，可编辑）。"""
    import json as _json
    migrated = 0
    for json_path in list(skills_dir.glob("auto_*.json")):
        try:
            data = _json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        skill_id = data.get("id") or json_path.stem
        target_dir = skills_dir / skill_id
        if target_dir.exists():
            # 目录已存在，直接删除旧 JSON
            json_path.unlink(missing_ok=True)
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        name = data.get("name") or skill_id
        description = data.get("description") or ""
        skill_content = f"""---
name: {name}
description: {description[:120] if len(description) > 120 else description}
---

# {name}

## 技能概述

此技能由对话自动生成（从旧格式迁移）。

## 描述

{description}
"""
        (target_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
        json_path.unlink(missing_ok=True)
        migrated += 1
        logger.info(f"已迁移自动技能: {skill_id}")
    if migrated:
        logger.info(f"共迁移 {migrated} 个旧格式自动技能")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("=" * 50)
    logger.info("Codebot 启动中...")
    logger.info("=" * 50)
    
    # 1. 确保数据目录存在
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # 2. 确保技能目录存在，并从打包资源中种子复制内置技能
    settings.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _seed_builtin_skills(settings.SKILLS_DIR)
    _migrate_auto_json_skills(settings.SKILLS_DIR)
    
    # 3. 检查并安装 OpenCode
    if app_config.opencode.auto_install:
        logger.info("检查 OpenCode 安装状态...")
        installed = await check_and_install_opencode()
        if installed:
            logger.info("OpenCode 已安装或安装成功")
        else:
            logger.warning("OpenCode 安装失败，请手动安装")
    
    # 4. 初始化数据库
    logger.info("初始化数据库...")
    conversations_db.connect()
    conversations_db.init_tables()
    tasks_db.connect()
    tasks_db.init_tables()
    
    # 5. 初始化核心组件
    logger.info("初始化核心组件...")
    global opencode_ws, memory_manager, notification_service, lark_ws_bot, sandbox_manager
    
    opencode_ws = OpenCodeClient(app_config.opencode.server_url)
    chat.opencode_ws = opencode_ws
    memory_manager = MemoryManager()
    memory.memory_manager = memory_manager
    lark.memory_manager = memory_manager
    notification_service = NotificationService(app_config.notification)
    notifications.notification_service = notification_service

    # 初始化沙箱管理器（无论是否启用，保持对象存在以便 API 查询状态）
    sandbox_manager = SandboxManager(
        data_dir=settings.DATA_DIR,
        config=app_config.sandbox,
        skills_dir=settings.SKILLS_DIR,
    )
    sandbox_router.sandbox_manager = sandbox_manager
    chat.sandbox_manager = sandbox_manager
    if app_config.sandbox.enabled:
        logger.info("沙箱功能已启用，正在初始化运行时...")
        asyncio.create_task(sandbox_manager.initialize())
    else:
        logger.info("沙箱功能未启用（sandbox.enabled=false）")
    
    # 6. 初始化定时任务调度器
    logger.info("初始化定时任务调度器...")
    scheduler_router.scheduler = TaskScheduler(
        opencode_ws=opencode_ws,
        memory_manager=memory_manager,
        notification_service=notification_service
    )
    await scheduler_router.scheduler.start()
    
    force_auto_start = os.environ.get("CODEBOT_FORCE_OPENCODE_AUTOSTART", "").strip().lower() in {"1", "true", "yes", "on"}
    should_auto_start = bool(force_auto_start or app_config.opencode.auto_start or os.environ.get("CODEBOT_DATA_DIR"))
    if should_auto_start:
        parsed = urlparse(app_config.opencode.server_url)
        configured_port = parsed.port or 11200
        preferred_port_raw = os.environ.get("CODEBOT_OPENCODE_PREFERRED_PORT", "").strip()
        fallback_port_raw = os.environ.get("CODEBOT_OPENCODE_FALLBACK_PORT", "").strip()
        try:
            preferred_port = int(preferred_port_raw) if preferred_port_raw else 11200
        except Exception:
            preferred_port = 11200
        try:
            fallback_port = int(fallback_port_raw) if fallback_port_raw else 11201
        except Exception:
            fallback_port = 11201
        candidate_ports = []
        for p in [preferred_port, configured_port, fallback_port]:
            if not isinstance(p, int) or p < 1 or p > 65535:
                continue
            if p not in candidate_ports:
                candidate_ports.append(p)
        actual_port = 0
        for port in candidate_ports:
            actual_port = await start_opencode_server(port)
            if actual_port:
                break
        if actual_port:
            new_url = f"http://127.0.0.1:{actual_port}"
            if opencode_ws.base_url != new_url:
                logger.info(f"OpenCode Server 运行地址: {new_url}")
                opencode_ws.base_url = new_url
                chat.opencode_ws.base_url = new_url
        else:
            logger.warning("OpenCode Server 未能自动启动，相关 AI 功能可能不可用")
    
    # 8. 尝试连接 OpenCode Server
    async def connect_opencode():
        logger.info("尝试连接 OpenCode Server...")
        try:
            await opencode_ws.connect()
            logger.info("成功连接到 OpenCode Server")
        except Exception as e:
            logger.warning(f"无法连接到 OpenCode Server: {e}")
            logger.warning("请确保 OpenCode Server 已启动")
    
    asyncio.create_task(connect_opencode())

    # 8b. 启动时全量同步 MCP 到 opencode（Skills 不同步，由用户自主管理）
    async def sync_to_opencode():
        # 等待 opencode 连接就绪后再同步
        for _ in range(20):
            await asyncio.sleep(1)
            if opencode_ws and opencode_ws.connected:
                break
        try:
            from api.routes.mcp import full_sync_mcp_to_opencode
            full_sync_mcp_to_opencode()
            logger.info("[Startup] MCP 全量同步到 opencode 完成")
        except Exception as _e:
            logger.warning(f"[Startup] MCP 同步到 opencode 失败: {_e}")

    asyncio.create_task(sync_to_opencode())

    # 9. 启动记忆自动整理循环
    global _organize_loop_task
    _organize_loop_task = asyncio.create_task(
        run_organize_loop(
            get_memory_manager_fn=lambda: memory_manager,
            get_opencode_ws_fn=lambda: opencode_ws,
            get_config_fn=lambda: app_config,
        )
    )
    logger.info("记忆自动整理循环已启动")

    if app_config.lark_bot.enabled and (app_config.lark_bot.connection_mode or "ws") == "ws":
        try:
            import lark_oapi as lark_sdk
            supports_ws = hasattr(lark_sdk, "ws") and hasattr(lark_sdk, "EventDispatcherHandler")
        except Exception:
            supports_ws = False
        if supports_ws:
            try:
                loop = asyncio.get_running_loop()
                lark_ws_bot = LarkWsBot(app_config.lark_bot, loop, memory_manager)
                lark_ws_bot.start()
                logger.info("飞书对话机器人已启动（长连接模式）")
            except Exception as e:
                logger.error(f"飞书对话机器人启动失败: {e}")
        else:
            logger.warning("飞书长连接未启动：当前 lark-oapi 版本不支持 ws，请改用 webhook 模式")

    # 显示访问地址
    local_ip = get_local_ip()
    logger.info("=" * 50)
    logger.info("✨ Codebot 启动成功！")
    logger.info("=" * 50)
    logger.info(f"📍 本地访问：http://127.0.0.1:{app_config.network.port}")
    logger.info(f"🌐 局域网访问：http://{local_ip}:{app_config.network.port}")
    logger.info(f"📱 移动端：使用手机浏览器访问局域网地址")
    logger.info("=" * 50)
    
    yield
    
    # 关闭时清理
    logger.info("Codebot 关闭中...")

    if _organize_loop_task and not _organize_loop_task.done():
        _organize_loop_task.cancel()
        try:
            await _organize_loop_task
        except asyncio.CancelledError:
            pass

    if lark_ws_bot:
        try:
            lark_ws_bot.stop()
            lark_ws_bot.close()
        except Exception:
            pass

    if scheduler_router.scheduler:
        await scheduler_router.scheduler.stop()
    
    if sandbox_manager:
        await sandbox_manager.shutdown()

    if opencode_ws and opencode_ws.connected:
        await opencode_ws.disconnect()
    
    if memory_manager:
        memory_manager.close()
    
    if notification_service:
        notification_service.close()
    
    stop_opencode_server()

    conversations_db.close()
    tasks_db.close()
    
    logger.info("Codebot 已关闭")


def get_local_ip():
    """获取局域网 IP"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


def _is_port_available(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


# 创建 FastAPI 应用
app = FastAPI(
    title="Codebot",
    description="基于 OpenCode 的个人 AI 助手",
    version="2.1.0",
    lifespan=lifespan
)

# CORS 配置 (支持局域网访问)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(chat.router, prefix="/api/chat", tags=["聊天"])
app.include_router(memory.router, prefix="/api/memory", tags=["记忆"])
app.include_router(scheduler_router.router, prefix="/api/scheduler", tags=["定时任务"])
app.include_router(skills.router, prefix="/api/skills", tags=["技能"])
app.include_router(mcp_router.router, prefix="/api/mcp", tags=["MCP"])
app.include_router(config_router.router, prefix="/api/config", tags=["配置"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["通知"])
app.include_router(lark.router, prefix="/api/lark", tags=["飞书"])
app.include_router(sandbox_router.router, prefix="/api/sandbox", tags=["沙箱"])


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "version": "2.1.0",
        "opencode_connected": opencode_ws.connected if opencode_ws else False,
        "runtime_source": "packaged" if getattr(sys, "frozen", False) else "source",
        "pid": os.getpid()
    }


@app.get("/api/network-info")
async def network_info():
    """返回本机访问地址信息"""
    local_ip = get_local_ip()
    port = int(app_config.network.port)
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "lan_url": f"http://{local_ip}:{port}",
        "local_ip": local_ip,
        "port": port,
    }


@app.get("/api/ready")
async def readiness_check():
    """就绪检查"""
    return {
        "ready": True
    }

@app.get("/logo.ico")
async def logo_icon():
    logo_path = Path(__file__).parent.parent / "logo.ico"
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(logo_path)


app.include_router(logs.router, prefix="/api/logs", tags=["日志"])

frontend_dist = None
frontend_dist_env = os.getenv("CODEBOT_FRONTEND_DIST")
if frontend_dist_env:
    frontend_dist = Path(frontend_dist_env)
elif hasattr(sys, "_MEIPASS"):
    frontend_dist = Path(sys._MEIPASS) / "frontend-dist"
else:
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
    logger.info(f"前端静态文件已挂载：{frontend_dist}")

    index_html = frontend_dist / "index.html"

    @app.middleware("http")
    async def spa_history_fallback(request: Request, call_next):
        response = await call_next(request)
        if response.status_code != 404:
            return response
        if request.method != "GET":
            return response

        path = request.url.path
        if path.startswith("/api/"):
            return response
        if "." in Path(path).name:
            return response
        if not index_html.exists():
            return response
        return FileResponse(index_html)
else:
    logger.warning(f"前端静态文件不存在：{frontend_dist}")


if __name__ == "__main__":
    import uvicorn

    host = app_config.network.host
    port = int(app_config.network.port)
    if not _is_port_available(host, port):
        logger.error(
            f"端口被占用，无法启动：{host}:{port}\n"
            f"请先停止已运行的 Codebot 后端实例，或修改 data/config.json 中的 network.port。"
        )
        raise SystemExit(1)

    uvicorn.run(app, host=host, port=port, log_level="info")
