"""JevAI 的窄决策层；Agent 会话、工具和文件操作仍由原执行器掌管。"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

from config import app_config, settings


PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/alpha/decisions", "typesafe/jev-1.13", "CODEBOT_JEV_OPENROUTER_API_KEY"),
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "jev-1.13.0", "CODEBOT_JEV_TYPESAFE_API_KEY"),
}
_desktop_keys: Dict[str, str] = {}
_active_routes: Dict[str, TaskRoute] = {}
_stage_checkpoints: Dict[tuple[str, int], dict] = {}
_current_stages: Dict[str, int] = {}


class JevAIError(RuntimeError):
    """可安全显示给用户的 JevAI 配置或决策错误。"""


@dataclass(frozen=True)
class TaskRoute:
    provider: str
    api_key: str
    executor: str
    models: Dict[str, str]
    confidence_threshold: float


def register_run(run_id: str, route: TaskRoute) -> None:
    _active_routes[run_id] = route
    _current_stages[run_id] = 0


def set_current_stage(run_id: str, stage: int) -> None:
    active_route(run_id)
    _current_stages[run_id] = stage


def active_route(run_id: str) -> TaskRoute:
    route = _active_routes.get(run_id)
    if route is None:
        raise JevAIError("JevAI 任务已结束，无法继续分类")
    return route


def finish_run(run_id: str) -> None:
    _active_routes.pop(run_id, None)
    _current_stages.pop(run_id, None)
    for key in [key for key in _stage_checkpoints if key[0] == run_id]:
        _stage_checkpoints.pop(key, None)


def save_checkpoint(run_id: str, stage: int, status: str, summary: str, artifacts: List[str]) -> dict:
    active_route(run_id)
    if not 1 <= stage <= 20 or status not in {"completed", "blocked"} or _current_stages.get(run_id) != stage:
        raise JevAIError("无效的 JevAI 阶段状态")
    if (run_id, stage) in _stage_checkpoints:
        raise JevAIError("该阶段已经提交检查点")
    checkpoint = {
        "stage": stage, "status": status, "summary": summary[:2000],
        "artifacts": [str(item)[:500] for item in artifacts[:100]],
    }
    _stage_checkpoints[(run_id, stage)] = checkpoint
    return checkpoint


def take_checkpoint(run_id: str, stage: int) -> Optional[dict]:
    return _stage_checkpoints.pop((run_id, stage), None)


def validate_media_artifacts(artifacts: List[str], output_kind: str, project_dir: Optional[str]) -> None:
    """媒体阶段只有在原执行器确实留下非空文件时才算完成。"""
    suffixes = {
        "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"},
        "audio": {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"},
        "video": {".mp4", ".mov", ".webm", ".mkv"},
    }
    if output_kind not in suffixes:
        return
    base = Path(project_dir).expanduser() if project_dir else Path.cwd()
    for artifact in artifacts:
        path = Path(artifact).expanduser()
        if not path.is_absolute():
            path = base / path
        try:
            if path.suffix.lower() in suffixes[output_kind] and path.is_file() and path.stat().st_size > 0:
                return
        except OSError:
            continue
    raise JevAIError(f"媒体阶段未确认生成有效的 {output_kind} 文件，任务已暂停；不会自动重放")


def record_stage(run_id: str, conversation_id: int, stage: int, status: str, model: str, summary: str, decision: Optional[dict] = None, artifacts: Optional[List[str]] = None) -> None:
    """只记录阶段和短摘要；中断后可审查进度而不自动重放写操作。"""
    with closing(sqlite3.connect(settings.CONVERSATIONS_DB, timeout=5)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jevai_stages (
            run_id TEXT NOT NULL, conversation_id INTEGER NOT NULL, stage INTEGER NOT NULL,
            status TEXT NOT NULL, model TEXT NOT NULL, summary TEXT NOT NULL,
            jev_model TEXT NOT NULL DEFAULT '', route_role TEXT NOT NULL DEFAULT '', confidence REAL,
            artifacts TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, stage))""")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jevai_stages)")}
        for column, definition in (("jev_model", "TEXT NOT NULL DEFAULT ''"), ("route_role", "TEXT NOT NULL DEFAULT ''"), ("confidence", "REAL"), ("artifacts", "TEXT NOT NULL DEFAULT '[]'")):
            if column not in existing:
                conn.execute(f"ALTER TABLE jevai_stages ADD COLUMN {column} {definition}")
        selected = decision or {}
        conn.execute("""INSERT INTO jevai_stages (run_id, conversation_id, stage, status, model, summary, jev_model, route_role, confidence, artifacts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, stage) DO UPDATE SET
            status=excluded.status, model=excluded.model, summary=excluded.summary,
            jev_model=excluded.jev_model, route_role=excluded.route_role, confidence=excluded.confidence, artifacts=excluded.artifacts,
            updated_at=CURRENT_TIMESTAMP""",
            (run_id, conversation_id, stage, status, model, summary[:2000],
             str(selected.get("jev_model") or ""), str(selected.get("role") or ""), selected.get("confidence"),
             json.dumps(artifacts or [], ensure_ascii=False)))


def set_desktop_key(provider: str, key: str) -> None:
    if provider not in PROVIDERS:
        raise JevAIError("不支持的 Jev 供应商")
    if key:
        _desktop_keys[provider] = key.strip()
    else:
        _desktop_keys.pop(provider, None)


def credential_configured(provider: str) -> bool:
    return bool(get_credential(provider))


def get_credential(provider: str) -> str:
    if provider not in PROVIDERS:
        return ""
    return _desktop_keys.get(provider) or os.environ.get(PROVIDERS[provider][2], "").strip()


def route_snapshot(executor: str, available_ids: Iterable[str]) -> TaskRoute:
    if executor not in {"opencode", "codex"}:
        raise JevAIError("JevAI 只支持 OpenCode 与 Codex 对话")
    cfg = app_config.jevai
    models = getattr(cfg, executor).model_dump()
    llm_models = [models[role] for role in ("fast", "balanced", "strong")]
    selected = [value for value in models.values() if value]
    if any(not value for value in llm_models):
        raise JevAIError(f"请先在模式设置中为 {executor} 指定三款不同的模型")
    if len(set(llm_models)) != len(llm_models):
        raise JevAIError(f"{executor} 的轻、中、强模型不能重复")
    available = set(available_ids)
    missing = [model for model in selected if model not in available]
    if missing:
        raise JevAIError("所选模型当前不可运行：" + "、".join(missing))
    key = get_credential(cfg.provider)
    if not key:
        raise JevAIError("请先在模式设置中配置 Jev 供应商密钥")
    return TaskRoute(cfg.provider, key, executor, models, cfg.confidence_threshold)


async def decide(route: TaskRoute, state: Any, questions: Dict[str, dict]) -> dict:
    """两个供应商共享 Jev 问题协议；只重试限流/过载，不更换供应商。"""
    endpoint, model, _ = PROVIDERS[route.provider]
    payload = {"model": model, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {route.api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        for attempt in range(3):
            try:
                response = await client.post(endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise JevAIError("Jev 服务连接失败，任务已暂停") from exc
            if response.status_code in {429, 529} and attempt < 2:
                await asyncio.sleep(min(0.5 * (2 ** attempt), 2.0))
                continue
            if response.status_code != 200:
                raise JevAIError(f"Jev 服务返回 HTTP {response.status_code}，任务已暂停")
            try:
                data = response.json()
            except ValueError as exc:
                raise JevAIError("Jev 返回的数据不是合法 JSON，任务已暂停") from exc
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict) or not isinstance(data.get("model"), str) or not data["model"].strip():
                raise JevAIError("Jev 返回的数据缺少决策结果，任务已暂停")
            return data
    raise JevAIError("Jev 服务暂不可用，任务已暂停")


async def choose_model(route: TaskRoute, state: dict, *, first_stage: bool = False) -> dict:
    criteria = {
        "fast": "简单翻译、抽取、格式化等短任务；优先减少费用和等待时间。",
        "balanced": "常规写作、检索、一般编码或需要适度推理的任务。",
        "strong": "复杂推理、多文件修改、长任务、失败恢复或高风险判断。",
    }
    questions: Dict[str, dict] = {
        "model_role": {
            "type": "choice",
            "instructions": "根据当前阶段的任务复杂度，在三档文字 LLM 中选最合适的一档。",
            "criteria": criteria,
        },
        "output_kind": {
            "type": "choice",
            "instructions": "判断当前阶段需要交付的实际内容类型。只有用户要生成可用的图片、音频或视频文件时选择相应媒体类型；图片描述、绘图代码、音频脚本和普通问答选 text。",
            "criteria": {
                "text": "文字、代码、文档或规划；没有实际图片/音频文件交付。",
                "image": "需要实际生成或编辑图片文件。",
                "audio": "需要实际生成或编辑音频文件。",
                "video": "需要实际生成或编辑视频文件。",
            },
        },
    }
    if first_stage:
        questions["multi_stage"] = {
            "type": "noul",
            "instructions": "这个请求是否需要两个或更多有明确完成边界的执行阶段？简单问答和单次生成应回答否。",
        }
    result = await decide(route, state, questions)
    answer = result["answers"].get("model_role")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JevAIError("Jev 未返回有效的模型选择，任务已暂停")
    role = answer.get("choice")
    confidence = answer.get("confidence")
    probabilities = answer.get("probabilities")
    llm_roles = {"fast", "balanced", "strong"}
    if role not in llm_roles or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not isinstance(probabilities, dict) or set(probabilities) != llm_roles or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in probabilities.values()) or abs(sum(probabilities.values()) - 1) > 0.02:
        raise JevAIError("Jev 返回了无效模型或置信度，任务已暂停")
    if confidence < route.confidence_threshold:
        role = "strong"
    output_answer = result["answers"].get("output_kind")
    output_kinds = {"text", "image", "audio", "video"}
    if not isinstance(output_answer, dict) or output_answer.get("type") != "choice" or output_answer.get("choice") not in output_kinds:
        raise JevAIError("Jev 未返回有效的媒体类型判断，任务已暂停")
    output_confidence = output_answer.get("confidence")
    output_probabilities = output_answer.get("probabilities")
    if not isinstance(output_confidence, (int, float)) or not 0 <= output_confidence <= 1 or not isinstance(output_probabilities, dict) or set(output_probabilities) != output_kinds or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in output_probabilities.values()) or abs(sum(output_probabilities.values()) - 1) > 0.02:
        raise JevAIError("Jev 返回了无效的媒体类型概率，任务已暂停")
    output_kind = output_answer["choice"]
    if output_confidence < route.confidence_threshold:
        raise JevAIError("Jev 对本阶段输出类型的判断置信度不足，任务已暂停")
    if output_kind != "text":
        if output_kind == "video":
            raise JevAIError("当前未配置视频生成协议，任务已暂停")
        from core.media_runtime import configured_service, credential_configured, MediaError
        service_kind = "image" if output_kind == "image" else "speech"
        try:
            configured_service(service_kind)
        except MediaError as exc:
            raise JevAIError(str(exc)) from exc
        if not credential_configured(service_kind):
            raise JevAIError(f"请先在设置 → 媒体中配置{service_kind}服务密钥")
    elif state.get("image_input") and route.models.get("multimodal"):
        role = "multimodal"
    multi = result["answers"].get("multi_stage") if first_stage else None
    if first_stage and (not isinstance(multi, dict) or multi.get("type") != "noul" or not isinstance(multi.get("noul"), (int, float)) or not 0 <= multi["noul"] <= 1):
        raise JevAIError("Jev 未返回有效的阶段判断，任务已暂停")
    return {
        "role": role,
        "model": route.models[role],
        "planning_model": route.models["strong" if confidence < route.confidence_threshold else answer["choice"]],
        "output_kind": output_kind,
        "confidence": float(confidence),
        "multi_stage": bool(multi and multi["noul"] >= 0.65),
        "jev_model": str(result.get("model") or ""),
        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
    }


async def classify_records(route: TaskRoute, records: List[dict], categories: Dict[str, str]) -> List[dict]:
    """LLM 给出稳定选项后，由 Jev 只返回分类，不触碰文件。"""
    if not 2 <= len(categories) <= 50 or any(
        not isinstance(key, str) or not key or len(key) > 80 or
        not isinstance(value, str) or not value.strip() or len(value) > 500
        for key, value in categories.items()
    ) or sum(len(key) + len(value) for key, value in categories.items()) > 6000:
        raise JevAIError("分类选项须为 2–50 个有描述的固定类别")
    if not 1 <= len(records) <= 200:
        raise JevAIError("一次分类须包含 1–200 条记录")
    if any(not isinstance(item, dict) or not str(item.get("id") or "").strip() or
           not isinstance(item.get("text"), str) or not item["text"].strip() or
           len(item["text"]) > 800 for item in records):
        raise JevAIError("每条分类记录须包含 ID 和不超过 800 字的文本摘要")
    if len({str(item["id"]) for item in records}) != len(records):
        raise JevAIError("分类记录 ID 必须唯一")
    output: List[dict] = []
    # 限制每批 state 与问题长度，避免 100 篇文档挤入单次上下文。
    for offset in range(0, len(records), 12):
        chunk = records[offset:offset + 12]
        state = {str(index): item["text"] for index, item in enumerate(chunk)}
        questions = {
            f"record_{index}": {
                "type": "choice",
                "instructions": f"将 state 中键为 `{index}` 的记录归入一个类别。",
                "criteria": categories,
            }
            for index in range(len(chunk))
        }
        result = await decide(route, state, questions)
        for index, item in enumerate(chunk):
            answer = result["answers"].get(f"record_{index}")
            if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("choice") not in categories:
                raise JevAIError("Jev 返回了无效分类，任务已暂停")
            confidence = answer.get("confidence")
            probabilities = answer.get("probabilities")
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not isinstance(probabilities, dict) or set(probabilities) != set(categories) or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in probabilities.values()) or abs(sum(probabilities.values()) - 1) > 0.02:
                raise JevAIError("Jev 分类概率无效，任务已暂停")
            output.append({
                "id": str(item["id"]), "category": answer["choice"],
                "confidence": confidence, "probabilities": probabilities,
                "jev_model": result.get("model"),
            })
    return output
