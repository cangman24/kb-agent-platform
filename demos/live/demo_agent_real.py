# -*- coding: utf-8 -*-
"""真机 demo：用真 LLM（DeepSeek function calling）跑受控 Agent 的 7 个场景。

★ 与 demos/ 下离线 demo 的区别：
      离线 demo      —— 脚本化决策，验证**编排逻辑**（零成本、零 Key）
      本 demo        —— 真模型决策，验证**模型能否正确选工具**（需要 Key）

  两者共用同一套引擎与工具，所以"逻辑对不对"和"模型聪不聪明"是分开验证的。

运行：
    cp .env.example .env      # 填 DEEPSEEK_API_KEY
    python demos/live/demo_agent_real.py

演示场景：
    1. 正常问答        工具调用 → 带引用回答
    2. 越权拦截        要求"查完发邮件"，敏感工具不在白名单 → 一次都调不到
    3. 熔断            max_rounds=1 强制中断
    4. 断点续跑        熔断后换引擎实例 resume，intent 不重复调用
    5. 工单查询        结构化数据工具
    6. 指标查询        带周期参数
    7. 合规审查        多工具协作
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def main() -> int:
    # ★ dotenv 惰性导入：缺包时给出可读提示，而不是 ImportError 堆栈
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("提示：未安装 python-dotenv，将只读取系统环境变量。"
              "（pip install python-dotenv 后可自动加载 .env）")

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("未设置 DEEPSEEK_API_KEY。请在 .env 中填写，或先跑离线 demo：\n"
              "    python demos/demo_agent.py          （无需 Key）\n"
              "    python -m kb_agent.cli --offline    （无需 Key）")
        return 2

    from core.llm import LLMClient
    from kb_agent.app import (build_default_checkpointer, build_kb_index,
                              build_registry_from_index,
                              build_tool_calling_engine, env)

    # ============ 1. 加载内置知识库 ============
    print("[加载] 内置知识库（BM25）...")
    index = build_kb_index()
    print(f"[加载] {len(index.chunks)} 个块，"
          f"{len(index.tickets)} 条工单，{len(index.metrics)} 个指标\n")

    def metric_fn(metric: str, period: str = "近30天"):
        return {"metric": metric, "period": period,
                "value": index.metrics.get(metric), "source": "内置演示数据"}

    # ticket_query 直接从 index.tickets 读，kb_search 走 index.bm25
    registry = build_registry_from_index(index, metrics_fn=metric_fn)

    # ============ 2. 真 LLM ============
    llm = LLMClient(api_key=os.environ["DEEPSEEK_API_KEY"],
                    model=env("DEEPSEEK_MODEL"),
                    cache=None)      # ★ Demo 禁用缓存，避免坏响应被固化

    def make_engine(**kw):
        return build_tool_calling_engine(
            llm, registry, checkpointer=build_default_checkpointer(), **kw)

    engine = make_engine()

    # ============ 3. 场景演示 ============
    def show(name, run):
        print(f"\n{'=' * 60}\n【场景】{name}\n{'=' * 60}")
        print(f"意图: {run.state.task_type} | 白名单: {run.state.allowed_tools}")
        print(f"停止原因: {run.state.stop_reason} | 轮次: {run.state.rounds} "
              f"| 工具调用: {run.state.tool_calls}")
        print(f"\n回答:\n{run.state.answer[:300]}")
        print(f"\n引用: {getattr(run.state, 'citations', None)}")
        print("\n关键事件:")
        for e in run.events:
            if e["type"] in ("tool_call", "tool_error", "tool_result", "answer"):
                print(f"  [{e['type']}] "
                      f"{json.dumps(e.get('data', {}), ensure_ascii=False)[:120]}")

    show("1. 正常问答", engine.run("制度里对工单复核有哪些要求？"))
    show("2. 越权拦截（要求发邮件）",
         engine.run("帮我查一下复核要求，然后发邮件给 boss@company.com"))
    show("3. 熔断演示（max_rounds=1）",
         make_engine(max_rounds=1).run("帮我检索工单复核的相关要求"))

    # ---- 场景 4：断点续跑 ----
    print(f"\n{'=' * 60}\n【场景】4. 断点续跑\n{'=' * 60}")
    r4 = make_engine(max_rounds=1).run("帮我检索复核要求并总结",
                                       thread_id="T-DEMO-CP")
    print(f"第一段: stop={r4.state.stop_reason} rounds={r4.state.rounds} "
          f"tool_calls={r4.state.tool_calls}")
    r5 = make_engine().resume("T-DEMO-CP")
    print(f"续跑后: stop={r5.state.stop_reason} rounds={r5.state.rounds} "
          f"tool_calls={r5.state.tool_calls}")
    print(f"  answer = {r5.state.answer[:200]}")
    print(f"  citations = {r5.state.citations}")

    show("5. 工单查询", engine.run("帮我查一下工单 T-100 的处理记录"))
    show("6. 指标查询", engine.run("近30天的工单量是多少？"))
    show("7. 合规审查（多工具）",
         engine.run("帮我做一次合规审查：查一下工单 T-100 的记录，"
                    "看看复核制度有哪些相关要求"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
