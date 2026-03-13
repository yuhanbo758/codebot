"""
OpenCode HTTP 客户端
"""
import asyncio
import json
import time
from typing import Optional, AsyncGenerator, List, Dict, Any
from urllib.parse import urlparse, urlunparse
from loguru import logger
import httpx
from datetime import datetime

# 全局任务队列：每个对话有自己的任务队列和当前 session_id
# key: conversation_id (str), value: asyncio.Queue of coroutines
_conversation_queues: dict = {}
_conversation_current_session: dict = {}  # key: conversation_id -> current session_id
_queue_lock = asyncio.Lock()


class TaskResult:
    """任务执行结果"""
    def __init__(
        self,
        success: bool,
        content: str = "",
        tokens_used: int = 0,
        error: Optional[str] = None,
        parts: Optional[List[Dict[str, Any]]] = None
    ):
        self.success = success
        self.content = content
        self.tokens_used = tokens_used
        self.error = error
        self.parts = parts or []
        self.timestamp = datetime.now()


class OpenCodeClient:
    """OpenCode HTTP 客户端"""
    
    def __init__(self, server_url: str = "http://127.0.0.1:1120"):
        self.base_url = self._normalize_http_base_url(server_url)
        self._client: Optional[httpx.AsyncClient] = None
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5

    def _normalize_http_base_url(self, server_url: str) -> str:
        if not server_url:
            return "http://127.0.0.1:1120"
        parsed = urlparse(server_url)
        scheme = parsed.scheme or "http"
        if scheme == "":
            parsed = urlparse(f"http://{server_url}")
            scheme = "http"
        if scheme in ["ws", "wss"]:
            scheme = "https" if scheme == "wss" else "http"
        if scheme not in ["http", "https"]:
            scheme = "http"
        netloc = parsed.netloc or parsed.path
        if "/" in netloc:
            netloc = netloc.split("/", 1)[0]
        return urlunparse((
            scheme,
            netloc,
            "",
            "",
            "",
            ""
        ))

    def _build_prompt_payload(self, prompt: str, model: Optional[str] = None, mode: Optional[str] = None) -> dict:
        actual_prompt = prompt
        if mode == "plan":
            actual_prompt = (
                "请以「规划模式」回答以下问题：\n"
                "1. 先分析需求，拆解为清晰的步骤列表\n"
                "2. 对每个步骤说明目标、方法和预期输出\n"
                "3. 识别潜在风险和依赖关系\n"
                "4. 最后给出执行建议\n"
                "不要直接执行，只输出详细的计划方案。\n\n"
                f"用户需求：{prompt}"
            )
        payload: dict = {
            "parts": [
                {
                    "type": "text",
                    "text": actual_prompt
                }
            ]
        }
        if model:
            if "/" in model:
                provider_id, model_id = model.split("/", 1)
                payload["model"] = {"providerID": provider_id, "modelID": model_id}
            else:
                payload["model"] = {"modelID": model}
        return payload

    def _extract_event_session_id(self, properties: dict) -> Optional[str]:
        if not isinstance(properties, dict):
            return None
        direct = properties.get("sessionID")
        if isinstance(direct, str) and direct:
            return direct
        part = properties.get("part")
        if isinstance(part, dict):
            sid = part.get("sessionID")
            if isinstance(sid, str) and sid:
                return sid
        info = properties.get("info")
        if isinstance(info, dict):
            sid = info.get("sessionID")
            if isinstance(sid, str) and sid:
                return sid
        return None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        return self._client

    async def _health_check(self) -> bool:
        client = await self._get_client()
        response = await client.get(f"{self.base_url}/global/health")
        response.raise_for_status()
        data = response.json()
        return bool(data.get("healthy"))
    
    async def connect(self):
        """建立连接"""
        while self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                ok = await self._health_check()
                if not ok:
                    raise ConnectionError("OpenCode Server 未就绪")
                self.connected = True
                self.reconnect_attempts = 0
                logger.info(f"成功连接到 OpenCode Server: {self.base_url}")
                return
            except Exception as e:
                self.reconnect_attempts += 1
                wait_time = 2 ** self.reconnect_attempts
                logger.warning(
                    f"连接失败 (尝试 {self.reconnect_attempts}/{self.max_reconnect_attempts}): {e}. "
                    f"{wait_time}秒后重试..."
                )
                await asyncio.sleep(wait_time)

        raise ConnectionError("无法连接到 OpenCode Server")

    async def try_connect(self, attempts: int = 8, delay: float = 0.5, open_timeout: float = 1.0) -> bool:
        if self.connected:
            return True
        for _ in range(max(1, attempts)):
            try:
                ok = await self._health_check()
                if ok:
                    self.connected = True
                    self.reconnect_attempts = 0
                    return True
            except Exception:
                self.connected = False
                await asyncio.sleep(delay)
        return False
    
    async def disconnect(self):
        """断开连接"""
        client = self._client
        self._client = None
        self.connected = False
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
    
    async def ensure_connected(self):
        """确保连接状态"""
        if not self.connected:
            await self.connect()
    
    async def get_models(self) -> list:
        """获取 OpenCode 可用模型列表（从 /provider 端点解析已连接的 provider）"""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/provider", timeout=10)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return []
            connected_ids = set(data.get("connected") or [])
            all_providers = data.get("all") or []
            models = []
            for provider in all_providers:
                if not isinstance(provider, dict):
                    continue
                provider_id = provider.get("id") or ""
                if provider_id not in connected_ids:
                    continue
                provider_name = provider.get("name") or provider_id
                provider_models = provider.get("models") or {}
                # models is a dict keyed by model ID
                if isinstance(provider_models, dict):
                    for model_key, model_info in provider_models.items():
                        if isinstance(model_info, dict):
                            display_name = model_info.get("name") or model_key
                        else:
                            display_name = model_key
                        # 格式：providerID/modelID，与 OpenCode 内部格式一致
                        full_id = f"{provider_id}/{model_key}"
                        models.append({
                            "id": full_id,
                            "name": f"{display_name} ({provider_name})",
                            "provider": provider_id,
                            "model": model_key,
                        })
            return models
        except Exception as e:
            logger.warning(f"获取模型列表失败: {e}")
            return []

    async def abort_session(self, session_id: str) -> bool:
        """终止一个运行中的 session"""
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/session/{session_id}/abort",
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"终止 session 失败: {e}")
            return False

    async def delete_session(self, session_id: str) -> bool:
        """删除一个 session"""
        try:
            client = await self._get_client()
            response = await client.delete(
                f"{self.base_url}/session/{session_id}",
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"删除 session 失败: {e}")
            return False

    async def execute_task(
        self,
        prompt: str,
        model: Optional[str] = None,
        mode: Optional[str] = None,
        workspace: Optional[str] = None,
        timeout: int = 300,
        conversation_id: Optional[str] = None
    ) -> TaskResult:
        """
        执行任务
        
        Args:
            prompt: 任务提示词
            model: 模型名称
            mode: agent 模式，"plan" 或 "build"
            workspace: 工作目录
            timeout: 超时时间 (秒)
            conversation_id: 对话ID，用于追踪当前 session 以支持 abort
        
        Returns:
            TaskResult: 任务执行结果
        """
        await self.ensure_connected()

        try:
            client = await self._get_client()
            session_body: dict = {}
            session_response = await client.post(
                f"{self.base_url}/session",
                params={"directory": workspace} if workspace else None,
                json=session_body if session_body else None
            )
            session_response.raise_for_status()
            session_id = session_response.json().get("id")
            if not session_id:
                raise ValueError("OpenCode 会话创建失败")

            # 追踪当前 session_id，供 abort 使用
            if conversation_id:
                _conversation_current_session[str(conversation_id)] = session_id

            payload = self._build_prompt_payload(prompt=prompt, model=model, mode=mode)
            message_response = await client.post(
                f"{self.base_url}/session/{session_id}/message",
                json=payload,
                timeout=timeout
            )
            message_response.raise_for_status()
            data = message_response.json()
            parts = data.get("parts", []) if isinstance(data, dict) else []
            content = "".join([p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"])
            return TaskResult(
                success=True,
                content=content,
                parts=parts if isinstance(parts, list) else []
            )
        except Exception as e:
            self.connected = False
            logger.error(f"任务执行失败：{e}")
            return TaskResult(
                success=False,
                error=str(e)
            )
        finally:
            # 清除 session 追踪
            if conversation_id:
                _conversation_current_session.pop(str(conversation_id), None)

    async def execute_task_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        mode: Optional[str] = None,
        workspace: Optional[str] = None,
        timeout: int = 300,
        conversation_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        await self.ensure_connected()
        final_parts: List[dict] = []
        final_content = ""
        try:
            client = await self._get_client()
            session_response = await client.post(
                f"{self.base_url}/session",
                params={"directory": workspace} if workspace else None,
            )
            session_response.raise_for_status()
            session_id = session_response.json().get("id")
            if not session_id:
                raise ValueError("OpenCode 会话创建失败")

            if conversation_id:
                _conversation_current_session[str(conversation_id)] = session_id

            payload = self._build_prompt_payload(prompt=prompt, model=model, mode=mode)

            async with client.stream(
                "GET",
                f"{self.base_url}/global/event",
                headers={"Accept": "text/event-stream"},
                timeout=timeout
            ) as event_response:
                event_response.raise_for_status()
                prompt_resp = await client.post(
                    f"{self.base_url}/session/{session_id}/prompt_async",
                    json=payload,
                    timeout=30
                )
                if prompt_resp.status_code not in (200, 202, 204):
                    raise RuntimeError(f"prompt_async 失败: HTTP {prompt_resp.status_code}")

                part_buffers: Dict[str, str] = {}
                assistant_message_id: Optional[str] = None
                delta_buffer = ""
                last_emit = 0.0

                def _should_flush_buffer() -> bool:
                    if not delta_buffer:
                        return False
                    if len(delta_buffer) >= 48:
                        return True
                    if "\n" in delta_buffer:
                        return True
                    return (time.monotonic() - last_emit) >= 0.08

                async def _emit_buffer():
                    nonlocal delta_buffer, last_emit, final_content
                    if not delta_buffer:
                        return
                    final_content += delta_buffer
                    yield_payload = {"type": "content_delta", "delta": delta_buffer, "content": final_content}
                    delta_buffer = ""
                    last_emit = time.monotonic()
                    return yield_payload

                async for raw_line in event_response.aiter_lines():
                    if raw_line is None:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload_text = line[5:].strip()
                    if not payload_text:
                        continue
                    try:
                        event = json.loads(payload_text)
                    except Exception:
                        continue
                    payload_obj = event.get("payload")
                    if not isinstance(payload_obj, dict):
                        continue
                    event_type = payload_obj.get("type")
                    properties = payload_obj.get("properties") if isinstance(payload_obj.get("properties"), dict) else {}
                    event_session_id = self._extract_event_session_id(properties)
                    if event_session_id != session_id:
                        continue

                    if event_type == "message.updated":
                        info = properties.get("info")
                        if isinstance(info, dict) and info.get("role") == "assistant":
                            msg_id = info.get("id")
                            if isinstance(msg_id, str) and msg_id:
                                assistant_message_id = msg_id
                        continue

                    if event_type == "message.part.delta":
                        message_id = properties.get("messageID")
                        if assistant_message_id and message_id != assistant_message_id:
                            continue
                        if properties.get("field") != "text":
                            continue
                        delta = properties.get("delta")
                        if not isinstance(delta, str) or delta == "":
                            continue
                        part_id = properties.get("partID")
                        if isinstance(part_id, str) and part_id:
                            part_buffers[part_id] = part_buffers.get(part_id, "") + delta
                        delta_buffer += delta
                        if _should_flush_buffer():
                            payload_event = await _emit_buffer()
                            if payload_event:
                                yield payload_event
                        continue

                    if event_type == "message.part.updated":
                        part = properties.get("part")
                        if not isinstance(part, dict):
                            continue
                        message_id = part.get("messageID")
                        if assistant_message_id and message_id != assistant_message_id:
                            continue
                        part_type = part.get("type")
                        if part_type == "text":
                            part_id = part.get("id")
                            part_text = part.get("text")
                            if isinstance(part_id, str) and isinstance(part_text, str):
                                existing = part_buffers.get(part_id, "")
                                if len(part_text) > len(existing):
                                    delta = part_text[len(existing):]
                                    part_buffers[part_id] = part_text
                                    if delta:
                                        delta_buffer += delta
                                        if _should_flush_buffer():
                                            payload_event = await _emit_buffer()
                                            if payload_event:
                                                yield payload_event
                            continue
                        payload_event = await _emit_buffer()
                        if payload_event:
                            yield payload_event
                        final_parts.append(part)
                        yield {"type": "tool_event", "part": part}
                        continue

                    if event_type == "session.idle":
                        payload_event = await _emit_buffer()
                        if payload_event:
                            yield payload_event
                        break

            payload_event = await _emit_buffer()
            if payload_event:
                yield payload_event

            if not final_content:
                latest_resp = await client.get(
                    f"{self.base_url}/session/{session_id}/message",
                    params={"limit": 20},
                    timeout=20
                )
                latest_resp.raise_for_status()
                latest_data = latest_resp.json()
                if isinstance(latest_data, list):
                    for msg in reversed(latest_data):
                        if not isinstance(msg, dict):
                            continue
                        info = msg.get("info")
                        if not isinstance(info, dict) or info.get("role") != "assistant":
                            continue
                        parts = msg.get("parts") if isinstance(msg.get("parts"), list) else []
                        final_parts = [p for p in parts if isinstance(p, dict)]
                        final_content = "".join([
                            p.get("text", "") for p in final_parts
                            if isinstance(p, dict) and p.get("type") == "text"
                        ])
                        break

            yield {"type": "done", "content": final_content, "parts": final_parts}
        except Exception as e:
            self.connected = False
            logger.error(f"流式任务执行失败：{e}")
            yield {"type": "error", "error": str(e)}
        finally:
            if conversation_id:
                _conversation_current_session.pop(str(conversation_id), None)
    
    async def stream_task(
        self,
        prompt: str,
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        流式执行任务
        
        Args:
            prompt: 任务提示词
            model: 模型名称
        
        Yields:
            str: 响应片段
        """
        async for event in self.execute_task_stream(prompt=prompt, model=model):
            if event.get("type") == "content_delta":
                yield event.get("delta", "")
    
    async def check_server(self) -> bool:
        """检查 OpenCode Server 是否可用"""
        try:
            return await self.try_connect(attempts=1, delay=0)
        except Exception:
            return False

    def get_current_session_id(self, conversation_id: str) -> Optional[str]:
        """获取当前对话正在执行的 session_id"""
        return _conversation_current_session.get(str(conversation_id))
    
    async def execute_with_multimodal(
        self,
        prompt: str,
        images: Optional[list] = None,
        primary_model: Optional[str] = None,
        multimodal_model: Optional[str] = None
    ) -> TaskResult:
        """
        多模态任务执行
        
        Args:
            prompt: 任务提示词
            images: 图片列表
            primary_model: 主模型
            multimodal_model: 多模态模型
        
        Returns:
            TaskResult: 任务执行结果
        """
        # 如果有图片，先用多模态模型识别
        if images and multimodal_model:
            image_prompt = "请分析这张图片并详细描述内容："
            image_result = await self.execute_task(
                image_prompt,
                model=multimodal_model
            )
            
            if image_result.success:
                # 将图片识别结果加入原始提示
                prompt = f"{prompt}\n\n图片分析：{image_result.content}"
        
        # 用主模型执行任务
        return await self.execute_task(prompt, model=primary_model)


OpenCodeWebSocket = OpenCodeClient
