# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**nano-strix** — LLM-driven cybersecurity penetration testing agent. Built on nanobot (root-agent) and strix (specialized agents for per-file deep analysis, cross-file vulnerability analysis, and exploitation verification).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Common Commands

```bash
# Run the CLI
.venv/bin/nano-strix --help
.venv/bin/nano-strix hello

# Run tests
.venv/bin/pytest -v

# Run a single test
.venv/bin/pytest tests/test_cli.py::test_hello -v

# Lint
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/

# Auto-fix lint issues
.venv/bin/ruff check --fix src/ tests/
```

## Architecture

- `src/nano_strix/` — main package (src layout)
  - `cli.py` — Click command group and subcommands
- `tests/` — pytest tests using Click's `CliRunner`
- Entry point: `nano_strix.cli:main` (registered as `nano-strix` console script)



# Claude Code 配置：superpowers + gstack

主干由两个插件组成：
- superpowers —— 思考与流程层（plan/brainstorm/debug/TDD/review/verify）
- gstack —— 执行与外部世界层（browser/QA/ship/deploy/canary/护栏）

类比：superpowers 是大脑，gstack 是手脚。

## 核心原则

1. 流程归 superpowers：所有 plan、brainstorm、debug、TDD、verify、
   code review 默认走 superpowers。
2. 执行归 gstack：所有浏览器操作、QA 测试、ship、deploy、canary、
   retro 走 gstack。
3. 独立 reviewer 通道：作者和审查者绝不在同一上下文里互评。
4. 证据优先：声明完成前必须收集可验证的证据。
5. 遇到歧义先 brainstorm。

## 浏览器规则

/browse 是唯一的浏览器入口。禁止使用 mcp__claude-in-chrome__*
和 mcp__computer-use__* 来操作浏览器。

## 不要重复造轮子

下列能力只走 superpowers：
- plan / brainstorm / writing-plans / executing-plans
- TDD / debugging / verification
- code review（请求和接收）
- subagent / parallel dispatch
- worktrees

下列能力只走 gstack：
- 浏览器、QA、ship、deploy、canary、retro、护栏