# 本地 Gateway 与浏览器 UI 设计（Phase G）

> **结论**：`myagent web` 用**标准库 `http.server`** 起一个只监听 loopback 的进程，把
> **同一个 `AgentLoop`**（记忆、RAG、工具、上下文预算一个不少）暴露成 9 个 JSON 路由，
> 并直接 serve 一个**无构建步骤**的三件套前端（`index.html` / `app.js` / `style.css`）。
> 浏览器是**传输层**，不是第二个 runtime：装配仍然只有一处
> （`src/myagent/runtime.py:47`），配置仍然只有一份（`.env`，ADR-0004）。

- 关联决策：[ADR-0011](./decision-records/0011-local-gateway-and-webui.md)（为什么不用框架、不用 Node、不用 WebSocket）
- 关联记录：[`docs/records/phase-g-gateway-webui.md`](./records/phase-g-gateway-webui.md)（真实冒烟与质量门）
- 上游参照：`nanobot/nanobot/webui/ws_http.py`（HTTP 路由形状）、`nanobot/webui/`（React 前端）

## 1. 目标与边界

| 做 | 不做（本阶段） |
| --- | --- |
| 浏览器里与 agent 对话（工具、记忆、RAG、上下文预算全部沿用） | 流式输出（V1 的模型层没有 stream，见 `src/myagent/models/openai_compat.py:110`） |
| 查看 / 修改 API 配置（模型、Base URL、Key、max tokens、上下文窗口、温度）并写回 `.env` | 多用户、登录、会话共享 |
| 列出 / 打开 / 删除会话（与 CLI 共用 `data/sessions/`） | 多频道（Telegram / Feishu …）、定时任务、MCP 面板 |
| 每轮显示 Phase 6 的上下文账本（used / budget / dropped / 压缩） | 富文本编排、工作流、Agent 图形化调试 |
| 一个进程、一条命令、零构建（`myagent web`） | 生产部署、反向代理、TLS |

范围取舍来自 PLAN §2.2 的「暂不重点实现」清单：那里的「复杂 Web UI」指的正是上游那种
多频道 + 工作流 + 设置中心量级的前端；Phase G 只做**核心能力的浏览器入口**，因此
`docs/decision-records/0011` 把「不用框架、不用 Node 工具链」写成了硬约束。

## 2. 一条请求的完整路径

```text
浏览器 (index.html + app.js)
   │  fetch POST /api/chat {"message": "...", "session_key": "web:default"}
   ▼
GatewayRequestHandler._route()            src/myagent/gateway/server.py:193
   │  _authorize()  Host/Origin 必须指向本机       src/myagent/gateway/server.py:233
   │  _read_json()  限长 1 MiB、必须是 application/json src/myagent/gateway/server.py:243
   ▼
GatewayApp.chat()                          src/myagent/gateway/app.py:207
   │  校验 message / session_key            src/myagent/gateway/app.py:230
   ▼
ChatRunner.turn()  ──submit──▶ 常驻事件循环线程（一个进程一个 loop）  src/myagent/gateway/runner.py:77
   │
   ▼  AgentLoop.run_turn()                  src/myagent/agent/loop.py:150
   │  build  → memory.recall / retriever.recall / 预算裁剪       （Phase 3–6，未改动）
   │  run    → model + tools（会话内串行、跨会话并行）            src/myagent/agent/loop.py:320
   │  save   → JSONL 追加写 data/sessions/                       src/myagent/session/manager.py:71
   ▼
_turn_payload()  → {content, stop_reason, tools_used, error, context}
   │   context = ContextReport 的字段子集     src/myagent/gateway/app.py:286
   ▼
write_response() → HTTP/1.1 + 精确 Content-Length  src/myagent/gateway/server.py:110
```

前端拿到 `context` 后只做两件事：把 `used/budget/dropped` 拼成徽标，把每个 section 的
`action` 放进 `title` 悬停（`src/myagent/gateway/assets/app.js:116`）。

## 3. 代码布局

| 文件 | 职责 | 关键锚点 |
| --- | --- | --- |
| `src/myagent/gateway/server.py` | HTTP 路由、安全边界、`serve()` 主循环 | `_route` `src/myagent/gateway/server.py:193`、`serve` `src/myagent/gateway/server.py:277` |
| `src/myagent/gateway/app.py` | 与 HTTP 无关的应用层：bootstrap / 配置 / 会话 / 对话 | `GatewayApp` `src/myagent/gateway/app.py:63` |
| `src/myagent/gateway/config.py` | API 配置的掩码、校验与写回 | `merge_config` `src/myagent/gateway/config.py:86` |
| `src/myagent/gateway/runner.py` | 常驻事件循环线程（`ChatRunner`） | `ChatRunner` `src/myagent/gateway/runner.py:37` |
| `src/myagent/gateway/assets.py` | 静态资源白名单读取（防目录穿越） | `Assets.path_for` `src/myagent/gateway/assets.py:50` |
| `src/myagent/gateway/errors.py` | `GatewayError(status, message)`：状态码跟着异常走 | `src/myagent/gateway/errors.py:15` |
| `src/myagent/gateway/assets/*` | 前端三件套 + favicon（无构建步骤） | `src/myagent/gateway/assets/index.html:1` |
| `src/myagent/cli.py` | `myagent web` 子命令 | `_web` `src/myagent/cli.py:464` |

分层的意义是**可测性**：`app.py` 里没有 `http.server` 类型，所以 `tests/gateway/test_app.py`
不开 socket 就能覆盖对话与配置的全部行为；只有 `tests/gateway/test_server.py` 用真实
loopback 端口验证线上行为（保活、413、403）。

## 4. HTTP API

| 方法 | 路径 | 作用 | 失败 |
| --- | --- | --- | --- |
| GET | `/`、`/index.html` | 返回页面 | — |
| GET | `/static/<name>` | 静态资源（只允许扁平文件名） | 404 |
| GET | `/api/bootstrap` | 版本、模型徽标、功能开关、路由表 | — |
| GET | `/api/config` | 掩码后的 LLM 配置 | — |
| POST | `/api/config` | 校验 → 写 `.env` → 重建 agent | 400 |
| POST | `/api/config/test` | 用**尚未保存**的配置发一次 ping | 200 + `ok:false` |
| GET | `/api/sessions` | 会话列表 | — |
| GET | `/api/sessions/<key>` | 一个会话的 transcript（去掉 system 段） | — |
| DELETE | `/api/sessions/<key>` | 删掉一个会话（含 JSONL 文件） | — |
| POST | `/api/chat` | 跑一轮并返回答案 + 上下文账本 | 400 / 413 / 502 |

约定：**错误一律是 `{"error": "..."}`**，`405` 的文案会点明该用哪个方法
（`src/myagent/gateway/server.py:326`）。

`POST /api/chat`（下面是真实运行输出，见阶段记录 §7.3）：

```json
{
  "session_key": "web:default",
  "turn_id": "682abfc1",
  "content": "计算结果：**(12+8)*3 = 60**。",
  "stop_reason": "completed",
  "tools_used": ["calculator", "search_local"],
  "error": null,
  "context": {"budget": 122880, "used": 1898, "dropped": 0, "compacted": false,
              "sections": [{"name": "rag", "priority": 5, "required": false,
                            "budget": 43008, "used": 1382, "dropped": 0, "action": ""}]}
}
```

两个刻意的语义：

- **`stop_reason` / `error` 区分「这一轮”和「这一次模型调用”**。模型报错时 turn 仍然正常结束
  （`stop_reason="error"`，`error` 里是 provider 的原文，`content` 是同一句话），页面用红框标注，
  不是 500。只有「连 outbound 都没有」才是 502（`src/myagent/gateway/app.py:270`）。
- **`context` 可以为 `null`**：注入的 ContextManager 若不产出 `ContextReport`，页面就不显示数字，
  而不是编一个（`src/myagent/gateway/app.py:286`）。

## 5. 安全边界

这个进程能在用户机器上调用工具、读写 `workspace/`、花掉 API 额度，所以边界写死在代码里：

| 措施 | 位置 | 挡住什么 |
| --- | --- | --- |
| 默认只绑 `127.0.0.1` | `src/myagent/gateway/server.py:55` | 同网段其他机器 |
| `Host` 必须是 loopback（`127.0.0.0/8`、`::1`、`localhost`） | `src/myagent/gateway/server.py:85` | DNS rebinding：恶意页面把自己的域名解析到 127.0.0.1，但浏览器发的 `Host` 仍是它的域名 |
| `Origin` 存在时也必须是 loopback | `src/myagent/gateway/server.py:104` | 其他站点发起的跨源请求 |
| 不返回任何 CORS 头 | `src/myagent/gateway/server.py:110` | 浏览器里读不到响应的跨源 `fetch` |
| `POST` 必须 `Content-Type: application/json` | `src/myagent/gateway/server.py:243` | 跨站表单（表单只能发 `text/plain` / `urlencoded` / `multipart`） |
| 静态资源按**名字白名单**读取 | `src/myagent/gateway/assets.py:50` | `../`、绝对路径、百分号编码穿越 |
| 请求体上限 1 MiB、长度非法即拒 | `src/myagent/gateway/server.py:332` | 内存与磁盘被拖垮 |
| `GET /api/config` 只回掩码（`sk-…wCr4`） | `src/myagent/gateway/config.py:55` | 页面、截图、日志里出现完整 key |
| 拒绝未经读取的请求体时关闭连接 | `src/myagent/gateway/server.py:178` | 保活连接上残留字节被当成下一个请求 |

**没有认证**是刻意的：这是本机单用户工具，加一套登录只会让人把 token 贴进浏览器历史。
需要从别的机器访问时用 `--allow-remote`，它会打印一条明确的警告
（`src/myagent/gateway/server.py:308`），提示「此模式下任何人都能驱动你的 agent」；
更安全的做法是 SSH 端口转发，让 `Host` 仍然是 loopback。

## 6. 常驻事件循环：为什么不能一请求一 `asyncio.run()`

`AgentLoop` 为每个会话保存一把 `asyncio.Lock`（`src/myagent/agent/loop.py:320`），
而 `asyncio` 的锁一旦被某个 loop 等待过就**绑定**在那个 loop 上。`http.server` 是同步的：
如果每个请求各自 `asyncio.run()`，第二次请求就会用一把属于已关闭 loop 的锁，
抛 `RuntimeError: ... is bound to a different event loop`。

所以网关自己起一个线程跑**唯一**的事件循环（`ChatRunner`，`src/myagent/gateway/runner.py:37`）：

- 启动时用 `concurrent.futures.Future` 把 loop 交给调用方（`src/myagent/gateway/runner.py:96`），
  避免「以为起来了其实还在等」的竞态；
- `submit()` 先 `start()` 再建协程，关闭后的 runner 直接拒绝，不会留下未 await 的协程
  （`src/myagent/gateway/runner.py:61`）；
- `close()` 停 loop、join 线程，并把还在飞的协程取消掉再关 loop
  （`src/myagent/gateway/runner.py:108`），所以反复保存配置不会泄漏线程与告警。

好处不只是「不崩」：**同一会话的轮次自动排队、不同会话真正并行**——这正是 Phase 3 设计那把锁
时想要的性质，而 CLI 因为一轮一进程从没验证过它。

## 7. 配置写回 `.env` 的路径

```text
POST /api/config
   │  merge_config(current, payload)     校验 + 生成 7 条赋值      src/myagent/gateway/config.py:86
   ▼
apply_config()  逐条 remember_env()      写 .env（按行替换/追加）  src/myagent/config/env.py:124
   ▼
GatewayApp.reload()  Settings.from_env() → build_agent() → 换掉 runner  src/myagent/gateway/app.py:156
   ▼
GET /api/config 掩码回显                 src/myagent/gateway/config.py:64
```

三条规则：

1. **先校验，后写入**：`merge_config()` 用 `LLMSettings` 的构造器做校验
   （`src/myagent/config/settings.py:490`），字段非法就 400，`os.environ` 与 `.env` 一个字节都不动
   （测试 `test_a_rejected_configuration_is_not_written`）。
2. **省略 = 不变，空串 = 清空**：页面上没动过的字段不会覆盖既有值；用户清空输入框就是把
   `LLM_MODEL=` 这类空赋值写进 `.env`，而 `get_env` 本来就把空值当未设置
   （`src/myagent/config/env.py:91`）。
3. **改完必须重建**：`.env` 变了不等于运行中的 loop 变了——它的模型、token 计数器、上下文预算都是
   从旧 `LLMSettings` 推导出来的。`reload()` 先建好新的 `AgentLoop` 再关旧的 runner，
   中途失败也不会把服务留在半死状态（`src/myagent/gateway/app.py:156`）。

「测试连接」走的是另一条路：`test_config()` 用 payload **合并出的临时配置**发一句
`Reply with the single word: pong.`（`src/myagent/gateway/app.py:52`），不写文件、不影响运行中的
agent；失败也返回 200 + `{"ok": false, "error": "..."}`——那是一次检查的结果，不是坏请求。

## 8. 前端

四个静态文件，浏览器直接跑，没有 npm / Vite / TypeScript：

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `src/myagent/gateway/assets/index.html` | 99 | 侧栏（会话列表）+ 聊天区 + `<dialog>` 设置面板 |
| `src/myagent/gateway/assets/app.js` | 385 | 三个渲染函数 + 一个 `fetch` 包装，无框架 |
| `src/myagent/gateway/assets/style.css` | 403 | 暗色主题、两栏布局、设置面板 |
| `src/myagent/gateway/assets/favicon.svg` | 6 | 内联 SVG |

几个刻意的选择：

- **只用 `textContent` 写模型输出**（`src/myagent/gateway/assets/app.js:62`）：模型可以返回任何
  字符串，`innerHTML` 会把它变成 DOM；不用富文本渲染也就不用引入 sanitizer。
- **`localStorage` 只存会话 key**（`src/myagent/gateway/assets/app.js:187`）：密钥只在 `.env`，
  浏览器不备份任何凭据。
- **工具结果不单独成气泡**：`tool` 角色属于它所在的那一轮，页面把消息过滤成 user / assistant，
  工具名进 meta 行（`src/myagent/gateway/assets/app.js:100`）。
- **会话默认 key 是 `web:default`**（`src/myagent/gateway/app.py:43`），与 CLI 的 `cli:default`
  分开但同源：两边都从 `data/sessions/` 读写，用 CLI 看得到网页里的对话。

## 9. 与上游 nanobot webui 的对照

| 维度 | 上游 nanobot | 本阶段 Phase G |
| --- | --- | --- |
| 前端 | React + Vite 构建产物（`nanobot/webui/`，`nanobot/webui/src/lib/nanobot-client.ts:189` 是 WS 多路复用客户端） | 原生三件套，网关直接 serve，零构建 |
| 传输 | WebSocket 长连接 + 大量 HTTP 路由（`nanobot/nanobot/webui/ws_http.py:665` 是 bootstrap） | HTTP 请求/响应（`src/myagent/gateway/server.py:193`） |
| 路由规模 | 设置 / MCP / 技能 / 定时任务 / 文件预览等数十条（`nanobot/nanobot/webui/ws_http.py:1429`） | 9 条：会话、配置、对话（§4） |
| 会话列表形状 | `{"sessions": [...]}`（`nanobot/nanobot/webui/ws_http.py:834`） | 同形状（`src/myagent/gateway/app.py:166`），方便对照阅读 |
| 兼容 API | 另有 aiohttp 的 OpenAI 兼容服务（`nanobot/nanobot/api/server.py:475`） | 不做：本阶段只要浏览器能用 |
| 依赖 | `websockets`、`aiohttp`、React 工具链（`nanobot/pyproject.toml:31`） | 标准库；运行时依赖仍是 4 条（`pyproject.toml:22`） |

学上游的是**接口形状**（bootstrap 里给出模型与路由信息、sessions 列表结构、错误 JSON 的形状），
不搬的是它的实现规模：我们的目标是让「浏览器能用」这件事本身可解释、可测、可答辩。

## 10. 已知限制

- **非流式**：模型层 V1 的 `stream()` 是保留接口（`src/myagent/models/openai_compat.py:110`），
  所以一轮回答一次性返回，页面用「思考中…」占位。等提供方流式落地，`ChatRunner` 已经具备
  推送事件的条件（SSE 或 WS 都只需要在 `gateway/` 内新增一层）。
- **单用户、无认证**：见 §5，`--allow-remote` 打开的就是「任何能访问该端口的人」。
- **内存/RAG 开关不在 UI 里**：它们是 `.env` 的 `MYAGENT_MEMORY_ENABLED` /
  `MYAGENT_RAG_ENABLED`（`src/myagent/config/settings.py:107`），UI 只展示生效状态
  （`src/myagent/gateway/app.py:91` 的 `features`）。理由：Phase 8 要用这两个开关做对照实验，
  让浏览器随手一改会让实验口径不稳定。
- **页面不做 Markdown 渲染**：模型输出里的 `**`、表格会原样显示。取舍见 §8。
- **没有自动化前端测试**：`tests/gateway/` 覆盖 Python；JS 只有一次性的无头冒烟
  （阶段记录 §7.4）。要长期维护前端就该引入 Vitest/Playwright，那需要先记一条 ADR。

## 11. 答辩问题

**Q1：为什么不用 FastAPI / aiohttp？**
三层理由：（1）V1 的模型层不流式，WebSocket 只能把一次结果推一次，收益为零；
（2）`pyproject.toml` 的依赖策略要求每个运行依赖都有 ADR，而这个网关总共 9 条路由、一个进程、
无并发写共享状态，`http.server` 足够；（3）框架会把「谁在什么时候读了请求体」这类细节藏起来，
而这里的 `Content-Length` 纪律（`src/myagent/gateway/server.py:243`）正是保活连接能不能用的前提。
代价是手写的部分要自己测：`tests/gateway/test_server.py` 用真实 socket 覆盖了 405/411/413/415/403。

**Q2：浏览器直连本地端口，安全边界在哪？**
默认只绑 loopback 且校验 `Host`/`Origin`（挡 DNS rebinding 与跨源），不返回 CORS 头，
拒绝非 JSON 请求体（挡跨站表单），静态资源按名字白名单读，配置只回掩码。
没有认证是明说的限制而不是隐藏的假设：`--allow-remote` 会打印警告。

**Q3：UI 和 CLI 会不会变成两套状态？**
不会。两者都走 `myagent.runtime.build_agent()`（`src/myagent/runtime.py:47`），都读同一份 `.env`、
同一个 `data/sessions/`、同一个 Qdrant collection。差异只有会话 key（`web:default` vs
`cli:default`）和呈现方式——这正是把装配收敛到一处（PLAN 3.5）之后才敢做的事。
