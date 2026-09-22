# Phase G 工作记录：浏览器 UI + 本地 Gateway

- 日期：2026-09-22
- 状态：已完成
- 关联提交：`feat(gateway): serve the browser UI from a local gateway`、
  `chore(tests): cover the gateway with socket-level tests`、
  `docs(phase-g): document the gateway design, decisions and experiments`
- 关联文档：[`docs/gateway-design.md`](../gateway-design.md)（设计）、
  [`docs/decision-records/0011-local-gateway-and-webui.md`](../decision-records/0011-local-gateway-and-webui.md)（选型与安全边界）、
  [`docs/design.md`](../design.md) §2（调用链）、ADR-0004（密钥）、ADR-0006（依赖策略）

## 1. 阶段目标

给已经能跑的核心 runtime（Loop / Memory / RAG / Context）补一个**浏览器入口**，
让「用起来」不再依赖终端：

| 目标 | 判定方式 |
| --- | --- |
| 浏览器里能和同一个 agent 对话（工具、记忆、RAG、上下文预算一个不少） | 网关发起的 turn 的 `tools_used` 与 CLI 一致（§7.2） |
| 能在页面上看 / 改 API 配置，并写回 `.env` | `POST /api/config` 后 `myagent chat` 读到新值（§7.3、`tests/gateway/test_config.py`） |
| 能列出 / 打开 / 删除会话 | `GET /api/sessions`、`GET|DELETE /api/sessions/<key>`（§7.3） |
| 不引入新的运行依赖、不引入 Node 构建 | `pyproject.toml:22` 仍只有 4 条运行依赖（§5） |
| 一个进程、一条命令 | `myagent web`（`src/myagent/cli.py:464`） |

**边界（本阶段不做）**：流式输出、多用户/认证、多频道、MCP/技能面板、定时任务、
Markdown 渲染、生产部署。范围取舍见 `docs/gateway-design.md` §1 与 ADR-0011。

## 2. 问题定义

前六个阶段把 runtime 做完了，但只有一个入口 `myagent chat`，于是有三件事没有答案：

1. **浏览器能不能驱动同一个 runtime，而不是另起一套**？如果网关自己 new 一个
   `AgentLoop`，记忆、RAG、上下文预算的接线就会分叉成两份，Phase 3～6 的收敛成果作废。
2. **一轮对话的并发模型是什么**？`AgentLoop` 每个会话一把 `asyncio.Lock`
   （`src/myagent/agent/loop.py:320`）保证同会话不交错，而 HTTP 是同步阻塞的
   `http.server`——两者需要一个明确的桥（§4.2）。
3. **没有登录的前提下，暴露多少就不算事故**？配置里有 API Key，工具能读写本地文件，
   所以「默认拒绝」的边界必须写下来并被测试钉住（§6）。

## 3. Baseline（Phase 6 结束时，`docs/records/phase-6-context.md` §2）

| 项 | Phase 6 结束 | Phase G 结束 |
| --- | --- | --- |
| 测试 | 641 项 | 799 项（+158；`tests/gateway/` 5 个文件 + `tests/gateway_helpers.py`） |
| 覆盖率 | 4070 stmts / 978 branches，100% | 4578 stmts / 1096 branches，100% |
| `src/myagent/` | 55 个文件 / 9660 行 | 62 个文件 / 10783 行（+7 个 `.py`，`gateway/` 包） |
| 用户入口 | `myagent chat`（CLI） | `myagent chat` + `myagent web`（HTTP） |
| 运行依赖 | 4 条 | 4 条（未变） |
| 网络暴露面 | 无（模型 API 出站） | 入站 loopback 端口，默认仅本机 |

测量方式同前：`scripts/check.sh`（ruff / mypy strict / pytest + coverage）与
`.venv/bin/python scripts/check_doc_anchors.py`；文件与行数用 `find` / `wc` 统计
（`find src/myagent -name '*.py'`）。

## 4. 方案设计

### 4.1 分层：HTTP 归 HTTP，应用归应用

```text
src/myagent/gateway/
├── server.py    HTTP 路由 / 安全边界 / Content-Length 纪律 / serve() 主循环
├── app.py       与传输无关的应用层：bootstrap / config / sessions / chat
├── config.py    配置掩码、校验、写回 .env
├── runner.py    常驻事件循环线程（ChatRunner）
├── assets.py    静态资源白名单读取
└── errors.py    GatewayError(status, message)
```

`app.py` 里没有 `http.server` 的类型，`server.py` 里没有模型与存储的类型。结果是
**测试可以按层开火**：`tests/gateway/test_app.py` 不开 socket 覆盖全部对话与配置行为，
只有 `tests/gateway/test_server.py` 用真实 loopback socket 覆盖 HTTP 语义（§5）。

### 4.2 `ChatRunner`：为什么必须有一个常驻 loop

这是本阶段**唯一一个不写就不成立**的设计点。`AgentLoop._session_lock()` 缓存的
`asyncio.Lock` 绑定「首次 await 它的 loop」（`src/myagent/agent/loop.py:320`）。
如果每个 HTTP 请求各自 `asyncio.run(...)`，第二个请求拿到的是绑在**已关闭 loop** 上的锁，
直接抛 "bound to a different event loop"。

所以网关自己养一个 loop：一个 daemon 线程跑 `loop.run_forever()`
（`_serve`，`src/myagent/gateway/runner.py:96`），请求通过
`run_coroutine_threadsafe` 提交并阻塞等结果（`submit`，`:61`）。换来的行为正好是锁想要的效果：

| 场景 | 行为 |
| --- | --- |
| 同一会话的两条消息 | 串行（锁生效） |
| 不同会话的两条消息 | 并行（不同锁、不同任务） |
| 一个标签页在等长回答 | 另一个标签页仍能列会话（`ThreadingHTTPServer` 每连接一线程） |

### 4.3 路由表（9 条）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/`、`/index.html` | 主页面（`src/myagent/gateway/assets/index.html`） |
| GET | `/static/<name>` | 静态资源（名字白名单） |
| GET | `/api/bootstrap` | 版本、模型、features、endpoints（`src/myagent/gateway/app.py:91`） |
| GET / POST | `/api/config` | 读（掩码）/ 写（校验后落 `.env`）（`:121`、`:125`） |
| POST | `/api/config/test` | 发一句 `pong` 探活（`:137`） |
| POST | `/api/chat` | 一轮对话（`:207`） |
| GET | `/api/sessions` | 会话列表（`:166`） |
| GET / DELETE | `/api/sessions/<key>` | 读历史 / 清空（`:182`、`:201`） |

路由形状对齐上游（`nanobot/nanobot/webui/ws_http.py:665` 的 bootstrap、`:834` 的 sessions 列表），
但传输从 WS 换成请求/响应——理由见 ADR-0011。

### 4.4 配置写回的路径

```text
POST /api/config {"model": "...", "api_key": "...", ...}
  └── merge_config()    src/myagent/gateway/config.py:86   省略 = 不变，空串 = 清空
        └── LLMSettings(...)  校验在 __post_init__   src/myagent/config/settings.py:490
  └── apply_config()    src/myagent/gateway/config.py:129  先校验、后写
        ├── remember_env()   逐行改写 .env     src/myagent/config/env.py:124
        └── GatewayApp.reload()  重建 agent 并关掉旧 runner   src/myagent/gateway/app.py:156
```

两条不变量：**校验先于写入**（被拒绝的编辑不会留下半更新的 `.env`），
**Key 只以掩码出站**（`mask_api_key`，`src/myagent/gateway/config.py:55`，§7.3 的
`api_key_hint: "sk-…wCr4"`）。

### 4.5 前端：三个渲染函数、一个 fetch 包装

`index.html`（99 行）+ `app.js`（388 行）+ `style.css`（403 行）+ `favicon.svg`（6 行），
无框架、无构建。`api()`（`src/myagent/gateway/assets/app.js:19`）是唯一的网络出口，
负责把 4xx 的 `{"error": ...}` 变成页面顶部的一条提示。

三个刻意的选择：

- **所有模型输出走 `textContent`**（`src/myagent/gateway/assets/app.js:62`）：模型可以返回任意
  字符串，`innerHTML` 会把它变成 DOM；不渲染富文本也就不用引入 sanitizer。
- **`localStorage` 只存会话 key**（`src/myagent/gateway/assets/app.js:187`）：密钥只在 `.env`，
  浏览器不备份任何凭据。
- **工具结果不单独成气泡**（`src/myagent/gateway/assets/app.js:93` 的过滤）：`tool` 角色属于
  它所在的那一轮，工具名进 meta 行（`addBubble`，`:59`）。

## 5. 实现（文件清单）

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| `src/myagent/gateway/__init__.py` | 28 | 包导出（`GatewayApp` / `serve` / `create_server` / `WEB_SESSION_KEY`） |
| `src/myagent/gateway/server.py` | 368 | 路由、`_authorize`（`:233`）、`_read_json`（`:243`）、`write_response`（`:110`）、`serve`（`:277`） |
| `src/myagent/gateway/app.py` | 309 | `GatewayApp`（`:63`）：bootstrap / config / sessions / chat / reload / close |
| `src/myagent/gateway/config.py` | 176 | `mask_api_key`（`:55`）、`config_payload`（`:64`）、`merge_config`（`:86`）、`apply_config`（`:129`） |
| `src/myagent/gateway/runner.py` | 114 | `ChatRunner`（`:37`）：常驻 loop、`turn`（`:77`）、`close`（`:82`） |
| `src/myagent/gateway/assets.py` | 70 | `Assets.path_for`（`:50`）：扁平名白名单，防目录穿越 |
| `src/myagent/gateway/errors.py` | 20 | `GatewayError`（`:14`）：状态码跟着异常走 |
| `src/myagent/gateway/assets/{index.html,app.js,style.css,favicon.svg}` | 99/388/403/6 | 前端三件套 + 图标 |
| `src/myagent/cli.py` | +38 | `myagent web` 子命令（`_web`，`:464`；parser 在 `:108`） |
| `pyproject.toml` | +4 | 为 `gateway/*.py` 的 JSON 边界加 `ANN401` per-file-ignore |
| `tests/gateway/{conftest,test_app,test_assets,test_chat_runner,test_config,test_server}.py` | 47/485/78/122/199/693 | 单元 + socket 级测试 |
| `tests/gateway_helpers.py` | 116 | 共享装配（`tests/fakes.py` 的假件 + 真实 `build_agent()`） |

`tests/gateway/conftest.py` 的 `isolated_llm_env`（`:38`）在每条网关测试前后快照
`LLM_*` 环境变量——因为 `remember_env()` 会同时写 `os.environ` 和 `.env`，
不隔离就会让「保存配置」的测试污染后面的测试（这是实现时才发现的真实问题，§9.3）。

## 6. 实验设置

| 项 | 设置 |
| --- | --- |
| 离线单测 | `scripts/check.sh`；`tests/fakes.py` 的假模型，不打网络 |
| socket 级 | `tests/gateway/test_server.py` 绑 `127.0.0.1:0`（系统分配端口），跑真实 HTTP |
| 真实服务 | `PYTHONPATH=src .venv/bin/python -m myagent web --port 8099 --no-open`，模型 `deepseek-v4.1-flash` |
| 真实样本 | 5 次对话（3 次强制工具：`current_time` / `calculator` / `search_local`）+ 3 条 API 配置路由 |
| 前端 | 一次性无头脚本 `/tmp/ui_smoke.mjs`：Node + DOM stub 跑真 `app.js` 打真服务（未入库，见 §10） |

命令：

```bash
PYTHONPATH=src .venv/bin/python -m myagent web --port 8099 --no-open   # 终端 A
curl -s http://127.0.0.1:8099/api/bootstrap                            # 终端 B
curl -s -X POST http://127.0.0.1:8099/api/chat -H 'Content-Type: application/json' \
     -d '{"message":"必须调用 current_time 工具告诉我现在几点，再用 calculator 算 7*6"}'
SMOKE_SERVER=http://127.0.0.1:8099 node /tmp/ui_smoke.mjs              # 复用同一个服务
```

## 7. 结果

### 7.1 启动与静态资源

```text
$ curl -s http://127.0.0.1:8099/api/bootstrap
{"app": "myagent", "version": "0.1.0", "transport": "http", "session_key": "web:default",
 "model": {"provider": "openai_compat", "name": "deepseek-v4.1-flash",
           "base_url": "https://ws-7lv991lp9ds3yjuw.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
           "configured": true},
 "features": {"chat": true, "config": true, "sessions": true, "memory": true, "rag": true},
 "limits": {"max_message_chars": 8000},
 "endpoints": {"chat": "/api/chat", "config": "/api/config",
               "config_test": "/api/config/test", "sessions": "/api/sessions"}}

$ curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" http://127.0.0.1:8099/
200 text/html; charset=utf-8 3871
```

`features.memory` / `features.rag` 为 `true` 表示 Phase 4 / Phase 5 的组件在这一实例里是接上的；
页面侧栏与模型徽标都读这两个路由（§7.4）。

### 7.2 一轮带工具的对话（网关 = 同一个 AgentLoop）

```text
$ curl -s -X POST .../api/chat -d '{"message":"必须调用 current_time 工具告诉我现在几点，再用 calculator 算 7*6"}'
{
  "session_key": "web:default",
  "turn_id": "d724e280",
  "content": "现在是 2026-09-22 15:06:49（+08:00，Asia/Shanghai）；7*6 = 42。",
  "stop_reason": "completed",
  "tools_used": ["current_time", "calculator"],
  "error": null
}
context: {'budget': 122880, 'used': 2148, 'dropped': 0, 'compacted': False}
```

工具真的执行了（时间戳是真实时钟、`7*6 = 42` 来自 `calculator`），说明网关没有绕开
`AgentLoop` 的工具编排。另一次不强制工具的提问给出了另一种结果——**恰好是「模型没说」而不是
「系统没做」的对照**：

```text
$ curl -s -X POST .../api/chat -d '{"message":"用一句话说明你现在走了哪个 gateway，顺便用 calculator 算 (12+8)*3"}'
  "content": "(12+8)*3 = 60；至于 gateway，我依然查不到——工作区里没有 gateway 相关配置…",
  "tools_used": []
```

同一条 prompt 在先前一次冒烟里确实调了工具（见 §7.3 transcript 的第一轮，
`tools_used: ['calculator', 'search_local']`），这次没有。结论是**工具可用性由模型决定，
不由传输层决定**——网关只负责把 `tools` schema 交给它（`src/myagent/gateway/app.py:207`）。

### 7.3 配置、会话与历史

```text
$ curl -s http://127.0.0.1:8099/api/config
{"provider": "openai_compat", "providers": ["openai_compat"], "model": "deepseek-v4.1-flash",
 "base_url": "https://ws-…/compatible-mode/v1", "resolved_base_url": "https://ws-…/compatible-mode/v1",
 "max_tokens": 4096, "context_window": 128000, "temperature": 0.1,
 "api_key_set": true, "api_key_hint": "sk-…wCr4", "missing": []}

$ curl -s http://127.0.0.1:8099/api/sessions
7 sessions: ['broken', 'broken3', 'cli:default', 'cli:phase3', 'cli:phase6', 'cli:phase6-smoke', 'web:default']

$ curl -s http://127.0.0.1:8099/api/sessions/web%3Adefault
{'key': 'web:default', 'created_at': '2026-09-22T06:59:34+00:00', 'last_archived': 0, 'summary': ''}
  user      |            | 用一句话说明你现在走了哪个 gateway，顺便用 calculator 算 (12+8)*3
  assistant | calculator, search_local | （无正文，仅工具调用）
  tool      |            | (12+8)*3 = 60
  tool      |            | No matches for 'gateway' under /Users/…/kyobot/workspace
  assistant |            | 计算结果：**(12+8)*3 = 60**。……
  user      |            | 必须调用 current_time 工具告诉我现在几点，再用 calculator 算 7*6
  assistant | current_time, calculator | （无正文，仅工具调用）
  tool      |            | 2026-09-22T15:06:49+08:00
  tool      |            | 7*6 = 42
  assistant |            | 现在是 2026-09-22 15:06:49（+08:00，Asia/Shanghai）；7*6 = 42。
```

两点证据：

1. **`web:default` 与 `cli:*` 同源**：会话列表里既有网页会话也有 CLI 会话，全部来自同一个
   `data/sessions/`（`src/myagent/gateway/app.py:166`）——所以 `myagent chat --session web:default`
   能看到网页里聊过什么。
2. **Key 只以掩码出站**：`api_key_hint: "sk-…wCr4"`，响应里没有 `api_key` 字段。

### 7.4 无头 UI 冒烟（真 `app.js` + 真服务）

浏览器自动化在本会话不可用，于是用一个 Node 脚本把 `app.js` 跑在 DOM stub 上，
打的是同一个 `:8099` 服务：

```text
session key : web:default
model badge : deepseek-v4.1-flash
sidebar     : ['broken', 'broken3', 'cli:default', 'cli:phase3', 'cli:phase6',
               'cli:phase6-smoke', 'web:default']
bubbles     : 12
history     : [
  '用一句话说明你现在走了哪个 gateway，顺便用 calculator 算 (12+8)*3',
  '（调用了工具：calculator, search_local）',
  '计算结果：**(12+8)*3 = 60**。……',
  '只回复两个字：收到', '收到',
  '必须调用 current_time 工具告诉我现在几点，再用 calculator 算 7*6',
  '（调用了工具：current_time, calculator）',
  '现在是 2026-09-22 15:06:49（+08:00，Asia/Shanghai）；7*6 = 42。'
]
after send  : 14 bubbles
last two    : [ '只回复两个字：收到', '收到' ]
context badge: 上下文 2488/122880 token | hidden: false
error box   :  | hidden: true
dialog open : true
cfg model   : deepseek-v4.1-flash | provider: openai_compat
cfg key hint: 已配置：sk-…wCr4（留空表示不修改）
cfg test    : 连接成功：deepseek-v4.1-flash 回复 "pong"
```

它覆盖了四条用户路径：**渲染历史**（工具名进 meta 行、`tool` 不出气泡）、
**发消息**（真发真收，气泡从 12 涨到 14）、**上下文徽标**（`2488/122880`，来自 §4.3 的
`context` 字段）、**设置面板**（掩码正确、探活成功）。

被跳过的两件事也说清楚：真实浏览器里的点击与 CSS 布局（DOM stub 没有样式），
以及 `localStorage` 的持久化跨刷新（stub 实现了 `getItem`/`setItem`，但没有真正的存储）。

### 7.5 安全边界的负例（`tests/gateway/test_server.py`）

| 请求 | 期望 | 覆盖 |
| --- | --- | --- |
| `Host: evil.example` | 403 | DNS rebinding（`host_is_loopback`，`src/myagent/gateway/server.py:85`） |
| `Origin: http://evil.example` | 403 | 跨源页面（`origin_is_loopback`，`:104`） |
| `POST` 无 `Content-Length` | 411 | `_content_length`（`:332`） |
| `POST` 体 > 1 MiB | 413 | `MAX_BODY_BYTES`（`:58`） |
| `POST` `Content-Type: text/plain` | 415 | 挡跨站表单（`_read_json`，`:243`） |
| `GET /api/chat`、`POST /api/sessions` | 405 | `_only`（`:326`） |
| `GET /static/../app.py`、`/static/.env` | 404 | 名字白名单（`Assets.path_for`，`src/myagent/gateway/assets.py:50`） |

## 8. 质量门与验收判定

```text
$ scripts/check.sh
== ruff format --check ==   100 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 62 source files
== pytest ==                799 passed（覆盖率 4578 stmts / 1096 branches，100%）

$ .venv/bin/python scripts/check_doc_anchors.py
checked 1280 anchor(s) in 24 document(s)
all anchors resolve
```

| 验收项 | 结果 |
| --- | --- |
| 浏览器里与同一个 agent 对话（工具 / 记忆 / RAG / 预算沿用） | ✅ §7.2（`tools_used: ["current_time", "calculator"]`）、§7.1（`features` 全 true） |
| 查看 / 修改 API 配置并写回 `.env` | ✅ §7.3（掩码）、`tests/gateway/test_config.py`（校验 → `remember_env` → `reload`） |
| 列出 / 打开 / 删除会话 | ✅ §7.3、`tests/gateway/test_app.py` |
| 页面正常渲染并发消息 | ✅ §7.4（无头 `app.js`） |
| 不新增运行依赖 | ✅ `pyproject.toml:22` 仍是 4 条 |
| 默认不可从别的机器访问 | ✅ §7.5（403 两类） |
| `scripts/check.sh` 全绿 | ✅ 799 项、100% 覆盖 |

## 9. 过程中发现并修掉的真实问题

### 9.1 每个请求一个 `asyncio.run()` 会崩在会话锁上

第一版设计里 HTTP 处理函数直接 `asyncio.run(loop.run_turn(...))`。第二条消息立刻报
"bound to a different event loop"：`AgentLoop` 的会话锁（`src/myagent/agent/loop.py:320`）
绑定首次 await 它的 loop，而 `asyncio.run` 每次新建一个 loop 并在结束时关掉它。
这不是「偶尔的竞态」——它**第二条消息必现**。修法就是 §4.2 的 `ChatRunner`：
一个常驻 loop + 线程安全的提交。这个问题的形状（锁绑定 loop）在写代码前读 `loop.py` 时就能
看出来，值得记下来当作「先读原码再动手」的正面例子。

### 9.2 拒绝一个没读完的 body 时必须关连接

`_read_json` 对非 JSON 请求体抛 415，但此时请求体**一个字都没读**（`:243` 在检查
Content-Type 之前不碰 `rfile`）。HTTP/1.1 保活连接下，剩下的字节会被当成下一个请求来解析。
修法是在 `_handle`（`src/myagent/gateway/server.py:178`）里对所有 `GatewayError` 设
`self.close_connection = True`。这正是手写 HTTP 才看得见的一类问题——也是 ADR-0011
记下「不用框架」这条取舍时给的代价。

### 9.3 `remember_env()` 会污染后面的测试

`myagent.config.env.remember_env()` 既写 `.env` 也写 `os.environ`（`src/myagent/config/env.py:124`），
而 `Settings.from_env()` 优先读环境变量。于是「保存配置」的测试会让**之后所有测试**读到那个
假 key 与假 base_url，表现为一串看起来毫不相干的失败。修法是 `tests/gateway/conftest.py:38`
的 autouse fixture：每条网关测试前后快照 / 还原 7 个 `LLM_*` 变量。这和 Phase 2 给
`EMBED_DIM` 加的护栏是同一种问题（探测写回 `.env`）。

### 9.4 同名测试文件会让 pytest 直接报 import 错误

`tests/` 与 `tests/gateway/` 都没有 `__init__.py`，pytest 默认的 `prepend` 导入模式下
两个同名 `test_runner.py` 会撞成 "import file mismatch"。把网关那份改名成
`tests/gateway/test_chat_runner.py`（被测对象的真实名字）而不是加 `__init__.py`——
后者会改变整个测试树的可导入方式（`tests/fakes.py` 的同级导入会跟着变）。

### 9.5 `cli.py` 加了 38 行，33 处文档锚点集体漂移

`myagent web` 让 `src/myagent/cli.py` 从 554 行变 592 行，`_chat`、`_session_compact`、
`_tools` 等行号全部下移。文档锚点校验器（`scripts/check_doc_anchors.py`）立刻报错，
涉及 `docs/rag-design.md`、`docs/design.md`、`docs/context-design.md`、`docs/memory-design.md`、
`docs/records/phase-5-rag.md`、`docs/decision-records/0006-phase2-dependencies.md` 与 `PLAN.md`
共 **33 处**。修法是用 `difflib` 按 diff 精确重映射，而不是按行数差硬加。
教训是：**改 `cli.py` 会连带改多个阶段的文档**，所以锚点重映射必须和代码改动放在同一个提交里，
否则中间任何一个提交的质量门都是红的。

## 10. 结论与遗留问题

结论（证据见 §7～§8）：

1. **浏览器是传输层，不是第二个 runtime**：网关只有一份装配
   （`build_agent()`，`src/myagent/runtime.py:47`）、一份配置（`.env`）、一个会话目录
   （`data/sessions/`），所以网页里的对话在 CLI 里看得见（§7.3）；
2. **`ChatRunner` 是必需的而不是优化**：`AgentLoop` 的会话锁绑定 loop
   （`src/myagent/agent/loop.py:320`），常驻 loop 让同会话串行、跨会话并行，
   并且顺手修掉了「第二条消息必崩」（§9.1）；
3. **9 条路由 + 标准库够用**：不引入框架与 WebSocket 的代价是手写 HTTP 纪律，
   但那份纪律是可测的（§7.5），而收益是运行依赖仍然是 4 条（ADR-0011）；
4. **默认拒绝的安全边界是可验收的**：loopback 绑定 + `Host`/`Origin` 校验 +
   仅 JSON 请求体 + 静态资源白名单 + Key 掩码，每一项都有负例测试（§7.5）；
5. **前端零构建也能覆盖四条核心路径**：真实 `app.js` 在无头环境里渲染历史（工具名进 meta
   行）、发消息（12 → 14 气泡）、显示上下文徽标（2488/122880）、保存/测试配置
   （"连接成功 … pong"）（§7.4）。

遗留问题（明确不做或留给后续）：

- **非流式**：模型层 V1 的 `stream()` 是保留接口（`src/myagent/models/openai_compat.py:110`），
  一轮回答一次性返回，页面用「思考中…」占位。真流式落地后 SSE / WS 只需在 `gateway/` 内加一层；
- **单用户、无认证**：`--allow-remote` 打开的就是「任何能访问该端口的人」，这是明说的限制；
- **前端没有自动化测试**：无头冒烟脚本是一次性的、没入库；要长期维护需要 Vitest / Playwright，
  并先补一条 ADR（`docs/gateway-design.md` §10 已登记）；
- **页面不渲染 Markdown**：`**`、代码块原样显示，取舍是不引入 sanitizer（所有写入走 `textContent`）；
- **memory / rag 开关不在 UI 里**：它们是 `.env` 的 `MYAGENT_MEMORY_ENABLED` /
  `MYAGENT_RAG_ENABLED`，UI 只展示生效状态（`src/myagent/gateway/app.py:91`）——
  原因是 Phase 8 要用这两个开关做对照实验，让浏览器随手一改会让实验口径不稳定；
- **`myagent web` 不做端口占用处理**：端口被占会抛 `OSError`，指定 `--port 0` 可让系统分配。
