"""
沙箱模块
提供基于工作目录隔离的安全执行环境，参考 LobsterAI 的本地执行模式。
不依赖 QEMU/VM，通过限制工作目录、进程超时和资源限制实现隔离。
"""
from .manager import SandboxManager

__all__ = ["SandboxManager"]
