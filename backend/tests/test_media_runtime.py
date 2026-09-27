"""媒体协议用模拟响应验证，不消耗真实模型额度。"""
from __future__ import annotations

import base64
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import MediaConfig, MediaServiceConfig, app_config, settings  # noqa: E402
from core import media_runtime  # noqa: E402
from core.opencode_ws import OpenCodeClient  # noqa: E402
from api.routes import chat  # noqa: E402
from api.routes import media as media_routes  # noqa: E402


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
MP3 = b"ID3" + b"test-audio"
WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"test-audio"


class MediaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = app_config.media
        self.temp = tempfile.TemporaryDirectory(prefix="codebot-media-tests-")
        self.original_data_dir = settings.DATA_DIR
        settings.DATA_DIR = Path(self.temp.name)
        app_config.media = MediaConfig()

    async def asyncTearDown(self):
        app_config.media = self.original
        settings.DATA_DIR = self.original_data_dir
        self.temp.cleanup()

    async def _mocked(self, handler, kind, prompt, **kwargs):
        real_client = httpx.AsyncClient
        def factory(**options):
            return real_client(transport=httpx.MockTransport(handler), **options)
        protocol = getattr(app_config.media, kind).protocol
        with patch.object(media_runtime.httpx, "AsyncClient", side_effect=factory), \
             patch.dict(os.environ, {f"CODEBOT_MEDIA_{kind.upper()}_API_KEY": "test-secret",
                                  f"CODEBOT_MEDIA_{kind.upper()}_{protocol.upper()}_API_KEY": "test-secret"}):
            return await media_runtime.generate(kind, prompt, **kwargs)

    async def test_gpt_image_and_seedream_use_distinct_request_contracts(self):
        calls = []
        def handler(request):
            body = json.loads(request.read())
            calls.append((str(request.url), body))
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        app_config.media.image = MediaServiceConfig(protocol="openai", model="gpt-image-2")
        first = await self._mocked(handler, "image", "画一只猫")
        self.assertNotIn("response_format", calls[0][1])
        self.assertTrue(Path(first["path"]).is_file())
        app_config.media.image = MediaServiceConfig(protocol="ark", model="doubao-seedream-5-0-lite-260128")
        second = await self._mocked(handler, "image", "画一只狗")
        self.assertEqual(calls[1][1]["response_format"], "b64_json")
        self.assertEqual(calls[1][0], "https://ark.cn-beijing.volces.com/api/v3/images/generations")
        self.assertIn("/api/media/assets/", second["content"])

    async def test_openai_and_doubao_speech(self):
        calls = []
        def handler(request):
            calls.append((str(request.url), dict(request.headers), request.read()))
            if request.url.path.endswith("/tts/unidirectional"):
                return httpx.Response(200, text=json.dumps({"code": 0, "data": base64.b64encode(MP3).decode()}))
            return httpx.Response(200, content=MP3)
        app_config.media.speech = MediaServiceConfig(protocol="openai", model="gpt-4o-mini-tts", voice="alloy")
        await self._mocked(handler, "speech", "你好")
        app_config.media.speech = MediaServiceConfig(protocol="volc_tts", model="seed-tts-2.0",
            base_url="https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional", voice="zh_female_vv_uranus_bigtts")
        result = await self._mocked(handler, "speech", "你好")
        self.assertTrue(Path(result["path"]).read_bytes().startswith(b"ID3"))
        self.assertIn("/api/v3/plan/tts/unidirectional", calls[1][0])
        self.assertEqual(calls[1][1]["x-api-resource-id"], "seed-tts-2.0")
        self.assertNotIn("authorization", calls[1][1])
        combined = json.dumps({"code": 0, "data": base64.b64encode(MP3).decode()}) + json.dumps({"code": 0, "data": base64.b64encode(b"tail").decode()})
        self.assertEqual(media_runtime._volc_audio_chunks(combined), MP3 + b"tail")

    async def test_transcription_openai_and_ark_audio(self):
        calls = []
        def handler(request):
            calls.append((str(request.url), request.read()))
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(200, json={"choices": [{"message": {"content": "豆包识别结果"}}]})
            return httpx.Response(200, json={"text": "OpenAI 识别结果"})
        app_config.media.transcription = MediaServiceConfig(protocol="openai", model="gpt-4o-mini-transcribe")
        first = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        app_config.media.transcription = MediaServiceConfig(protocol="ark_chat_audio", model="doubao-seed-2-0-lite")
        second = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(first["text"], "OpenAI 识别结果")
        self.assertEqual(second["text"], "豆包识别结果")
        body = json.loads(calls[1][1])
        self.assertEqual(body["messages"][0]["content"][1]["input_audio"]["format"], "audio/mpeg")

    async def test_xiaomi_tts_and_asr_follow_distinct_chat_contracts(self):
        calls = []
        def handler(request):
            body = json.loads(request.read())
            calls.append((str(request.url), dict(request.headers), body))
            if body["model"] == "mimo-v2.5-tts":
                return httpx.Response(200, json={"choices": [{"message": {"audio": {"data": base64.b64encode(WAV).decode()}}}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "小米识别结果"}}]})
        app_config.media.speech = MediaServiceConfig(protocol="xiaomi_tts", model="mimo-v2.5-tts", voice="mimo_default")
        speech = await self._mocked(handler, "speech", "你好")
        self.assertTrue(speech["asset"].endswith(".wav"))
        self.assertEqual(calls[0][2]["messages"], [{"role": "assistant", "content": "你好"}])
        self.assertEqual(calls[0][2]["audio"], {"format": "wav", "voice": "mimo_default"})
        app_config.media.transcription = MediaServiceConfig(protocol="xiaomi_asr", model="mimo-v2.5-asr")
        result = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(result["text"], "小米识别结果")
        self.assertEqual(calls[1][0], "https://api.xiaomimimo.com/v1/chat/completions")
        self.assertEqual(calls[1][1]["api-key"], "test-secret")
        self.assertEqual(len(calls[1][2]["messages"][0]["content"]), 1)
        self.assertTrue(calls[1][2]["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/mpeg;base64,"))
        with self.assertRaisesRegex(media_runtime.MediaError, "MP3 或 WAV"):
            await self._mocked(handler, "transcription", "", audio=b"not mp3", filename="voice.mp3")

    async def test_minimax_image_tts_and_asr_protocols(self):
        calls = []
        def handler(request):
            calls.append((str(request.url), dict(request.headers), request.read()))
            if request.url.path.endswith("/image_generation"):
                return httpx.Response(200, json={"base_resp": {"status_code": 0},
                                                 "data": {"image_base64": [base64.b64encode(PNG).decode()]}})
            if request.url.path.endswith("/t2a_v2"):
                return httpx.Response(200, json={"base_resp": {"status_code": 0}, "data": {"audio": MP3.hex()}})
            return httpx.Response(200, json={"text": "MiniMax 识别结果"})
        app_config.media.image = MediaServiceConfig(protocol="minimax_image", model="image-01", base_url="https://api.minimaxi.com/v1")
        image = await self._mocked(handler, "image", "画猫")
        self.assertTrue(image["asset"].endswith(".png"))
        self.assertEqual(calls[0][0], "https://api.minimaxi.com/v1/image_generation")
        self.assertEqual(json.loads(calls[0][2])["response_format"], "base64")
        app_config.media.speech = MediaServiceConfig(protocol="minimax_tts", model="speech-2.8-hd", voice="female-shaonv")
        speech = await self._mocked(handler, "speech", "你好")
        self.assertTrue(speech["asset"].endswith(".mp3"))
        self.assertEqual(json.loads(calls[1][2])["voice_setting"]["voice_id"], "female-shaonv")
        app_config.media.transcription = MediaServiceConfig(protocol="minimax_asr", model="asr-1.0")
        result = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(result["text"], "MiniMax 识别结果")
        self.assertTrue(calls[2][0].endswith("/v1/speech_to_text"))
        self.assertIn(b'name="model"', calls[2][2])

    async def test_minimax_body_error_is_not_treated_as_success(self):
        app_config.media.image = MediaServiceConfig(protocol="minimax_image", model="image-01")
        def handler(_request):
            return httpx.Response(200, json={"base_resp": {"status_code": 1008, "status_msg": "balance exhausted"}})
        with self.assertRaisesRegex(media_runtime.MediaError, "MiniMax 服务返回失败"):
            await self._mocked(handler, "image", "画猫")

    async def test_qwen_image_tts_and_asr_contracts(self):
        calls = []
        image_url = "https://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/result.png?signature=demo"
        audio_url = "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/result.wav?signature=demo"
        def handler(request):
            calls.append((str(request.url), request.method, request.read() if request.method == "POST" else b""))
            if request.method == "GET":
                return httpx.Response(200, content=PNG if request.url.path.endswith(".png") else WAV)
            body = json.loads(request.read())
            if body["model"] == "qwen-image-3.0":
                self.assertNotIn("response_format", body)
                return httpx.Response(200, json={"data": [{"url": image_url}]})
            if body["model"] == "qwen3-tts-flash":
                self.assertEqual(body["input"], {"text": "你好", "voice": "Cherry"})
                return httpx.Response(200, json={"status_code": 200, "output": {"audio": {"url": audio_url}}})
            self.assertEqual(body["messages"][0]["content"][0]["type"], "input_audio")
            return httpx.Response(200, json={"choices": [{"message": {"content": "千问识别结果"}}]})
        app_config.media.image = MediaServiceConfig(protocol="qwen_image", model="qwen-image-3.0")
        image = await self._mocked(handler, "image", "画猫")
        self.assertTrue(Path(image["path"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(calls[0][0], "https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations")
        app_config.media.speech = MediaServiceConfig(protocol="qwen_tts", model="qwen3-tts-flash", voice="Cherry")
        speech = await self._mocked(handler, "speech", "你好")
        self.assertTrue(speech["asset"].endswith(".wav"))
        self.assertTrue(any(url.startswith("https://dashscope-result-bj.oss-") for url, method, _ in calls if method == "GET"))
        app_config.media.transcription = MediaServiceConfig(protocol="qwen_asr", model="qwen3-asr-flash")
        result = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(result["text"], "千问识别结果")
        self.assertTrue(calls[-1][0].endswith("/compatible-mode/v1/chat/completions"))

    async def test_tencent_hunyuan_image_asr_and_tokenhub_tts_contracts(self):
        calls = []
        def handler(request):
            calls.append((str(request.url), request.method, request.read() if request.method == "POST" else b""))
            if request.method == "GET":
                return httpx.Response(200, content=PNG)
            body = json.loads(request.read())
            if body["model"] == "hy-image-v3":
                self.assertNotIn("response_format", body)
                return httpx.Response(200, json={"data": [{"url": "https://aigc-output-image-file-1.cos.ap-guangzhou.myqcloud.com/result.png"}]})
            if body["model"] == "hy-asr-3.0-preview":
                self.assertEqual(body["voice_encode_format"], "mp3")
                self.assertEqual(base64.b64decode(body["data"]), MP3)
                return httpx.Response(200, json={"status": "completed", "output": {"text": "混元识别结果"}})
            self.assertEqual(body["voice_setting"]["voice_id"], "female-shaonv")
            return httpx.Response(200, json={"base_resp": {"status_code": 0}, "data": {"audio": MP3.hex()}})
        app_config.media.image = MediaServiceConfig(protocol="hunyuan_image", model="hy-image-v3")
        image = await self._mocked(handler, "image", "画猫")
        self.assertTrue(Path(image["path"]).is_file())
        self.assertEqual(calls[0][0], "https://tokenhub.tencentmaas.com/v1/wand/hunyuan-image/v3-generation")
        app_config.media.transcription = MediaServiceConfig(protocol="hunyuan_asr", model="hy-asr-3.0-preview")
        result = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(result["text"], "混元识别结果")
        app_config.media.speech = MediaServiceConfig(protocol="tencent_minimax_tts", model="minimax-speech-2.8-hd", voice="female-shaonv")
        speech = await self._mocked(handler, "speech", "你好")
        self.assertTrue(speech["asset"].endswith(".mp3"))
        self.assertTrue(calls[-1][0].endswith("/minimax-tts/sync_tts"))

    async def test_provider_asset_url_is_restricted_and_not_followed(self):
        app_config.media.image = MediaServiceConfig(protocol="qwen_image", model="qwen-image-3.0")
        for hostile_url in ("http://127.0.0.1/private", "https://aliyuncs.com.evil.test/result.png"):
            def handler(_request):
                return httpx.Response(200, json={"data": [{"url": hostile_url}]})
            with self.assertRaisesRegex(media_runtime.MediaError, "不受信任"):
                await self._mocked(handler, "image", "画猫")
        safe_url = "https://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/result.png"
        def redirected(request):
            if request.method == "GET":
                return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
            return httpx.Response(200, json={"data": [{"url": safe_url}]})
        with self.assertRaisesRegex(media_runtime.MediaError, "下载失败"):
            await self._mocked(redirected, "image", "画猫")
        def oversized(request):
            if request.method == "GET":
                return httpx.Response(200, content=PNG + b"x" * media_runtime.MAX_ASSET_BYTES)
            return httpx.Response(200, json={"data": [{"url": safe_url}]})
        with self.assertRaisesRegex(media_runtime.MediaError, "过大"):
            await self._mocked(oversized, "image", "画猫")

    async def test_openrouter_image_speech_and_transcription(self):
        calls = []
        def handler(request):
            body = json.loads(request.read())
            calls.append((str(request.url), dict(request.headers), body))
            if request.url.path.endswith("/images"):
                self.assertNotIn("response_format", body)
                return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
            if request.url.path.endswith("/audio/speech"):
                return httpx.Response(200, content=MP3, headers={"Content-Type": "audio/mpeg"})
            return httpx.Response(200, json={"text": "OpenRouter 识别结果"})
        app_config.media.image = MediaServiceConfig(protocol="openrouter_image", model="bytedance-seed/seedream-4.5")
        image = await self._mocked(handler, "image", "画猫")
        self.assertTrue(image["asset"].endswith(".png"))
        self.assertEqual(calls[0][0], "https://openrouter.ai/api/v1/images")
        app_config.media.speech = MediaServiceConfig(protocol="openrouter_tts", model="openai/gpt-4o-mini-tts-2025-12-15", voice="nova")
        speech = await self._mocked(handler, "speech", "你好")
        self.assertTrue(speech["asset"].endswith(".mp3"))
        self.assertEqual(calls[1][2]["response_format"], "mp3")
        app_config.media.transcription = MediaServiceConfig(protocol="openrouter_asr", model="openai/whisper-1")
        transcript = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(transcript["text"], "OpenRouter 识别结果")
        self.assertEqual(calls[2][2]["input_audio"]["format"], "mp3")
        self.assertEqual(base64.b64decode(calls[2][2]["input_audio"]["data"]), MP3)

    async def test_gemini_image_tts_and_inline_audio_transcription(self):
        calls = []
        pcm = b"\x01\x00\x02\x00" * 20
        def handler(request):
            body = json.loads(request.read())
            calls.append((str(request.url), dict(request.headers), body))
            if "flash-image" in request.url.path:
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                    {"text": "准备图片"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}
                ]}}]})
            if "flash-tts" in request.url.path:
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                    {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}
                ]}}]})
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Gemini 识别结果"}]}}]})
        app_config.media.image = MediaServiceConfig(protocol="gemini_image", model="gemini-3.1-flash-image")
        image = await self._mocked(handler, "image", "画猫")
        self.assertTrue(image["asset"].endswith(".png"))
        self.assertEqual(calls[0][0], "https://generativelanguage.googleapis.com/v1/models/gemini-3.1-flash-image:generateContent")
        self.assertEqual(calls[0][2]["generationConfig"]["responseModalities"], ["IMAGE"])
        self.assertEqual(calls[0][1]["x-goog-api-key"], "test-secret")
        self.assertNotIn("authorization", calls[0][1])
        app_config.media.speech = MediaServiceConfig(protocol="gemini_tts", model="gemini-3.8-flash-tts", voice="Kore")
        speech = await self._mocked(handler, "speech", "你好")
        self.assertTrue(speech["asset"].endswith(".wav"))
        self.assertEqual(calls[1][2]["generationConfig"]["speechConfig"]["voiceConfig"]["voice"], "Kore")
        app_config.media.transcription = MediaServiceConfig(protocol="gemini_asr", model="gemini-3.8-flash")
        transcript = await self._mocked(handler, "transcription", "", audio=MP3, filename="voice.mp3")
        self.assertEqual(transcript["text"], "Gemini 识别结果")
        self.assertEqual(calls[2][2]["contents"][0]["parts"][1]["inlineData"]["mimeType"], "audio/mp3")

    async def test_gemini_missing_or_wrong_media_fails_closed(self):
        app_config.media.image = MediaServiceConfig(protocol="gemini_image", model="gemini-3.1-flash-image")
        def no_image(_request):
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "only text"}]}}]})
        with self.assertRaisesRegex(media_runtime.MediaError, "未返回"):
            await self._mocked(no_image, "image", "画猫")
        app_config.media.speech = MediaServiceConfig(protocol="gemini_tts", model="gemini-3.8-flash-tts", voice="Kore")
        def wrong_rate(_request):
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "audio/L16;rate=16000", "data": base64.b64encode(b"\x01\x00").decode()}}
            ]}}]})
        with self.assertRaisesRegex(media_runtime.MediaError, "采样率"):
            await self._mocked(wrong_rate, "speech", "你好")
        with self.assertRaisesRegex(media_runtime.MediaError, "模型 ID"):
            media_runtime.validate_service("image", MediaServiceConfig(protocol="gemini_image", model="../bad"))

    def test_media_keys_are_bound_to_protocol(self):
        app_config.media.image = MediaServiceConfig(protocol="openrouter_image", model="bytedance-seed/seedream-4.5")
        try:
            media_runtime.set_desktop_key("image", "router-secret", "openrouter_image")
            self.assertTrue(media_runtime.credential_configured("image"))
            app_config.media.image = MediaServiceConfig(protocol="gemini_image", model="gemini-3.1-flash-image")
            with patch.dict(os.environ, {"CODEBOT_MEDIA_IMAGE_API_KEY": "legacy-secret",
                                      "CODEBOT_MEDIA_IMAGE_GEMINI_IMAGE_API_KEY": ""}):
                self.assertFalse(media_runtime.credential_configured("image"))
                with self.assertRaisesRegex(media_runtime.MediaError, "密钥"):
                    media_runtime._api_key("image")
            self.assertTrue(media_runtime.credential_configured("image", "openrouter_image"))
        finally:
            media_runtime.set_desktop_key("image", "", "openrouter_image")
        app_config.media.image = MediaServiceConfig(protocol="qwen_image", model="qwen-image-3.0")
        with patch.dict(os.environ, {"CODEBOT_MEDIA_IMAGE_API_KEY": "legacy-secret",
                                  "CODEBOT_MEDIA_IMAGE_QWEN_IMAGE_API_KEY": ""}):
            self.assertTrue(media_runtime.credential_configured("image"))

    async def test_missing_credentials_and_invalid_media_fail_closed(self):
        app_config.media.image = MediaServiceConfig(protocol="openai", model="gpt-image-2")
        with patch.dict(os.environ, {"CODEBOT_MEDIA_IMAGE_API_KEY": ""}):
            with self.assertRaisesRegex(media_runtime.MediaError, "密钥"):
                await media_runtime.generate("image", "画猫")
        with self.assertRaisesRegex(media_runtime.MediaError, "HTTPS"):
            media_runtime.validate_service("image", MediaServiceConfig(base_url="http://example.com/v1"))
        with self.assertRaisesRegex(media_runtime.MediaError, "图片文件"):
            media_runtime._save_asset("image", b"plain text")

    def test_image_attachment_becomes_native_file_part(self):
        attached = chat.AttachedFile(name="screen.png", type="image/png", is_text=False,
                                     content=base64.b64encode(PNG).decode())
        files = chat._image_inputs([attached])
        payload = OpenCodeClient("http://127.0.0.1:11200")._build_prompt_payload("看图", files=files)
        self.assertEqual(payload["parts"][1]["type"], "file")
        self.assertTrue(payload["parts"][1]["url"].startswith("data:image/png;base64,"))

    async def test_all_chat_modes_use_same_media_action_and_queue_keeps_it(self):
        for mode in ("build", "plan", "agent", "jevai"):
            with patch.object(chat, "_run_media_action", AsyncMock(return_value="![图片](/api/media/assets/a.png)")) as action:
                content = await chat._execute_opencode("画猫", mode=mode, conversation_id="99", media_action="image")
                self.assertIn("图片", content)
                action.assert_awaited_once()
        request = chat.SendMessageRequest(conversation_id=99, message="画猫", mode="plan", media_action="image")
        with patch.object(chat, "_resolve_conversation_execution", AsyncMock(return_value={})), \
             patch.object(chat, "is_conversation_running", return_value=True):
            response = await chat.send_to_opencode(request)
        self.assertTrue(response["data"]["queued"])
        queued = chat._task_queues["99"].get_nowait()
        self.assertEqual(queued["media_action"], "image")
        chat._task_queues.pop("99", None)

    async def test_abort_cancels_provider_request(self):
        started = asyncio.Event()
        async def slow_generate(_kind, _prompt, **_kwargs):
            started.set()
            await asyncio.sleep(60)
        with patch.object(chat, "generate_media", side_effect=slow_generate):
            pending = asyncio.create_task(chat._generate_media_for_conversation("77", "image", "画猫"))
            await started.wait()
            chat._active_media_requests["77"].cancel()
            with self.assertRaisesRegex(media_runtime.MediaError, "终止"):
                await pending
        self.assertNotIn("77", chat._active_media_requests)

    async def test_desktop_media_key_requires_bridge_token_and_is_not_echoed(self):
        body = media_routes.MediaDesktopKeyRequest(kind="image", protocol="openai", api_key="private-media-key")
        def request(token):
            return Request({"type": "http", "method": "POST", "path": "/api/media/desktop-key",
                            "headers": [(b"x-codebot-desktop-token", token.encode())],
                            "client": ("127.0.0.1", 4500), "server": ("127.0.0.1", 18080)})
        with patch.dict(os.environ, {"CODEBOT_DESKTOP_BRIDGE_TOKEN": "test-bridge-token",
                                  "CODEBOT_MEDIA_IMAGE_GEMINI_IMAGE_API_KEY": ""}):
            with self.assertRaises(HTTPException) as blocked:
                await media_routes.update_media_desktop_key(request("wrong"), body)
            self.assertEqual(blocked.exception.status_code, 404)
            try:
                await media_routes.update_media_desktop_key(request("test-bridge-token"), body)
                status = await media_routes.get_media_config()
                self.assertTrue(status["data"]["credentials"]["image"])
                self.assertTrue(status["data"]["credential_protocols"]["image"]["openai"])
                self.assertFalse(status["data"]["credential_protocols"]["image"]["gemini_image"])
                self.assertNotIn("private-media-key", repr(status))
            finally:
                media_runtime.set_desktop_key("image", "")

    async def test_partial_media_update_keeps_other_services(self):
        app_config.media.speech = MediaServiceConfig(protocol="volc_tts", model="seed-tts-2.0", voice="speaker")
        with patch.object(media_routes, "save_config"):
            response = await media_routes.update_media_config(media_routes.MediaConfigUpdate(
                image=MediaServiceConfig(protocol="ark", model="seedream")))
        self.assertEqual(response["data"]["image"]["model"], "seedream")
        self.assertEqual(response["data"]["speech"]["model"], "seed-tts-2.0")


if __name__ == "__main__":
    unittest.main()
