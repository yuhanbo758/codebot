"""
QEMU VM 进程管理
负责：
  - 构建并启动 QEMU 命令行
  - macOS/Linux：使用 -virtfs (9p) 挂载共享目录
  - Windows：使用 virtio-serial + TCP 桥接（VirtioSerialBridge）实现文件 IPC
  - 监控 VM 进程生存状态
  - 提供 push_file / read_file 原语（Windows IPC 用）

设计参考 LobsterAI coworkVmRunner.ts
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from .runtime import SandboxRuntime


# ── 常量 ─────────────────────────────────────────────────────────────────────

# QEMU 用户网络：宿主机在 10.0.2.2 可达
QEMU_HOST_ALIAS = "10.0.2.2"

# virtio-serial socket 端口（Windows TCP 桥）
VIRTIO_SERIAL_PORT = 9600

# IPC 目录内文件名约定（与 VM 内 agent 约定一致）
IPC_REQUEST_FILE = "request.json"
IPC_RESPONSE_FILE = "response.json"
IPC_STATUS_FILE = "status.txt"


# ── VirtioSerialBridge（Windows 专用） ────────────────────────────────────────

class VirtioSerialBridge:
    """
    在 Windows 上通过 TCP socket 模拟 virtio-serial IPC。
    QEMU 以 `-chardev socket,... -device virtio-serial,...` 方式暴露一个 TCP 端口，
    本类在宿主机侧通过 TCP 连接该端口收发换行分隔的 JSON 消息。

    协议（行协议 JSON）：
      → {"type":"push_file","path":"<guest_path>","content":"<base64>"}
      ← {"type":"ack","path":"<guest_path>","ok":true}
      → {"type":"read_file","path":"<guest_path>"}
      ← {"type":"file_data","path":"<guest_path>","content":"<base64>"}
      → {"type":"exec","cmd":"<shell_cmd>"}
      ← {"type":"exec_result","exit_code":0,"stdout":"...","stderr":"..."}
    """

    def __init__(self, host: str = "127.0.0.1", port: int = VIRTIO_SERIAL_PORT):
        self._host = host
        self._port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self.connected = False

    async def connect(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=2.0,
                )
                self.connected = True
                logger.debug(f"VirtioSerialBridge 已连接: {self._host}:{self._port}")
                return True
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                await asyncio.sleep(1.0)
        logger.error(f"VirtioSerialBridge 连接超时: {self._host}:{self._port}")
        return False

    async def disconnect(self):
        self.connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _send(self, msg: dict) -> dict:
        if not self.connected or not self._writer:
            raise RuntimeError("VirtioSerialBridge 未连接")
        async with self._lock:
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            self._writer.write(line.encode())
            await self._writer.drain()
            resp_line = await asyncio.wait_for(self._reader.readline(), timeout=30.0)
            return json.loads(resp_line.decode().strip())

    async def push_file(self, guest_path: str, content: bytes) -> bool:
        import base64
        payload = base64.b64encode(content).decode()
        resp = await self._send({"type": "push_file", "path": guest_path, "content": payload})
        return resp.get("ok", False)

    async def read_file(self, guest_path: str) -> Optional[bytes]:
        import base64
        resp = await self._send({"type": "read_file", "path": guest_path})
        raw = resp.get("content")
        if raw is None:
            return None
        return base64.b64decode(raw)

    async def exec_cmd(self, cmd: str) -> dict:
        return await self._send({"type": "exec", "cmd": cmd})


# ── VmRunner ──────────────────────────────────────────────────────────────────

class VmRunner:
    """
    QEMU 虚拟机进程管理器。

    - 启动 VM（支持快照模式）
    - 等待 VM 内 agent 就绪
    - 提供 IPC 目录（9p virtfs on macOS/Linux）或 VirtioSerialBridge（Windows）
    - 关闭 VM

    用法::

        runner = VmRunner(runtime, ipc_dir=Path("/tmp/codebot_ipc"), config=cfg)
        await runner.start()
        # ... 使用 runner.ipc_dir 或 runner.serial_bridge ...
        await runner.stop()
    """

    def __init__(
        self,
        runtime: SandboxRuntime,
        ipc_dir: Path,
        workspace_dir: Optional[Path] = None,
        config=None,
    ):
        self._runtime = runtime
        self._ipc_dir = ipc_dir
        self._workspace_dir = workspace_dir
        self._cfg = config
        self._process: Optional[subprocess.Popen] = None
        self._monitor_task: Optional[asyncio.Task] = None

        self.serial_bridge: Optional[VirtioSerialBridge] = None
        self.running = False

        self._ipc_dir.mkdir(parents=True, exist_ok=True)
        if workspace_dir:
            workspace_dir.mkdir(parents=True, exist_ok=True)

    # ── 启动 ─────────────────────────────────────────────────────────────────

    async def start(self, startup_timeout: float = 60.0) -> bool:
        if self.running:
            logger.warning("VM 已在运行")
            return True

        cmd = self._build_qemu_cmd()
        logger.info(f"启动 QEMU VM: {' '.join(str(c) for c in cmd)}")

        try:
            # Windows 下 asyncio.create_subprocess_exec 会因无效句柄继承失败（WinError 6）。
            # 改用 subprocess.Popen + CREATE_NO_WINDOW + DEVNULL 完全切断句柄继承链。
            kwargs: dict = dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

            self._process = subprocess.Popen(cmd, **kwargs)
        except FileNotFoundError:
            logger.error(f"QEMU 二进制未找到: {cmd[0]}")
            return False
        except Exception as e:
            logger.error(f"启动 QEMU 失败: {e}")
            return False

        self.running = True
        self._monitor_task = asyncio.create_task(self._monitor_process())

        # Windows：通过 VirtioSerialBridge 等待 VM 就绪
        if SandboxRuntime.is_windows():
            self.serial_bridge = VirtioSerialBridge(port=VIRTIO_SERIAL_PORT)
            connected = await self.serial_bridge.connect(timeout=startup_timeout)
            if not connected:
                await self.stop()
                return False
        else:
            # macOS/Linux：等待 IPC 就绪文件出现
            ready = await self._wait_for_ready_file(timeout=startup_timeout)
            if not ready:
                await self.stop()
                return False

        logger.info("沙箱 VM 已就绪")
        return True

    # ── 停止 ─────────────────────────────────────────────────────────────────

    async def stop(self):
        self.running = False

        if self.serial_bridge:
            await self.serial_bridge.disconnect()
            self.serial_bridge = None

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                # 在线程里等待，最多 5 秒，避免阻塞事件循环
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: self._process.wait(timeout=5))
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"停止 QEMU 进程时出错: {e}")
        self._process = None
        logger.info("沙箱 VM 已停止")

    # ── IPC：9p（macOS/Linux）────────────────────────────────────────────────

    def write_ipc_request(self, payload: dict) -> Path:
        """将请求写入 IPC 目录（9p 模式）"""
        req_path = self._ipc_dir / IPC_REQUEST_FILE
        resp_path = self._ipc_dir / IPC_RESPONSE_FILE
        # 清除旧响应
        resp_path.unlink(missing_ok=True)
        with open(req_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return req_path

    async def wait_for_ipc_response(self, timeout: float = 300.0) -> Optional[dict]:
        """等待 VM 将响应写入 IPC 目录（9p 模式）"""
        resp_path = self._ipc_dir / IPC_RESPONSE_FILE
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if resp_path.exists():
                try:
                    with open(resp_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"读取 IPC 响应失败: {e}")
            await asyncio.sleep(0.5)
        return None

    # ── 内部：构建 QEMU 命令行 ────────────────────────────────────────────────

    def _build_qemu_cmd(self) -> list:
        qemu = self._runtime.get_qemu_path()
        image = self._runtime.get_image_path()
        memory_mb = 2048
        snapshot = True
        network_enabled = True
        extra_args: list = []

        if self._cfg:
            memory_mb = getattr(self._cfg, "memory_mb", memory_mb)
            snapshot = getattr(self._cfg, "snapshot_mode", snapshot)
            network_enabled = getattr(self._cfg, "network_enabled", network_enabled)
            extra_args = list(getattr(self._cfg, "extra_qemu_args", []) or [])

        cmd = [
            qemu,
            "-m", str(memory_mb),
            "-drive", f"file={image},format=qcow2" + (",snapshot=on" if snapshot else ""),
            "-nographic",
            "-serial", "none",
        ]

        # 网络
        if network_enabled:
            cmd += ["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"]
        else:
            cmd += ["-nic", "none"]

        if SandboxRuntime.is_windows():
            cmd += self._build_virtio_serial_args()
        else:
            cmd += self._build_virtfs_args()

        cmd += extra_args
        return cmd

    def _build_virtfs_args(self) -> list:
        """9p virtfs 挂载参数（macOS/Linux）"""
        args = [
            "-virtfs",
            f"local,path={self._ipc_dir},mount_tag=ipc,security_model=mapped-xattr,id=ipc",
        ]
        if self._workspace_dir:
            args += [
                "-virtfs",
                f"local,path={self._workspace_dir},mount_tag=workspace,security_model=mapped-xattr,id=ws",
            ]
        return args

    def _build_virtio_serial_args(self) -> list:
        """virtio-serial TCP 桥参数（Windows）"""
        return [
            "-chardev", f"socket,id=serial0,host=127.0.0.1,port={VIRTIO_SERIAL_PORT},server=on,wait=off",
            "-device", "virtio-serial-pci",
            "-device", "virtserialport,chardev=serial0,name=org.codebot.serial.0",
        ]

    # ── 内部：监控进程 ────────────────────────────────────────────────────────

    async def _monitor_process(self):
        """在后台轮询 Popen 进程状态，进程退出时更新 running 标志。
        
        使用 asyncio.sleep 轮询而不是 run_in_executor(process.wait)，
        避免永久占用线程池线程直至进程退出。
        """
        while self._process and self._process.poll() is None:
            await asyncio.sleep(1.0)
        rc = self._process.returncode if self._process else None
        if self.running:
            logger.warning(f"QEMU 进程意外退出，返回码: {rc}")
            self.running = False

    # ── 内部：等待 VM 就绪文件（9p 模式） ────────────────────────────────────

    async def _wait_for_ready_file(self, timeout: float) -> bool:
        ready_path = self._ipc_dir / "ready"
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if ready_path.exists():
                logger.debug("沙箱 VM 就绪文件已出现")
                return True
            if not self.running:
                logger.error("QEMU 进程在就绪前退出")
                return False
            await asyncio.sleep(1.0)
        logger.error(f"等待沙箱 VM 就绪超时（{timeout}s）")
        return False

    # ── 属性 ─────────────────────────────────────────────────────────────────

    @property
    def ipc_dir(self) -> Path:
        return self._ipc_dir

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None
