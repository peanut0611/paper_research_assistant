"use strict";
const byId = id => document.getElementById(id);
const form = byId("question-form");
const input = byId("question");
const conversation = byId("conversation");
const notice = byId("notice");
let busy = false;
let ready = false;
const labels = {answered: "回答完成", insufficient_evidence: "证据不足", no_evidence: "未找到可用证据",
  model_refusal: "模型未作答", incomplete: "回答未完成", invalid_response: "回答未通过引用校验"};
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function details(parent, title, text) {
  const node = element("details");
  node.append(element("summary", title), element("p", text));
  parent.append(node);
}
function setBusy(value) {
  busy = value;
  ["submit", "question", "method", "top-k", "exact-count", "clear"].forEach(id => {
    byId(id).disabled = value || (!ready && ["submit", "question"].includes(id));
  });
  byId("submit").textContent = value ? "正在生成…" : "发送问题 ↗";
}
async function loadStatus() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    if (!response.ok) throw new Error("无法读取服务状态。");
    const data = await response.json();
    byId("files").replaceChildren(...data.files.map(name => element("li", name)));
    byId("corpus-summary").textContent = `${data.files.length} 份资料 · ${data.chunk_count} 个片段`;
    ready = data.ready && data.key_ready;
    notice.textContent = !data.ready ? data.error : !data.key_ready
      ? "请在启动页面的终端配置 OPENAI_API_KEY 后重启服务，配置方法见 README。"
      : "已连接本地资料。首次提问需要加载模型，请稍候；回答生成会调用 OpenAI。";
    notice.classList.toggle("error", !ready);
  } catch (_) {
    ready = false;
    notice.textContent = "无法连接本地服务，请确认终端中的服务仍在运行，然后刷新页面。";
    notice.classList.add("error");
  }
  setBusy(false);
}
function renderResult(container, payload) {
  const result = payload.result;
  container.append(element("div", labels[result.status] || result.status,
    "status" + (result.status === "answered" ? "" : " warning")));
  if (result.answer) container.append(element("div", result.answer, "answer"));
  else container.append(element("div", "本次没有可展示的有效答案，可下载结果查看详情。", "answer"));
  for (const citation of result.citations) {
    const sources = [...new Set(citation.sources.map(source => `${source.filename} · 第 ${source.page_number} 页`))];
    details(container, `[${citation.evidence_id}] 查看来源与引文`, sources.join("\n") + "\n\n" + citation.quote);
  }
  const usage = result.usage;
  if (usage) container.append(element("p", `${payload.retrieval_method.toUpperCase()} · 输入 ${usage.input_tokens} / 输出 ${usage.output_tokens} tokens · 估算费用 $${result.estimated_cost_usd.toFixed(6)}`, "muted"));
  const evidence = payload.context.evidence;
  details(container, "查看本次参考片段", evidence.length ? evidence.map(item => `[${item.evidence_id}] ${item.text}`).join("\n\n") : "没有选入参考片段。");
  const download = element("button", "下载本次结果 JSON", "download");
  download.type = "button";
  download.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: "application/json;charset=utf-8"}));
    const link = element("a");
    link.href = url; link.download = "paper-answer.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  container.append(download);
}
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !ready) return;
  const question = input.value.trim();
  const topK = Number(byId("top-k").value);
  if (!question || question.length > 4000 || !Number.isInteger(topK) || topK < 1 || topK > 10) return;
  const request = {question, method: byId("method").value, top_k: topK,
    token_count_mode: byId("exact-count").checked ? "api" : "local"};
  setBusy(true);
  byId("empty").hidden = true;
  const turn = element("article", undefined, "turn");
  const answer = element("div");
  turn.append(element("p", "你", "role"), element("div", question, "question"), element("p", "资料助手", "role"), answer);
  conversation.append(turn);
  answer.append(element("div", "正在检索资料并生成回答…", "muted"));
  input.value = "";
  turn.scrollIntoView({behavior: "smooth", block: "start"});
  try {
    const response = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(request)});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "问答请求失败。");
    answer.replaceChildren();
    renderResult(answer, payload);
  } catch (error) {
    answer.replaceChildren(element("div", error instanceof TypeError
      ? "连接中断，没有自动重试。请确认服务状态并检查 API 用量后再决定是否重试。"
      : error.message, "error"));
  } finally {
    setBusy(false);
    input.focus();
  }
});
input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); form.requestSubmit();
  }
});
byId("clear").addEventListener("click", () => {
  if (!busy) { conversation.replaceChildren(); byId("empty").hidden = false; }
});
loadStatus();
