"""
Codebot 配置管理
"""
import os
import json
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class MemoryConfig(BaseModel):
    """记忆配置"""
    auto_cleanup_enabled: bool = False
    cleanup_days: int = Field(ge=0, le=365, default=180)
    archive_enabled: bool = True
    archive_days: int = Field(ge=0, le=365, default=90)
    vector_search_top_k: int = Field(ge=1, le=100, default=5)
    similarity_threshold: float = Field(ge=0.0, le=1.0, default=0.7)
    show_archived_in_search: bool = True
    # 自动整理
    organize_enabled: bool = False
    organize_time: str = "03:00"          # 每日整理时间，格式 "HH:MM"
    organize_last_run: Optional[str] = None  # ISO datetime，上次整理时间


class LogConfig(BaseModel):
    """日志配置"""
    task_log_retention_days: int = Field(ge=0, le=365, default=30)
    system_log_retention_days: int = Field(ge=1, le=90, default=7)
    log_level: str = "INFO"


class NotificationConfig(BaseModel):
    """通知配置"""
    app_enabled: bool = True
    desktop_enabled: bool = False
    lark_enabled: bool = False
    lark_webhook_url: Optional[str] = None
    lark_secret: Optional[str] = None
    email_enabled: bool = False
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_from: str = ""
    email_to: List[str] = []
    poll_interval: int = Field(ge=5, le=120, default=30)


class LarkBotConfig(BaseModel):
    """飞书对话机器人配置"""
    enabled: bool = False
    connection_mode: str = "ws"
    app_id: str = ""
    app_secret: str = ""
    verify_token: str = ""
    encrypt_key: str = ""
    receive_id_type: str = "chat_id"



class OpenCodeConfig(BaseModel):
    """OpenCode 配置"""
    server_url: str = "http://127.0.0.1:1120"
    auto_install: bool = True
    auto_start: bool = True


class McpServerConfig(BaseModel):
    """单个 MCP Server 配置"""
    id: str = ""
    name: str = ""
    description: str = ""
    transport: str = "stdio"          # stdio | sse
    command: Optional[str] = None     # stdio 模式：命令
    args: List[str] = []              # stdio 模式：参数
    url: Optional[str] = None         # sse 模式：HTTP endpoint URL
    env: Dict[str, str] = {}          # 额外环境变量
    enabled: bool = True
    installed_at: str = ""


class ModelConfig(BaseModel):
    """模型配置"""
    primary_model: str = ""
    multimodal_model: str = ""
    models: List[Dict] = []


class NetworkConfig(BaseModel):
    """网络配置"""
    host: str = "0.0.0.0"
    port: int = 8080


class IntegrationConfig(BaseModel):
    """第三方集成配置"""
    modelscope_api_key: str = ""


class SkillsConfig(BaseModel):
    """技能配置"""
    custom_skill_dirs: List[str] = []


class SandboxConfig(BaseModel):
    """沙箱 VM 配置"""
    # 执行模式：auto | local | sandbox
    execution_mode: str = "auto"
    # QEMU 运行时二进制路径（为空则自动检测）
    runtime_binary: str = ""
    # 沙箱磁盘镜像路径（为空则自动检测/下载）
    image_path: str = ""
    # 工作目录（沙箱内挂载的主机目录，为空则使用默认数据目录）
    workspace_dir: str = ""
    # 沙箱 VM 内存大小（MB）
    memory_mb: int = 2048
    # 沙箱启动超时（秒）
    startup_timeout: int = 60
    # 沙箱执行超时（秒）
    exec_timeout: int = 300
    # IPC 目录（主机侧与 VM 通信的共享目录）
    ipc_dir: str = ""
    # 是否自动下载运行时和镜像
    auto_download: bool = True
    # 自定义 QEMU 启动参数（附加）
    extra_qemu_args: List[str] = []
    # 快照模式：每次启动使用全新快照（安全）
    snapshot_mode: bool = True
    # 网络访问：允许沙箱访问互联网
    network_enabled: bool = True
    # 沙箱镜像下载 URL（空则使用默认 CDN）
    image_url: str = ""
    # 运行时下载 URL（空则使用默认 CDN）
    runtime_url: str = ""
    # 是否启用沙箱功能
    enabled: bool = False


class AppConfig(BaseModel):
    """应用配置"""
    version: str = "1.0.0"
    memory: MemoryConfig = MemoryConfig()
    logs: LogConfig = LogConfig()
    notification: NotificationConfig = NotificationConfig()
    lark_bot: LarkBotConfig = LarkBotConfig()
    opencode: OpenCodeConfig = OpenCodeConfig()
    models: ModelConfig = ModelConfig()
    network: NetworkConfig = NetworkConfig()
    integration: IntegrationConfig = IntegrationConfig()
    skills: SkillsConfig = SkillsConfig()
    sandbox: SandboxConfig = SandboxConfig()


def _resolve_base_dir() -> Path:
    """Resolve the application base directory.

    In a PyInstaller-frozen build the source files live inside ``_internal/``
    which is not writable on a typical Windows installation.  When the
    ``CODEBOT_DATA_DIR`` environment variable is set (injected by Electron) we
    use that as the writable data root.  Otherwise we fall back to the
    repository root so that plain ``python main.py`` dev launches still work.
    """
    env_data_dir = os.environ.get("CODEBOT_DATA_DIR", "").strip()
    if env_data_dir:
        return Path(env_data_dir)
    # Dev / source layout: config.py lives in backend/, repo root is one level up.
    return Path(__file__).parent.parent


class Settings(BaseSettings):
    """全局设置"""
    # 基础路径
    BASE_DIR: Path = _resolve_base_dir()
    DATA_DIR: Path = BASE_DIR / "data"
    SKILLS_DIR: Path = BASE_DIR / "skills"
    LOGS_DIR: Path = BASE_DIR / "logs"
    
    # 数据库路径
    CONVERSATIONS_DB: Path = DATA_DIR / "conversations.db"
    SCHEDULED_TASKS_DB: Path = DATA_DIR / "scheduled_tasks.db"
    TASK_LOGS_DB: Path = DATA_DIR / "task_logs.db"
    CHROMA_DIR: Path = DATA_DIR / "chroma"
    BACKUPS_DIR: Path = DATA_DIR / "backups"
    MCP_SERVERS_FILE: Path = DATA_DIR / "mcp_servers.json"
    
    # 配置
    APP_TOKEN: str = "codebot-secret-token-2024"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局设置实例
settings = Settings()


def load_config() -> AppConfig:
    """加载配置文件"""
    config_path = settings.DATA_DIR / "config.json"
    
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return AppConfig(**data)
    else:
        # 创建默认配置
        config = AppConfig()
        save_config(config)
        return config


def save_config(config: AppConfig):
    """保存配置文件"""
    config_path = settings.DATA_DIR / "config.json"
    
    # 确保目录存在
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)


# 全局配置实例
app_config = load_config()
