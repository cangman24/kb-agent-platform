# ---------------------------------------------------------------------------
# Makefile —— 常用命令入口（Windows 用 `make`，或照着里面的命令手动跑）
#
#   make help            列出所有命令
#   make test            跑全部离线单测（345 项，不需要任何第三方包）
#   make demo            跑离线 demo 冒烟
#   make cli             命令行交互（离线模式，无需 API Key）
#   make serve           起 HTTP 服务（需要 fastapi + DEEPSEEK_API_KEY）
#   make clean           清理 __pycache__ 与运行时产物
# ---------------------------------------------------------------------------
PY ?= python

.PHONY: help test demo cli serve clean install install-all

help:  ## 列出所有命令
	@echo ""
	@echo "  KB Agent Platform —— 常用命令"
	@echo ""
	@echo "  make test     跑全部离线单测（345 项，零第三方依赖）"
	@echo "  make demo     跑离线 demo 冒烟"
	@echo "  make cli      命令行交互（离线，无需 API Key）"
	@echo "  make serve    起 HTTP 服务（需 fastapi + API Key）"
	@echo "  make clean    清理运行时产物"
	@echo ""

install:  ## 最小安装（离线可跑）
	$(PY) -m pip install -e .

install-all:  ## 完整安装（真机 + 服务）
	$(PY) -m pip install -e ".[llm,serve,dev]"

test:  ## 跑全部离线单测（零第三方依赖）
	$(PY) -m unittest discover -s tests -v

demo:  ## 逐个跑离线 demo（任一失败即退出）
	@for d in demo_agent demo_agent2 demo_agenteval demo_mcp; do \
	  printf "  %-18s " "$$d"; \
	  if $(PY) "demos/$$d.py" > /dev/null 2>&1; then echo "OK"; else echo "FAIL"; exit 1; fi; \
	done

cli:  ## 命令行交互（离线模式，无需 API Key）
	$(PY) -m kb_agent.cli --offline --tools

serve:  ## 起 HTTP 服务（需要 fastapi 与 DEEPSEEK_API_KEY）
	$(PY) -m kb_agent.server --port 8000

clean:  ## 清理 __pycache__、checkpoint 与缓存
	@rm -rf data/checkpoints data/cache core/__pycache__ kb_agent/__pycache__ \
	        tests/__pycache__ demos/__pycache__ .pytest_cache 2>/dev/null || true
	@find . -name "__pycache__" -type d -not -path "./.git/*" \
	     -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理"
