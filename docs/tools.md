# 工具系统 (Tools)

## 概述

工具系统为 agent 提供与目标环境交互的能力。采用函数式工具 + `@register_tool` 装饰器模式，参考 strix 项目设计。

## 架构

```
tools/
```
__init__.py            # 包导出，触发装饰器注册
registry.py            # 工具注册中心
argument_parser.py     # 参数类型自动转换
executor.py            # 工具执行引擎
context.py             # Agent 上下文 (ContextVar)
terminal/              # Shell 命令执行工具
file_ops/              # 文件系统操作工具
scanner/               # 安全扫描工具
```

## 核心组件

### Registry (`registry.py`)

工具注册中心，使用装饰器模式自动注册工具函数。

```python
from nano_strix.tools.registry import register_tool, get_tool_by_name, get_tools_prompt

@register_tool
def my_tool(param: str) -> dict[str, Any]:
    return {"result": param}
```

#### 主要函数

| 函数 | 说明 |
|------|------|
| `register_tool(func)` | 装饰器，注册工具函数 |
| `get_tool_by_name(name)` | 按名称查找工具 |
| `get_tool_names()` | 获取所有已注册工具名 |
| `get_tool_param_schema(name)` | 获取工具参数 schema |
| `get_tools_prompt()` | 生成 LLM 可用的工具描述 XML |
| `clear_registry()` | 清空注册表（测试用） |

#### Schema 加载

工具参数 schema 从同目录下的 `{module}_schema.xml` 文件自动加载。

XML 格式示例：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<tools>
  <tool name="terminal_execute">
    <description>Execute a shell command.</description>
    <parameters>
      <parameter name="command" type="string" required="true">
        <description>The shell command to execute.</description>
      </parameter>
    </parameters>
  </tool>
</tools>
```

### Argument Parser (`argument_parser.py`)

自动将 LLM 返回的字符串参数转换为函数签名中的类型注解。

支持的类型：`int`, `float`, `bool`, `str`, `list`, `dict`, `Optional`

```python
from nano_strix.tools.argument_parser import convert_arguments

def my_func(count: int, flag: bool) -> None: ...

result = convert_arguments(my_func, {"count": "42", "flag": "true"})
# result = {"count": 42, "flag": True}
```

### Executor (`executor.py`)

工具执行引擎，提供验证和结果格式化。

```python
from nano_strix.tools.executor import execute_tool, execute_tool_with_validation

# 直接执行
result = await execute_tool("terminal_execute", command="echo hello")

# 带验证执行（检查工具存在、必需参数）
result = await execute_tool_with_validation("terminal_execute", {"command": "echo hello"})
```

#### 主要函数

| 函数 | 说明 |
|------|------|
| `execute_tool(name, **kwargs)` | 执行工具，自动转换参数类型 |
| `execute_tool_with_validation(name, kwargs)` | 验证后执行，返回错误 dict 而非抛异常 |
| `validate_tool_availability(name)` | 检查工具是否已注册 |
| `validate_tool_arguments(name, kwargs)` | 检查必需参数 |
| `format_tool_result(name, result)` | 格式化结果，超 10000 字符自动截断 |

### Context (`context.py`)

使用 `contextvars.ContextVar` 传递 agent 上下文，线程/协程安全。

```python
from nano_strix.tools.context import get_current_agent_id, set_current_agent_id

set_current_agent_id("agent-001")
print(get_current_agent_id())  # "agent-001"
```

## 工具清单

### Terminal 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `terminal_execute` | `command: str`, `timeout: int = 30` | 执行 shell 命令，返回 stdout/stderr/exit_code |

### File Ops 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `file_read` | `path: str`, `max_lines: int = 1000` | 读取文件内容 |
| `file_write` | `path: str`, `content: str` | 写入文件 |
| `directory_list` | `path: str`, `recursive: bool = False` | 列出目录内容 |
| `file_search` | `path: str`, `pattern: str` | Glob 搜索文件 |

### Scanner 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `nmap_scan` | `target: str`, `ports: str = ""`, `flags: str = ""` | 网络扫描 |
| `nikto_scan` | `target: str`, `flags: str = ""` | Web 漏洞扫描 |
| `sqlmap_scan` | `target: str`, `flags: str = ""` | SQL 注入扫描 |

## 工具返回格式

所有工具返回 `dict[str, Any]`，错误时包含 `"error"` 键：

```python
# 成功
{"exit_code": 0, "stdout": "hello", "stderr": "", "command": "echo hello"}

# 失败
{"error": "Command timed out after 30s", "command": "sleep 60"}
```

## LLM 集成

工具系统与 LLM 层的集成流程：

1. `get_tools_prompt()` 生成工具描述 XML，作为 system prompt 的一部分
2. LLM 返回 `ToolCall` 对象（已在 `llm/adapter.py` 中定义）
3. `execute_tool_with_validation()` 执行工具调用
4. `format_tool_result()` 格式化结果，追加到对话历史
5. 重复直到 LLM 返回 `finish_reason: "stop"`

## 文件清单

| 文件 | 说明 |
|------|------|
| `src/nano_strix/tools/__init__.py` | 包导出 |
| `src/nano_strix/tools/registry.py` | 工具注册中心 |
| `src/nano_strix/tools/argument_parser.py` | 参数类型转换 |
| `src/nano_strix/tools/executor.py` | 工具执行引擎 |
| `src/nano_strix/tools/context.py` | Agent 上下文 |
| `src/nano_strix/tools/terminal/terminal_actions.py` | terminal_execute |
| `src/nano_strix/tools/terminal/terminal_schema.xml` | Terminal schema |
| `src/nano_strix/tools/file_ops/file_ops_actions.py` | 4 个文件操作工具 |
| `src/nano_strix/tools/file_ops/file_ops_schema.xml` | File ops schema |
| `src/nano_strix/tools/scanner/scanner_actions.py` | 3 个扫描工具 |
| `src/nano_strix/tools/scanner/scanner_schema.xml` | Scanner schema |
| `tests/test_tools_registry.py` | Registry 测试 (7 个) |
| `tests/test_tools_argument_parser.py` | Argument parser 测试 (10 个) |
| `tests/test_tools_executor.py` | Executor 测试 (9 个) |
| `tests/test_tools_terminal.py` | Terminal 测试 (4 个) |
| `tests/test_tools_file_ops.py` | File ops 测试 (9 个) |
| `tests/test_tools_scanner.py` | Scanner 测试 (6 个) |
