"""
自我循环任务求解器
当一种方法失败时自动切换其他方法
"""
import asyncio
import time
from typing import Callable, List, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class SolverConfig:
    """求解器配置"""
    max_iterations: int = 5
    max_duration: int = 300  # 秒
    max_tokens: int = 100000


@dataclass
class TaskResult:
    """任务结果"""
    success: bool
    content: str = ""
    tokens_used: int = 0
    error: Optional[str] = None
    iteration: int = 0


class TaskSolver:
    """自我循环任务求解器"""
    
    def __init__(self, config: SolverConfig = None):
        self.config = config or SolverConfig()
        self.strategies: List[Callable] = []
    
    def add_strategy(self, strategy: Callable):
        """添加解决策略"""
        self.strategies.append(strategy)
        logger.info(f"添加解决策略：{strategy.__name__}")
    
    async def solve(
        self,
        task: str,
        opencode_ws,
        progress_callback: Optional[Callable] = None
    ) -> TaskResult:
        """
        解决任务
        当一种方法失败时自动切换其他方法
        """
        start_time = time.time()
        iteration = 0
        total_tokens = 0
        
        logger.info(f"开始求解任务：{task[:50]}...")
        
        while iteration < self.config.max_iterations:
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed > self.config.max_duration:
                logger.warning(f"任务求解超时 ({self.config.max_duration}秒)")
                return TaskResult(
                    success=False,
                    error=f"任务求解超时 ({self.config.max_duration}秒)"
                )
            
            # 检查 token 限制
            if total_tokens > self.config.max_tokens:
                logger.warning(f"达到最大 Token 限制 ({self.config.max_tokens})")
                return TaskResult(
                    success=False,
                    error=f"达到最大 Token 限制 ({self.config.max_tokens})"
                )
            
            # 选择策略
            strategy = self.strategies[iteration % len(self.strategies)]
            
            try:
                logger.info(f"尝试策略：{strategy.__name__} (迭代 {iteration + 1})")
                
                # 执行策略
                result = await strategy(task, opencode_ws)
                total_tokens += result.tokens_used
                
                if result.success:
                    logger.info(f"任务求解成功 (迭代 {iteration + 1})")
                    result.iteration = iteration + 1
                    return result
                
                # 通知进度
                if progress_callback:
                    await progress_callback(
                        iteration=iteration,
                        error=result.error,
                        strategy=strategy.__name__
                    )
                
            except Exception as e:
                logger.error(f"策略执行失败：{e}")
                
                if progress_callback:
                    await progress_callback(
                        iteration=iteration,
                        error=str(e),
                        strategy=strategy.__name__
                    )
            
            iteration += 1
        
        logger.error(f"任务求解失败：达到最大尝试次数")
        return TaskResult(
            success=False,
            error="任务求解失败：达到最大尝试次数"
        )


# 预定义策略
async def strategy_direct(task: str, ws) -> TaskResult:
    """直接执行策略"""
    result = await ws.execute_task(task)
    return TaskResult(
        success=result.success,
        content=result.content,
        tokens_used=result.tokens_used,
        error=result.error
    )


async def strategy_with_context(task: str, ws) -> TaskResult:
    """带上下文的执行策略"""
    prompt = f"""请仔细分析并执行以下任务:

任务：{task}

要求:
1. 理解任务的背景和目标
2. 分析可能需要的步骤
3. 逐步执行
4. 检查结果是否正确

请开始执行:"""
    
    result = await ws.execute_task(prompt)
    return TaskResult(
        success=result.success,
        content=result.content,
        tokens_used=result.tokens_used,
        error=result.error
    )


async def strategy_decompose(task: str, ws) -> TaskResult:
    """任务分解策略"""
    # 第一步：分解任务
    decompose_prompt = f"""请将以下任务分解为具体的执行步骤:

任务：{task}

请列出:
1. 任务目标
2. 需要的步骤
3. 每个步骤的具体操作
4. 预期结果"""
    
    decompose_result = await ws.execute_task(decompose_prompt)
    
    if not decompose_result.success:
        return TaskResult(
            success=False,
            error=f"任务分解失败：{decompose_result.error}"
        )
    
    # 第二步：执行分解后的任务
    execute_prompt = f"""根据以下任务分析和步骤，执行任务:

{decompose_result.content}

请开始执行:"""
    
    result = await ws.execute_task(execute_prompt)
    return TaskResult(
        success=result.success,
        content=result.content,
        tokens_used=result.tokens_used + decompose_result.tokens_used,
        error=result.error
    )


async def strategy_verify_and_retry(task: str, ws) -> TaskResult:
    """验证并重试策略"""
    # 第一次执行
    result = await ws.execute_task(task)
    
    if not result.success:
        return result
    
    # 验证结果
    verify_prompt = f"""请验证以下任务执行结果是否正确和完整:

任务：{task}

结果：{result.content}

如果结果有问题，请指出并补充完善。"""
    
    verify_result = await ws.execute_task(verify_prompt)
    
    return TaskResult(
        success=verify_result.success,
        content=verify_result.content,
        tokens_used=result.tokens_used + verify_result.tokens_used,
        error=verify_result.error
    )
