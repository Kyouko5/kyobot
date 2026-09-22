# ADR 0011：本地 Gateway 用标准库 HTTP，前端用零构建三件套

- 状态：已接受
- 日期：2026-09-22
- 关联：Phase G（浏览器 UI）；
  设计说明 [`docs/gateway-design.md`](../gateway-design.md)；
  工作记录 [`docs/records/phase-g-gateway-webui.md`](../records/phase-g-gateway-webui.md)；
  实现入口 `GatewayApp`（`src/myagent/gateway/app.py:63`）、
  `GatewayRequestHandler._route`（`src/myagent/gateway/server.py:193`）、
  `ChatRunner`（`src/myagent/gateway/runner.py:37`）、
  `myagent web`（`src/myagent/cli.py:464`）；
  依赖策略 ADR-0006、密钥策略 ADR-0004

## 背景

项目到 Phase 6 为止只有一个入口：`myagent chat`。它把「浏览器可用」这件事留到了
PLAN §2.2 的「暂不重点实现」（连同「复杂 Web UI」一起）。现在要补一个**核心能力的浏览器
入口**——聊天与 API 配置——并且要回答三个只能在此刻决定的问题：

1. **传输层用什么**：框架（FastAPI / aiohttp）还是标准库？
2. **前端怎么交付**：Node 构建产物还是直接 serve 源文件？
3. **没有登录的前提下，安全边界画在哪**？

约束来自现状而非偏好：V1 的模型层没有流式（`BaseModel.stream()` 是保留接口，
`src/myagent/models/base.py:88`；兼容实现 `src/myagent/models/openai_compat.py:110` 只 `yield`
最终结果），网关规模是 9 条路由、一个进程、单用户，而 `pyproject.toml` 的依赖策略
（ADR-0006）要求**每一个新的运行依赖都要有 ADR**。

## 决策

1. **传输层用标准库 `http.server`**：`GatewayRequestHandler`（`src/myagent/gateway/server.py:150`）
   继承 `BaseHTTPRequestHandler`，`GatewayServer`（`:126`）用 `ThreadingHTTPServer` 给每条连接
   一个线程。不引入 FastAPI / aiohttp / uvicorn / websockets。运行时依赖保持 4 条
   （`pyproject.toml:22`）。
2. **不用 WebSocket，用请求/响应**：`POST /api/chat` 一轮一次返回
   （`src/myagent/gateway/app.py:207`）。等模型层真正流式之后，SSE 或 WS 只需要在
   `gateway/` 内加一层，不需要动 `app.py` 的语义。
3. **前端是零构建的四个文件**（`index.html` / `app.js` / `style.css` / `favicon.svg`，
   `src/myagent/gateway/assets/`），由网关直接 serve；不引 Node、不引打包器。
4. **默认只绑 loopback 并校验 `Host` / `Origin`**：`DEFAULT_HOST = "127.0.0.1"`
   （`src/myagent/gateway/server.py:55`），`_authorize()`（`:233`）用
   `host_is_loopback()`（`:85`）挡 DNS rebinding、用 `origin_is_loopback()`（`:104`）拒绝跨源页面；
   不返回任何 CORS 头。
5. **写接口只接受 `application/json`**：`_read_json()`（`src/myagent/gateway/server.py:243`）
   对其它 Content-Type 抛 415，这同时也是挡跨站表单 POST 的闸门；请求体上限
   `MAX_BODY_BYTES = 1 MiB`（`:58`），`Content-Length` 缺失即 411（`_content_length`，`:332`）。
6. **静态资源按“扁平文件名白名单”读**：`Assets.path_for()`（`src/myagent/gateway/assets.py:50`）
   拒绝分隔符、`.` 前缀与 `..`，不做路径拼接。
7. **API 配置只回掩码**：`config_payload()`（`src/myagent/gateway/config.py:64`）返回
   `api_key_hint`（`mask_api_key`，`:55` 输出 `sk-…wCr4` 形式），Key 本身永不离开进程；
   写回走 `apply_config()`（`:129`）→ `remember_env()`（`src/myagent/config/env.py:124`），
   **先校验后写**，`.env` 仍是唯一凭据来源（ADR-0004）。
8. **一个进程一个事件循环**：`ChatRunner`（`src/myagent/gateway/runner.py:37`）起一个常驻线程跑
   `loop.run_forever()`，所有 turn 用 `run_coroutine_threadsafe` 提交（`submit`，`:61`）。
9. **`--allow-remote` 是唯一解除 loopback 检查的开关**：它同时意味着「任何能访问该端口的人
   都能用这个 agent」，启动时打印警告（`src/myagent/gateway/server.py:308`）。

## 理由

- **不用框架，因为收益小于成本**：这个网关没有模板、没有 ORM、没有异步依赖注入——它只是
  「把 JSON 映射到 `GatewayApp` 的方法」。`http.server` 的 `ThreadingHTTPServer` 已经给出
  需要的并发模型：请求在 `ChatRunner` 上阻塞，慢 turn 不能拖住另一个标签页列会话
  （`GatewayServer` 的注释，`src/myagent/gateway/server.py:126`）。引入 uvicorn 还要处理
  它自己的 loop 与 `ChatRunner` 的 loop 的关系，反而多一层。
- **框架会藏住 `Content-Length` 纪律**：手写 `write_response()`（`src/myagent/gateway/server.py:110`）
  明确写出精确长度、`no-store`、`nosniff`、`no-referrer`。HTTP/1.1 保活连接下，「拒绝一个
  没读完 body 的请求」必须先关闭连接，否则残留字节会被当成下一个请求解析——这条逻辑写在
  `_handle()`（`:178`）的 `close_connection = True`，框架不会替我们表达。
- **没有流式就不需要 WebSocket**：WS 的价值是把多段事件按时间推给页面；V1 一轮只有一次
  结果，WS 只是把一次结果推一次，等于用长连接的复杂度换零收益。上游用 WS 是因为它有
  多频道、工具进度、定时任务等真正的推送源（`nanobot/nanobot/webui/ws_http.py:665`）。
- **零构建前端让「核心功能」真的可交付**：四个人工可读的文件意味着改一个按钮不需要
  `npm install`；也意味着**这套前端不需要 Node 才能被别人跑起来**。代价是没有组件化与
  类型检查（见「后果」）。
- **loopback + `Host`/`Origin` 校验是无认证下最强的默认**：恶意页面可以让自己的域名解析到
  `127.0.0.1`（DNS rebinding），但浏览器的 `Host` 头仍是那个域名，`host_is_loopback()`
  就拦得住；非本机页面发起的跨源请求带 `Origin`，`origin_is_loopback()` 拦得住；拒绝非
  `application/json` 又堵住了不需要预检的跨站表单。三层都是「默认拒绝」，不需要用户配置。
- **掩码而不是隐藏字段**：配置面板要让人确认「key 还在、就是那个」，`sk-…wCr4`
  足够识别又不可用（`src/myagent/gateway/config.py:55`）；短于 12 字符的 key 整体掩成 `…`，
  因为前后缀加起来可能已是全部。
- **单循环是 `AgentLoop` 的锁逼出来的**：`AgentLoop` 每个会话一把 `asyncio.Lock`
  （`src/myagent/agent/loop.py:320`），保证同一会话的两轮不交错；而 `asyncio.Lock` 绑定首次
  `await` 它的 loop。如果每个 HTTP 请求各自 `asyncio.run()`，第二个请求会拿到一个绑在
  **已关闭 loop** 上的锁而报错。一个常驻 loop 既修掉这个 bug，又顺带兑现了锁想要的效果：
  同会话串行、跨会话并行（`src/myagent/gateway/runner.py:1`）。
- **先校验后写 `.env`**：`apply_config()`（`src/myagent/gateway/config.py:129`）先构造
  `LLMSettings`（校验在 `src/myagent/config/settings.py:490` 的 `__post_init__`）再调用
  `remember_env()`。被拒绝的编辑不会留下半更新的 `.env` 或 `os.environ`。

## 后果

- **手写的部分要自己测**：`tests/gateway/test_server.py` 用真实 loopback socket 覆盖
  405 / 411 / 413 / 415 / 403 与保活行为；`app.py` 里没有 `http.server` 类型，所以
  `tests/gateway/test_app.py` 不开 socket 就能覆盖对话与配置的全部路径
  （设计说明 §3）。
- **无认证是明说的限制**：单用户本机工具，端口一旦被转发或被 `--allow-remote` 打开，
  就等于把 agent（含它的工具与文件访问）交给对方。这是接受的风险，不是漏掉的实现。
- **前端没有自动化测试**：JS 只有一次性的无头冒烟（阶段记录 §7.4）。要长期维护就得引入
  Vitest / Playwright，那需要先记一条 ADR——本 ADR 只覆盖「现在怎么交付」。
- **页面不渲染 Markdown**：模型输出的 `**`、代码块会原样显示。选择是不引入 sanitizer，
  因为所有写入 DOM 的路径都走 `textContent`（`src/myagent/gateway/assets/app.js:62`）。
- **`GET /api/config` 依赖 `.env` 可写**：只读文件系统上「保存」会失败并返回错误，
  对话照常可用（配置只存在内存里，直到重启）。
- **同时打开的标签页共享一个 agent 实例**：`GatewayApp` 只持有一份
  `AgentLoop`（`src/myagent/gateway/app.py:63`）；改配置会 `reload()` 并关掉旧 runner
  （`:156`），正在跑的 turn 会被取消。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| FastAPI / Starlette + uvicorn | 为 9 条路由引入框架与 ASGI 服务器，且它的 loop 与 `ChatRunner` 的常驻 loop 需要额外协调；依赖策略要求先写 ADR，而收益（自动文档、依赖注入）本阶段用不上 |
| aiohttp（与上游 `api/server.py:475` 同款） | 上游用它是因为要同时跑 WS 与 OpenAI 兼容 API；我们两者都不做 |
| WebSocket 推流（对齐上游 `ws_http.py:665`） | V1 模型层不流式，一次结果推一次没有意义；真流式落地后再加，位置已留好 |
| React + Vite（对齐上游 `nanobot/webui/`） | 需要 Node 工具链与构建产物，与「核心功能、可答辩」的目标不成比例；零构建版本同样能覆盖聊天与配置 |
| 前端内嵌为 Python 字符串 | 无法用编辑器语法高亮、无法缓存、审查 diff 困难 |
| 用 CORS 放开跨源 | 会把「任何网页都能驱动本机 agent」变成默认行为；需要跨机时用 `--allow-remote` 明示 |
| 加一个 token 认证 | 单用户本机工具，认证的密钥又要存进浏览器（`localStorage`），安全收益与复杂度不成比例；真要暴露到网络应先上反向代理与 TLS（本阶段不做） |
| 每个请求 `asyncio.run()` | `AgentLoop` 的会话锁绑定 loop，第二个请求必然报 "bound to a different event loop" |
| 把配置另存一份 JSON（如 `data/config.json`） | 会造出第二个凭据来源，违反 ADR-0004 的「密钥只有一条读取路径」 |
