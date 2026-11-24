# TASKS

| 状态 | 工作项 | 验收依据 |
| --- | --- | --- |
| 已验收 | 独立环境与离线入口 | 按 [README](README.md) 运行测试和轨迹主演示。 |
| 已验收 | 工具、任务预算和循环控制 | `python -m unittest discover -s tests -p test_agent_budget.py`。覆盖单工具超时、任务总超时、重复调用与检查点。 |
| 已验收 | 简历展示入口 | [项目主页](README.md) 提供主演示。输出含工具调用、停止原因、未完成任务续跑、轨迹回放与耗时归因。 |
| 待验证 | GitHub Actions | 本地已复验 Windows 和 Linux。远端 CI 以推送后的实际运行记录为准。 |
