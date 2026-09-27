"""JevAI 决策协议和阶段边界模拟；不调用真实供应商或执行器。"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-jevai-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)

from config import JevAIConfig, JevModelTiers, MediaServiceConfig, app_config  # noqa: E402
from core import jevai  # noqa: E402
from core.opencode_ws import OpenCodeClient  # noqa: E402
from api.routes import chat  # noqa: E402
from api.routes import config as config_routes  # noqa: E402


def route(provider="typesafe"):
    return jevai.TaskRoute(provider, "test-key", "opencode", {
        "fast": "provider/fast", "balanced": "provider/balanced", "strong": "provider/strong",
        "multimodal": "provider/media",
    }, 0.7)


class JevAITests(unittest.IsolatedAsyncioTestCase):
    async def test_both_provider_protocols_and_low_confidence_upgrade(self):
        real_client = httpx.AsyncClient
        calls = []

        def handler(request):
            calls.append((str(request.url), request.headers.get("Authorization"), request.read()))
            return httpx.Response(200, json={
                "model": "actual-jev-version", "answers": {
                    "model_role": {"type": "choice", "choice": "fast", "confidence": 0.4,
                                   "probabilities": {"fast": 0.5, "balanced": 0.3, "strong": 0.2}},
                    "output_kind": {"type": "choice", "choice": "text", "confidence": 0.95,
                                    "probabilities": {"text": 0.95, "image": 0.02, "audio": 0.02, "video": 0.01}},
                    "multi_stage": {"type": "noul", "noul": 0.1},
                }, "usage": {"input_tokens": 10, "output_tokens": 5},
            })

        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(jevai.httpx, "AsyncClient", side_effect=client_factory):
            for provider in ("typesafe", "openrouter"):
                decision = await jevai.choose_model(route(provider), {"user_request": "翻译你好"}, first_stage=True)
                self.assertEqual(decision["model"], "provider/strong")
                self.assertEqual(decision["jev_model"], "actual-jev-version")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], jevai.PROVIDERS["typesafe"][0])
        self.assertEqual(calls[1][0], jevai.PROVIDERS["openrouter"][0])
        self.assertTrue(all(authorization == "Bearer test-key" for _, authorization, _ in calls))

    async def test_invalid_response_pauses_without_fallback(self):
        with patch.object(jevai, "decide", AsyncMock(return_value={"answers": {"model_role": {
            "type": "choice", "choice": "other", "confidence": 0.9,
        }}})):
            with self.assertRaises(jevai.JevAIError):
                await jevai.choose_model(route(), {"user_request": "任务"})

    async def test_classify_one_hundred_in_bounded_batches(self):
        calls = []

        async def fake_decide(_route, state, questions):
            calls.append((state, questions))
            return {"model": "jev-1.13.0", "answers": {
                key: {"type": "choice", "choice": "news", "confidence": 0.9,
                      "probabilities": {"news": 0.9, "essay": 0.1}}
                for key in questions
            }}

        records = [{"id": str(index), "text": f"文档 {index} 摘要"} for index in range(100)]
        with patch.object(jevai, "decide", side_effect=fake_decide):
            result = await jevai.classify_records(route(), records, {"news": "新闻", "essay": "散文"})
        self.assertEqual(len(result), 100)
        self.assertEqual(len(calls), 9)
        self.assertLessEqual(max(len(state) for state, _ in calls), 12)
        self.assertEqual({item["category"] for item in result}, {"news"})

    async def test_stage_switch_preserves_executor_and_conversation(self):
        used = []
        decisions = [
            {"role": "fast", "model": "provider/fast", "confidence": 0.9,
             "multi_stage": True, "jev_model": "jev-v1"},
            {"role": "fast", "model": "provider/fast", "confidence": 0.9,
             "multi_stage": False, "jev_model": "jev-v1"},
            {"role": "strong", "model": "provider/strong", "confidence": 0.9,
             "multi_stage": False, "jev_model": "jev-v1"},
        ]

        async def fake_execute(prompt, **kwargs):
            used.append((kwargs["model"], kwargs["target"], kwargs["conversation_id"], kwargs["project_dir"], kwargs["mode"]))
            if kwargs["mode"] == "plan":
                yield {"type": "done", "content": '{"stages":["写文档","分类归档"]}'}
                return
            run_id = re.search(r"JevAI 任务编号：([a-f0-9]+)", prompt).group(1)
            stage = int(re.search(r"当前阶段 (\d+)/", prompt).group(1))
            jevai.save_checkpoint(run_id, stage, "completed", f"阶段 {stage} 完成", [f"file-{stage}"])
            yield {"type": "done", "content": f"结果 {stage}"}

        with patch.object(chat, "choose_model", AsyncMock(side_effect=decisions)), \
             patch.object(chat, "_stream_execute_opencode_with_meta", side_effect=fake_execute), \
             patch.object(chat, "record_stage"):
            events = [event async for event in chat._stream_jevai_task(
                "生成并分类", jev_route=route(), conversation_id="17", user_message_id=2,
                project_dir="D:/project", target="codex", knowledge_paths=[], decision_message="生成并分类",
            )]
        self.assertEqual([item[0] for item in used], ["provider/fast", "provider/fast", "provider/strong"])
        self.assertTrue(all(item[1:4] == ("codex", "17", "D:/project") for item in used))
        self.assertEqual(events[-1]["content"], "结果 2")

    async def test_missing_checkpoint_pauses_without_replaying_stage(self):
        executed = []

        async def fake_execute(prompt, **kwargs):
            executed.append(kwargs["mode"])
            if kwargs["mode"] == "plan":
                yield {"type": "done", "content": '{"stages":["创建文件","检查结果"]}'}
            else:
                yield {"type": "done", "content": "执行过文件写入，但没有确认结果"}

        decision = {"role": "fast", "model": "provider/fast", "confidence": 0.9,
                    "multi_stage": True, "jev_model": "jev-v1"}
        with patch.object(chat, "choose_model", AsyncMock(return_value=decision)), \
             patch.object(chat, "_stream_execute_opencode_with_meta", side_effect=fake_execute), \
             patch.object(chat, "record_stage") as record:
            with self.assertRaisesRegex(jevai.JevAIError, "缺少检查点"):
                [event async for event in chat._stream_jevai_task(
                    "创建并检查", jev_route=route(), conversation_id="19", user_message_id=3,
                    project_dir="D:/project", target="codex", knowledge_paths=[], decision_message="创建并检查",
                )]
        self.assertEqual(executed, ["plan", "agent"])
        self.assertEqual(record.call_args.args[3], "paused")

    async def test_abort_prevents_next_stage(self):
        executed = []

        async def fake_execute(prompt, **kwargs):
            executed.append(kwargs["mode"])
            if kwargs["mode"] == "plan":
                yield {"type": "done", "content": '{"stages":["阶段一","阶段二"]}'}
                return
            run_id = re.search(r"JevAI 任务编号：([a-f0-9]+)", prompt).group(1)
            jevai.save_checkpoint(run_id, 1, "completed", "第一阶段完成", [])
            chat._jevai_aborted.add("21")
            yield {"type": "done", "content": "完成"}

        decision = {"role": "fast", "model": "provider/fast", "confidence": 0.9,
                    "multi_stage": True, "jev_model": "jev-v1"}
        with patch.object(chat, "choose_model", AsyncMock(return_value=decision)), \
             patch.object(chat, "_stream_execute_opencode_with_meta", side_effect=fake_execute), \
             patch.object(chat, "record_stage"):
            with self.assertRaisesRegex(jevai.JevAIError, "终止"):
                [event async for event in chat._stream_jevai_task(
                    "两阶段", jev_route=route(), conversation_id="21", user_message_id=4,
                    project_dir="D:/project", target="codex", knowledge_paths=[], decision_message="两阶段",
                )]
        self.assertEqual(executed, ["plan", "agent"])

    async def test_queue_keeps_route_snapshot_and_plain_decision_text(self):
        request = chat.SendMessageRequest(
            conversation_id=31, message="翻译附件", mode="jevai", target="codex",
            attached_files=[chat.AttachedFile(name="private.txt", type="text/plain", content="附件正文不应交给 Jev")],
        )

        async def resolve(value):
            value.target = "codex"
            value.project_dir = "D:/project"
            return {}

        async def prepare(value):
            value._jev_route = route()
            value.model = None

        try:
            with patch.object(chat, "_resolve_conversation_execution", side_effect=resolve), \
                 patch.object(chat, "_prepare_jevai_request", side_effect=prepare), \
                 patch.object(chat, "is_conversation_running", return_value=True):
                response = await chat.send_to_opencode(request)
            self.assertTrue(response["data"]["queued"])
            queued = chat._task_queues["31"].get_nowait()
            self.assertEqual(queued["jev_route"], route())
            self.assertEqual(queued["decision_message"], "翻译附件")
            self.assertIn("附件正文", queued["message"])
        finally:
            chat._task_queues.pop("31", None)

    async def test_desktop_key_bridge_requires_token_and_never_echoes_secret(self):
        body = config_routes.JevAIDesktopKeyRequest(provider="typesafe", api_key="private-test-key")

        def request(token):
            return Request({"type": "http", "method": "POST", "path": "/api/config/jevai/desktop-key",
                            "headers": [(b"x-codebot-desktop-token", token.encode())],
                            "client": ("127.0.0.1", 4500), "server": ("127.0.0.1", 18080)})

        with patch.dict(os.environ, {"CODEBOT_DESKTOP_BRIDGE_TOKEN": "test-bridge-token"}):
            with self.assertRaises(HTTPException) as blocked:
                await config_routes.set_jevai_desktop_key(request("wrong"), body)
            self.assertEqual(blocked.exception.status_code, 404)
            try:
                await config_routes.set_jevai_desktop_key(request("test-bridge-token"), body)
                status = await config_routes.get_jevai_config()
                self.assertTrue(status["data"]["credentials"]["typesafe"])
                self.assertNotIn("private-test-key", repr(status))
            finally:
                jevai.set_desktop_key("typesafe", "")

    def test_missing_model_and_credential_are_rejected_before_send(self):
        original = app_config.jevai
        try:
            app_config.jevai = JevAIConfig(provider="typesafe", opencode=JevModelTiers(
                fast="provider/fast", balanced="provider/balanced", strong="provider/strong"))
            with patch.dict(os.environ, {"CODEBOT_JEV_TYPESAFE_API_KEY": ""}):
                with self.assertRaisesRegex(jevai.JevAIError, "密钥"):
                    jevai.route_snapshot("opencode", app_config.jevai.opencode.model_dump().values())
            with patch.dict(os.environ, {"CODEBOT_JEV_TYPESAFE_API_KEY": "test-key"}):
                with self.assertRaisesRegex(jevai.JevAIError, "不可运行"):
                    jevai.route_snapshot("opencode", ["provider/fast", "provider/balanced"])
        finally:
            app_config.jevai = original

    async def test_media_choice_uses_configured_generation_service(self):
        answers = {
            "model_role": {"type": "choice", "choice": "fast", "confidence": 0.9,
                           "probabilities": {"fast": 0.9, "balanced": 0.06, "strong": 0.04}},
            "output_kind": {"type": "choice", "choice": "image", "confidence": 0.94,
                            "probabilities": {"text": 0.03, "image": 0.94, "audio": 0.02, "video": 0.01}},
            "multi_stage": {"type": "noul", "noul": 0.1},
        }
        with patch.object(jevai, "decide", AsyncMock(return_value={"model": "jev-1.13.0", "answers": answers})), \
             patch("core.media_runtime.configured_service"), \
             patch("core.media_runtime.credential_configured", return_value=True):
            decision = await jevai.choose_model(route(), {"user_request": "生成图片"}, first_stage=True)
            self.assertEqual((decision["role"], decision["model"], decision["output_kind"]),
                             ("fast", "provider/fast", "image"))
        with patch.object(jevai, "decide", AsyncMock(return_value={"model": "jev-1.13.0", "answers": answers})), \
             patch("core.media_runtime.configured_service", side_effect=__import__("core.media_runtime", fromlist=["MediaError"]).MediaError("未配置图片服务")):
            with self.assertRaisesRegex(jevai.JevAIError, "未配置图片服务"):
                await jevai.choose_model(route(), {"user_request": "生成图片"})

    def test_media_artifacts_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "picture.png"
            image.write_bytes(b"PNG content")
            jevai.validate_media_artifacts(["picture.png"], "image", directory)
            with self.assertRaisesRegex(jevai.JevAIError, "未确认"):
                jevai.validate_media_artifacts(["picture.png"], "audio", directory)
            image.unlink()
            with self.assertRaisesRegex(jevai.JevAIError, "未确认"):
                jevai.validate_media_artifacts(["picture.png"], "image", directory)

    async def test_media_stage_switch_and_artifact_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "generated.png"
            image.write_bytes(b"generated image bytes")
            decision = {"role": "multimodal", "model": "provider/media", "confidence": 0.95,
                        "multi_stage": False, "jev_model": "jev-v1", "output_kind": "image"}
            generated = []

            async def fake_generate(kind, prompt):
                generated.append((kind, prompt))
                return {"path": str(image), "content": "![生成的图片](/api/media/assets/generated.png)"}

            with patch.object(chat, "choose_model", AsyncMock(return_value=decision)), \
                 patch.object(chat, "generate_media", side_effect=fake_generate), \
                 patch.object(chat, "configured_service", return_value=MediaServiceConfig(protocol="ark", model="seedream")), \
                 patch.object(chat, "record_stage"):
                events = [event async for event in chat._stream_jevai_task(
                    "生成图片", jev_route=route(), conversation_id="25", user_message_id=5,
                    project_dir=directory, target="codebot", knowledge_paths=[], decision_message="生成图片",
                )]
            self.assertEqual(generated[0][0], "image")
            self.assertIn("生成图片", generated[0][1])
            self.assertEqual(events[-1]["type"], "done")
            self.assertTrue(any(event.get("event_type") == "jevai.media" for event in events))

    async def test_multi_stage_switches_llm_to_media_and_back(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "picture.png"
            image.write_bytes(b"generated image bytes")
            decisions = [
                {"role": "strong", "model": "provider/strong", "planning_model": "provider/strong",
                 "confidence": 0.9, "multi_stage": True, "jev_model": "jev-v1", "output_kind": "text"},
                {"role": "fast", "model": "provider/fast", "confidence": 0.9,
                 "multi_stage": False, "jev_model": "jev-v1", "output_kind": "text"},
                {"role": "multimodal", "model": "provider/media", "confidence": 0.9,
                 "multi_stage": False, "jev_model": "jev-v1", "output_kind": "image"},
                {"role": "balanced", "model": "provider/balanced", "confidence": 0.9,
                 "multi_stage": False, "jev_model": "jev-v1", "output_kind": "text"},
            ]
            used = []

            async def fake_execute(prompt, **kwargs):
                used.append((kwargs["model"], kwargs["conversation_id"], kwargs["project_dir"]))
                if kwargs["mode"] == "plan":
                    yield {"type": "done", "content": '{"stages":["拟稿","生成配图","写结论"]}'}
                    return
                run_id = re.search(r"JevAI 任务编号：([a-f0-9]+)", prompt).group(1)
                stage = int(re.search(r"当前阶段 (\d+)/", prompt).group(1))
                jevai.save_checkpoint(run_id, stage, "completed", "完成", ["picture.png"] if stage == 2 else [])
                yield {"type": "done", "content": f"阶段 {stage} 完成"}

            async def fake_generate(kind, prompt):
                self.assertEqual(kind, "image")
                self.assertIn("生成配图", prompt)
                return {"path": str(image), "content": "![生成的图片](/api/media/assets/picture.png)"}

            with patch.object(chat, "choose_model", AsyncMock(side_effect=decisions)), \
                 patch.object(chat, "_stream_execute_opencode_with_meta", side_effect=fake_execute), \
                 patch.object(chat, "generate_media", side_effect=fake_generate), \
                 patch.object(chat, "configured_service", return_value=MediaServiceConfig(protocol="ark", model="seedream")), \
                 patch.object(chat, "record_stage"):
                events = [event async for event in chat._stream_jevai_task(
                    "写带配图的文章", jev_route=route(), conversation_id="26", user_message_id=6,
                    project_dir=directory, target="codebot", knowledge_paths=[], decision_message="写带配图的文章",
                )]
            self.assertEqual([item[0] for item in used],
                             ["provider/strong", "provider/fast", "provider/balanced"])
            self.assertTrue(all(item[1:] == ("26", directory) for item in used))
            self.assertEqual(events[-1]["content"], "阶段 3 完成")

    async def test_audio_stage_uses_llm_script_then_speech_service(self):
        decision = {"role": "fast", "model": "provider/fast", "confidence": 0.9,
                    "multi_stage": False, "jev_model": "jev-v1", "output_kind": "audio"}
        calls = []

        async def fake_execute(prompt, **kwargs):
            calls.append(("llm", kwargs["model"], kwargs["mode"]))
            yield {"type": "done", "content": "你好，世界！"}

        async def fake_generate(kind, prompt, **_kwargs):
            calls.append(("media", kind, prompt))
            return {"path": "D:/media/voice.mp3", "content": "[播放生成的语音](/api/media/assets/voice.mp3)"}

        with patch.object(chat, "choose_model", AsyncMock(return_value=decision)), \
             patch.object(chat, "_stream_execute_opencode_with_meta", side_effect=fake_execute), \
             patch.object(chat, "configured_service", return_value=MediaServiceConfig(protocol="openai", model="tts")), \
             patch.object(chat, "generate_media", side_effect=fake_generate), \
             patch.object(chat, "record_stage"):
            events = [event async for event in chat._stream_jevai_task(
                "用语音打招呼", jev_route=route(), conversation_id="28", user_message_id=8,
                project_dir="D:/project", target="codebot", knowledge_paths=[], decision_message="用语音打招呼",
            )]
        self.assertEqual(calls, [("llm", "provider/fast", "plan"), ("media", "speech", "你好，世界！")])
        self.assertEqual(events[-1]["type"], "done")

    def test_route_snapshot_accepts_optional_media_model(self):
        original = app_config.jevai
        try:
            app_config.jevai = JevAIConfig(provider="typesafe", opencode=JevModelTiers(
                fast="provider/fast", balanced="provider/balanced", strong="provider/strong",
                multimodal="provider/media"))
            with patch.dict(os.environ, {"CODEBOT_JEV_TYPESAFE_API_KEY": "test-key"}):
                snapshot = jevai.route_snapshot("opencode", app_config.jevai.opencode.model_dump().values())
            self.assertEqual(snapshot.models["multimodal"], "provider/media")
            app_config.jevai.opencode.multimodal = "provider/strong"
            with patch.dict(os.environ, {"CODEBOT_JEV_TYPESAFE_API_KEY": "test-key"}):
                reused = jevai.route_snapshot("opencode", app_config.jevai.opencode.model_dump().values())
            self.assertEqual(reused.models["multimodal"], "provider/strong")
        finally:
            app_config.jevai = original

    async def test_opencode_catalog_keeps_declared_media_output(self):
        client = OpenCodeClient("http://127.0.0.1:11200")
        provider_response = httpx.Response(200, json={
            "connected": ["test"],
            "all": [{"id": "test", "name": "Test", "models": {
                "image-model": {"name": "Image", "capabilities": {"input": ["text"], "output": ["image"]}},
                "vision-only": {"name": "Vision", "modalities": {"input": ["image"], "output": ["text"]}},
                "gpt-vision": {"name": "GPT", "capabilities": {"input": {"text": True, "image": True}, "output": {"text": True}}},
                "attachment-model": {"name": "Attachment", "capabilities": {"attachment": True}},
            }}],
        }, request=httpx.Request("GET", "http://127.0.0.1:11200/provider"))
        mock_client = AsyncMock()
        mock_client.get.return_value = provider_response
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_client)):
            models = await client.get_models(prefer_cli=False)
        self.assertEqual(models[0]["modalities"]["output"], ["image"])
        self.assertEqual(models[1]["modalities"]["output"], ["text"])
        self.assertIn("image", models[2]["modalities"]["input"])
        self.assertIn("image", models[3]["modalities"]["input"])
        merged, _ = chat._mark_model_sources(
            [{"id": item["id"], "name": item["name"]} for item in models], models)
        self.assertEqual(merged[0]["modalities"]["output"], ["image"])

    async def test_old_three_model_config_update_keeps_media_slot(self):
        original = app_config.jevai
        try:
            app_config.jevai = JevAIConfig(provider="typesafe", opencode=JevModelTiers(
                fast="provider/fast", balanced="provider/balanced", strong="provider/strong",
                multimodal="provider/media"))
            request = config_routes.JevAIConfigUpdateRequest(
                provider="typesafe",
                opencode={"fast": "provider/fast", "balanced": "provider/balanced", "strong": "provider/strong"},
                codex={"fast": "codex/fast", "balanced": "codex/balanced", "strong": "codex/strong"},
            )
            with patch.object(config_routes, "save_config"):
                response = await config_routes.update_jevai_config(request)
            self.assertEqual(response["data"]["opencode"]["multimodal"], "provider/media")
            request.opencode["multimodal"] = "provider/strong"
            with patch.object(config_routes, "save_config"):
                reused = await config_routes.update_jevai_config(request)
            self.assertEqual(reused["data"]["opencode"]["multimodal"], "provider/strong")
        finally:
            app_config.jevai = original

    def test_stage_record_keeps_model_version_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "conversations.db"
            with patch.object(jevai.settings, "CONVERSATIONS_DB", db_path):
                jevai.record_stage("run-1", 7, 1, "running", "provider/fast", "开始", {
                    "jev_model": "jev-1.13.0", "role": "fast", "confidence": 0.91,
                })
                jevai.record_stage("run-1", 7, 1, "completed", "provider/fast", "完成", {
                    "jev_model": "jev-1.13.0", "role": "fast", "confidence": 0.91,
                }, ["doc-1.md"])
            with closing(sqlite3.connect(db_path)) as connection:
                row = connection.execute("SELECT status, jev_model, route_role, confidence, artifacts FROM jevai_stages").fetchone()
            self.assertEqual(row[0:3], ("completed", "jev-1.13.0", "fast"))
            self.assertEqual(row[4], '["doc-1.md"]')


if __name__ == "__main__":
    unittest.main()
