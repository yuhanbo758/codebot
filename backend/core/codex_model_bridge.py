"""把 Codex Responses 请求桥接到 OpenCode 已配置的第三方模型协议。

Codex 自定义 model provider 当前只会发送 Responses API 请求，而 OpenCode
中的大量模型（例如官方 DeepSeek）使用 OpenAI-compatible Chat Completions，
少量模型使用 Anthropic Messages。该模块只做“协议翻译”，不会实现第二套
Agent 循环：工具调用仍由 Codex App Server 决策和执行，Codebot 只把模型采样
结果还原成 Responses output item 交还给 Codex。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import httpx


MAX_UPSTREAM_RESPONSE_BYTES = 16 * 1024 * 1024


class CodexModelBridgeError(RuntimeError):
    """携带可安全返回给 Codex 的 HTTP 状态码和错误信息。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = max(400, min(int(status_code or 502), 599))


@dataclass(frozen=True)
class BridgedResponsesResult:
    """一个可同时序列化为 JSON 与 Responses SSE 的完成结果。"""

    response: Dict[str, Any]

    def as_sse(self) -> str:
        """Codex 只要求合法的 Responses 事件序列，不要求上游逐 token 流式。

        这里先用非流式请求兼容尽可能多的 OpenCode provider，再把完整 message /
        tool call 作为 output_item.done 返回。Codex 官方测试同样接受该最小序列。
        """
        response_id = str(self.response.get("id") or f"resp_codebot_{uuid.uuid4().hex}")
        events: List[Dict[str, Any]] = [
            {"type": "response.created", "response": {"id": response_id}},
        ]
        for item in self.response.get("output") or []:
            if isinstance(item, dict):
                events.append({"type": "response.output_item.done", "item": item})
        events.append({"type": "response.completed", "response": self.response})
        return "".join(
            f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            for event in events
        )


@dataclass(frozen=True)
class BridgedChatResult:
    """一个 OpenAI-compatible Chat Completions 完成结果。"""

    response: Dict[str, Any]

    def as_sse(self) -> str:
        """把单次上游采样转换为合法 Chat Completions SSE。

        上游协议不一定支持一致的流式工具调用格式，因此桥接层先完成一次采样，
        再以可取消的本机 SSE 返回正文和工具调用；这不会创建 OpenCode Session，
        也不会引入第二个 Agent 循环。
        """
        response_id = str(self.response.get("id") or f"chatcmpl-codebot-{uuid.uuid4().hex}")
        created = int(self.response.get("created") or time.time())
        model = str(self.response.get("model") or "")
        choices = self.response.get("choices") if isinstance(self.response.get("choices"), list) else []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        base = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "system_fingerprint": self.response.get("system_fingerprint"),
        }
        chunks: List[Dict[str, Any]] = []
        chunks.append({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text") or "") if isinstance(part, dict) else str(part)
                for part in content
            )
        text = str(content or "")
        # 小块输出能让 Rakazo Pi 使用与真实 provider 相同的增量消费路径。
        for offset in range(0, len(text), 96):
            chunks.append({
                **base,
                "choices": [{"index": 0, "delta": {"content": text[offset:offset + 96]}, "finish_reason": None}],
            })
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
            chunks.append({
                **base,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": index,
                            "id": str(raw_call.get("id") or f"call_{uuid.uuid4().hex}"),
                            "type": "function",
                            "function": {
                                "name": str(function.get("name") or "tool"),
                                "arguments": str(function.get("arguments") or "{}"),
                            },
                        }],
                    },
                    "finish_reason": None,
                }],
            })
        chunks.append({
            **base,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": choice.get("finish_reason") or ("tool_calls" if message.get("tool_calls") else "stop"),
            }],
            "usage": self.response.get("usage"),
        })
        return "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


@dataclass(frozen=True)
class ProtocolBridgeAdapter:
    """一个 OpenCode 上游协议到 Codex Responses 的注册式适配器。

    OpenCode 的模型目录用 ``api.npm`` 表示它实际采用的 AI SDK provider。
    这里把“包名识别、请求端点、鉴权、请求转换、响应恢复”收敛成显式注册表，
    后续增加 Google/Bedrock 等协议时只新增独立适配器，不再修改运行时路由主流程。
    """

    protocol: str
    label: str
    public_protocol: str
    npm_packages: Tuple[str, ...]
    endpoint_suffix: str
    auth_kind: str
    request_builder: Callable[[Mapping[str, Any]], Tuple[Dict[str, Any], Dict[str, str]]]
    response_converter: Callable[..., BridgedResponsesResult]


def _json_text(value: Any) -> str:
    """把工具输出或结构化内容稳定压成模型可读取的文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content_to_chat(content: Any) -> Any:
    """把 Responses message content 转为 OpenAI-compatible chat content。"""
    if isinstance(content, str) or content is None:
        return content or ""
    if not isinstance(content, list):
        return _json_text(content)

    parts: List[Dict[str, Any]] = []
    text_only = True
    for part in content:
        if not isinstance(part, dict):
            parts.append({"type": "text", "text": _json_text(part)})
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"input_text", "output_text", "text"}:
            parts.append({"type": "text", "text": str(part.get("text") or "")})
            continue
        if part_type in {"input_image", "image_url"}:
            image_url = part.get("image_url") or part.get("url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            if image_url:
                text_only = False
                parts.append({"type": "image_url", "image_url": {"url": str(image_url)}})
            continue
        # 未知内容块不作为指令解释，仅降级成紧凑 JSON 文本，防止静默丢上下文。
        parts.append({"type": "text", "text": _json_text(part)})

    if text_only:
        return "\n".join(str(part.get("text") or "") for part in parts).strip()
    return parts


def _tool_output_text(item: Mapping[str, Any]) -> str:
    output = item.get("output")
    if isinstance(output, list):
        texts: List[str] = []
        for part in output:
            if isinstance(part, dict) and part.get("type") in {"input_text", "output_text", "text"}:
                texts.append(str(part.get("text") or ""))
            else:
                texts.append(_json_text(part))
        return "\n".join(texts)
    return _json_text(output)


def _chat_tool_definition(tool: Mapping[str, Any]) -> Optional[Tuple[Dict[str, Any], str]]:
    """把 Responses function/custom tool 映射为 Chat Completions function tool。

    Chat Completions 没有 Responses 的自由文本 custom tool。对此使用一个只含
    ``input`` 字符串的函数参数壳；返回时再恢复为 custom_tool_call，Codex 仍会
    通过自己的 apply_patch/shell 等工具实现执行。
    """
    tool_type = str(tool.get("type") or "")
    name = str(tool.get("name") or "").strip()
    if not name or tool_type not in {"function", "custom"}:
        return None
    if tool_type == "function":
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
    else:
        parameters = {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "传给 Codex custom tool 的原始文本输入",
                }
            },
            "required": ["input"],
            "additionalProperties": False,
        }
    definition = {
        "type": "function",
        "function": {
            "name": name,
            "description": str(tool.get("description") or ""),
            "parameters": parameters,
        },
    }
    return definition, tool_type


def build_chat_completions_request(payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """将 Codex 发出的 Responses 请求转换成非流式 Chat Completions 请求。"""
    messages: List[Dict[str, Any]] = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": _content_to_chat(instructions)})

    raw_input = payload.get("input")
    input_items: Iterable[Any]
    if isinstance(raw_input, list):
        input_items = raw_input
    else:
        input_items = [{"type": "message", "role": "user", "content": raw_input or ""}]

    for item in input_items:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": _json_text(item)})
            continue
        item_type = str(item.get("type") or "message")
        if item_type in {"message", "easy_input_message"}:
            role = str(item.get("role") or "user")
            if role in {"developer", "system"}:
                # DeepSeek 等 OpenAI-compatible provider 对 developer role 的实现并不一致，
                # 统一折叠为 system 能获得更稳定的兼容性。
                role = "system"
            if role not in {"system", "user", "assistant"}:
                role = "user"
            messages.append({"role": role, "content": _content_to_chat(item.get("content"))})
            continue
        if item_type in {"function_call", "custom_tool_call"}:
            call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}")
            name = str(item.get("name") or "tool")
            arguments = item.get("arguments")
            if item_type == "custom_tool_call":
                arguments = json.dumps({"input": str(item.get("input") or "")}, ensure_ascii=False)
            elif not isinstance(arguments, str):
                arguments = _json_text(arguments or {})
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }],
            })
            continue
        if item_type in {"function_call_output", "custom_tool_call_output"}:
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or item.get("id") or ""),
                "content": _tool_output_text(item),
            })
            continue
        # reasoning 等仅供 Responses 状态管理的 item 不应伪装成用户指令。

    tool_kinds: Dict[str, str] = {}
    chat_tools: List[Dict[str, Any]] = []
    for raw_tool in payload.get("tools") or []:
        if not isinstance(raw_tool, dict):
            continue
        converted = _chat_tool_definition(raw_tool)
        if converted is None:
            continue
        definition, original_type = converted
        name = str(definition["function"]["name"])
        tool_kinds[name] = original_type
        chat_tools.append(definition)

    request: Dict[str, Any] = {
        "model": str(payload.get("model") or ""),
        "messages": messages or [{"role": "user", "content": ""}],
        "stream": False,
    }
    if chat_tools:
        request["tools"] = chat_tools
        request["parallel_tool_calls"] = bool(payload.get("parallel_tool_calls", True))
        tool_choice = payload.get("tool_choice")
        if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
            request["tool_choice"] = tool_choice
        elif isinstance(tool_choice, dict):
            choice_name = str(tool_choice.get("name") or "").strip()
            if choice_name:
                request["tool_choice"] = {"type": "function", "function": {"name": choice_name}}
    for source, target in (
        ("max_output_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
    ):
        value = payload.get(source)
        if value is not None:
            request[target] = value
    reasoning = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else {}
    effort = str(reasoning.get("effort") or "").strip()
    if effort in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
        # OpenCode 模型目录中的 reasoning variants 采用同名 reasoningEffort；
        # OpenAI-compatible 请求字段使用 snake_case。
        request["reasoning_effort"] = effort
    return request, tool_kinds


def _usage_from_chat(usage: Any) -> Dict[str, Any]:
    data = usage if isinstance(usage, dict) else {}
    input_tokens = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
    output_tokens = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": None,
        "output_tokens": output_tokens,
        "output_tokens_details": None,
        "total_tokens": int(data.get("total_tokens") or (input_tokens + output_tokens)),
    }


def chat_completion_to_responses(
    data: Mapping[str, Any],
    *,
    requested_model: str,
    tool_kinds: Mapping[str, str],
) -> BridgedResponsesResult:
    """把一次 Chat Completions 结果恢复为 Codex 可消费的 Responses output。"""
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    if not choices or not isinstance(choices[0], dict):
        raise CodexModelBridgeError(502, "上游 Chat Completions 没有返回 choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise CodexModelBridgeError(502, "上游 Chat Completions 没有返回 assistant message")

    output: List[Dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "")
    if text:
        output.append({
            "type": "message",
            "role": "assistant",
            "id": f"msg_codebot_{uuid.uuid4().hex}",
            "content": [{"type": "output_text", "text": text}],
        })

    for raw_call in message.get("tool_calls") or []:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        call_id = str(raw_call.get("id") or f"call_{uuid.uuid4().hex}")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = _json_text(arguments or {})
        if tool_kinds.get(name) == "custom":
            custom_input = arguments
            try:
                parsed = json.loads(arguments)
                if isinstance(parsed, dict) and "input" in parsed:
                    custom_input = str(parsed.get("input") or "")
            except (TypeError, ValueError):
                pass
            output.append({
                "type": "custom_tool_call",
                "call_id": call_id,
                "name": name,
                "input": custom_input,
            })
        else:
            output.append({
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            })

    if not output:
        raise CodexModelBridgeError(502, "上游模型既没有返回正文，也没有返回工具调用")
    response_id = str(data.get("id") or f"resp_codebot_{uuid.uuid4().hex}")
    return BridgedResponsesResult({
        "id": response_id,
        "object": "response",
        "created_at": int(data.get("created") or time.time()),
        "status": "completed",
        "model": str(data.get("model") or requested_model),
        "output": output,
        "usage": _usage_from_chat(data.get("usage")),
    })


def _merge_anthropic_message(messages: List[Dict[str, Any]], role: str, blocks: List[Dict[str, Any]]) -> None:
    """Anthropic 要求 user/assistant 交替；相邻同角色内容在边界处合并。"""
    if messages and messages[-1].get("role") == role:
        existing = messages[-1].get("content")
        if isinstance(existing, list):
            existing.extend(blocks)
            return
    messages.append({"role": role, "content": blocks})


def build_anthropic_request(payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """经统一 Chat 中间表示生成 Anthropic Messages 请求。"""
    chat_request, tool_kinds = build_chat_completions_request(payload)
    system_parts: List[str] = []
    messages: List[Dict[str, Any]] = []
    for message in chat_request.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if role == "system":
            system_parts.append(_json_text(message.get("content")))
            continue
        if role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": _json_text(message.get("content"))})
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) and isinstance(call.get("function"), dict) else {}
                arguments = function.get("arguments")
                try:
                    parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (TypeError, ValueError):
                    parsed_arguments = {"input": str(arguments or "")}
                if not isinstance(parsed_arguments, dict):
                    parsed_arguments = {"input": parsed_arguments}
                blocks.append({
                    "type": "tool_use",
                    "id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
                    "name": str(function.get("name") or "tool"),
                    "input": parsed_arguments,
                })
            _merge_anthropic_message(messages, "assistant", blocks or [{"type": "text", "text": ""}])
            continue
        if role == "tool":
            _merge_anthropic_message(messages, "user", [{
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": _json_text(message.get("content")),
            }])
            continue
        content = message.get("content")
        if isinstance(content, list):
            blocks = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    # 外部 URL 图片并非所有 Anthropic-compatible 网关都支持，作为文本引用保留。
                    blocks.append({"type": "text", "text": _json_text(part)})
                else:
                    blocks.append({"type": "text", "text": _json_text(part.get("text") if isinstance(part, dict) else part)})
        else:
            blocks = [{"type": "text", "text": _json_text(content)}]
        _merge_anthropic_message(messages, "user", blocks)

    anthropic_tools: List[Dict[str, Any]] = []
    for tool in chat_request.get("tools") or []:
        function = tool.get("function") if isinstance(tool, dict) and isinstance(tool.get("function"), dict) else {}
        if function.get("name"):
            anthropic_tools.append({
                "name": str(function.get("name")),
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            })
    request: Dict[str, Any] = {
        "model": str(payload.get("model") or ""),
        "messages": messages or [{"role": "user", "content": [{"type": "text", "text": ""}]}],
        "max_tokens": int(payload.get("max_output_tokens") or 8192),
        "stream": False,
    }
    if system_parts:
        request["system"] = "\n\n".join(part for part in system_parts if part)
    if anthropic_tools:
        request["tools"] = anthropic_tools
        tool_choice = chat_request.get("tool_choice")
        if tool_choice == "required":
            request["tool_choice"] = {"type": "any"}
        elif tool_choice == "auto":
            request["tool_choice"] = {"type": "auto"}
        elif isinstance(tool_choice, dict):
            function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
            name = str(function.get("name") or "").strip()
            if name:
                request["tool_choice"] = {"type": "tool", "name": name}
        elif tool_choice == "none":
            # Anthropic Messages 没有 ``none`` 取值；不发送 tools 才是同义行为。
            request.pop("tools", None)
    return request, tool_kinds


def anthropic_to_responses(
    data: Mapping[str, Any],
    *,
    requested_model: str,
    tool_kinds: Mapping[str, str],
) -> BridgedResponsesResult:
    """先转为 Chat 兼容结构，再复用统一 Responses 输出恢复逻辑。"""
    content_blocks = data.get("content") if isinstance(data.get("content"), list) else []
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": str(block.get("id") or f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or "tool"),
                    "arguments": _json_text(block.get("input") or {}),
                },
            })
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    pseudo_chat = {
        "id": data.get("id") or f"resp_codebot_{uuid.uuid4().hex}",
        "created": int(time.time()),
        "model": data.get("model") or requested_model,
        "choices": [{"message": {"role": "assistant", "content": "\n".join(text_parts), "tool_calls": tool_calls}}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens") or 0,
            "completion_tokens": usage.get("output_tokens") or 0,
        },
    }
    return chat_completion_to_responses(
        pseudo_chat,
        requested_model=requested_model,
        tool_kinds=tool_kinds,
    )


def build_responses_proxy_request(payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """为需要自定义 header/query 的 Responses 上游生成一次非流式请求。"""
    request = dict(payload)
    request["stream"] = False
    return request, {}


def responses_proxy_to_result(
    data: Mapping[str, Any],
    *,
    requested_model: str,
    tool_kinds: Mapping[str, str],
) -> BridgedResponsesResult:
    """校验 Responses 代理结果，避免把上游错误 JSON 当作成功事件。"""
    del tool_kinds
    output = data.get("output")
    if not isinstance(output, list):
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = str(error.get("message") or "上游 Responses 没有返回 output")
        raise CodexModelBridgeError(502, message[:2000])
    response = dict(data)
    response.setdefault("id", f"resp_codebot_{uuid.uuid4().hex}")
    response.setdefault("object", "response")
    response.setdefault("created_at", int(time.time()))
    response.setdefault("status", "completed")
    response.setdefault("model", requested_model)
    return BridgedResponsesResult(response)


# 协议注册表只收录已实现并保留 Codex 工具调用语义的转换器。不能仅因某个
# provider “大体兼容 OpenAI”就猜测端点，否则错误会表现成选中 A 模型却由 B
# 模型回答。新增适配器时必须同时补充请求/响应与工具回环测试。
_PROTOCOL_ADAPTERS: Dict[str, ProtocolBridgeAdapter] = {
    "responses_proxy": ProtocolBridgeAdapter(
        protocol="responses_proxy",
        label="Responses 安全代理",
        public_protocol="responses-proxy",
        # 默认 Responses 模型仍由 Codex 直连；只有需要逐模型 header/query 的
        # 路由才显式选择此适配器，所以不通过 npm 包名自动命中。
        npm_packages=(),
        endpoint_suffix="/responses",
        auth_kind="bearer",
        request_builder=build_responses_proxy_request,
        response_converter=responses_proxy_to_result,
    ),
    "chat_completions": ProtocolBridgeAdapter(
        protocol="chat_completions",
        label="OpenAI-compatible Chat Completions",
        public_protocol="chat-completions-bridge",
        npm_packages=("@ai-sdk/openai-compatible",),
        endpoint_suffix="/chat/completions",
        auth_kind="bearer",
        request_builder=build_chat_completions_request,
        response_converter=chat_completion_to_responses,
    ),
    "anthropic": ProtocolBridgeAdapter(
        protocol="anthropic",
        label="Anthropic Messages",
        public_protocol="anthropic-bridge",
        npm_packages=("@ai-sdk/anthropic",),
        endpoint_suffix="/messages",
        auth_kind="anthropic",
        request_builder=build_anthropic_request,
        response_converter=anthropic_to_responses,
    ),
}
_NPM_TO_PROTOCOL: Dict[str, str] = {
    package.lower(): adapter.protocol
    for adapter in _PROTOCOL_ADAPTERS.values()
    for package in adapter.npm_packages
}


def bridge_protocol_for_npm(npm_package: str) -> Optional[str]:
    """返回已验证的桥接协议；未知 AI SDK provider 必须显式返回 ``None``。"""
    return _NPM_TO_PROTOCOL.get(str(npm_package or "").strip().lower())


def bridge_adapter(protocol: str) -> Optional[ProtocolBridgeAdapter]:
    """按内部协议名读取不可变适配器元数据。"""
    return _PROTOCOL_ADAPTERS.get(str(protocol or "").strip())


def bridge_protocol_metadata() -> List[Dict[str, Any]]:
    """返回不含地址或凭据的适配能力说明，供状态页和测试展示。"""
    return [
        {
            "protocol": adapter.protocol,
            "label": adapter.label,
            "publicProtocol": adapter.public_protocol,
            "npmPackages": list(adapter.npm_packages),
        }
        for adapter in _PROTOCOL_ADAPTERS.values()
    ]


def _safe_upstream_error(response: httpx.Response) -> str:
    """只提取短错误信息，不把上游 HTML、凭据或完整响应写入 Codebot 错误。"""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return str(message)[:2000]
        if data.get("message"):
            return str(data.get("message"))[:2000]
    return f"上游模型返回 HTTP {response.status_code}"


async def _post_upstream(url: str, headers: Dict[str, str], query: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    timeout = httpx.Timeout(180.0, connect=15.0, read=180.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(url, headers=headers, params=query or None, json=body)
    if len(response.content) > MAX_UPSTREAM_RESPONSE_BYTES:
        raise CodexModelBridgeError(502, "上游模型响应超过 16 MiB 安全上限")
    if response.status_code >= 400:
        raise CodexModelBridgeError(response.status_code, _safe_upstream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise CodexModelBridgeError(502, "上游模型没有返回合法 JSON") from exc
    if not isinstance(data, dict):
        raise CodexModelBridgeError(502, "上游模型返回的数据结构无效")
    return data


async def bridge_responses_request(route: Any, payload: Mapping[str, Any]) -> BridgedResponsesResult:
    """按注册表选择协议翻译器并完成一次模型采样。

    这里始终只有一次上游模型采样；线程、shell、apply_patch、审批和继续执行
    仍完全由 Codex App Server 掌管，不会嵌套 OpenCode Agent 循环。
    """
    protocol = str(getattr(route, "upstream_protocol", "") or "")
    base_url = str(getattr(route, "base_url", "") or "").rstrip("/")
    effective_payload = dict(payload)
    requested_model = str(effective_payload.get("model") or getattr(route, "codex_model", "") or "")
    max_output_tokens = getattr(route, "max_output_tokens", None)
    requested_max = effective_payload.get("max_output_tokens")
    try:
        requested_max_value = int(requested_max) if requested_max is not None else None
    except (TypeError, ValueError):
        requested_max_value = None
    if max_output_tokens and (requested_max_value is None or requested_max_value > int(max_output_tokens)):
        effective_payload["max_output_tokens"] = int(max_output_tokens)
    headers = {
        str(key): str(value)
        for key, value in (getattr(route, "request_headers", ()) or ())
        if str(key).strip() and value is not None
    }
    query = {
        str(key): str(value)
        for key, value in (getattr(route, "query_params", ()) or ())
        if str(key).strip() and value is not None
    }
    api_key = str(getattr(route, "api_key", "") or "")

    adapter = bridge_adapter(protocol)
    if adapter is None:
        raise CodexModelBridgeError(400, f"Codebot 尚未注册协议桥接器：{protocol or 'unknown'}")

    request, tool_kinds = adapter.request_builder(effective_payload)
    _apply_opencode_oauth_responses_contract(route, request, headers)
    if api_key and adapter.auth_kind == "bearer":
        headers.setdefault("Authorization", f"Bearer {api_key}")
    elif api_key and adapter.auth_kind == "anthropic":
        headers.setdefault("x-api-key", api_key)
    if adapter.auth_kind == "anthropic":
        headers.setdefault("anthropic-version", "2023-06-01")
    headers.setdefault("Content-Type", "application/json")
    data = await _post_upstream(
        f"{base_url}{adapter.endpoint_suffix}",
        headers,
        query,
        request,
    )
    return adapter.response_converter(
        data,
        requested_model=requested_model,
        tool_kinds=tool_kinds,
    )


def _chat_payload_to_responses(payload: Mapping[str, Any], upstream_model: str) -> Dict[str, Any]:
    """把 Rakazo/Pi 的 Chat Completions 输入转换为 Responses 输入。"""
    instructions: List[str] = []
    input_items: List[Dict[str, Any]] = []
    for raw_message in payload.get("messages") or []:
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role") or "user")
        if role in {"system", "developer"}:
            instructions.append(_json_text(raw_message.get("content")))
            continue
        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": str(raw_message.get("tool_call_id") or ""),
                "output": _json_text(raw_message.get("content")),
            })
            continue
        if role == "assistant":
            if raw_message.get("content"):
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": raw_message.get("content"),
                })
            for raw_call in raw_message.get("tool_calls") or []:
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
                input_items.append({
                    "type": "function_call",
                    "call_id": str(raw_call.get("id") or f"call_{uuid.uuid4().hex}"),
                    "name": str(function.get("name") or "tool"),
                    "arguments": str(function.get("arguments") or "{}"),
                })
            continue
        input_items.append({
            "type": "message",
            "role": "user",
            "content": raw_message.get("content") or "",
        })

    tools: List[Dict[str, Any]] = []
    for raw_tool in payload.get("tools") or []:
        if not isinstance(raw_tool, dict):
            continue
        function = raw_tool.get("function") if isinstance(raw_tool.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        tools.append({
            "type": "function",
            "name": name,
            "description": str(function.get("description") or ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            "strict": bool(function.get("strict", False)),
        })

    request: Dict[str, Any] = {
        "model": upstream_model,
        "input": input_items or [{"type": "message", "role": "user", "content": ""}],
        "stream": False,
    }
    if instructions:
        request["instructions"] = "\n\n".join(part for part in instructions if part)
    if tools:
        request["tools"] = tools
        request["parallel_tool_calls"] = bool(payload.get("parallel_tool_calls", True))
        tool_choice = payload.get("tool_choice")
        if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
            request["tool_choice"] = tool_choice
        elif isinstance(tool_choice, dict):
            function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
            name = str(function.get("name") or "").strip()
            if name:
                request["tool_choice"] = {"type": "function", "name": name}
    max_tokens = payload.get("max_completion_tokens", payload.get("max_tokens"))
    if max_tokens is not None:
        request["max_output_tokens"] = max_tokens
    for key in ("temperature", "top_p"):
        if payload.get(key) is not None:
            request[key] = payload.get(key)
    effort = str(payload.get("reasoning_effort") or "").strip()
    if effort:
        request["reasoning"] = {"effort": effort}
    return request


def _apply_opencode_oauth_responses_contract(
    route: Any,
    request: Dict[str, Any],
    headers: Dict[str, str],
) -> None:
    """复用 OpenCode 内置 OpenAI OAuth 的纯 Responses 传输合同。

    这只调整一次模型请求的端点合同，不调用 OpenCode Session/Agent。OAuth
    token 与账号 header 均来自宿主内存中的精确路由，绝不传给 Rakazo 容器。
    """
    if str(getattr(route, "credential_mode", "") or "") != "opencode_oauth":
        return
    # OpenCode 的 ``chat.params`` 会清除 maxOutputTokens 以匹配 Codex 后端；
    # 同时每次请求提供独立 session-id。探测仍会核验真实 usage 与结束原因。
    request.pop("max_output_tokens", None)
    request["store"] = False
    request.setdefault("instructions", "Answer the user request directly and use tools when required.")
    headers.setdefault("session-id", str(uuid.uuid4()))


def _responses_to_chat(result: BridgedResponsesResult, route: Any) -> BridgedChatResult:
    """把 Responses output 恢复为 Rakazo Pi 可消费的 Chat Completion。"""
    response = result.response
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text_parts.append(str(part.get("text") or ""))
        elif item_type == "function_call":
            tool_calls.append({
                "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": str(item.get("name") or "tool"),
                    "arguments": str(item.get("arguments") or "{}"),
                },
            })
        elif item_type == "custom_tool_call":
            tool_calls.append({
                "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": str(item.get("name") or "tool"),
                    "arguments": json.dumps({"input": str(item.get("input") or "")}, ensure_ascii=False),
                },
            })
    if not text_parts and not tool_calls:
        raise CodexModelBridgeError(502, "上游 Responses 既没有正文，也没有工具调用")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    requested_model = str(getattr(route, "display_id", "") or "")
    actual_model = str(response.get("model") or getattr(route, "codex_model", "") or "")
    response_status = str(response.get("status") or "completed")
    finish_reason = "tool_calls" if tool_calls else ("length" if response_status == "incomplete" else "stop")
    completion = {
        "id": str(response.get("id") or f"chatcmpl-codebot-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": int(response.get("created_at") or time.time()),
        # 对外模型身份固定为用户选择的 OpenCode route；上游实际 model 单独记录，
        # 避免别名改写历史，同时保留探测时核验真实身份的证据。
        "model": requested_model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "\n".join(part for part in text_parts if part) or None,
                **({"tool_calls": tool_calls} if tool_calls else {}),
            },
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
        },
        "system_fingerprint": f"codebot:{str(getattr(route, 'route_fingerprint', ''))[:24]}",
        "codebot_route": {
            "routeId": requested_model,
            "provider": str(getattr(route, "opencode_provider", "") or ""),
            "requestedModel": requested_model,
            "upstreamModel": str(getattr(route, "codex_model", "") or ""),
            "actualModel": actual_model,
            "protocol": str(getattr(route, "upstream_protocol", "") or ""),
            "routeFingerprint": str(getattr(route, "route_fingerprint", "") or ""),
        },
    }
    return BridgedChatResult(completion)


def _chat_request_for_upstream(payload: Mapping[str, Any], route: Any) -> Dict[str, Any]:
    """保留明确支持的 Chat Completions 字段，拒绝把内部字段透传上游。"""
    allowed = {
        "messages", "tools", "tool_choice", "parallel_tool_calls", "temperature", "top_p",
        "max_tokens", "max_completion_tokens", "stop", "response_format", "seed",
        "frequency_penalty", "presence_penalty", "logit_bias", "user", "reasoning_effort",
    }
    request = {key: value for key, value in payload.items() if key in allowed}
    request["model"] = str(getattr(route, "codex_model", "") or "")
    request["stream"] = False
    route_limit = getattr(route, "max_output_tokens", None)
    if route_limit:
        requested = request.get("max_completion_tokens", request.get("max_tokens"))
        try:
            requested_value = int(requested) if requested is not None else None
        except (TypeError, ValueError):
            requested_value = None
        if requested_value is None or requested_value > int(route_limit):
            if "max_completion_tokens" in request:
                request["max_completion_tokens"] = int(route_limit)
            else:
                request["max_tokens"] = int(route_limit)
    return request


async def bridge_chat_completions_request(route: Any, payload: Mapping[str, Any]) -> BridgedChatResult:
    """对一个精确 OpenCode 路由执行一次原始模型采样。

    本函数不导入 OpenCodeClient、不创建 session，也不调用任何 Agent 执行入口。
    Rakazo 的工具循环完全留在 Rakazo Pi 内，Codebot 只做协议转换和凭据注入。
    """
    protocol = str(getattr(route, "upstream_protocol", "") or "")
    base_url = str(getattr(route, "base_url", "") or "").rstrip("/")
    if not base_url:
        raise CodexModelBridgeError(400, "模型路由缺少上游 API 地址")
    if protocol == "unknown":
        raise CodexModelBridgeError(400, "模型协议尚未适配")

    if protocol == "chat_completions":
        adapter = bridge_adapter(protocol)
        if adapter is None:
            raise CodexModelBridgeError(400, "Chat Completions 协议适配器未注册")
        headers = {
            str(key): str(value)
            for key, value in (getattr(route, "request_headers", ()) or ())
            if str(key).strip() and value is not None
        }
        query = {
            str(key): str(value)
            for key, value in (getattr(route, "query_params", ()) or ())
            if str(key).strip() and value is not None
        }
        api_key = str(getattr(route, "api_key", "") or "")
        if api_key:
            headers.setdefault("Authorization", f"Bearer {api_key}")
        headers.setdefault("Content-Type", "application/json")
        data = await _post_upstream(
            f"{base_url}{adapter.endpoint_suffix}",
            headers,
            query,
            _chat_request_for_upstream(payload, route),
        )
        # 先复用已验证的 Chat -> Responses 工具语义，再统一恢复成 Chat 格式。
        tool_kinds = {
            str((tool.get("function") or {}).get("name")): "function"
            for tool in (payload.get("tools") or [])
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        }
        responses_result = chat_completion_to_responses(
            data,
            requested_model=str(getattr(route, "codex_model", "") or ""),
            tool_kinds=tool_kinds,
        )
        return _responses_to_chat(responses_result, route)

    responses_payload = _chat_payload_to_responses(
        payload,
        str(getattr(route, "codex_model", "") or ""),
    )
    if protocol == "responses":
        headers = {
            str(key): str(value)
            for key, value in (getattr(route, "request_headers", ()) or ())
            if str(key).strip() and value is not None
        }
        query = {
            str(key): str(value)
            for key, value in (getattr(route, "query_params", ()) or ())
            if str(key).strip() and value is not None
        }
        api_key = str(getattr(route, "api_key", "") or "")
        if api_key:
            headers.setdefault("Authorization", f"Bearer {api_key}")
        headers.setdefault("Content-Type", "application/json")
        _apply_opencode_oauth_responses_contract(route, responses_payload, headers)
        data = await _post_upstream(f"{base_url}/responses", headers, query, responses_payload)
        responses_result = responses_proxy_to_result(
            data,
            requested_model=str(getattr(route, "codex_model", "") or ""),
            tool_kinds={},
        )
    else:
        responses_result = await bridge_responses_request(route, responses_payload)
    return _responses_to_chat(responses_result, route)


async def _stream_upstream_sse(
    url: str,
    headers: Dict[str, str],
    query: Dict[str, str],
    body: Dict[str, Any],
) -> AsyncIterator[Tuple[str, Any]]:
    """逐事件读取上游 SSE，并让取消操作关闭真实 HTTP 响应。

    这里不缓存完整响应、不创建 OpenCode Session。调用方停止迭代或任务被取消时，
    ``httpx`` 的流上下文会立即关闭上游连接，因此 Rakazo 的停止按钮能够传递到
    provider，而不是只停止本机对已完成结果的切片。
    """
    timeout = httpx.Timeout(180.0, connect=15.0, read=180.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("POST", url, headers=headers, params=query or None, json=body) as response:
            if response.status_code >= 400:
                content = await response.aread()
                if len(content) > MAX_UPSTREAM_RESPONSE_BYTES:
                    raise CodexModelBridgeError(502, "上游模型错误响应超过 16 MiB 安全上限")
                raise CodexModelBridgeError(response.status_code, _safe_upstream_error(response))

            total_bytes = 0
            event_name = ""
            data_lines: List[str] = []

            def decode_event() -> Optional[Tuple[str, Any]]:
                nonlocal event_name, data_lines
                if not data_lines:
                    event_name = ""
                    return None
                raw_data = "\n".join(data_lines)
                current_name = event_name
                event_name = ""
                data_lines = []
                if raw_data.strip() == "[DONE]":
                    return current_name, "[DONE]"
                try:
                    value = json.loads(raw_data)
                except ValueError as exc:
                    raise CodexModelBridgeError(502, "上游流返回了无效 JSON 事件") from exc
                if not isinstance(value, dict):
                    raise CodexModelBridgeError(502, "上游流事件不是 JSON 对象")
                if current_name and not value.get("type"):
                    value["type"] = current_name
                return current_name, value

            async for line in response.aiter_lines():
                total_bytes += len(line.encode("utf-8", errors="replace")) + 1
                if total_bytes > MAX_UPSTREAM_RESPONSE_BYTES:
                    raise CodexModelBridgeError(502, "上游模型流超过 16 MiB 安全上限")
                if line == "":
                    decoded = decode_event()
                    if decoded is not None:
                        yield decoded
                    continue
                if line.startswith(":"):
                    continue
                field, separator, value = line.partition(":")
                if not separator:
                    continue
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    event_name = value.strip()
                elif field == "data":
                    data_lines.append(value)
            decoded = decode_event()
            if decoded is not None:
                yield decoded


def _route_stream_headers(route: Any, *, auth_kind: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    headers = {
        str(key): str(value)
        for key, value in (getattr(route, "request_headers", ()) or ())
        if str(key).strip() and value is not None
    }
    query = {
        str(key): str(value)
        for key, value in (getattr(route, "query_params", ()) or ())
        if str(key).strip() and value is not None
    }
    api_key = str(getattr(route, "api_key", "") or "")
    if api_key and auth_kind == "bearer":
        headers.setdefault("Authorization", f"Bearer {api_key}")
    elif api_key and auth_kind == "anthropic":
        headers.setdefault("x-api-key", api_key)
    if auth_kind == "anthropic":
        headers.setdefault("anthropic-version", "2023-06-01")
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "text/event-stream")
    return headers, query


def _chat_stream_chunk(
    route: Any,
    *,
    response_id: str,
    created: int,
    actual_model: str,
    delta: Optional[Mapping[str, Any]] = None,
    finish_reason: Optional[str] = None,
    usage: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    requested_model = str(getattr(route, "display_id", "") or "")
    chunk: Dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": requested_model,
        "choices": [{
            "index": 0,
            "delta": dict(delta or {}),
            "finish_reason": finish_reason,
        }],
        "system_fingerprint": f"codebot:{str(getattr(route, 'route_fingerprint', ''))[:24]}",
        "codebot_route": {
            "routeId": requested_model,
            "provider": str(getattr(route, "opencode_provider", "") or ""),
            "requestedModel": requested_model,
            "upstreamModel": str(getattr(route, "codex_model", "") or ""),
            "actualModel": actual_model,
            "protocol": str(getattr(route, "upstream_protocol", "") or ""),
            "routeFingerprint": str(getattr(route, "route_fingerprint", "") or ""),
        },
    }
    if usage is not None:
        chunk["usage"] = dict(usage)
    return chunk


def _chat_sse(chunk: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(dict(chunk), ensure_ascii=False)}\n\n"


def _chat_usage_from_responses(value: Any) -> Dict[str, int]:
    usage = value if isinstance(value, Mapping) else {}
    prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens") or (prompt + completion)),
    }


async def _bridge_native_chat_stream(route: Any, payload: Mapping[str, Any]) -> AsyncIterator[str]:
    adapter = bridge_adapter("chat_completions")
    if adapter is None:
        raise CodexModelBridgeError(400, "Chat Completions 协议适配器未注册")
    base_url = str(getattr(route, "base_url", "") or "").rstrip("/")
    headers, query = _route_stream_headers(route, auth_kind="bearer")
    request = _chat_request_for_upstream(payload, route)
    request["stream"] = True
    request["stream_options"] = {"include_usage": True}
    response_id = f"chatcmpl-codebot-{uuid.uuid4().hex}"
    created = int(time.time())
    actual_model = ""
    saw_event = False
    # 显式持有并关闭上游生成器。仅写成 ``async for`` 时，调用方在本层
    # ``yield`` 处执行 ``aclose()`` 不保证同步关闭内层 HTTP SSE 生成器，
    # Python 的异步生成器终结器可能稍后再次关闭它，从而产生并发 aclose。
    upstream = _stream_upstream_sse(
        f"{base_url}{adapter.endpoint_suffix}", headers, query, request,
    )
    try:
        async for _, event in upstream:
            if event == "[DONE]":
                break
            if not isinstance(event, Mapping):
                continue
            saw_event = True
            response_id = str(event.get("id") or response_id)
            created = int(event.get("created") or created)
            if event.get("model"):
                actual_model = str(event.get("model"))
            choices = event.get("choices") if isinstance(event.get("choices"), list) else []
            usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else None
            if not choices and usage is not None:
                yield _chat_sse(_chat_stream_chunk(
                    route,
                    response_id=response_id,
                    created=created,
                    actual_model=actual_model,
                    delta={},
                    usage=usage,
                ))
                continue
            for raw_choice in choices[:1]:
                if not isinstance(raw_choice, Mapping):
                    continue
                delta = raw_choice.get("delta") if isinstance(raw_choice.get("delta"), Mapping) else {}
                yield _chat_sse(_chat_stream_chunk(
                    route,
                    response_id=response_id,
                    created=created,
                    actual_model=actual_model,
                    delta=delta,
                    finish_reason=raw_choice.get("finish_reason"),
                    usage=usage,
                ))
    finally:
        await upstream.aclose()
    if not saw_event:
        raise CodexModelBridgeError(502, "上游 Chat Completions 没有返回任何 SSE 事件")
    yield "data: [DONE]\n\n"


async def _bridge_responses_stream(route: Any, payload: Mapping[str, Any]) -> AsyncIterator[str]:
    protocol = str(getattr(route, "upstream_protocol", "") or "")
    adapter = bridge_adapter("responses_proxy")
    if adapter is None:
        raise CodexModelBridgeError(400, "Responses 协议适配器未注册")
    base_url = str(getattr(route, "base_url", "") or "").rstrip("/")
    headers, query = _route_stream_headers(route, auth_kind="bearer")
    request = _chat_payload_to_responses(payload, str(getattr(route, "codex_model", "") or ""))
    request["stream"] = True
    _apply_opencode_oauth_responses_contract(route, request, headers)
    response_id = f"chatcmpl-codebot-{uuid.uuid4().hex}"
    created = int(time.time())
    actual_model = ""
    tool_indexes: Dict[str, int] = {}
    tool_started: set[str] = set()
    tool_seen = False
    completed = False
    endpoint = f"{base_url}/responses" if protocol == "responses" else f"{base_url}{adapter.endpoint_suffix}"
    upstream = _stream_upstream_sse(endpoint, headers, query, request)
    try:
        async for event_name, event in upstream:
            if event == "[DONE]":
                break
            if not isinstance(event, Mapping):
                continue
            event_type = str(event.get("type") or event_name or "")
            response = event.get("response") if isinstance(event.get("response"), Mapping) else {}
            if response.get("id"):
                response_id = str(response.get("id"))
            if response.get("created_at"):
                created = int(response.get("created_at") or created)
            if response.get("model"):
                actual_model = str(response.get("model"))

            if event_type in {"response.output_text.delta", "response.text.delta"}:
                delta_text = str(event.get("delta") or "")
                if delta_text:
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"content": delta_text},
                    ))
            elif event_type in {
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
                "response.reasoning.delta",
            }:
                reasoning = str(event.get("delta") or "")
                if reasoning:
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"reasoning_content": reasoning},
                    ))
            elif event_type in {"response.output_item.added", "response.output_item.done"}:
                item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
                if str(item.get("type") or "") in {"function_call", "custom_tool_call"}:
                    item_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}")
                    if item_id not in tool_indexes:
                        tool_indexes[item_id] = len(tool_indexes)
                    if item_id not in tool_started:
                        tool_started.add(item_id)
                        tool_seen = True
                        arguments = str(item.get("arguments") or "") if event_type.endswith(".done") else ""
                        yield _chat_sse(_chat_stream_chunk(
                            route, response_id=response_id, created=created, actual_model=actual_model,
                            delta={"tool_calls": [{
                                "index": tool_indexes[item_id],
                                "id": item_id,
                                "type": "function",
                                "function": {"name": str(item.get("name") or "tool"), "arguments": arguments},
                            }]},
                        ))
            elif event_type in {"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"}:
                item_id = str(event.get("call_id") or event.get("item_id") or f"call_index_{event.get('output_index', 0)}")
                if item_id not in tool_indexes:
                    tool_indexes[item_id] = int(event.get("output_index") or len(tool_indexes))
                tool_seen = True
                yield _chat_sse(_chat_stream_chunk(
                    route, response_id=response_id, created=created, actual_model=actual_model,
                    delta={"tool_calls": [{
                        "index": tool_indexes[item_id],
                        "id": item_id,
                        "type": "function",
                        "function": {"name": "", "arguments": str(event.get("delta") or "")},
                    }]},
                ))
            elif event_type == "response.completed":
                completed = True
                usage = _chat_usage_from_responses(response.get("usage"))
                yield _chat_sse(_chat_stream_chunk(
                    route, response_id=response_id, created=created, actual_model=actual_model,
                    delta={}, finish_reason="tool_calls" if tool_seen else "stop", usage=usage,
                ))
            elif event_type == "response.incomplete":
                # 推理模型可能把输出预算全部用于 reasoning，并以合法的
                # response.incomplete 结束。它是明确的 length 终态，不应被误报
                # 成“协议没有 response.completed”；若完全无正文/推理/工具，模型
                # 探测仍会在内容检查处失败关闭。
                completed = True
                usage = _chat_usage_from_responses(response.get("usage"))
                yield _chat_sse(_chat_stream_chunk(
                    route, response_id=response_id, created=created, actual_model=actual_model,
                    delta={}, finish_reason="tool_calls" if tool_seen else "length", usage=usage,
                ))
            elif event_type in {"response.failed", "response.error", "error"}:
                error = response.get("error") if isinstance(response.get("error"), Mapping) else event.get("error")
                message = error.get("message") if isinstance(error, Mapping) else error
                raise CodexModelBridgeError(502, str(message or "上游 Responses 流失败")[:2000])
    finally:
        await upstream.aclose()
    if not completed:
        raise CodexModelBridgeError(502, "上游 Responses 流未返回 completed/incomplete 终态")
    yield "data: [DONE]\n\n"


def _anthropic_finish_reason(value: Any, *, tool_seen: bool) -> str:
    reason = str(value or "")
    if reason == "tool_use" or tool_seen:
        return "tool_calls"
    if reason == "max_tokens":
        return "length"
    if reason in {"end_turn", "stop_sequence", "stop"}:
        return "stop"
    return reason or "stop"


async def _bridge_anthropic_stream(route: Any, payload: Mapping[str, Any]) -> AsyncIterator[str]:
    adapter = bridge_adapter("anthropic")
    if adapter is None:
        raise CodexModelBridgeError(400, "Anthropic Messages 协议适配器未注册")
    base_url = str(getattr(route, "base_url", "") or "").rstrip("/")
    headers, query = _route_stream_headers(route, auth_kind="anthropic")
    responses_payload = _chat_payload_to_responses(payload, str(getattr(route, "codex_model", "") or ""))
    request, _ = adapter.request_builder(responses_payload)
    request["stream"] = True
    response_id = f"chatcmpl-codebot-{uuid.uuid4().hex}"
    created = int(time.time())
    actual_model = ""
    prompt_tokens = 0
    completion_tokens = 0
    stop_reason = ""
    tool_seen = False
    completed = False
    tool_by_content_index: Dict[int, Dict[str, Any]] = {}
    upstream = _stream_upstream_sse(
        f"{base_url}{adapter.endpoint_suffix}", headers, query, request,
    )
    try:
        async for event_name, event in upstream:
            if event == "[DONE]":
                break
            if not isinstance(event, Mapping):
                continue
            event_type = str(event.get("type") or event_name or "")
            if event_type == "message_start":
                message = event.get("message") if isinstance(event.get("message"), Mapping) else {}
                response_id = str(message.get("id") or response_id)
                actual_model = str(message.get("model") or actual_model)
                usage = message.get("usage") if isinstance(message.get("usage"), Mapping) else {}
                prompt_tokens = int(usage.get("input_tokens") or 0)
                yield _chat_sse(_chat_stream_chunk(
                    route, response_id=response_id, created=created, actual_model=actual_model,
                    delta={"role": "assistant"},
                ))
            elif event_type == "content_block_start":
                index = int(event.get("index") or 0)
                block = event.get("content_block") if isinstance(event.get("content_block"), Mapping) else {}
                block_type = str(block.get("type") or "")
                if block_type == "text" and block.get("text"):
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"content": str(block.get("text"))},
                    ))
                elif block_type == "tool_use":
                    tool_seen = True
                    tool = {
                        "index": index,
                        "id": str(block.get("id") or f"call_{uuid.uuid4().hex}"),
                        "name": str(block.get("name") or "tool"),
                    }
                    tool_by_content_index[index] = tool
                    initial = block.get("input")
                    arguments = json.dumps(initial, ensure_ascii=False) if isinstance(initial, Mapping) and initial else ""
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"tool_calls": [{
                            "index": index, "id": tool["id"], "type": "function",
                            "function": {"name": tool["name"], "arguments": arguments},
                        }]},
                    ))
            elif event_type == "content_block_delta":
                index = int(event.get("index") or 0)
                delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
                delta_type = str(delta.get("type") or "")
                if delta_type == "text_delta" and delta.get("text"):
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"content": str(delta.get("text"))},
                    ))
                elif delta_type in {"thinking_delta", "signature_delta"} and delta.get("thinking"):
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"reasoning_content": str(delta.get("thinking"))},
                    ))
                elif delta_type == "input_json_delta":
                    tool = tool_by_content_index.get(index) or {
                        "index": index, "id": f"call_index_{index}", "name": "",
                    }
                    tool_seen = True
                    yield _chat_sse(_chat_stream_chunk(
                        route, response_id=response_id, created=created, actual_model=actual_model,
                        delta={"tool_calls": [{
                            "index": index, "id": tool["id"], "type": "function",
                            "function": {"name": "", "arguments": str(delta.get("partial_json") or "")},
                        }]},
                    ))
            elif event_type == "message_delta":
                delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
                stop_reason = str(delta.get("stop_reason") or stop_reason)
                usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else {}
                completion_tokens = int(usage.get("output_tokens") or completion_tokens)
            elif event_type == "message_stop":
                completed = True
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
                yield _chat_sse(_chat_stream_chunk(
                    route, response_id=response_id, created=created, actual_model=actual_model,
                    delta={}, finish_reason=_anthropic_finish_reason(stop_reason, tool_seen=tool_seen), usage=usage,
                ))
            elif event_type == "error":
                error = event.get("error") if isinstance(event.get("error"), Mapping) else {}
                raise CodexModelBridgeError(502, str(error.get("message") or "上游 Anthropic 流失败")[:2000])
    finally:
        await upstream.aclose()
    if not completed:
        raise CodexModelBridgeError(502, "上游 Anthropic 流未返回 message_stop")
    yield "data: [DONE]\n\n"


async def bridge_chat_completions_stream(route: Any, payload: Mapping[str, Any]) -> AsyncIterator[str]:
    """把精确 OpenCode 路由的真实上游流转换为 Rakazo 可消费的 Chat SSE。"""
    protocol = str(getattr(route, "upstream_protocol", "") or "")
    base_url = str(getattr(route, "base_url", "") or "").rstrip("/")
    if not base_url:
        raise CodexModelBridgeError(400, "模型路由缺少上游 API 地址")
    if protocol == "chat_completions":
        selected_stream = _bridge_native_chat_stream(route, payload)
    elif protocol in {"responses", "responses_proxy"}:
        selected_stream = _bridge_responses_stream(route, payload)
    elif protocol == "anthropic":
        selected_stream = _bridge_anthropic_stream(route, payload)
    else:
        raise CodexModelBridgeError(400, f"模型协议尚未实现原生流式：{protocol or 'unknown'}")

    # 确保 Rakazo 中断本层迭代时，同步向下游桥和真实 HTTP 流传播关闭。
    try:
        async for item in selected_stream:
            yield item
    finally:
        await selected_stream.aclose()
