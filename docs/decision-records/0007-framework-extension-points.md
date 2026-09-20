# ADR 0007：扩展点用 Protocol + 装配注入

- 状态：已接受
- 日期：2026-09-20
- 关联：Phase 3.4（统一接口）、Phase 3.5（装配与配置）；
  实现入口 `src/myagent/models/base.py`、`src/myagent/tools/base.py`、`src/myagent/memory/base.py`、
  `src/myagent/rag/`、`src/myagent/agent/context.py`、`src/myagent/session/base.py`、
  `src/myagent/runtime.py`、`tests/test_contracts.py`

## 背景

Phase 2 的 Loop 已经是「构造注入」，但注入的类型是具体类：`AgentLoop.__init__` 拿的是
`OpenAICompatModel`、`ContextBuilder`、`SessionManager`（`docs/design.md` §1 的依赖表）。
后果有三个，都在 Phase 3 要解决：

1. **换实现要改核心代码**：换一个 embedding 或向量库，就要动 Loop / Runner 的 import 和装配；
2. **核心依赖第三方 SDK**：`AgentRunner` 只要碰一次 `openai` 类型，测试就得造假 SDK；
3. **装配没有归属**：装配写在 `cli.build_agent_loop()`（Phase 2 已知妥协），
   「用假件替换一个组件」在测试里等于把装配逻辑重写一遍。

PLAN 3.4 要求为六个扩展点（`BaseModel` / `BaseTool` / `BaseMemory` / `BaseEmbedder` /
`BaseVectorStore` / `BaseRetriever`）定契约，并把决策记在本文件。

## 决策

1. **跨模块边界一律用 `typing.Protocol` 描述形状**，就近定义在各自模块里，不建汇总的
   `protocols.py`：
   `BaseModel`（`src/myagent/models/base.py:71`）、`BaseTool`（`src/myagent/tools/base.py:152`）、
   `BaseMemory`（`src/myagent/memory/base.py:88`）、`BaseEmbedder`（`src/myagent/rag/embedder.py:15`）、
   `BaseVectorStore`（`src/myagent/rag/vectorstore.py:19`）、`BaseRetriever`
   （`src/myagent/rag/retriever.py:19`）。
   另外两个同样是 Protocol 的 Loop 边界：`ContextManager`（`src/myagent/agent/context.py:166`）
   与 `SessionStore`（`src/myagent/session/base.py:44`）——PLAN 只在 3.4 列了六个扩展点，
   这两个是 3.2/3.3 的产物，但决策相同，一并记在这里。
2. **ABC 不算被禁用，而是降级为「可选的便利实现」**：`Tool(ABC)`
   （`src/myagent/tools/base.py:187`）实现 `BaseTool` 的 schema / 类型纠正 / 校验，
   内置工具继承它省代码；不继承也完全可用（`tests/test_contracts.py:70` 的 `DuckTool`
   是一个不继承任何东西的假工具，registry 与 runner 照常驱动它）。
3. **装配收敛到一处**：`myagent.runtime.build_agent()`（`src/myagent/runtime.py:34`）
   是唯一知道「哪个类实现哪个契约」的地方；核心模块只 import 契约。
4. **契约由测试守住，而不是靠约定**：`tests/test_contracts.py` 用两个手段固定这条边界——
   每个契约一个不继承任何东西的最小实现（`tests/test_contracts.py:55` 起），
   以及 AST 级别的 import 检查（`tests/test_contracts.py:435`）。

## 理由

- **结构类型把「实现方」也解放了**：Phase 5 的 `QdrantVectorStore`、Phase 4 的分层 Memory
  都不需要继承我们的基类，甚至不 import `myagent`；只要形状对，`isinstance`/mypy 都认。
- **依赖方向真的变成单向**：`agent/` 只 import 契约模块，`openai` / `qdrant_client` / `sqlite3`
  只出现在实现文件里——`tests/test_contracts.py:435` 用 AST 遍历 import 语句来证明这一点，
  而不是靠 code review 记得住。
- **替换实现零改动**：换模型只改 `.env`（`tests/test_contracts.py:387` 断言两个不同的
  `Settings` 装出两个 base_url 不同的 `OpenAICompatModel`，其余组件类型不变）；
  加一个工具只调 `registry.register(...)`（`tests/test_contracts.py:322` 断言
  runner/loop 完全不知道新工具的存在）。
- **`isinstance` 分支是被明确否掉的替代品**：一旦核心代码写成
  `if isinstance(x, QdrantVectorStore)`，每加一个实现都要回到核心改代码，
  而且核心必须 import 那个实现——正是 3.2 要拆掉的耦合。
- **就近定义而不是 `protocols.py`**：汇总文件会让每个模块都 import 它，形成新的共享中心；
  就近定义则让「谁定义契约」与「谁拥有接口语义」一致，`BaseRetriever` 的 `score`
  为什么必须在契约里（Phase 8 用 `hit@k`）写在 `src/myagent/rag/retriever.py:1` 的模块注释里。

## 后果

- **`runtime_checkable` 只检查成员是否存在，不检查签名**。`isinstance` 能过、调用时参数错，
  要到 mypy 或运行时才暴露。两个已知的边界都写成了测试：
  `ScriptedModel` 只有 `generate`，因此**不**满足 `BaseModel`（`tests/test_contracts.py:274`）；
  带数据成员的 Protocol 上 `issubclass` 会抛 `TypeError`（`tests/test_contracts.py:266`）。
  结论：**契约的成员一旦增加，所有 fake 都要跟上**，这是有意的摩擦。
- **`Protocol` 不能带运行时状态**：需要默认实现或共享状态时就用 ABC（`Tool`）或组合
  （`SectionedContextManager` 持有 `workspace`），而不是把状态塞进契约。
- **`stream()` / `count_tokens()` 现在没人调用**，但已经在 `BaseModel` 里，
  所以模型实现必须写全（哪怕返回 `None`）。这是 PLAN 2.2 定下的接口，
  Phase 6 直接用；代价是第三方模型适配层要多写两个空实现。
- 未来若真的要支持第三方插件，入口是「一个 `BaseTool` 实现 + 一行 `register`」，
  不是「改核心代码」；自动发现（entry points）不在本阶段（见下表）。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| 抽象基类（ABC）+ 继承 | 实现方被迫继承我们的类，跨包/第三方适配最不划算；且共享父类会把 `agent` 与 `models` 重新绑在一起 |
| 保留具体类型 + `isinstance` 分支 | 新增实现要改核心代码，核心也仍 import 具体实现（违反 3.2 的目标） |
| 一个汇总 `myagent/protocols.py` | 制造新的共享中心，且把「接口语义」与「实现所在模块」拆开，注释与代码容易走散 |
| 只写文档约定，不加测试 | 没有任何机制阻止下一个人 import 具体类；`test_contracts.py` 才是那条不可回退的线 |
| DI 容器（dependency-injector / punq） | 六个组件的装配用 4 行默认值就够了，容器会增加一层需要解释的间接性 |
| 插件自动发现（`importlib.metadata` entry points） | PLAN 2.3/3.4 都明确本阶段显式注册；自动发现会引入加载顺序与命名冲突问题 |
