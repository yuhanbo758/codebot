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
    organize_chat_enabled: bool = True
    organize_time: str = "03:00"          # 每日整理时间，格式 "HH:MM"
    organize_last_run: Optional[str] = None  # ISO datetime，上次整理时间


class LogConfig(BaseModel):
    """日志配置"""
    task_log_retention_days: int = Field(ge=0, le=365, default=30)
    chat_log_retention_days: int = Field(ge=0, le=365, default=30)
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
    server_url: str = "http://127.0.0.1:11200"
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
    """
    沙箱配置（工作目录隔离模式）
    参考 LobsterAI 的本地执行架构，不依赖 QEMU/VM。
    """
    # 执行模式：auto | local | sandbox
    # auto    — 包含高风险操作时使用隔离工作目录执行，否则本地执行
    # local   — 始终本地执行（不使用隔离工作目录）
    # sandbox — 始终使用隔离工作目录执行
    execution_mode: str = "auto"
    # 是否启用沙箱功能（启用后使用隔离工作目录）
    enabled: bool = False
    # 工作目录（为空则自动在数据目录下创建 sandbox_workspace/）
    workspace_dir: str = ""
    # 沙箱执行超时（秒）
    exec_timeout: int = 300
    # 网络访问：是否允许沙箱访问互联网（本地模式下始终允许）
    network_enabled: bool = True
    # 以下字段保留以兼容旧配置文件，不再使用
    runtime_binary: str = ""
    image_path: str = ""
    memory_mb: int = 2048
    startup_timeout: int = 60
    ipc_dir: str = ""
    auto_download: bool = False
    extra_qemu_args: List[str] = []
    snapshot_mode: bool = False
    image_url: str = ""
    runtime_url: str = ""


class AppConfig(BaseModel):
    """应用配置"""
    version: str = "2.0.0"
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
    APP_TOKEN: str = ""
    
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
