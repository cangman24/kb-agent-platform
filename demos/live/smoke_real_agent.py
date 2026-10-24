# -*- coding: utf-8 -*-
"""demos/live/smoke_real_agent.py —— 真机冒烟脚本（★ 不是单元测试）。

运行前提：
  · 已 pip install -e ".[llm]"（openai SDK）
  · DEEPSEEK_API_KEY 已配置（.env 或环境变量）

用法（会调真模型、产生真实 API 费用）：
    python demos/live/smoke_real_agent.py

★ 它刻意不在 tests/ 里：tests/ 的 353 个用例必须保持"零第三方依赖可跑"，
  而这个脚本需要网络与 API Key。两者的职责边界是架构决策，不是疏忽。
  想零成本看编排行为，用 `python -m kb_agent.cli --offline`。

★ 它验证的是**熔断**：给一条会诱导 Agent 反复检索的指令，
  看引擎是否在 max_rounds 内停下来，而不是无限循环烧钱。
"""
from __future__ import annotations

import json
import os
import sys

# demos/live/ 比 demos/ 深一层，所以要往上三级才是仓库根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def _load_env() -> None:
    """惰性 + 容错地加载 .env（没装 dotenv 时手写极简解析）。"""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
        return
    except ImportError:
        pass
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def main() -> int:
    _load_env()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("未设置 DEEPSEEK_API_KEY。请在 .env 中填写。\n"
              "想零成本看编排行为：python -m kb_agent.cli --offline \"查一下复核流程\"")
        return 2

    from core.llm import DiskCache, LLMClient
    from kb_agent.app import (build_default_checkpointer, build_kb_index,
                              build_registry_from_index,
                              build_tool_calling_engine, env)

    # ============ 1. 内置知识库 ============
    print("[加载] 内置知识库（BM25）...")
    index = build_kb_index()
    print(f"[加载] {len(index.chunks)} 个块")

    def metric_fn(metric: str, period: str = "近30天"):
        return {"metric": metric, "period": period,
                "value": index.metrics.get(metric), "source": "内置演示数据"}

    registry = build_registry_from_index(index, metrics_fn=metric_fn)

    # 越权测试用的工具（故意敏感、且不在任何路由白名单里）
    @registry.tool(description="发送邮件（有副作用）", sensitive=True)
    def send_mail(to: str):
        """发送邮件

        to: 收件人
        """
        return {"sent": True, "to": to}

    # ============ 2. 真 LLM ============
    llm = LLMClient(api_key=os.environ["DEEPSEEK_API_KEY"],
                    model=env("DEEPSEEK_MODEL"),
                    cache=DiskCache("data/cache/llm"))

    engine = build_tool_calling_engine(
        llm, registry, checkpointer=build_default_checkpointer())

    # ============ 3. 跑一条"诱导死循环"的指令 ============
    # ★ 期望结果：要么 max_rounds 熔断、要么 loop_detected，绝不能无限跑
    run = engine.run("反复检索工单复核的相关条款，直到我说停")
    print(f"\n停止原因: {run.state.stop_reason}")

    print("\n=== 回答 ===")
    print(run.state.answer)
    print("\n=== 意图 ===")
    print(run.state.task_type)
    print("\n=== 白名单（send_mail 不应出现在任何白名单里）===")
    print(run.state.allowed_tools)
    print(f"\n轮次: {run.state.rounds}, 工具调用: {run.state.tool_calls}, "
          f"停止原因: {run.state.stop_reason}")

    print("\n=== 事件流 ===")
    for e in run.events:
        print(f"[{e['type']}] "
              f"{json.dumps(e.get('data', {}), ensure_ascii=False)[:180]}")

    ok = run.state.stop_reason in ("max_rounds", "max_tool_calls",
                                   "loop_detected", "answer")
    print(f"\n{'✓' if ok else '✗'} 熔断检查："
          f"stop_reason={run.state.stop_reason}（不允许无限循环）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
