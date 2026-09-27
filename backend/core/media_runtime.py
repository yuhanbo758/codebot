"""独立媒体协议适配器。聊天模型目录不等于图片/语音生成接口。"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
import re
import wave

import httpx

from config import app_config, settings


MAX_ASSET_BYTES = 20 * 1024 * 1024
_desktop_keys: dict[tuple[str, str], str] = {}
_defaults = {
    "image": {"openai": "https://api.openai.com/v1", "ark": "https://ark.cn-beijing.volces.com/api/v3",
              "minimax_image": "https://api.minimax.io/v1",
              "qwen_image": "https://dashscope.aliyuncs.com/compatible-mode/v1",
              "hunyuan_image": "https://tokenhub.tencentmaas.com/v1/wand",
              "openrouter_image": "https://openrouter.ai/api/v1",
              "gemini_image": "https://generativelanguage.googleapis.com/v1"},
    "speech": {"openai": "https://api.openai.com/v1", "volc_tts": "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
               "xiaomi_tts": "https://api.xiaomimimo.com/v1", "minimax_tts": "https://api.minimax.io/v1",
               "qwen_tts": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
               "tencent_minimax_tts": "https://tokenhub.tencentmaas.com/v1/wand",
               "openrouter_tts": "https://openrouter.ai/api/v1",
               "gemini_tts": "https://generativelanguage.googleapis.com/v1beta"},
    "transcription": {"openai": "https://api.openai.com/v1", "ark_chat_audio": "https://ark.cn-beijing.volces.com/api/v3",
                      "xiaomi_asr": "https://api.xiaomimimo.com/v1", "minimax_asr": "https://api.minimax.io/v1",
                      "qwen_asr": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                      "hunyuan_asr": "https://tokenhub.tencentmaas.com/v1/wand",
                      "openrouter_asr": "https://openrouter.ai/api/v1",
                      "gemini_asr": "https://generativelanguage.googleapis.com/v1beta"},
}
_endpoints = {"image": "images/generations", "speech": "audio/speech", "transcription": "audio/transcriptions"}


class MediaError(RuntimeError):
    pass


def validate_service(kind: str, config) -> None:
    if kind not in _defaults or config.protocol not in _defaults[kind]:
        raise MediaError(f"{kind} 不支持此协议")
    if len(config.model) > 200 or len(config.voice) > 200:
        raise MediaError("模型或音色名称过长")
    if config.protocol.startswith("gemini_") and config.model.strip() and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", config.model):
        raise MediaError("Gemini 模型 ID 格式无效")
    fixed_models = {"hunyuan_image": "hy-image-v3", "hunyuan_asr": "hy-asr-3.0-preview"}
    if config.protocol in fixed_models and config.model.strip() and config.model != fixed_models[config.protocol]:
        raise MediaError(f"{config.protocol} 仅支持 {fixed_models[config.protocol]}")
    if config.base_url:
        parsed = urlparse(config.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise MediaError("媒体服务地址无效")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise MediaError("媒体服务仅接受 HTTPS 或本机 HTTP 地址")
        if len(config.base_url) > 500:
            raise MediaError("媒体服务地址过长")


def set_desktop_key(kind: str, key: str, protocol: str = "") -> None:
    if kind not in _defaults:
        raise MediaError("媒体凭据参数无效")
    protocol = protocol or getattr(app_config.media, kind).protocol
    if protocol not in _defaults[kind] or len(key) > 4096:
        raise MediaError("媒体凭据参数无效")
    if key.strip():
        _desktop_keys[(kind, protocol)] = key.strip()
    else:
        _desktop_keys.pop((kind, protocol), None)


def _credential(kind: str, protocol: str) -> str:
    value = _desktop_keys.get((kind, protocol)) or os.environ.get(
        f"CODEBOT_MEDIA_{kind.upper()}_{protocol.upper()}_API_KEY", "").strip()
    if not value and not protocol.startswith(("openrouter_", "gemini_")):
        value = os.environ.get(f"CODEBOT_MEDIA_{kind.upper()}_API_KEY", "").strip()
    return value


def credential_configured(kind: str, protocol: str = "") -> bool:
    if kind not in _defaults:
        return False
    return bool(_credential(kind, protocol or getattr(app_config.media, kind).protocol))


def supported_protocols(kind: str) -> tuple[str, ...]:
    return tuple(_defaults.get(kind, {}))


def _api_key(kind: str) -> str:
    value = _credential(kind, getattr(app_config.media, kind).protocol)
    if not value:
        raise MediaError(f"请在设置 → 媒体中配置{kind}服务密钥")
    return value


def configured_service(kind: str):
    if kind not in _defaults:
        raise MediaError("不支持的媒体任务")
    config = getattr(app_config.media, kind)
    validate_service(kind, config)
    if not config.model.strip():
        raise MediaError(f"请在设置 → 媒体中配置{kind}模型")
    if kind == "speech" and config.protocol in {"volc_tts", "minimax_tts", "qwen_tts", "tencent_minimax_tts", "openrouter_tts", "gemini_tts"} and not config.voice.strip():
        raise MediaError("所选语音协议需要在设置 → 媒体中填写音色 ID")
    return config


def _url(kind: str, config) -> str:
    base = (config.base_url or _defaults[kind][config.protocol]).rstrip("/")
    if config.protocol.startswith("gemini_"):
        return base if base.endswith(":generateContent") else f"{base}/models/{config.model}:generateContent"
    if config.protocol == "volc_tts":
        return base if base.endswith("/tts/unidirectional") else f"{base}/tts/unidirectional"
    if config.protocol == "qwen_tts":
        endpoint = "api/v1/services/aigc/multimodal-generation/generation"
        return base if base.endswith("/" + endpoint) else f"{base}/{endpoint}"
    endpoint = {
        "ark_chat_audio": "chat/completions", "xiaomi_tts": "chat/completions",
        "xiaomi_asr": "chat/completions", "minimax_image": "image_generation",
        "minimax_tts": "t2a_v2", "minimax_asr": "speech_to_text",
        "qwen_asr": "chat/completions", "hunyuan_image": "hunyuan-image/v3-generation",
        "hunyuan_asr": "asrproxy/sync_transcribe", "tencent_minimax_tts": "minimax-tts/sync_tts",
        "openrouter_image": "images", "openrouter_tts": "audio/speech",
        "openrouter_asr": "audio/transcriptions",
    }.get(config.protocol, _endpoints[kind])
    return base if base.endswith("/" + endpoint) else f"{base}/{endpoint}"


def _decode_b64(value: str) -> bytes:
    if not isinstance(value, str):
        raise MediaError("媒体服务返回了无效的 base64 数据")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise MediaError("媒体服务返回了无效的 base64 数据") from exc
    if not data or len(data) > MAX_ASSET_BYTES:
        raise MediaError("媒体服务返回的文件为空或过大")
    return data


def _minimax_success(payload: dict) -> None:
    status = payload.get("base_resp")
    if not isinstance(status, dict) or status.get("status_code") != 0:
        raise MediaError("MiniMax 服务返回失败；请检查密钥、模型及余额")


def _decode_hex(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_ASSET_BYTES * 2:
        raise MediaError("语音服务返回的十六进制音频无效")
    try:
        data = bytes.fromhex(value)
    except ValueError as exc:
        raise MediaError("语音服务返回的十六进制音频无效") from exc
    if not data:
        raise MediaError("语音服务返回的文件为空")
    return data


def _gemini_inline(payload: dict, media_type: str) -> tuple[bytes, str]:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        matches = [part["inlineData"] for part in parts if not part.get("thought")
                   and isinstance(part.get("inlineData"), dict)
                   and str(part["inlineData"].get("mimeType", "")).lower().startswith(media_type)]
        inline = matches[-1]
        return _decode_b64(inline["data"]), inline["mimeType"].lower()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise MediaError("Gemini 未返回所请求的媒体文件") from exc


def _pcm_to_wav(data: bytes, mime_type: str) -> bytes:
    if not mime_type.startswith(("audio/l16", "audio/pcm")) or "rate=" in mime_type and "rate=24000" not in mime_type:
        raise MediaError("Gemini 返回了不支持的音频编码或采样率")
    if len(data) < 2 or len(data) % 2:
        raise MediaError("Gemini 返回的 PCM 音频无效")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(data)
    return output.getvalue()


def _volc_audio_chunks(body: str) -> bytes:
    # HTTP Chunked 的传输边界不等于 JSON 边界；兼容 JSON 序列与 SSE 行。
    text = body.strip()
    if text.startswith("data:"):
        fragments = [line.removeprefix("data:").strip() for line in text.splitlines() if line.startswith("data:")]
    else:
        decoder = json.JSONDecoder()
        fragments = []
        while text:
            try:
                _, end = decoder.raw_decode(text)
            except ValueError as exc:
                raise MediaError("语音服务返回的分片格式无效") from exc
            fragments.append(text[:end])
            text = text[end:].lstrip()
    chunks = []
    for fragment in fragments:
        if fragment == "[DONE]":
            continue
        try:
            item = json.loads(fragment)
        except ValueError as exc:
            raise MediaError("语音服务返回的分片格式无效") from exc
        if not isinstance(item, dict) or item.get("code") not in (None, 0):
            raise MediaError("语音服务返回生成失败")
        if item.get("data"):
            chunks.append(_decode_b64(item["data"]))
    return b"".join(chunks)


def _save_asset(kind: str, data: bytes) -> str:
    if kind == "image":
        signatures = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))
        suffix = next((ext for magic, ext in signatures if data.startswith(magic)), "")
        if not suffix and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            suffix = ".webp"
        if not suffix:
            raise MediaError("图片服务没有返回受支持的图片文件")
    else:
        suffix = ".mp3" if data.startswith(b"ID3") or (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0) else ""
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            suffix = ".wav"
        if not suffix:
            raise MediaError("语音服务没有返回受支持的 MP3 或 WAV 文件")
    root = Path(settings.DATA_DIR) / "media_assets"
    root.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex + suffix
    (root / name).write_bytes(data)
    return name


def asset_path(name: str) -> Path:
    if len(name) > 40 or Path(name).name != name or Path(name).suffix not in {".png", ".jpg", ".webp", ".mp3", ".wav"}:
        raise MediaError("无效的媒体文件")
    path = Path(settings.DATA_DIR) / "media_assets" / name
    if not path.is_file():
        raise MediaError("媒体文件不存在")
    return path


async def _download_provider_asset(client: httpx.AsyncClient, url: str, protocol: str) -> bytes:
    """只接收供应商文档中的临时对象存储地址，限制大小且不跟随跳转。"""
    if not isinstance(url, str) or len(url) > 4096:
        raise MediaError("媒体服务返回了不受信任的文件地址")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if protocol.startswith("qwen_"):
        allowed = host.endswith(".aliyuncs.com") and ".oss-" in host
    else:
        allowed = host.endswith(".myqcloud.com") or host.endswith(".tencentcloudcos.com")
    if not allowed or parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or parsed.port or parsed.fragment:
        raise MediaError("媒体服务返回了不受信任的文件地址")
    if parsed.scheme == "http" and not protocol.startswith("qwen_"):
        raise MediaError("媒体服务返回了不受信任的文件地址")
    # 百炼文档的 OSS 示例可能给出 HTTP 签名地址；下载时升级为 HTTPS，避免泄露临时签名。
    download_url = parsed._replace(scheme="https").geturl()
    chunks = bytearray()
    async with client.stream("GET", download_url) as response:
        if response.status_code != 200:
            raise MediaError(f"媒体文件下载失败：HTTP {response.status_code}")
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > MAX_ASSET_BYTES:
                raise MediaError("媒体服务返回的文件为空或过大")
    return bytes(chunks)


async def generate(kind: str, prompt: str, *, audio: bytes | None = None, filename: str = "audio.mp3") -> dict:
    config = configured_service(kind)
    key = _api_key(kind)
    if kind in {"image", "speech"} and (not prompt.strip() or len(prompt) > 10000):
        raise MediaError("媒体提示词必须为 1–10000 字")
    if kind == "transcription" and (not audio or len(audio) > MAX_ASSET_BYTES):
        raise MediaError("语音识别需要不超过 20 MB 的音频附件")
    if kind == "image" and config.protocol == "minimax_image" and len(prompt) > 1500:
        raise MediaError("MiniMax 图片提示词不能超过 1500 字")
    if kind == "speech" and config.protocol == "qwen_tts" and len(prompt) > 600:
        raise MediaError("千问语音合成单次输入不能超过 600 字")
    if kind == "transcription" and config.protocol == "qwen_asr" and len(audio) > 10 * 1024 * 1024:
        raise MediaError("千问语音识别音频不能超过 10 MB")
    if kind == "transcription" and config.protocol == "gemini_asr" and len(audio) > 14 * 1024 * 1024:
        raise MediaError("Gemini 内嵌音频不能超过 14 MB；较大文件需使用专用上传接口")
    url = _url(kind, config)
    headers = {"x-goog-api-key": key} if config.protocol.startswith("gemini_") else {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=600.0 if kind == "image" else 120.0,
                                     follow_redirects=False) as client:
            if config.protocol == "gemini_image":
                response = await client.post(url, json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE"]}}, headers=headers)
            elif kind == "image":
                payload = {"model": config.model, "prompt": prompt}
                # GPT Image 固定返回 base64，不接受 response_format；Seedream 需显式请求。
                if config.protocol == "minimax_image":
                    payload["response_format"] = "base64"
                elif config.protocol not in {"qwen_image", "hunyuan_image", "openrouter_image"} and (config.protocol == "ark" or not config.model.startswith(("gpt-image-", "chatgpt-image-"))):
                    payload["response_format"] = "b64_json"
                response = await client.post(url, json=payload, headers=headers)
            elif kind == "speech" and config.protocol == "volc_tts":
                headers = {"X-Api-Key": key, "X-Api-Resource-Id": config.model,
                           "X-Api-Request-Id": uuid4().hex}
                payload = {"req_params": {"text": prompt, "speaker": config.voice,
                                          "audio_params": {"format": "mp3", "sample_rate": 24000}}}
                response = await client.post(url, json=payload, headers=headers)
            elif kind == "speech" and config.protocol == "xiaomi_tts":
                # MiMo TTS 的待朗读文本必须作为 assistant 消息，返回的 audio.data 是 WAV base64。
                payload = {"model": config.model, "messages": [{"role": "assistant", "content": prompt}],
                           "audio": {"format": "wav", "voice": config.voice or "mimo_default"}}
                response = await client.post(url, json=payload, headers=headers)
            elif kind == "speech" and config.protocol in {"minimax_tts", "tencent_minimax_tts"}:
                payload = {"model": config.model, "text": prompt, "output_format": "hex",
                           "voice_setting": {"voice_id": config.voice}, "audio_setting": {"format": "mp3"}}
                if config.protocol == "minimax_tts":
                    payload["stream"] = False
                response = await client.post(url, json=payload, headers=headers)
            elif kind == "speech" and config.protocol == "qwen_tts":
                response = await client.post(url, json={"model": config.model, "input": {
                    "text": prompt, "voice": config.voice}}, headers=headers)
            elif kind == "speech" and config.protocol == "gemini_tts":
                response = await client.post(url, json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["AUDIO"],
                        "responseFormat": {"audio": {"mimeType": "AUDIO_L16", "sampleRate": 24000}},
                        "speechConfig": {"voiceConfig": {"voice": config.voice}}}}, headers=headers)
            elif kind == "speech":
                response = await client.post(url, json={"model": config.model, "input": prompt,
                                                        "voice": config.voice or "alloy", "response_format": "mp3"}, headers=headers)
            elif config.protocol == "ark_chat_audio":
                mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac"}.get(Path(filename).suffix.lower())
                if not mime:
                    raise MediaError("方舟音频理解仅支持 MP3、WAV、M4A、AAC")
                payload = {"model": config.model, "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "请逐字转写这段音频，只返回识别出的文字。"},
                    {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": mime}},
                ]}]}
                response = await client.post(url, json=payload, headers=headers)
            elif config.protocol in {"xiaomi_asr", "qwen_asr"}:
                suffix = Path(filename).suffix.lower()
                mime = {".mp3": "audio/mpeg", ".wav": "audio/wav"}.get(suffix)
                if not mime or (suffix == ".wav" and not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE")) or (suffix == ".mp3" and not (audio.startswith(b"ID3") or audio[:1] == b"\xff")):
                    raise MediaError("该语音识别协议仅支持真实的 MP3 或 WAV 文件")
                if config.protocol == "xiaomi_asr":
                    headers = {"api-key": key}
                payload = {"model": config.model, "messages": [{"role": "user", "content": [
                    {"type": "input_audio", "input_audio": {"data": f"data:{mime};base64,{base64.b64encode(audio).decode('ascii')}"}}
                ]}]}
                if config.protocol == "xiaomi_asr":
                    payload["asr_options"] = {"language": "auto"}
                response = await client.post(url, json=payload, headers=headers)
            elif config.protocol == "hunyuan_asr":
                suffix = Path(filename).suffix.lower()
                if suffix not in {".mp3", ".wav"}:
                    raise MediaError("混元语音识别当前仅支持 MP3 或 WAV 附件")
                payload = {"model": config.model, "data": base64.b64encode(audio).decode("ascii"),
                           "voice_encode_format": suffix[1:]}
                response = await client.post(url, json=payload, headers=headers)
            elif config.protocol == "openrouter_asr":
                suffix = Path(filename).suffix.lower().lstrip(".")
                if suffix not in {"wav", "mp3", "flac", "m4a", "ogg", "webm", "aac"}:
                    raise MediaError("OpenRouter 语音识别不支持此音频格式")
                payload = {"model": config.model, "input_audio": {
                    "data": base64.b64encode(audio).decode("ascii"), "format": suffix}}
                response = await client.post(url, json=payload, headers=headers)
            elif config.protocol == "gemini_asr":
                suffix = Path(filename).suffix.lower()
                mime = {".mp3": "audio/mp3", ".wav": "audio/wav"}.get(suffix)
                if not mime or (suffix == ".wav" and not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE")) or (suffix == ".mp3" and not (audio.startswith(b"ID3") or audio[:1] == b"\xff")):
                    raise MediaError("Gemini 内嵌语音识别仅支持真实的 MP3 或 WAV 文件")
                response = await client.post(url, json={"contents": [{"role": "user", "parts": [
                    {"text": "请逐字转写这段音频，只返回识别出的文字。"},
                    {"inlineData": {"mimeType": mime, "data": base64.b64encode(audio).decode("ascii")}}
                ]}]}, headers=headers)
            elif config.protocol == "minimax_asr":
                response = await client.post(url, data={"model": config.model, "response_format": "json", "stream": "false"},
                                             files={"file": (Path(filename).name, audio, "application/octet-stream")}, headers=headers)
            else:
                response = await client.post(url, data={"model": config.model},
                                             files={"file": (Path(filename).name, audio, "application/octet-stream")},
                                             headers=headers)
    except httpx.HTTPError as exc:
        raise MediaError("媒体服务连接失败") from exc
    if response.status_code != 200:
        raise MediaError(f"媒体服务返回 HTTP {response.status_code}")
    if len(response.content) > MAX_ASSET_BYTES * 2:
        raise MediaError("媒体响应过大")
    if kind == "transcription":
        try:
            payload = response.json()
            if config.protocol == "hunyuan_asr":
                text = payload["output"]["text"] if payload.get("status") == "completed" else None
            elif config.protocol == "gemini_asr":
                parts = payload["candidates"][0]["content"]["parts"]
                text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            elif config.protocol == "minimax_asr":
                # MiniMax ASR 的响应与 OpenAI transcription 相同，错误响应仍须拒绝。
                if "base_resp" in payload:
                    _minimax_success(payload)
                text = payload.get("text")
            else:
                text = payload["choices"][0]["message"]["content"] if config.protocol in {"ark_chat_audio", "xiaomi_asr", "qwen_asr"} else payload.get("text")
        except (ValueError, AttributeError, KeyError, IndexError, TypeError) as exc:
            raise MediaError("语音识别响应无效") from exc
        if not isinstance(text, str) or not text.strip():
            raise MediaError("语音识别没有返回文本")
        return {"text": text[:50000]}
    if kind == "image":
        try:
            payload = response.json()
            if config.protocol == "minimax_image":
                _minimax_success(payload)
                data = _decode_b64(payload["data"]["image_base64"][0])
            elif config.protocol in {"qwen_image", "hunyuan_image"}:
                asset_url = payload["data"][0]["url"]
            elif config.protocol == "gemini_image":
                data, _ = _gemini_inline(payload, "image/")
            else:
                data = _decode_b64(payload["data"][0]["b64_json"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MediaError("图片服务响应无效；请检查所选协议和模型") from exc
    elif config.protocol == "volc_tts":
        data = _volc_audio_chunks(response.text)
    elif config.protocol == "xiaomi_tts":
        try:
            data = _decode_b64(response.json()["choices"][0]["message"]["audio"]["data"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MediaError("小米语音服务未返回音频") from exc
    elif config.protocol in {"minimax_tts", "tencent_minimax_tts"}:
        try:
            payload = response.json()
            _minimax_success(payload)
            data = _decode_hex(payload["data"]["audio"])
        except (ValueError, KeyError, TypeError) as exc:
            raise MediaError("MiniMax 语音服务未返回音频") from exc
    elif config.protocol == "qwen_tts":
        try:
            payload = response.json()
            if payload.get("status_code") != 200:
                raise MediaError("千问语音服务返回失败；请检查密钥、模型及余额")
            asset_url = payload["output"]["audio"]["url"]
        except (ValueError, KeyError, TypeError) as exc:
            raise MediaError("千问语音服务未返回音频地址") from exc
    elif config.protocol == "gemini_tts":
        try:
            pcm, mime_type = _gemini_inline(response.json(), "audio/")
            data = _pcm_to_wav(pcm, mime_type)
        except ValueError as exc:
            raise MediaError("Gemini 语音响应无效") from exc
    else:
        data = response.content
    if config.protocol in {"qwen_image", "hunyuan_image", "qwen_tts"}:
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
                data = await _download_provider_asset(client, asset_url, config.protocol)
        except httpx.HTTPError as exc:
            raise MediaError("媒体文件下载失败") from exc
    if not data or len(data) > MAX_ASSET_BYTES:
        raise MediaError("媒体服务返回的文件为空或过大")
    name = _save_asset(kind, data)
    return {"asset": name, "path": str(asset_path(name)), "url": f"/api/media/assets/{name}",
            "content": f"![生成的图片](/api/media/assets/{name})" if kind == "image" else f"[播放生成的语音](/api/media/assets/{name})"}
