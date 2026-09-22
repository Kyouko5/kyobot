/* MyAgent gateway UI: plain DOM, no framework, no bundler (ADR-0011).
 *
 * The whole client is three view functions (messages, sessions, settings) over a
 * tiny API wrapper. Everything the agent produces is written with textContent,
 * never innerHTML, so a model answer can never turn into markup.
 */

"use strict";

const state = {
  bootstrap: null,
  sessionKey: null,
  sessions: [],
  busy: false,
};

const el = (id) => document.getElementById(id);

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = null;
    }
  }
  if (!response.ok) {
    throw new Error((payload && payload.error) || `HTTP ${response.status}`);
  }
  return payload;
}

const get = (path) => api(path);
const del = (path) => api(path, { method: "DELETE" });
const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/* errors ------------------------------------------------------------------- */

function showError(message) {
  const box = el("error");
  box.textContent = message;
  box.classList.remove("hidden");
}

function clearError() {
  el("error").classList.add("hidden");
}

/* messages ----------------------------------------------------------------- */

function addBubble(role, content, meta) {
  const bubble = document.createElement("div");
  bubble.className = `message ${role}`;
  bubble.textContent = content;
  if (meta && meta.length) {
    const line = document.createElement("div");
    line.className = "meta";
    line.textContent = meta.join(" · ");
    bubble.appendChild(line);
  }
  el("messages").appendChild(bubble);
  el("messages").scrollTop = el("messages").scrollHeight;
  return bubble;
}

function renderEmptyState() {
  const box = document.createElement("div");
  box.className = "empty";
  const title = document.createElement("p");
  title.textContent = "这是本地 gateway：浏览器直接驱动 myagent 的 AgentLoop。";
  const list = document.createElement("ul");
  for (const line of [
    "工具：读文件、搜索、计算都可用，回答里会标出用了哪些工具",
    "记忆与 RAG：与 myagent chat 共用同一份长期记忆和知识库",
    "上下文：每轮回答下会给出本轮 section 用量与预算",
  ]) {
    const item = document.createElement("li");
    item.textContent = line;
    list.appendChild(item);
  }
  box.append(title, list);
  el("messages").appendChild(box);
}

function renderMessages(session) {
  const box = el("messages");
  box.replaceChildren();
  /* Tool results are part of the assistant turn that called them, and the system
   * prompt is rebuilt every turn: neither is a bubble of its own. */
  const visible = session.messages.filter(
    (message) =>
      (message.role === "user" || message.role === "assistant") &&
      (message.content || message.tools.length),
  );
  if (!visible.length) {
    renderEmptyState();
    return;
  }
  for (const message of visible) {
    const role = message.role === "user" ? "user" : "assistant";
    const named = message.tools.join(", ");
    const meta = message.tools.length && message.content ? [`工具：${named}`] : [];
    addBubble(role, message.content || `（调用了工具：${named}）`, meta);
  }
}

function contextLine(context) {
  if (!context) {
    return null;
  }
  const budget = context.budget === null ? "不限" : context.budget;
  const parts = [`上下文 ${context.used}/${budget} token`];
  if (context.dropped) {
    parts.push(`裁剪 -${context.dropped}`);
  }
  if (context.compacted) {
    parts.push("已压缩");
  }
  return parts.join(" · ");
}

function showContextBadge(context) {
  const badge = el("context-badge");
  const line = contextLine(context);
  if (!line) {
    badge.classList.add("hidden");
    return;
  }
  badge.textContent = line;
  badge.title = context.sections
    .map(
      (section) =>
        `${section.name}: used=${section.used} budget=${section.budget} ${section.action}`,
    )
    .join("\n");
  badge.classList.remove("hidden");
}

/* sessions ----------------------------------------------------------------- */

function renderSessions() {
  const list = el("sessions");
  list.replaceChildren();
  for (const session of state.sessions) {
    const item = document.createElement("li");
    if (session.key === state.sessionKey) {
      item.className = "active";
    }
    const key = document.createElement("span");
    key.className = "key";
    key.textContent = session.key;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = session.compacted ? `${session.messages}·摘要` : String(session.messages);
    const remove = document.createElement("button");
    remove.className = "remove";
    remove.type = "button";
    remove.textContent = "✕";
    remove.title = "删除这个会话";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      forgetSession(session.key);
    });
    item.append(key, count, remove);
    item.addEventListener("click", () => openSession(session.key));
    list.appendChild(item);
  }
}

async function refreshSessions() {
  const payload = await get("/api/sessions");
  state.sessions = payload.sessions;
  renderSessions();
}

async function openSession(key) {
  clearError();
  state.sessionKey = key;
  localStorage.setItem("myagent.session", key);
  el("session-key").textContent = key;
  renderSessions();
  const session = await get(`/api/sessions/${encodeURIComponent(key)}`);
  renderMessages(session);
  showContextBadge(null);
}

async function forgetSession(key) {
  clearError();
  await del(`/api/sessions/${encodeURIComponent(key)}`);
  if (key === state.sessionKey) {
    renderEmptyState();
    showContextBadge(null);
  }
  await refreshSessions();
}

function newSession() {
  const entered = prompt("新会话的 key", `web:${Date.now().toString(36)}`);
  if (!entered || !entered.startsWith("web:")) {
    return;
  }
  openSession(entered);
}

/* chat --------------------------------------------------------------------- */

function setBusy(busy) {
  state.busy = busy;
  el("send").disabled = busy;
  el("send").textContent = busy ? "思考中…" : "发送";
}

async function send(text) {
  addBubble("user", text, []);
  const pending = addBubble("assistant", "思考中…", []);
  pending.classList.add("pending");
  setBusy(true);
  clearError();
  try {
    const answer = await post("/api/chat", { message: text, session_key: state.sessionKey });
    pending.remove();
    const meta = [];
    if (answer.tools_used.length) {
      meta.push(`工具：${answer.tools_used.join(", ")}`);
    }
    const context = contextLine(answer.context);
    if (context) {
      meta.push(context);
    }
    if (answer.stop_reason && answer.stop_reason !== "completed") {
      meta.push(`stop_reason=${answer.stop_reason}`);
    }
    const bubble = addBubble("assistant", answer.content, meta);
    if (answer.error) {
      bubble.classList.add("failed");
    }
    showContextBadge(answer.context);
    await refreshSessions();
  } catch (error) {
    pending.remove();
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

/* settings ----------------------------------------------------------------- */

function fillConfigForm(config) {
  const provider = el("cfg-provider");
  provider.replaceChildren();
  for (const name of config.providers) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    provider.appendChild(option);
  }
  provider.value = config.provider;
  el("cfg-model").value = config.model || "";
  el("cfg-base-url").value = config.base_url || "";
  el("cfg-api-key").value = "";
  el("cfg-api-key").placeholder = config.api_key_set ? config.api_key_hint : "尚未设置";
  el("cfg-api-key-hint").textContent = config.api_key_set
    ? `已配置：${config.api_key_hint}（留空表示不修改）`
    : `未配置；生效端点 ${config.resolved_base_url}`;
  el("cfg-max-tokens").value = config.max_tokens;
  el("cfg-context-window").value = config.context_window;
  el("cfg-temperature").value = config.temperature;
  setStatus("", "");
}

function setStatus(text, kind) {
  const box = el("cfg-status");
  box.textContent = text;
  box.className = `status ${kind}`;
}

function configPayload() {
  const payload = {
    provider: el("cfg-provider").value,
    model: el("cfg-model").value,
    base_url: el("cfg-base-url").value,
    max_tokens: Number(el("cfg-max-tokens").value),
    context_window: Number(el("cfg-context-window").value),
    temperature: Number(el("cfg-temperature").value),
  };
  const key = el("cfg-api-key").value;
  if (key) {
    payload.api_key = key; // 留空 = 不改动已保存的 key
  }
  return payload;
}

async function openConfig() {
  const config = await get("/api/config");
  fillConfigForm(config);
  el("config-dialog").showModal();
}

async function saveConfig() {
  setStatus("保存中…", "");
  try {
    const config = await post("/api/config", configPayload());
    fillConfigForm(config);
    setStatus("已保存到 .env，agent 已按新配置重建。", "ok");
    await refreshBootstrap();
  } catch (error) {
    setStatus(error.message, "bad");
  }
}

async function testConfig() {
  setStatus("正在请求模型…", "");
  try {
    const result = await post("/api/config/test", configPayload());
    if (result.ok) {
      setStatus(`连接成功：${result.model} 回复 ${JSON.stringify(result.reply)}`, "ok");
    } else {
      setStatus(`连接失败：${result.error}`, "bad");
    }
  } catch (error) {
    setStatus(error.message, "bad");
  }
}

/* bootstrap ---------------------------------------------------------------- */

function renderBootstrap(bootstrap) {
  el("version").textContent = `v${bootstrap.version}`;
  const model = bootstrap.model;
  const badge = el("model-badge");
  badge.textContent = model.configured ? model.name : "未配置模型";
  badge.classList.toggle("ready", model.configured);
  badge.title = `${model.provider} · ${model.base_url}`;
  el("model-hint").textContent = model.configured
    ? ""
    : "尚未配置模型或 API Key → 点左下角「API 配置」，保存后立即生效。";
}

async function refreshBootstrap() {
  state.bootstrap = await get("/api/bootstrap");
  renderBootstrap(state.bootstrap);
}

/* wiring ------------------------------------------------------------------- */

function wire() {
  el("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = el("input");
    const text = input.value.trim();
    if (!text || state.busy) {
      return;
    }
    input.value = "";
    send(text);
  });
  el("input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el("composer").requestSubmit();
    }
  });
  el("new-session").addEventListener("click", newSession);
  el("clear-session").addEventListener("click", () => forgetSession(state.sessionKey));
  el("open-config").addEventListener("click", openConfig);
  el("cfg-save").addEventListener("click", saveConfig);
  el("cfg-test").addEventListener("click", testConfig);
  el("cfg-cancel").addEventListener("click", () => el("config-dialog").close());
}

async function init() {
  wire();
  await refreshBootstrap();
  const stored = localStorage.getItem("myagent.session");
  await openSession(stored || state.bootstrap.session_key);
  await refreshSessions();
}

init().catch((error) => showError(error.message));
