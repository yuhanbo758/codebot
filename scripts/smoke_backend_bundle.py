"""在临时数据目录启动真实后端产物，验证本地 API 与首次向量操作；不调用真实模型。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import build_opener, ProxyHandler, Request


class _HealthStub(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/global/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"healthy":true}' if self.path == "/global/health" else b'{}')

    def log_message(self, *_args):
        pass


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    executable = root / ("codebot-backend.exe" if os.name == "nt" else "codebot-backend")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # 健康检查桩占用专用端口，避免启动或连接用户本机的 OpenCode、账号和模型。
    stub = ThreadingHTTPServer(("127.0.0.1", 0), _HealthStub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    opencode_port = str(stub.server_port)
    opener = build_opener(ProxyHandler({}))
    try:
        with tempfile.TemporaryDirectory(prefix="codebot-bundle-smoke-") as directory:
            data = Path(directory) / "data"
            data.mkdir()
            (data / "config.json").write_text(json.dumps({
                "network": {"host": "127.0.0.1"}, "codex": {"enabled": False},
                "rakazo": {"enabled": False}, "lark_bot": {"enabled": False},
                "sandbox": {"enabled": False},
            }), encoding="utf-8")
            env = {**os.environ, "CODEBOT_DATA_DIR": directory, "CODEBOT_BACKEND_PORT": str(port),
                   "CODEBOT_OPENCODE_SERVER_URL": f"http://127.0.0.1:{opencode_port}",
                   "CODEBOT_OPENCODE_PREFERRED_PORT": opencode_port,
                   "CODEBOT_OPENCODE_FALLBACK_PORT": opencode_port, "PYTHONUTF8": "1"}
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            log_path = Path(directory) / "backend.log"
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(executable)], cwd=root, env=env, stdout=log, stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError(f"Backend exited: {process.returncode}")
                        try:
                            with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=.5) as response:
                                assert json.load(response)["runtime_source"] == "packaged"
                            break
                        except OSError:
                            time.sleep(.1)
                    else:
                        raise TimeoutError("Backend startup timed out")
                    for api in ("/api/chat/conversations", "/api/memory/storage-status"):
                        with opener.open(f"http://127.0.0.1:{port}{api}", timeout=10) as response:
                            assert response.status == 200
                    db = sqlite3.connect(data / "conversations.db")
                    try:
                        cursor = db.execute("INSERT INTO long_term_memories(category,content) VALUES('test','bundle smoke')")
                        memory_id = cursor.lastrowid
                        db.commit()
                    finally:
                        db.close()
                    # 删除空向量记录会触发客户端和集合初始化，不下载嵌入模型或访问网络。
                    request = Request(f"http://127.0.0.1:{port}/api/memory/memories/{memory_id}", method="DELETE")
                    with opener.open(request, timeout=30) as response:
                        assert response.status == 200
                    db = sqlite3.connect(data / "chroma" / "chroma.sqlite3")
                    try:
                        collections = {row[0] for row in db.execute("SELECT name FROM collections")}
                        assert {"long_term_memory", "facts_memory"}.issubset(collections)
                    finally:
                        db.close()
                    print("Packaged backend APIs and lazy vector initialization verified")
                except Exception:
                    log.flush()
                    print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:], file=sys.stderr)
                    raise
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    finally:
        stub.shutdown()
        stub.server_close()


if __name__ == "__main__":
    main()
