"use strict";
(() => {
  const get = id => document.getElementById(id);
  const node = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
  let taskId = null, current = null, timer = null, submitting = false, keyReady = false, setBusy = () => {};
  let signature = "", pending = null, loading = false, actionPending = null;
  const activeStates = new Set(["queued", "running", "stopping"]);
  const stopLabels = {sufficient_evidence: "已核对正文与引用", insufficient_evidence: "已说明证据不足的范围",
    user_stopped: "已停止后续步骤", model_budget_exhausted: "达到本轮预算", runtime_budget_exhausted: "达到本轮时限",
    step_budget_exhausted: "达到本轮步骤上限", unverified_completion: "未完成有效引用校验", no_progress: "未取得更多证据"};
  const storageKey = () => `paper-assistant-chat-pending:${taskId}`;
  const actionStorageKey = () => `paper-assistant-chat-action:${taskId}`;
  const endpoint = () => `/api/research/jobs/${encodeURIComponent(taskId)}/conversation`;
  async function request(url, options) {
    const response = await fetch(url, {cache: "no-store", ...options});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || "无法读取对话。"); error.status = response.status; throw error; }
    return data;
  }
  function notice(text) { get("conversation-status").textContent = text; }
  function controls() {
    const busy = Boolean(current?.active_turn_id);
    const exhausted = current && ((current.all_turn_count ?? current.turns.length) >= current.max_turns || current.remaining_usd < .03);
    get("conversation-submit").disabled = !current?.available || !keyReady || busy || submitting || loading || exhausted || Boolean(actionPending);
    get("conversation-question").disabled = busy || submitting || Boolean(pending) || Boolean(actionPending);
    get("conversation-submit").textContent = submitting ? "正在提交…" : pending ? "重试本次提交" : "发送追问";
    get("conversation-stop").hidden = !busy;
    get("conversation-stop").disabled = submitting;
    for (const action of ["new", "reset"]) {
      get(`conversation-${action}`).disabled = !current || busy || submitting || loading || Boolean(pending)
        || Boolean(actionPending && actionPending.action !== action);
      get(`conversation-${action}`).textContent = actionPending?.action === action ? (action === "new" ? "重试新建对话" : "重试重置")
        : action === "new" ? "新建对话" : "重置当前对话";
    }
    get("conversation-select").disabled = !current || busy || submitting || loading || Boolean(pending) || Boolean(actionPending);
    setBusy(busy || submitting || Boolean(pending) || Boolean(actionPending));
  }
  function citations(answer, parent) {
    if (answer?.answer_kind === "bibliography") {
      if (!answer.references?.length) return;
      const details = node("details", undefined, "chat-citations");
      details.append(node("summary", `参考论文（${answer.references.length} 篇）`));
      for (const reference of answer.references) {
        const item = node("div", undefined, "citation");
        item.append(node("strong", reference.title), node("p", (reference.authors || []).join("; ")),
          node("p", `arXiv:${reference.arxiv_id}v${reference.version}`));
        if (/^https:\/\/arxiv\.org\/abs\/[A-Za-z0-9./-]+v[1-9][0-9]*$/.test(reference.abstract_url || "")) {
          const link = node("a", "打开论文"); link.href = reference.abstract_url; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link);
        }
        details.append(item);
      }
      parent.append(details); return;
    }
    if (!answer?.citations?.length) return;
    const details = node("details", undefined, "chat-citations");
    details.append(node("summary", `查看 ${answer.citations.length} 条引用`));
    for (const citation of answer.citations) {
      const pages = [...new Set((citation.sources || []).map(source => source.page_number))].join("、");
      const title = citation.source?.title || citation.source?.arxiv_id || "论文";
      const item = node("div", undefined, "citation");
      item.append(node("strong", `[${citation.evidence_id}] ${title} · PDF 第 ${pages} 页`), node("p", citation.quote));
      details.append(item);
    }
    parent.append(details);
  }
  function render(data) {
    if (current && (data.thread_id !== current.thread_id || data.generation !== current.generation)) {
      signature = ""; get("conversation-question").value = "";
    }
    current = data;
    if (actionPending && data.acknowledged_actions?.includes(actionPending.payload.request_id)) {
      actionPending = null; try { localStorage.removeItem(actionStorageKey()); } catch (_) {}
    }
    if (pending && (data.acknowledged_request_ids || data.turns.map(turn => turn.request_id)).includes(pending.request_id)) {
      pending = null; get("conversation-question").value = "";
      try { localStorage.removeItem(storageKey()); } catch (_) {}
    }
    get("conversation-select").replaceChildren(...(data.threads || [{id: "initial", name: "对话 1", turn_count: data.turns.length}]).map(thread => {
      const option = node("option", `${thread.name} · ${thread.turn_count} 轮`); option.value = thread.id; return option;
    }));
    get("conversation-select").value = data.thread_id || "initial";
    const changed = JSON.stringify(data.turns);
    if (changed !== signature) {
      signature = changed;
      get("conversation-messages").replaceChildren(...data.turns.map(turn => {
        const entry = node("article", undefined, "chat-turn");
        const question = node("div", undefined, "chat-question"); question.append(node("strong", "你"), node("p", turn.question));
        const answer = node("div", undefined, "chat-answer");
        answer.append(node("strong", "研究助手"));
        if (turn.answer) {
          answer.append(node("p", turn.answer.answer)); citations(turn.answer, answer);
          if (turn.answer.insufficient_evidence) answer.append(node("small", turn.answer.answer_kind === "bibliography"
            ? "未找到该范围的可追溯引用记录。" : "当前论文证据不足，以上回答已说明范围。", "muted"));
        } else answer.append(node("p", activeStates.has(turn.status) ? `正在检索与核验…（${turn.steps} 步）` : "本轮未形成经过核验的回答。"));
        if (turn.error) answer.append(node("p", turn.error, "error"));
        if (!activeStates.has(turn.status) && turn.stop_reason) answer.append(node("small",
          turn.answer?.answer_kind === "bibliography" && !turn.answer.insufficient_evidence ? "已核对引用记录与论文元数据"
            : stopLabels[turn.stop_reason] || "本轮已结束，可查看回答和错误信息。", "muted"));
        entry.append(question, answer); return entry;
      }));
    }
    get("conversation-usage").textContent = `当前对话 ${data.turns.length} 轮 · 本任务追问累计 ${data.all_turn_count ?? data.turns.length} / ${data.max_turns} 轮 · 累计 ${data.usage.tokens} token · 估算 $${Number(data.usage.cost_usd).toFixed(6)} · 研究与追问总费用 $${Number(data.research_and_discussion_cost_usd || 0).toFixed(6)}`;
    notice(data.active_turn_id ? "本轮正在执行，可停止后续步骤。" : actionPending ? "对话操作尚未确认，请重新连接检查；不会自动重复执行。" : !keyReady ? "请先在 .env 配置 OPENAI_API_KEY。" : !data.available ? data.reason : data.remaining_usd < .03 ? "本任务追问预算不足，新建或重置不会恢复已用预算。" : pending ? "尚未发现本次提交记录，可显式重试；不会自动发送。" : !data.turns.length ? "当前对话暂无追问，原研究结果与论文仍作为背景。可以开始新的讨论。" : "可以继续追问：正文结论核对证据，论文列表核对来源记录。");
    controls();
  }
  async function load() {
    if (!taskId || loading || submitting) return;
    clearTimeout(timer); loading = true; controls();
    const expected = taskId;
    try {
      const data = await request(endpoint());
      if (expected !== taskId) return;
      render(data);
      if (data.active_turn_id) timer = setTimeout(load, 1500);
    } catch (error) { if (expected === taskId) { notice(error.message + " 点击重新连接查看；不会自动重试提交。"); controls(); } }
    finally { loading = false; controls(); if (expected !== taskId) load(); }
  }
  globalThis.paperResearchView = (data, options) => {
    keyReady = options.keyReady; setBusy = options.setBusy;
    const changed = taskId !== data.job.task_id;
    if (changed) {
      clearTimeout(timer); taskId = data.job.task_id; current = null; signature = ""; pending = null; actionPending = null;
      get("conversation-messages").replaceChildren();
      try { pending = JSON.parse(localStorage.getItem(storageKey()) || "null"); } catch (_) {}
      try { actionPending = JSON.parse(localStorage.getItem(actionStorageKey()) || "null"); } catch (_) {}
      if (pending) get("conversation-question").value = pending.question;
    }
    const researchActive = activeStates.has(data.job.status);
    get("conversation-panel").hidden = researchActive;
    if (!researchActive && (changed || !current)) load();
  };
  get("conversation-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting || loading || actionPending || current?.active_turn_id || !current?.available || !keyReady) return;
    const question = get("conversation-question").value.trim();
    if (!question || question.length > 1000) { notice("请填写 1–1000 字追问。"); return; }
    if (!pending) pending = {question, request_id: `chat-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`,
      thread_id: current.thread_id || "initial", generation: current.generation ?? 0};
    try { localStorage.setItem(storageKey(), JSON.stringify(pending)); } catch (_) {}
    submitting = true; controls();
    try {
      render(await request(endpoint(), {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pending)}));
      timer = setTimeout(load, 300);
    } catch (error) {
      if (error.status >= 400 && error.status < 500) {
        pending = null; try { localStorage.removeItem(storageKey()); } catch (_) {}
      }
      notice(error.message + " 点击重新连接检查提交结果；不会自动重试。");
    }
    finally { submitting = false; controls(); }
  });
  get("conversation-refresh").addEventListener("click", load);
  async function manage(action) {
    if (!current || current.active_turn_id || submitting || loading || pending) return;
    if (actionPending && actionPending.action !== action) return;
    if (!actionPending) actionPending = {action, payload: {request_id: `action-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`,
      thread_id: current.thread_id || "initial", generation: current.generation ?? 0}};
    try { localStorage.setItem(actionStorageKey(), JSON.stringify(actionPending)); } catch (_) {}
    submitting = true; controls();
    try {
      render(await request(endpoint() + "/" + action, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(actionPending.payload)}));
    } catch (error) {
      if (error.status >= 400 && error.status < 500) {
        actionPending = null; try { localStorage.removeItem(actionStorageKey()); } catch (_) {}
      }
      notice(error.message + " 点击重新连接核对当前对话；不会自动重复执行。");
    } finally { submitting = false; controls(); }
  }
  get("conversation-new").addEventListener("click", () => manage("new"));
  get("conversation-reset").addEventListener("click", () => manage("reset"));
  get("conversation-select").addEventListener("change", async () => {
    if (!current || current.active_turn_id || submitting || loading || pending || actionPending) return;
    submitting = true; controls();
    try { render(await request(endpoint() + "/select", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({thread_id: get("conversation-select").value})})); }
    catch (error) { notice(error.message + " 请重新连接核对当前对话。"); get("conversation-select").value = current.thread_id || "initial"; }
    finally { submitting = false; controls(); }
  });
  get("conversation-stop").addEventListener("click", async () => {
    if (!current?.active_turn_id) return;
    try { render(await request(endpoint() + "/stop", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})); }
    catch (error) { notice(error.message); }
  });
})();
