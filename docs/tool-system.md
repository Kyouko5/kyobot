# Tool System

> 基线：nanobot v0.3.5 / `2fb165939`，本地只读参照在 `nanobot/`。
> **路径约定**：`agent/tools/base.py:159` 指上游包内的 `nanobot/nanobot/agent/tools/base.py` 第 159 行。
> 代码全部位于 `agent/tools/` 目录下。
> 一句话：**Tool 是「带 JSON Schema 的异步能力」；Registry 是「能力目录 + 调用前的类型/参数校验」；
> Loader 负责「自动发现」；Execution 负责「执行并把结果变成模型能读的观察」。**

## 0. 总览

```text
ToolLoader.discover()          # pkgutil 扫包 + entry_points 插件
        ↓  (list[type[Tool]])
ToolRegistry.register(tool)    # name → instance，同时失效 definitions 缓存
        ↓  get_definitions()
[{type: "function", function: {name, description, parameters}}]   # 直接塞给 provider
        ↓  模型返回 tool_calls
ToolRegistry.prepare_call()    # 取实例 → 参数 cast → JSON Schema 校验
        ↓
Tool.execute(**params)         # 业务实现，返回 str / ToolResult / 其它
        ↓  execute_tool_calls()
tool message {role, tool_call_id, name, content}   # 回到 Runner 的消息流
```

对应文件：

| 层 | 文件 | 行数 |
| --- | --- | --- |
| 契约（抽象基类 + Schema + 结果类型） | `agent/tools/base.py` | 350 |
| 目录（注册 / 查询 / 校验 / 执行入口） | `agent/tools/registry.py` | 212 |
| 发现（内置扫描 + 插件） | `agent/tools/loader.py` | 191 |
| 执行（并发分批 / 错误语义 / 边界护栏） | `agent/tools/execution.py` | 316 |
| JSON Schema 片段实现 | `agent/tools/schema.py` | 235 |
| 调用上下文（request / workspace / 文件状态） | `agent/tools/context.py` | — |

---

## 1. BaseTool：一个工具必须提供什么

```python
# agent/tools/base.py:159（节选）
class Tool(ABC):
    """Agent capability: read files, run commands, etc."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]: ...       # JSON Schema

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Run the tool; return content, or ``ToolResult.error(...)`` for failures."""
```

只有三个属性 + 一个方法。这种「极小接口」是后面我们照搬的关键：**工具作者不需要知道 agent 的存在**。

### 1.1 三个可选属性决定并发与安全

| 属性 | 位置 | 语义 |
| --- | --- | --- |
| `read_only` | `agent/tools/base.py:189` | 无副作用，默认 `False` |
| `concurrency_safe` | `agent/tools/base.py:194` | 默认 `read_only and not exclusive`，即「只读且非独占」才允许并行 |
| `exclusive` | `agent/tools/base.py:199` | 独占工具，永远单独执行 |

这三个属性是 `execution.py` 分批并行的唯一依据（见第 4 节），把「能不能并行」交给工具自己声明，
而不是在调度器里写死工具名列表。

### 1.2 结果类型：`ToolResult` 是 `str` 的子类

```python
# agent/tools/base.py:144
class ToolResult(str):
    """String-compatible tool output with structured status."""
    is_error: bool

    def __new__(cls, content: str, *, is_error: bool = False) -> ToolResult: ...

    @classmethod
    def error(cls, content: str) -> ToolResult:
        return cls(content, is_error=True)
```

为什么用 `str` 子类而不是 dataclass？

1. 工具结果最终要拼进 message 的 `content`，是字符串；
2. 又想区分「失败」与「正常但内容为空」，于是用 `is_error` 这个附加位；
3. `agent/tools/execution.py:15` 的 `is_tool_error_result()` 只在需要时检查 `is_error`，其余代码可以当字符串用。

### 1.3 Schema：手写 dict + 一个装饰器

`Tool.parameters` 直接返回 JSON Schema dict。手写很啰嗦，所以上游提供了类装饰器：

```python
# agent/tools/loader 之外的用法示例（agent/tools/base.py:335 tool_parameters docstring）
@tool_parameters({
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
})
class ReadFileTool(Tool):
    ...
```

装饰器把 schema 冻结在类上，每次访问返回**深拷贝**（避免调用方改坏共享字典），
并把它从 `__abstractmethods__` 里摘掉（`agent/tools/base.py:335-350`）。

Schema 的另一半职责是**调用前的校验与类型纠正**，都在 `Tool` 里：

| 方法 | 位置 | 作用 |
| --- | --- | --- |
| `cast_params` / `_cast_value` | `agent/tools/base.py:251`、`258` | 安全纠偏：`"3"` → `3`、`"true"` → `True`、标量 → `str` 等 |
| `validate_params` | `agent/tools/base.py:297` | 调 `Schema.validate_json_schema_value`，返回错误信息列表（空=合法） |
| `to_schema` | `agent/tools/base.py:306` | 产出 OpenAI function schema |

```python
# agent/tools/base.py:306
def to_schema(self) -> dict[str, Any]:
    """OpenAI function schema."""
    return {
        "type": "function",
        "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        },
    }
```

> 注意 `Schema.validate_json_schema_value`（`agent/tools/base.py:51`）是上游自己实现的一小套 JSON Schema 校验
> （支持 `type` / `enum` / `minimum` / `maxLength` / `required` / `additionalProperties` 等），
> 没有引入 `jsonschema` 依赖。我们要不要照搬，取决于 Phase 2 是否需要更完整的 Schema 支持。

---

## 2. Tool Registry：目录 + 校验网关

```python
# agent/tools/registry.py:19
class ToolRegistry:
    """Registry for agent tools. Allows dynamic registration and execution of tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
```

四个要点：

1. **注册即失效缓存**：`register` / `unregister`（`agent/tools/registry.py:30`、`35`）都会把
   `_cached_definitions` 置空，保证下一次取定义是新的。
2. **定义顺序是稳定前缀**：`get_definitions`（`agent/tools/registry.py:86`）把内置工具按名字排序放在前面，
   `mcp_*` 前缀的工具排序后追加。稳定的顺序对 prompt 缓存（provider 侧 KV cache）有实际收益，
   上游注释写得很直接：*stable ordering for cache-friendly prompts*。
3. **`prepare_call` 是唯一的校验入口**（`agent/tools/registry.py:110`）：取实例 → cast → validate，
   返回 `(tool, params, error)` 三元组。名字找不到时用 `_suggest_name`（`agent/tools/registry.py:58`）
   做「去符号 + 大小写无关」的模糊匹配，把 *Did you mean 'xxx'?* 直接回给模型。
4. **执行入口保留**：`execute`（`agent/tools/registry.py:187`）在没有拿到实例时兜底（测试/动态场景）。

`prepare_call` 的返回契约值得单独记：**它不抛异常，而是把错误作为第三个返回值**，
这样上层（execution）可以把错误统一转成「给模型的提示」而不是「崩掉整个 turn」。

---

## 3. Tool Discovery：内置自动扫描 + 插件

```python
# agent/tools/loader.py:26
class ToolLoader:
    def discover(self) -> list[type[Tool]]:
        ...
        for _importer, module_name, _ispkg in pkgutil.iter_modules(self._package.__path__):
            if module_name.startswith("_") or module_name in _SKIP_MODULES:
                continue
            module = importlib.import_module(f".{module_name}", self._package.__name__)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, Tool)
                        and attr is not Tool
                        and not getattr(attr, "__abstractmethods__", None)
                        and getattr(attr, "_plugin_discoverable", True)):
                    results.append(attr)
        results.sort(key=lambda cls: cls.__name__)
```

规则很明确（`agent/tools/loader.py:34-65`）：

- 扫 `agent/tools/` 下的模块，跳过 `_SKIP_MODULES`（`base`/`schema`/`registry`/`loader`… 那些不是工具）；
- 只认「`Tool` 的具体子类」，抽象类与 `_` 开头的不算；
- `_plugin_discoverable = False` 可以主动退出自动注册（`agent/tools/base.py:207`）；
- 结果按类名排序，保证发现顺序稳定；
- 外部插件通过 `entry_points(group="nanobot.tools")`（`agent/tools/loader.py:68`）注入，失败只记日志不影响启动。

`_SKIP_MODULES` + `__abstractmethods__` 两个过滤条件也解释了为什么 `@tool_parameters` 装饰器必须
把 `parameters` 从 `__abstractmethods__` 中移除：否则这个工具类会被判定为「仍是抽象类」而漏注册。

---

## 4. Tool Execution：执行、并发与错误语义

```python
# agent/tools/execution.py:56
async def execute_tool_calls(
    tools: ToolRegistry, tool_calls: list[ToolCallRequest], *,
    concurrent: bool, external_lookup_counts, workspace_violation_counts,
    hook, context, model_messages=None, compacted_tool_results=None,
) -> tuple[list[Any], list[dict[str, str]]]:
    """Execute one model response's tool calls in stable result order."""
```

### 4.1 并发分批

```python
# agent/tools/execution.py:292
def _partition_tool_batches(tools, tool_calls, *, concurrent: bool) -> list[list[ToolCallRequest]]:
    if not concurrent:
        return [[tool_call] for tool_call in tool_calls]
    # 连续的 concurrency_safe 工具合成一批（gather），其它工具单独成批（串行）
```

结果按**原始调用顺序**收集（`tool_results` 按 batch 顺序 extend），所以模型看到的 tool 消息顺序
始终与它发起的调用顺序一致——并行只影响耗时，不影响语义。

### 4.2 一次调用的四条失败路径

`_execute_tool_call`（`agent/tools/execution.py:114`）把所有失败都变成「一条可读的 tool 结果 + 一个事件」：

| 失败类型 | 触发点 | 返回给模型的内容 |
| --- | --- | --- |
| 重复外部查询 | `agent/tools/execution.py:123` | `repeated_external_lookup_error` 文案（拦截） |
| 工具名/参数校验失败 | `agent/tools/execution.py:147` `prepare_call` 的 `prep_error` | 错误信息 + `[Analyze the error above and try a different approach.]` |
| 工具抛异常 | `agent/tools/execution.py:177` `except Exception` | `Error: <Type>: <msg>` + 重试提示 |
| 工具主动报错 | `agent/tools/execution.py:196` `ToolResult.error(...)` | 原内容 + 重试提示 |

```python
# agent/tools/execution.py:14
_RETRY_HINT = "\n\n[Analyze the error above and try a different approach.]"
```

**Tool Error 的哲学**：工具失败不是系统的失败，而是模型需要观察到的新信息。
所以执行层只做两件事——保证信息可读、保证不会无限重试。

### 4.3 两类安全边界

`_classify_violation`（`agent/tools/execution.py:244`）把两种「不是普通 bug」的错误单独处理：

- **SSRF**（`is_ssrf_violation`，`agent/tools/execution.py:226`）：访问内网/私有地址被拦。
  返回一段明确的边界说明，告诉模型不要用 curl / 编码 IP / 换 DNS 等方式绕过（`agent/tools/execution.py:30`）。
- **Workspace 越界**（`_is_workspace_violation`，`agent/tools/execution.py:234`）：路径逃出工作区。
  首次给软提示，重复触发则升级为 `repeated_workspace_violation_error`（`agent/tools/execution.py:262`）。

这两类的共同点是：**必须是不可绕过的边界，但 conversational recovery 让 agent 能继续为用户服务**，
而不是直接终止这一轮。

### 4.4 读文件去重

`read_results()`（`agent/tools/execution.py:70`，`functools.cache` 只在批次内生效）会把已经出现在
`model_messages` 里的 tool 结果按 `tool_call_id` 建索引，配合 `file_read_context`（`agent/tools/execution.py:168`）
让 `read_file` 在模型重复读同一文件时直接复用已有结果——这是很典型的「省 token 又不改变语义」的优化。

---

## 5. 迁移到 myagent

| 上游机制 | 迁移计划 | 理由 |
| --- | --- | --- |
| `Tool` ABC（name/description/parameters/execute） | **保留** | 接口最小、易测试，是 Phase 2 的目标形态 |
| `ToolResult(str)` 带 `is_error` | 保留（可改为 dataclass） | 需要区分失败与空结果；若改用 dataclass，要额外处理序列化 |
| `@tool_parameters` 装饰器 | 保留 | 避免每个工具重复写 `parameters` 属性样板 |
| `Schema.validate_json_schema_value` 自研校验 | **先简化，Phase 2 评估** | 自研校验覆盖面有限；若引入 `jsonschema` 需在 ADR 里说明（当前 runtime 零依赖原则） |
| `ToolRegistry` + `prepare_call` | **保留**（含稳定排序与缓存失效） | 名字纠错、参数校验、稳定 prompt 前缀都直接受益 |
| `ToolLoader` 自动发现 | 简化：显式注册 + 可选扫描 | V1 工具数量少，「显式注册」可读性更好；扫描留到需要插件时再加 |
| `entry_points` 插件机制 | Phase 9 再评估 | 属于扩展性机制，不是核心链路 |
| `execute_tool_calls` 异步分批 | 保留（含顺序保证） | 并行只读工具是明确收益 |
| SSRF / workspace 护栏 + 重复调用节流 | 保留思想 | 垂直 Agent 会有真实网络检索，护栏必要 |
| 读文件去重 | 保留思想 | 省 token 的直接手段，Phase 8 度量收益 |

## 6. 速查索引

| 主题 | 位置 |
| --- | --- |
| `Schema` 抽象与校验 | `agent/tools/base.py:30`、`51` |
| `ToolResult` | `agent/tools/base.py:144` |
| `Tool` 抽象基类 | `agent/tools/base.py:159` |
| 并发安全属性 | `agent/tools/base.py:189-199` |
| 参数纠正 / 校验 / schema 输出 | `agent/tools/base.py:251`、`297`、`306` |
| `tool_parameters` 装饰器 | `agent/tools/base.py:335` |
| `ToolRegistry` | `agent/tools/registry.py:19` |
| 定义排序与缓存 | `agent/tools/registry.py:86` |
| `prepare_call` 校验网关 | `agent/tools/registry.py:110` |
| 自动发现 | `agent/tools/loader.py:34` |
| 插件发现 | `agent/tools/loader.py:68` |
| 执行入口与分批 | `agent/tools/execution.py:56`、`292` |
| 错误语义 | `agent/tools/execution.py:14`、`114` |
| 安全边界 | `agent/tools/execution.py:226`、`234`、`244` |
