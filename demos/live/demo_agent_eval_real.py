# -*- coding: utf-8 -*-
"""真机 demo：用真 LLM 跑 Agent 层四档消融评测。

★ 与 demos/demo_agenteval.py（离线）的分工：
      离线版   —— 脚本化决策，验证**评测框架本身**的指标算得对
      本 demo  —— 真模型决策，回答"每个能力开关到底值多少分"

  四档消融：关闭记忆 / 关闭压缩 / 关闭熔断 / 全开。
  结论要能回答"哪一层能力的边际收益最大"，而不是只看最终成功率。

运行：
    cp .env.example .env      # 填 DEEPSEEK_API_KEY
    python demos/live/demo_agent_eval_real.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

TASKS_PATH = "data/eval/demo_agent_tasks.jsonl"


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("提示：未安装 python-dotenv，将只读取系统环境变量。")

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("未设置 DEEPSEEK_API_KEY。请在 .env 中填写，或先跑离线评测 demo：\n"
              "    python demos/demo_agenteval.py")
        return 2

    from core.agent_eval import (agent_ablation_configs, load_agent_tasks,
                                 render_agent_ablation, run_agent_eval)
    from core.llm import LLMClient
    from kb_agent.app import (build_kb_index, build_registry_from_index,
                              build_tool_calling_ablation_engine, env,
                              rule_based_intent, rule_based_plan)

    # ============ 1. 内置知识库 ============
    index = build_kb_index()
    print(f"[加载] {len(index.chunks)} 个块，{len(index.tickets)} 条工单")

    def metric_fn(metric: str, period: str = "近30天"):
        return {"metric": metric, "period": period,
                "value": index.metrics.get(metric), "source": "内置演示数据"}

    registry = build_registry_from_index(index, metrics_fn=metric_fn)

    # ============ 2. 任务集 ============
    tasks = load_agent_tasks(TASKS_PATH)
    print(f"[加载] 任务数: {len(tasks)}")

    # ============ 3. 真 LLM ============
    llm = LLMClient(api_key=os.environ["DEEPSEEK_API_KEY"],
                    model=env("DEEPSEEK_MODEL"), cache=None)

    # ============ 4. 四档消融 ============
    reports = []
    for cfg in agent_ablation_configs():
        print(f"\n[跑评测] {cfg.name} ({cfg.label()}) ...")
        engine = build_tool_calling_ablation_engine(
            llm, registry, cfg,
            intent_fn=rule_based_intent,
            plan_fn=rule_based_plan,
        )
        # ★ run_fn 必须返回原生 AgentRun：评测要读 state 与 events 两层信息
        report = run_agent_eval(
            tasks,
            run_fn=lambda q, e=engine: e.run(q),
            config_name=f"{cfg.name} ({cfg.label()})",
        )
        reports.append(report)

    # ============ 5. 输出指标表 ============
    print("\n" + "=" * 80)
    print("Agent 层消融评测结果（真机）")
    print("=" * 80)
    print(render_agent_ablation(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
