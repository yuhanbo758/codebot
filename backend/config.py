"""
Codebot 配置管理
"""
import os
import json
import secrets
from pathlib import Path
from typing import Optional, List, Dict, Literal
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class MemoryConfig(BaseModel):
    """记忆配置"""
    auto_cleanup_enabled: bool = False
    cleanup_days: int = Field(ge=0, le=365, default=180)
    cleanup_archived_memories: bool = True
    archive_enabled: bool = True
    archive_days: int = Field(ge=0, le=365, default=90)
    vector_search_top_k: int = Field(ge=1, le=100, default=5)
    similarity_threshold: float = Field(ge=0.0, le=1.0, default=0.7)
    show_archived_in_search: bool = True
    # 自动整理
    organize_enabled: bool = True
    organize_chat_enabled: bool = True
    organize_time: str = "03:00"          # 每日整理时间，格式 "HH:MM"
    organize_last_run: Optional[str] = None  # ISO datetime，上次整理时间
    organize_model: Optional[str] = None  # 自动整理专用模型，None 表示使用当前聊天默认模型


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


class GeneralConfig(BaseModel):
    """通用配置"""
    # 聊天回复语言，默认中文。前端“设置 -> 通用设置 -> 语言”会写入该字段。
    language: str = "zh-CN"
    # 聊天界面紧凑显示，减少头像、气泡、间距占用。
    compact_mode: bool = False
    # 打开后聊天窗口按 OpenCode CLI 风格显示完整运行过程。
    opencode_cli_display: bool = True
    # 浏览器设置: "system" = 系统默认浏览器, "builtin" = 内置浏览器
    link_open_mode: str = "system"
    # AI 生成文件的默认存储路径（为空则使用系统默认位置）
    file_storage_path: str = ""
    # @ 插入文件时的搜索目录列表（为空则只搜索项目目录/BASE_DIR）
    file_search_dirs: List[str] = []
    # 聊天页面默认模型，供聊天页与网关复用
    chat_default_model: str = ""
    # 开启后，AI 生成的记忆/定时任务/技能先进入成长候选，由用户决定是否接受。
    growth_candidate_decision: bool = False
    # 开启后，定时任务进入成长候选时发送通知，提醒用户及时处理。
    task_candidate_notification_enabled: bool = True



class OpenCodeConfig(BaseModel):
    """OpenCode 配置"""
    server_url: str = "http://127.0.0.1:11200"
    cli_path: str = ""
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
    port: int = 15682


class SecurityConfig(BaseModel):
    """局域网访问认证配置；本机回环请求始终保持免认证。"""
    lan_auth_enabled: bool = True
    # 仅驻留内存；持久化到 data/.lan_api_token，绝不进入 config.json。
    lan_api_token: str = Field(default="", exclude=True)
    pairing_code_ttl_seconds: int = Field(default=300, ge=60, le=1800)
    session_hours: int = Field(default=720, ge=1, le=24 * 365)


class IntegrationConfig(BaseModel):
    """第三方集成配置"""
    modelscope_api_key: str = ""


class SkillsConfig(BaseModel):
    """技能配置"""
    custom_skill_dirs: List[str] = []


class CodexConfig(BaseModel):
    """Codex Agent Harness 集成配置。

    默认使用 ``openai-codex`` Python SDK 随包携带的固定版本运行时；只有
    用户明确选择 ``custom`` 时才读取 ``codex_bin``，避免打包版依赖系统 PATH。
    """
    enabled: bool = True
    auto_start: bool = True
    runtime_source: Literal["bundled", "custom"] = "bundled"
    codex_bin: str = ""
    approval_policy: Literal["interactive", "auto_review", "deny_all"] = "interactive"
    share_memory: bool = True
    share_scheduler: bool = True
    skill_dirs: List[str] = []


class RakazoConfig(BaseModel):
    """Rakazo 本机受控运行时配置。

    会话令牌、模型 API Key 和更新器一次性令牌均不属于该模型，避免被写入
    ``data/config.json``。首版默认关闭，仅允许显式启用的本机 Docker 运行时。
    """

    enabled: bool = False
    auto_start: bool = False
    api_url: str = "http://127.0.0.1:3100"
    compose_file: str = ""
    docker_project_name: str = "codebot-rakazo"
    # stable 只接受正式 Release；experimental 只接受 Codebot 兼容清单中同时
    # 固定了上游提交、官方镜像摘要和 Compose 摘要的受控实验版本。绝不直接
    # 跟踪会漂移的 main/edge 标签。
    release_channel: Literal["stable", "experimental"] = "stable"
    # 只有用户点击“安装并启动固定实验版”后才会置为 True。单纯手改 channel
    # 不能绕过显式风险确认，也不会让旧配置在升级后自动进入实验通道。
    experimental_runtime_enabled: bool = False
    network_policy: Literal["ask", "deny", "allow"] = "ask"
    default_project_read: bool = True
    default_project_write: bool = False
    default_command_execution: bool = False
    default_external_network: bool = False


class ObsidianKnowledgeBase(BaseModel):
    """A configured Obsidian vault or Markdown knowledge folder."""
    id: str = ""
    name: str = ""
    path: str = ""
    description: str = ""
    enabled: bool = True


class ObsidianConfig(BaseModel):
    """Obsidian integration config."""
    enabled: bool = True
    cli_path: str = "obsidian-cli"
    vault_path: str = ""
    knowledge_bases: List[ObsidianKnowledgeBase] = []


class SandboxConfig(BaseModel):
    """
    沙箱配置。

    Windows Sandbox 是显式可选的强隔离后端，默认既不选择、也不探测或启动。
    本地模式只适用于用户明确信任的命令；需要强隔离但未选择可用后端时失败关闭。
    """
    # 强隔离后端：none | windows_sandbox
    isolation_backend: Literal["none", "windows_sandbox"] = "none"
    # 执行模式：auto | local | sandbox
    # auto    — 高风险操作要求所选强隔离后端，否则拒绝执行
    # local   — 始终在宿主机的受控工作目录执行，仅适用于可信任务
    # sandbox — 始终要求所选强隔离后端，否则拒绝执行
    execution_mode: Literal["auto", "local", "sandbox"] = "auto"
    # 是否启用沙箱路由；默认关闭，不影响普通可信项目工作流
    enabled: bool = False
    # 工作目录（为空则自动在数据目录下创建 sandbox_workspace/）
    workspace_dir: str = ""
    # 沙箱执行超时（秒）
    exec_timeout: int = 300
    # 网络访问：Windows Sandbox 默认禁网；仅在用户明确需要时开启。
    network_enabled: bool = False
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
    version: str = "5.4.0"
    general: GeneralConfig = GeneralConfig()
    memory: MemoryConfig = MemoryConfig()
    logs: LogConfig = LogConfig()
    notification: NotificationConfig = NotificationConfig()
    lark_bot: LarkBotConfig = LarkBotConfig()
    opencode: OpenCodeConfig = OpenCodeConfig()
    models: ModelConfig = ModelConfig()
    network: NetworkConfig = NetworkConfig()
    security: SecurityConfig = SecurityConfig()
    integration: IntegrationConfig = IntegrationConfig()
    skills: SkillsConfig = SkillsConfig()
    codex: CodexConfig = CodexConfig()
    rakazo: RakazoConfig = RakazoConfig()
    obsidian: ObsidianConfig = ObsidianConfig()
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
    RAKAZO_DB: Path = DATA_DIR / "rakazo.db"
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


def load_or_create_lan_api_token(*, rotate: bool = False) -> str:
    """从独立密钥文件读取 LAN Token；环境变量优先且不会写盘。"""
    env_token = os.environ.get("CODEBOT_LAN_API_TOKEN", "").strip()
    if env_token and not rotate:
        return env_token
    token_path = settings.DATA_DIR / ".lan_api_token"
    if token_path.exists() and not rotate:
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return token


def migrate_legacy_agent_config(data: dict) -> tuple[dict, bool]:
    """把旧 Hermes 配置幂等迁移成固定字段的 CodexConfig。"""
    if not isinstance(data, dict):
        return data, False
    legacy_hermes = data.get("hermes")
    legacy_present = isinstance(legacy_hermes, dict)
    if legacy_present and "codex" not in data:
        legacy_cli = str(legacy_hermes.get("cli_path") or "").strip()
        use_custom_runtime = bool(legacy_cli and legacy_cli.lower() != "hermes")
        data["codex"] = {
            "enabled": bool(legacy_hermes.get("enabled", True)),
            "auto_start": bool(legacy_hermes.get("auto_start", True)),
            "runtime_source": "custom" if use_custom_runtime else "bundled",
            "codex_bin": legacy_cli if use_custom_runtime else "",
            "approval_policy": "interactive",
            "share_memory": bool(legacy_hermes.get("share_memory", True)),
            "share_scheduler": bool(legacy_hermes.get("share_scheduler", True)),
            "skill_dirs": list(legacy_hermes.get("skill_dirs") or []),
        }
    data.pop("hermes", None)
    return data, legacy_present


def load_config() -> AppConfig:
    """加载配置文件"""
    config_path = settings.DATA_DIR / "config.json"
    
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Hermes -> Codex 一次性配置迁移。迁移在 Pydantic 校验前完成，
            # 保存成功后旧 ``hermes`` 节点即被移除；重复启动不会再次迁移。
            data, migrated_hermes = migrate_legacy_agent_config(data)
            config = AppConfig(**data)
            config.security.lan_api_token = load_or_create_lan_api_token()
            needs_save = bool(migrated_hermes)
            if int(getattr(config.network, "port", 0) or 0) == 8080:
                config.network.port = 15682
                needs_save = True
            if needs_save:
                save_config(config)
            return config
    else:
        # 创建默认配置
        config = AppConfig()
        config.security.lan_api_token = load_or_create_lan_api_token()
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
