# AGENTS

- 在本仓库根目录验证改动，使用本仓库的 `.venv`。Domain RAG QA 有独立的同名 `core` 包。
- 引擎与工具逻辑在 `core/`，知识库和接口装配在 `kb_agent/`。执行控制改动同时核对自实现和 LangGraph 的停止原因、工具边界和检查点续跑。
- 回归运行 `python -m unittest discover -s tests` 和 `python demos/demo_agent_trace.py`。测试保留标准库离线入口。
- 对外展示时说明使用脚本化决策还是真模型。同步工具超时后可能继续执行，保持轨迹和文档中的语义一致。
- 先提交 diff 和验证结果给用户审计。得到用户确认后再创建 Git 提交。
