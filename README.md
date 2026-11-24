# KB Agent Platform

![CI](https://github.com/cangman24/kb-agent-platform/actions/workflows/ci.yml/badge.svg) ![python](https://img.shields.io/badge/python-%3E%3D3.10-blue) ![deps](https://img.shields.io/badge/test%20deps-0-brightgreen) ![license](https://img.shields.io/badge/license-MIT-blue)

一个**可控**的知识库 Agent：给大模型接上工具之后，把它关进一套工程约束里 ——
路由白名单决定"能调什么"，熔断决定"最多跑几轮"，Checkpoint 决定"中断后怎么续"，
事件流决定"每一步都看得见"。

核心主张：**Agent 的价值不在"能不能跑通"，而在"失控时会不会出事"**。
所以这个仓库的重点不是提示词，而是编排与管控 —— 而且这些逻辑全部
**零第三方依赖、离线可验证**。

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

在本仓库根目录执行。使用 Python 3.10 或 3.11，并给本仓库单独建一个 `.venv`，以隔离两个项目同名的 `core` 包。 Debian/Ubuntu 如缺少 `venv` 组件，可安装与 Python 版本匹配的 `python3-venv` 包。只做离线验收时，也可以在创建虚拟环境的命令中加上 `--without-pip`。

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
.\.venv\Scripts\python.exe -X utf8 demos/demo_agent_trace.py
```

```bash
# Linux
python3 -m venv .venv
.venv/bin/python -X utf8 -m unittest discover -s tests
.venv/bin/python -X utf8 demos/demo_agent_trace.py
```

主演示使用标准库和脚本化决策。运行后查看 `tool_timeout`、续跑前后的工具次数，以及轨迹文件和耗时归因。验收记录见 [TASKS.md](TASKS.md)。

### 用自己的问题试跑并回放

激活本仓库的 `.venv` 后执行。完成后的续跑会返回检查点中的答案。

```powershell
python -X utf8 -m kb_agent.cli --offline --stream --thread-id my-agent-demo "查一下工单复核流程"
python -X utf8 -m kb_agent.cli --offline --trace my-agent-demo
python -X utf8 -m kb_agent.cli --offline --resume my-agent-demo
```

### 浏览器轨迹面板（可选）

将本仓库与 `domain-rag-qa` 放在同一目录，分别在各自的 `.venv` 执行 `python -m pip install -e ".[serve]"`。然后在本仓库运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/start_local_demo.py
```

打开 [本机面板](http://127.0.0.1:18080/)。在「问答」输入“端侧推理时延要求是多少？”，点开引用查看原段落。在「Agent 轨迹」输入“查一下工单复核流程”，观察工具节点，然后用页内下拉框回放。按 Ctrl+C 关闭本次启动的服务。仓库不在同一目录时，用 `--rag-root` 指定 RAG 的路径。

Agent 离线使用规则决策。需要节点级实时推送时，可在 Agent 虚拟环境安装 `langgraph==0.2.28`。单独体验 Agent 浏览器后端时，可分别运行 `python -m uvicorn kb_agent.agent_api:app_factory --factory --port 8100` 和 `python -X utf8 tools/serve_ui.py`。

### 两个项目都使用真实模型

在两个项目各自的虚拟环境安装 `openai>=1.51,<2` 和 `python-dotenv`，然后在各自的 `.env` 配置 Key、Base URL 和模型名。运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/start_local_demo.py --llm
```

打开 [本机面板](http://127.0.0.1:18080/)。问答页由真实模型依据 RAG 演示文档作答，Agent 页使用内置业务知识库和真实模型选择工具。已安装 LangGraph 时使用图引擎，否则使用自实现引擎。独立启动真实模型 Agent 轨迹接口可运行 `python -m uvicorn kb_agent.agent_api:model_app_factory --factory --port 8100`。

如本机代理阻断模型接口，可在各自的 `.env` 设置 `NO_PROXY=127.0.0.1,localhost,模型服务域名`。修改配置后重启服务生效。

### 执行中止与回放

命令行与业务问答服务使用自实现引擎。`/agent/*` 轨迹接口优先使用 LangGraph，未安装时回退到自实现引擎。

- 默认任务预算为 120 秒，涵盖意图识别、规划、模型决策、工具执行和答案校验。单工具默认上限为 30 秒，执行使用任务剩余预算、引擎工具上限和该工具的 `ToolSpec.timeout_s` 三者中的最短值。
- 任务或工具超时时，引擎停止等待，分别记录 `total_timeout` 或 `tool_timeout`，关闭本次运行并保存检查点。同步工具函数可能仍在后台执行，已经产生的副作用也会保留。超时后本次运行和同一检查点的续跑都不会启动新工具。
- 连续三次调用同一工具且 JSON 参数相同，会记录 `loop_detected` 并在下一轮决策前停止。参数变化允许正常的多步调用。

### 接真模型和 HTTP 服务（可选）

```bash
source .venv/bin/activate                 # Windows PowerShell 可用 .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[llm,serve]"
cp .env.example .env                      # Windows PowerShell 同样支持 cp，填写 DEEPSEEK_API_KEY
python -m kb_agent.cli "复核岗的时效要求是什么"
python -m kb_agent.server                 # http://127.0.0.1:8000/docs
```

---

## 架构

```
core/                      零第三方依赖的业务内核（16 个模块，可选依赖全部懒导入）
├── agent.py               受控 Agent 引擎：熔断、循环检测、Checkpoint
├── mcp.py                 工具注册与 schema 生成（MCP 风格）
├── doc_vision.py          文档版式分析：版面/表格重建/图表读数（纯算法，零依赖）
├── doc_vision_tools.py    文档理解工具层：doc_analyze / doc_read_chart / doc_read_table
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
tests/                     419 项单测（25 项需可选依赖，无依赖环境自动跳过）
tools/                     make_demo_pdf.py：纯标准库生成带 ground truth 的迷你 PDF（测试 fixture）
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
| MCP 协议工具层 | 工具经 JSON-RPC 2.0 暴露（stdio + HTTP 双传输），客户端零 import 即可动态发现与调用；协议错误与工具失败分层处理 | `tests/test_mcp_server.py`，`demos/demo_mcp.py`（真子进程端到端） |
| 多模态文档理解 | PDF 版面/表格/图表 -> 带 element_id 的结构化事实：表格按跨行对齐重建网格（可行列寻址），柱状图走矢量算子**精确**读数，Agent 答案带引用 | `tests/test_doc_vision_pdf.py`（对照 ground truth），`demos/demo_doc_vision.py`（14 项自检） |

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
检索消融见独立项目 [Domain RAG QA](https://github.com/cangman24/domain-rag-qa)。拆分前的源码与报告见 [原双项目仓库](https://github.com/cangman24/rag-eval-agent-platform)。
两者共享同一套 `core/` 设计哲学（零依赖可测、逻辑与框架解耦），但**互不依赖**，
可独立运行与阅读。

## License

MIT

## Agent 任务集

当前可运行的演示集为 `data/eval/demo_agent_tasks.jsonl`，包含 5 条任务。
原双项目仓库的 150 条遥感与旧工单任务及复现说明已保存在 [历史归档](data/eval/archive/README.md)。
任务集的知识主题、工单编号和期望工具顺序需要与当前业务数据匹配，适配并复核后可指定输入运行真实模型评测。

```powershell
python demos/live/demo_agent_eval_real.py --tasks data/eval/demo_agent_tasks.jsonl
```

## 全量语料的本地检索体验

在 RAG 的模型目录准备好 BGE-M3 和 BGE Reranker 权重，并在其专用 Python 环境安装 CUDA PyTorch、transformers 和 HTTP 服务依赖后，使用全量模式启动。

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/start_local_demo.py --production --local --rag-python "C:\Users\jdy203205\miniconda3\envs\agent_rag\python.exe" --ui-port 20080 --rag-port 20000 --agent-port 20100
```

`--rag-python` 指向本机已装好模型依赖的 Python，其他机器请替换路径。若依赖安装在 RAG 自己的 `.venv`，可省略此参数。启动器最多等待 180 秒供模型加载与首次建库。

打开 [本机页面](http://127.0.0.1:20080/)，确认顶部为 303 个片段。点「＋新对话」后依次测试：

- “病虫害巡检的地面采样距离和抽样复核比例分别是多少？”应在首个出处中看到 2 厘米、20%。
- “NDVI 的计算公式是什么？”应命中 NDVI 文档，展开来源可见公式。
- “明天合肥的天气和最高气温是什么？”应拒答，引用数为 0。
- Agent 轨迹页可用“查一下工单复核流程”测试规则引擎和回放。`python demos/demo_agent_trace.py` 可进一步验证工具超时及未完成任务续跑。

`--production --local` 使用本地 BGE 和规则 Agent，页面显示检索原文片段。`--production` 会启用配置的远程模型生成回答，适用于已同意把检索片段交给该接口的场景。已有服务占用端口时，启动器会提示退出。由自己终端启动的服务可按 Ctrl+C 关闭。
