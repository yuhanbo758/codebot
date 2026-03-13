"""
记忆自动整理模块

每日在配置的时间点，通过 AI 对所有活跃长期记忆进行优化：
  - 合并高度重复的条目
  - 补全过于简短的描述
  - 标准化表达格式
  - 修正明显错误或矛盾

整理流程：
  1. 按类别分批加载活跃记忆（每批最多 MAX_BATCH_SIZE 条）
  2. 将每批内容拼成 Prompt，调用 AI 返回整理后的 JSON 列表
  3. 对比 AI 返回结果与原始内容：
     - 内容有实质改动的 → 更新
     - AI 标记为需要删除的（content 为空或带 __delete__ 标记）→ 归档
     - AI 新增的合并条目 → 插入
  4. 将本次整理摘要写入日志，并更新 organize_last_run
"""
import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from loguru import logger


MAX_BATCH_SIZE = 30   # 每批最多处理的记忆条数
_RUNNING = False       # 防并发


# ── AI 调用封装 ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位记忆整理助手。用户会给你一批个人记忆条目（JSON 列表），每条包含 id、category、content。
请对这批记忆做以下优化，然后以 JSON 列表返回结果：

1. **合并重复**：内容高度相似或互相包含的条目合并为一条（保留语义更完整的那条内容，id 保留最早的那个）。
   - 被合并掉的条目，在返回列表中设置 "__delete__": true（不要省略，这是删除信号）。
2. **补全简短描述**：若内容过于简短（如仅 2-3 字），根据上下文或常识适当补全，使其可读。
3. **标准化格式**：统一用第三人称或客观陈述，去掉冗余语气词，保持简洁。
4. **修正矛盾**：同一类别下明显矛盾的条目，保留更新的那条，较旧的设 "__delete__": true。

输出规则：
- 返回纯 JSON，不要包含任何解释文字或代码块标记。
- 格式：[{"id": <原始id>, "category": "<原始category>", "content": "<整理后内容>"}, ...]
- 被删除的条目格式：{"id": <原始id>, "__delete__": true}
- 不要增加新字段，不要改变 id 和 category。
- 如果某条记忆无需改动，仍然要在返回列表中包含它（保持原样）。
"""


async def _call_ai_organize(
    batch: List[Dict],
    opencode_ws,
) -> Optional[List[Dict]]:
    """调用 AI 整理一批记忆，返回 AI 给出的 JSON 列表，失败返回 None。"""
    if opencode_ws is None or not getattr(opencode_ws, "connected", False):
        logger.warning("[memory_organizer] OpenCode 未连接，跳过 AI 整理")
        return None

    input_items = [
        {"id": m["id"], "category": m.get("category", "note"), "content": m.get("content", "")}
        for m in batch
    ]
    user_content = json.dumps(input_items, ensure_ascii=False)

    try:
        # 复用 chat.py 风格：通过 opencode_ws 发起单轮对话
        session_id = f"memory_organize_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        response_text = await opencode_ws.send_message(
            session_id=session_id,
            message=user_content,
            system_prompt=_SYSTEM_PROMPT,
        )
        if not response_text:
            return None

        # 提取 JSON（容忍模型在回复里多输出文字）
        json_match = re.search(r"\[[\s\S]*\]", response_text)
        if not json_match:
            logger.warning("[memory_organizer] AI 回复中未找到 JSON 列表")
            return None

        result = json.loads(json_match.group(0))
        if not isinstance(result, list):
            return None
        return result

    except Exception as exc:
        logger.error(f"[memory_organizer] AI 调用失败: {exc}")
        return None


# ── 规则兜底整理（无 AI 时使用）────────────────────────────────────────────

def _rule_based_organize(batch: List[Dict]) -> Tuple[List[Dict], List[int]]:
    """
    不依赖 AI 的简单规则整理：
      - 内容完全相同的条目只保留最早一条
    返回 (updated_list, delete_ids)
    """
    seen: Dict[str, int] = {}   # content → first_id
    delete_ids: List[int] = []
    updated: List[Dict] = []

    for m in batch:
        content = (m.get("content") or "").strip()
        mid = int(m["id"])
        if content in seen:
            delete_ids.append(mid)
        else:
            seen[content] = mid
            updated.append(m)

    return updated, delete_ids


# ── 核心整理逻辑 ─────────────────────────────────────────────────────────────

async def organize_memories(
    memory_manager,
    opencode_ws=None,
) -> Dict:
    """
    对所有活跃长期记忆执行一次整理。

    返回摘要字典：
      {
        "total": <整理前总数>,
        "updated": <内容被更新的条数>,
        "archived": <被归档（删除）的条数>,
        "batches": <批次数>,
        "used_ai": <是否使用了 AI>,
        "finished_at": <ISO时间字符串>,
      }
    """
    global _RUNNING
    if _RUNNING:
        logger.warning("[memory_organizer] 整理任务已在运行中，跳过")
        return {"skipped": True, "reason": "already_running"}

    _RUNNING = True
    summary = {
        "total": 0, "updated": 0, "archived": 0,
        "batches": 0, "used_ai": False, "finished_at": None,
    }

    try:
        logger.info("[memory_organizer] 开始记忆整理...")

        # 按类别逐批处理
        categories = ["habit", "preference", "profile", "note", "contact", "address"]
        use_ai = (opencode_ws is not None and getattr(opencode_ws, "connected", False))

        for category in categories:
            offset = 0
            while True:
                batch = await memory_manager.get_memories(
                    category=category,
                    archived=False,
                    limit=MAX_BATCH_SIZE,
                    offset=offset,
                )
                if not batch:
                    break

                summary["total"] += len(batch)
                summary["batches"] += 1

                if use_ai:
                    ai_result = await _call_ai_organize(batch, opencode_ws)
                else:
                    ai_result = None

                if ai_result is not None:
                    summary["used_ai"] = True
                    await _apply_ai_result(batch, ai_result, memory_manager, summary)
                else:
                    _, delete_ids = _rule_based_organize(batch)
                    for did in delete_ids:
                        await memory_manager.archive_memory(did)
                        summary["archived"] += 1

                if len(batch) < MAX_BATCH_SIZE:
                    break
                offset += MAX_BATCH_SIZE

        summary["finished_at"] = datetime.now().isoformat()
        logger.info(
            f"[memory_organizer] 整理完成 — 总条数: {summary['total']}, "
            f"更新: {summary['updated']}, 归档: {summary['archived']}, "
            f"使用AI: {summary['used_ai']}"
        )

        # 更新上次运行时间
        try:
            from config import app_config, save_config
            app_config.memory.organize_last_run = summary["finished_at"]
            save_config(app_config)
        except Exception as cfg_err:
            logger.warning(f"[memory_organizer] 更新 organize_last_run 失败: {cfg_err}")

    except Exception as exc:
        logger.error(f"[memory_organizer] 整理过程出错: {exc}")
        summary["error"] = str(exc)
    finally:
        _RUNNING = False

    return summary


async def _apply_ai_result(
    original_batch: List[Dict],
    ai_result: List[Dict],
    memory_manager,
    summary: Dict,
):
    """将 AI 整理结果应用到数据库。"""
    original_map: Dict[int, Dict] = {int(m["id"]): m for m in original_batch}

    for item in ai_result:
        try:
            mid = int(item.get("id", -1))
            if mid <= 0 or mid not in original_map:
                continue

            # 删除信号
            if item.get("__delete__"):
                await memory_manager.archive_memory(mid)
                summary["archived"] += 1
                continue

            new_content = (item.get("content") or "").strip()
            if not new_content:
                await memory_manager.archive_memory(mid)
                summary["archived"] += 1
                continue

            orig_content = (original_map[mid].get("content") or "").strip()
            if new_content != orig_content:
                await memory_manager.update_long_term_memory(
                    memory_id=mid,
                    content=new_content,
                    metadata={
                        **((original_map[mid].get("metadata") or {})),
                        "organized_at": datetime.now().isoformat(),
                        "organized_from": orig_content[:100],
                    },
                )
                summary["updated"] += 1

        except Exception as item_err:
            logger.warning(f"[memory_organizer] 处理条目 {item} 时出错: {item_err}")


# ── 后台定时循环 ─────────────────────────────────────────────────────────────

async def run_organize_loop(get_memory_manager_fn, get_opencode_ws_fn, get_config_fn):
    """
    后台无限循环：每分钟检查一次当前时间是否到达配置的整理时间，到达则触发整理。

    参数均为无参可调用，返回对应的实例（支持热更新配置）：
      get_memory_manager_fn() -> MemoryManager
      get_opencode_ws_fn()    -> OpenCodeClient | None
      get_config_fn()         -> AppConfig
    """
    logger.info("[memory_organizer] 自动整理循环已启动")
    last_triggered_date: Optional[str] = None   # "YYYY-MM-DD"

    while True:
        try:
            await asyncio.sleep(30)   # 每 30 秒检查一次

            cfg = get_config_fn()
            mem_cfg = cfg.memory

            if not mem_cfg.organize_enabled:
                continue

            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # 解析配置的整理时间
            try:
                hour, minute = map(int, mem_cfg.organize_time.split(":"))
            except Exception:
                continue

            # 是否在该时间窗口内（允许 ±1 分钟误差）
            target_minute = hour * 60 + minute
            now_minute = now.hour * 60 + now.minute
            in_window = abs(now_minute - target_minute) <= 1

            if not in_window:
                continue

            # 今天是否已经触发过
            if last_triggered_date == today_str:
                continue

            # 检查 organize_last_run 是否今天已运行（防重启后重复触发）
            if mem_cfg.organize_last_run:
                try:
                    last_dt = datetime.fromisoformat(mem_cfg.organize_last_run)
                    if last_dt.strftime("%Y-%m-%d") == today_str:
                        last_triggered_date = today_str
                        continue
                except Exception:
                    pass

            last_triggered_date = today_str
            logger.info(f"[memory_organizer] 到达整理时间 {mem_cfg.organize_time}，启动自动整理...")

            mm = get_memory_manager_fn()
            ws = get_opencode_ws_fn()
            asyncio.create_task(organize_memories(mm, ws))

        except asyncio.CancelledError:
            logger.info("[memory_organizer] 自动整理循环已停止")
            break
        except Exception as loop_err:
            logger.error(f"[memory_organizer] 整理循环异常: {loop_err}")
