# KB Agent Platform

![CI](https://github.com/cangman24/kb-agent-platform/actions/workflows/ci.yml/badge.svg) ![python](https://img.shields.io/badge/python-%3E%3D3.10-blue) ![tests](https://img.shields.io/badge/tests-353%20passing-brightgreen) ![deps](https://img.shields.io/badge/test%20deps-0-brightgreen) ![license](https://img.shields.io/badge/license-MIT-blue)

一个**可控**的知识库 Agent：给大模型接上工具之后，把它关进一套工程约束里 ——
路由白名单决定"能调什么"，熔断决定"最多跑几轮"，Checkpoint 决定"中断后怎么续"，
事件流决定"每一步都看得见"。

核心主张：**Agent 的价值不在"能不能跑通"，而在"失控时会不会出事"**。
所以这个仓库的重点不是提示词，而是编排与管控 —— 而且这些逻辑全部
**零第三方依赖、离线可验证**。

```
pip install -e .                        # 最小安装（什么都不装也能跑）
python -m unittest discover -s tests    # 365 项单测；纯标准库可跑，CI 零依赖环境自动跳过 12 项 LangGraph 真库测试
python -m kb_agent.cli --offline "查一下工单复核流程"   # 无需 API Key
```

---

## 它解决什么问题

直接把工具塞给 LLM 会踩四个坑，每个都在生产环境里会变成事故：

| 问题 | 后果 | 这里的做法 |
|---|---|---|
| 模型想调什么就调什么 | 越权动作（发邮件、写库）真的被执行 | **路由白名单**：意图决定可用工具集，不在名单里的工具根本不在发给模型的 schema 里 |
| 反复调用停不下来 | 成本失控、请求挂死 | **三重熔断**：轮数 / 工具调用次数 / 总超时，任一触发即中断并记录 `stop_reason` |
| 长任务中断就前功尽弃 | 用户体验崩坏 | **Checkpoint 续跑**：状态落盘，换进程 `resume` 继续，intent 不重复计算 |
| 中途是个黑盒 | 出问题无法归因 | **SSE 事件流 + 事后轨迹**：每一步工具调用都有事件，可按 `thread_id` 回放审计 |

---

## 快速开始

```bash
pip install -e .

# 1) 看工具清单与权限白名单（离线）
python -m kb_agent.cli --offline --tools

# 2) 跑一条指令，看得到每一步（离线，无需 API Key）
python -m kb_agent.cli --offline --stream "查一下工单复核流程"

# 3) 接真模型（DeepSeek function calling）
cp .env.example .env        # 填 DEEPSEEK_API_KEY
python -m kb_agent.cli "复核岗的时效要求是什么"

# 4) 起 HTTP 服务
pip install -e ".[serve]"
python -m kb_agent.server    # http://127.0.0.1:8000/docs
```

只用标准库即可跑全部测试与离线 demo —— 不需要装 torch、不需要向量数据库、
不需要 API Key。这是有意为之的设计：编排逻辑与模型解耦，才可能低成本验证。

---

## 架构

```
core/                      零第三方依赖的业务内核（12 个模块）
├── agent.py               受控 Agent 引擎：熔断、循环检测、Checkpoint
├── mcp.py                 工具注册与 schema 生成（MCP 风格）
├── llm.py                 LLM 客户端：限流、缓存、错误分类、指数退避
├── service.py             服务层：收 dict 回 dict，框架无关
├── memory.py              三层记忆 + 上下文压缩（含降级摘要）
├── agent_eval.py          Agent 层评测与消融
├── sse.py                 SSE 编码/解析
├── bm25.py  text.py  metrics.py  retrieval.py  agent_langgraph.py

kb_agent/                  应用层装配
├── app.py                 引擎装配、内置知识库、路由表、环境配置
├── decisions.py           意图路由规则与白名单
├── prompts.py             提示词（版本化管理）
├── api_fastapi.py         HTTP 接线（薄适配，需要 fastapi）
├── cli.py                 命令行（含离线模式）
└── server.py              服务启动入口

demos/                     离线 demo（脚本化决策，无需 Key）
demos/live/                真机 demo（需要 Key 与网络）
tests/                     365 项单测（12 项 LangGraph 真库测试，无依赖环境自动跳过）
```

**分层原则**：业务逻辑在 `core/`（可离线验证），装配在 `kb_agent/`，
传输在 `api_fastapi.py`（只有一百来行）。换 FastAPI 为别的框架，
`core/` 一行都不用改。

---

## 关键机制与实测证据

以下每条都有对应的单测，测试名可直接搜索验证。

| 机制 | 行为 | 证据 |
|---|---|---|
| 路由白名单 | 敏感工具不在意图对应的白名单里，一次都调不到 | `TestAgentService.test_whitelist_blocks_sensitive_tool` |
| 熔断 | 轮数/工具次数/超时任一触发即停，并写入 `stop_reason` | `TestAgent`，`demos/live/smoke_real_agent.py` |
| Checkpoint 续跑 | 熔断中断后换引擎实例 `resume` 继续，intent 只算 1 次 | `TestAgentService.test_resume_continues` |
| 轨迹两级读取 | 同进程读内存（含事件明细）；跨进程回落 Checkpoint（读最终状态） | `test_trace_falls_back_to_checkpoint` |
| 错误分类重试 | 429/5xx 指数退避重试；4xx 快速失败不浪费额度 | `TestLLMClient.test_retry_then_success` |
| 缓存 | 同参数第二次调用命中缓存，**不发起网络请求** | `test_cache_hit_skips_transport` |
| 上下文压缩 | 三级压缩（工具截断 → 早期摘要 → 全量归档），摘要失败会降级不留空 | `TestContextCompressor` |
| 越权审计 | `send_mail` 被标记敏感，且在默认路由表中不开放 | `python -m kb_agent.cli --offline --tools` |
| LangGraph 双引擎 | 同一套业务逻辑可跑自实现状态机或真 `StateGraph`，接口一致、行为一致 | `TestRealLangGraph`（12 项，真机 langgraph 0.2.28 实测） |
| 多智能体拓扑 | supervisor 派活 + worker 编译子图 + verifier 汇总，Mermaid 拓扑可导出 | `demos/live/demo_langgraph_supervisor.py` |

---

## 内置知识库

`data/kb/` 下是一套自造的运维制度语料（工单处置、复核、权限、指标口径、
证据留痕、术语表），配 `tickets.json` 与 `metrics.json` 两个结构化数据源。
检索走 BM25，**不需要嵌入模型**，所以镜像只有约 200MB。

真机要换成向量检索时，只需给 `build_registry_from_index` 传入带 `bm25`/
`chunks` 契约的索引对象 —— 编排与管控逻辑一行都不用改。

---

## 工程化

- **依赖分层**：`pip install -e .` 装什么都不装也能跑；`[llm]` `[serve]` `[dev]` 按需
- **CI**：4 类 job（跨平台单测 / demo 与 CLI 冒烟 / ruff / 密钥防护），ubuntu 约 10 秒内、Windows 约 1 分钟内结束
- **密钥防护**：CI 扫描**全部 git 历史**，`.env` 一旦提交过就报红（镜像层与历史都不可变）
- **元测试**：`tests/test_engineering_config.py` 守护配置文件自身不腐烂
  （Makefile 引用的 demo 存在、demo 的 `sys.path` 指向仓库根、源码不出现 CRLF）
- **容器**：非 root 运行，checkpoint 与缓存挂卷，健康检查就绪探针

---

## 已知局限

如实列出，避免面试或 review 时被当成疏漏：

1. **LangGraph 适配层未用真库验证**：`core/agent_langgraph.py` 是自实现的等价状态机，
   API 形状对齐 LangGraph，但从未安装过真实 langgraph 库跑通。主力引擎是 `core/agent.py`。
2. **Dockerfile 未在本机构建过**：无 Docker 环境，逻辑经过审阅但首次构建可能需要调整。
3. **内置语料是自造的**：为让仓库开箱可跑而编写，不是真实业务数据。
4. **`agent_langgraph` 与记忆层的向量检索路径**未接真实嵌入模型（用桩验证契约）。
5. 真机 demo 需要 DeepSeek API Key，CI 不覆盖真实模型行为。

---

## 与姊妹仓库的关系

本仓库专注 **Agent 编排与管控**；检索侧的混合检索、父子块索引、精排与
消融评测在 [domain-rag-qa](https://github.com/cangman24/domain-rag-qa)。
两者共享同一套 `core/` 设计哲学（零依赖可测、逻辑与框架解耦），但**互不依赖**，
可独立运行与阅读。

## License

MIT
