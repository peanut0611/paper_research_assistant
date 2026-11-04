"use strict";
(() => {
  const get = id => document.getElementById(id);
  const make = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const activeStatuses = new Set(["queued", "running", "stopping"]);
  const tools = {search_literature: "查找文献", fetch_paper: "获取 PDF", ingest_paper: "建立全文索引",
    list_documents: "查看资料", search_documents: "检索正文", read_pages: "阅读页面", verify_citations: "核对引用"};
  const reasons = {sufficient_evidence: "证据检查完成", insufficient_evidence: "证据不足，已保留部分结果", no_results: "未找到可用结果",
    user_stopped: "已按请求停止", runtime_budget_exhausted: "达到任务时限", model_budget_exhausted: "达到模型预算",
    step_budget_exhausted: "达到步骤上限", tool_budget_exhausted: "达到工具调用上限", no_progress: "没有新增证据，已停止",
    repeated_action: "检测到重复动作，已停止", unverified_completion: "结束前未完成引用校验", invalid_decision_limit: "动作或引用校验持续失败，已停止",
    model_context_too_large: "研究上下文达到本地长度上限，已停止；已下载 PDF 保留，但可能尚未入库"};
  const materialLabels = {metadata_only: "仅元数据", abstract_only: "仅摘要", downloaded: "已获取 PDF", fulltext_ready: "全文可检索",
    unusable: "全文不可用", parse_failed: "解析失败", unavailable: "全文获取失败"};
  let keyReady = false, available = false, active = false, submitting = false, jobId = null, latest = null, timer = null;
  let answerSignature = "", candidatesSignature = "", discussionBusy = false, pending = null;
  let deleteTarget = null, deleting = false;
  get("research-delete-confirmation").hidden = true;
  const pendingKey = "paper-assistant-research-pending";
  const inputIds = ["research-topic", "research-start", "research-end", "research-questions", "research-source", "research-papers", "research-example"];
  function controls() {
    get("research-submit").disabled = !keyReady || active || submitting || discussionBusy || deleting || Boolean(deleteTarget);
    get("research-submit").textContent = submitting ? "正在启动…" : active ? "研究进行中…" : pending ? "重试本次提交" : "开始研究";
    inputIds.forEach(id => { get(id).disabled = active || submitting || discussionBusy || Boolean(pending); });
    get("research-stop").disabled = !active || submitting || latest?.job.stop_requested;
    get("research-stop").hidden = !active;
    get("research-download").disabled = !latest;
    get("research-dialogue-select").disabled = submitting || discussionBusy || Boolean(pending);
    get("research-dialogue-new").disabled = active || submitting || discussionBusy || Boolean(pending) || Boolean(deleteTarget);
    get("research-dialogue-select").disabled ||= Boolean(deleteTarget) || deleting;
    get("research-dialogue-delete").disabled = !jobId || latest?.job.task_id !== jobId || active || discussionBusy || submitting || Boolean(pending) || Boolean(deleteTarget) || deleting;
    get("research-delete-confirm").disabled = deleting || active || discussionBusy;
    get("research-delete-cancel").disabled = deleting;
  }
  function note(text, error = false) {
    get("research-notice").textContent = text;
    get("research-notice").classList.toggle("error", error);
  }
  async function request(url, options) {
    const response = await fetch(url, {cache: "no-store", ...options});
    const payload = await response.json();
    if (!response.ok) { const error = new Error(payload.error || "无法读取研究任务。"); error.status = response.status; throw error; }
    return payload;
  }
  function sourceNote() {
    const cached = get("research-source").value === "cached_example";
    get("research-settings-summary").textContent = cached ? "缓存示例" : "联网发现";
    get("research-source-note").textContent = cached
      ? "使用本机已有候选与 PDF 缓存；缺少的全文可能不可用。"
      : "从 arXiv 搜索并获取论文。";
  }
  function pinnedArxiv(source) {
    const id = String(source.arxiv_id || "");
    return /^(\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*\/\d{7})$/.test(id)
      && Number.isInteger(source.version) && source.version > 0 ? `${id}v${source.version}` : null;
  }
  function evidence(data) {
    const checked = data.state?.evidence_answer;
    const signature = JSON.stringify([checked, active]);
    if (signature === answerSignature) return;
    answerSignature = signature;
    get("research-citations").replaceChildren();
    get("research-answer").textContent = checked?.answer || (active ? "正在研究，回答将在引用核对后显示。" : "本次没有通过引用核对的回答，可展开执行详情查看原因。");
    if (checked) {
      get("research-citations").append(make("p", `${checked.citations?.length ? "引用可追溯性：已检查编号与原文摘录匹配。" : "引用可追溯性：本回答没有正文引用。"}结论支持度：未经自动语义核验，请结合上下文人工判断。`, "muted"));
      if (checked.insufficient_evidence) get("research-citations").append(make("p", "此回答证据不足；仅覆盖已取得的正文，请查看回答中的材料范围与缺口。", "muted"));
    }
    for (const citation of checked?.citations || []) {
      const detail = make("details");
      const source = citation.source || {};
      const pageNumbers = [...new Set((citation.sources || []).map(item => item.page_number).filter(page => Number.isInteger(page) && page > 0))];
      const pinned = pinnedArxiv(source);
      detail.append(make("summary", `[${citation.evidence_id}] ${source.title || source.document_id || "来源"}${pinned ? ` · ${pinned}` : ""} · 第 ${pageNumbers.join("、") || "未知"} 页`),
        make("p", "原文摘录"), make("blockquote", citation.quote));
      if (pinned && pageNumbers.length) {
        const link = make("a", "查看固定版本 PDF 原文 ↗");
        link.href = `https://arxiv.org/pdf/${pinned}#page=${pageNumbers[0]}`;
        link.target = "_blank"; link.rel = "noopener noreferrer";
        detail.append(link);
      }
      get("research-citations").append(detail);
    }
  }
  function candidates(data) {
    const rows = data.state?.candidates || [];
    const selections = data.state?.selections || {};
    const signature = JSON.stringify([rows, selections, data.job.request.target_papers, active]);
    if (signature === candidatesSignature) return;
    candidatesSignature = signature;
    const container = get("research-candidates");
    container.replaceChildren();
    const ready = rows.filter(row => row.status === "fulltext_ready").length;
    container.append(make("p", `实际入库正文 ${ready} / 目标 ${data.job.request.target_papers} 篇${active ? "（研究进行中）" : ready < data.job.request.target_papers ? "；未达到目标，回答仅限现有正文。" : ""}`, "muted"));
    for (const row of rows) {
      const card = make("div", undefined, "candidate");
      card.append(make("h3", row.title || row.arxiv_id), make("p", `${row.arxiv_id}v${row.version} · ${materialLabels[row.status] || row.status}${row.page_count ? ` · ${row.page_count} 页` : ""}`));
      if (row.fetch_status && row.fetch_status !== "downloaded") card.append(make("p", `全文获取：${row.fetch_status}`, "muted"));
      const selection = selections[row.arxiv_id];
      if (selection) card.append(make("p", `${selection.selected ? "选入" : "排除"}：${selection.reason}`, "muted"));
      if (row.last_error) card.append(make("p", `记录：${row.last_error}`, "muted"));
      const id = String(row.arxiv_id);
      if (/^(\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*\/\d{7})$/.test(id) && Number.isInteger(row.version) && row.version > 0) {
        const link = make("a", "查看 arXiv 来源 ↗");
        link.href = `https://arxiv.org/abs/${id}v${row.version}`; link.target = "_blank"; link.rel = "noopener noreferrer";
        card.append(link);
      }
      container.append(card);
    }
    if (!rows.length) container.append(make("p", "尚未发现候选。"));
  }
  function executionEvents(data) {
    const kinds = new Set(["model_decision", "tool_observation", "host_feedback", "execution_stopped"]);
    const events = (data.state?.agent_events || []).filter(event => kinds.has(event.type));
    if (!events.length) return false; // Older saved runs retain the tool-only trace below.
    get("research-events").replaceChildren(...events.map(event => {
      const detail = event.data || {}, node = make("li", undefined, "event");
      const action = detail.decision || {}, args = action.arguments || {};
      const name = tools[action.tool || detail.tool] || action.tool || detail.tool || (event.type === "host_feedback" ? "动作校验" : "结束");
      const label = event.type === "model_decision" ? `模型决策${detail.simulated ? "（模拟策略）" : ""}`
        : event.type === "tool_observation" ? "工具反馈" : event.type === "host_feedback" ? "宿主反馈" : "停止原因";
      node.append(make("strong", `${event.sequence}. ${label} · ${event.type === "execution_stopped" ? reasons[detail.reason] || detail.reason : name}`));
      if (event.type === "model_decision") {
        if (action.purpose) node.append(make("p", action.purpose));
        const target = [args.query, args.arxiv_id || args.document_id,
          args.start_page != null ? `第 ${args.start_page}${args.end_page != null ? `–${args.end_page}` : ""} 页` : ""].filter(Boolean);
        if (target.length) node.append(make("p", target.join(" · ")));
        for (const selection of action.updates?.selections || []) node.append(make("p", `${selection.arxiv_id} · ${selection.selected ? "选入" : "排除"}：${selection.reason}`));
      } else if (event.type === "execution_stopped") {
        if (detail.summary) node.append(make("p", detail.summary));
        for (const row of detail.materials || []) node.append(make("p", `${row.arxiv_id} · ${materialLabels[row.status] || row.status}`, "muted"));
      } else {
        node.append(make("p", detail.success ? "完成" : event.type === "host_feedback" ? "动作未执行" : "失败"));
        const summary = detail.summary || {};
        if (summary.candidates_count != null) node.append(make("p", `返回 ${summary.candidates_count} 篇候选：${(summary.candidate_ids || []).join("、")}`));
        if (summary.results_count != null || summary.pages_count != null) node.append(make("p",
          `${summary.pages_count != null ? `读取 ${summary.pages_count} 页` : `检索命中 ${summary.results_count} 条`} · 正文证据 ${(summary.evidence_ids || []).join("、") || "无"}`));
        if (summary.status) node.append(make("p", materialLabels[summary.status] || summary.status));
        if (detail.error) node.append(make("p", `${detail.error.code}：${detail.error.message}`, "event-error"));
        const recorded = (data.events || []).find(row => row.event_id === detail.event_id);
        if (recorded?.duration_ms != null) node.append(make("small", `${(recorded.duration_ms / 1000).toFixed(2)} 秒`));
        for (const warning of recorded?.warnings || []) node.append(make("p", warning, "muted"));
      }
      return node;
    }));
    return true;
  }
  function render(data) {
    const restoreForm = !latest || latest.job.task_id !== data.job.task_id;
    latest = data; jobId = data.job.task_id;
    if (pending && pending.request_id === data.job.request.request_id) {
      pending = null; try { localStorage.removeItem(pendingKey); } catch (_) {}
    }
    get("research-dialogue-select").value = jobId;
    active = activeStatuses.has(data.job.status);
    if (restoreForm) {
      const saved = data.job.request;
      get("research-topic").value = saved.topic; get("research-start").value = saved.start_date; get("research-end").value = saved.end_date;
      get("research-questions").value = saved.questions.join("\n"); get("research-papers").value = String(saved.target_papers);
      get("research-source").value = saved.source_mode; sourceNote();
      get("research-editor").open = false;
    }
    get("research-result").hidden = false;
    get("research-run-title").textContent = data.job.request.topic;
    get("research-run-id").textContent = `任务 ${jobId} · ${data.job.request.start_date} 至 ${data.job.request.end_date}（UTC，截止日不含）`;
    const state = data.state;
    get("research-status").textContent = active ? (data.job.stop_requested ? "正在停止后续步骤" : "研究进行中")
      : reasons[state?.stop_reason] || ({failed: "执行发生错误", interrupted: "执行状态未确认", finished: "任务已结束"}[data.job.status]) || data.job.status;
    const usage = state?.usage || {tokens: 0, cost_usd: 0, unknown_usage_calls: 0};
    const limits = state?.tool_limits || {};
    const elapsed = active ? Math.max(0, Math.floor((Date.now() - Date.parse(data.job.created_at)) / 1000)) : Math.round(state?.elapsed_seconds || 0);
    get("research-metrics").replaceChildren(...[
      ["步骤", `${state?.steps || 0} / 12`], ["获取 PDF", `${limits.downloaded_papers || 0} / ${data.job.request.target_papers}`],
      ["累计 token", String(usage.tokens)], ["初始研究估算", `$${Number(usage.cost_usd).toFixed(6)} / $${Number(data.budget?.max_cost_usd ?? data.job.request.max_cost_usd ?? .50).toFixed(2)}`],
      ["初始研究剩余", `$${Number(data.budget?.remaining_usd ?? 0).toFixed(6)}`],
      ["已计入或预留", `$${Number(data.budget?.charged_or_reserved_usd ?? usage.cost_usd).toFixed(6)}${data.budget?.usage_unconfirmed ? "（结果未确认）" : ""}`],
      ["耗时", `${elapsed} 秒`]
    ].map(([label, value]) => { const node = make("div", undefined, "metric"); node.append(make("span", label), make("strong", value)); return node; }));
    get("research-current").textContent = data.job.stop_requested && active ? "已请求停止。当前步骤可能仍在执行，结束后不再发起新步骤。"
      : state?.pending?.phase === "tool" ? `${tools[state.pending.tool] || state.pending.tool}…`
      : state?.pending?.phase === "decision" && active ? "正在分析文献并选择下一步…"
      : active ? "正在准备研究…" : state?.evidence_answer ? "研究已结束，可查看回答与参考论文。" : "研究已停止，可展开执行详情查看原因。";
    get("research-run-summary").textContent = state?.summary || "";
    const error = [data.job.error, state?.last_error?.message, usage.unknown_usage_calls ? "存在用量未知的调用，费用按预留保守计入，请核对 API 用量。" : ""].filter(Boolean).join("\n");
    get("research-error").hidden = !error; get("research-error").textContent = error;
    if (!executionEvents(data)) {
      get("research-events").replaceChildren(...data.events.map(event => {
        const node = make("li", undefined, "event");
        const status = event.status === "started" ? "执行中" : event.success ? "完成" : "失败";
        node.append(make("strong", `${event.event_id}. ${tools[event.tool] || event.tool} · ${status}`));
        if (event.purpose) node.append(make("p", event.purpose));
        if (event.duration_ms != null) node.append(make("small", `${(event.duration_ms / 1000).toFixed(2)} 秒`));
        if (event.error) node.append(make("p", `${event.error.code}：${event.error.message}`, "event-error"));
        for (const warning of event.warnings || []) node.append(make("p", warning, "muted"));
        return node;
      }));
      if (!data.events.length) get("research-events").append(make("li", "等待第一个工具动作…", "muted"));
    }
    get("research-gaps").textContent = state?.gaps?.length ? `尚缺信息：${state.gaps.join("；")}` : "";
    evidence(data); candidates(data); controls();
    globalThis.paperResearchView?.(data, {keyReady, setBusy(value) { discussionBusy = value; controls(); }});
  }
  async function poll() {
    clearTimeout(timer);
    const expected = jobId;
    try {
      const data = await request(`/api/research/jobs/${encodeURIComponent(expected)}`);
      if (jobId !== expected) return;
      render(data);
      get("research-reconnect").hidden = true;
      if (active) timer = setTimeout(poll, 1500);
    } catch (error) {
      if (jobId !== expected) return;
      if (error.status === 410 || error.status === 404) {
        clearView(); await refreshDialogues(); note(error.message, true); return;
      }
      get("research-result").hidden = false;
      get("research-current").textContent = "进度连接中断，后台任务可能仍在运行；不会重新启动任务。";
      get("research-error").hidden = false; get("research-error").textContent = error.message;
      get("research-reconnect").hidden = false;
    }
  }
  get("research-source").addEventListener("change", sourceNote);
  get("research-form").addEventListener("invalid", event => {
    get("research-editor").open = true;
  }, true);
  get("research-example").addEventListener("click", () => {
    get("research-topic").value = "retrieval augmented generation";
    get("research-start").value = "2023-01-01"; get("research-end").value = "2024-01-01";
    get("research-questions").value = "RAGTruth 如何构建和标注幻觉数据？";
    get("research-papers").value = "1";
    get("research-source").value = available ? "cached_example" : "online"; sourceNote();
  });
  get("research-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (active || submitting || discussionBusy || !keyReady) return;
    const questions = get("research-questions").value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const payload = pending || {topic: get("research-topic").value.trim(), start_date: get("research-start").value, end_date: get("research-end").value,
      questions, target_papers: Number(get("research-papers").value), source_mode: get("research-source").value};
    if (!payload.topic || payload.start_date >= payload.end_date || !questions.length || questions.length > 8 || questions.some(q => q.length > 1000)) {
      note("请填写主题、有效日期范围及 1–8 个问题，每个问题最多 1000 字。", true); return;
    }
    if (!pending) pending = {...payload, request_id: `research-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`};
    try { localStorage.setItem(pendingKey, JSON.stringify(pending)); } catch (_) {}
    submitting = true; controls();
    try {
      const data = await request("/api/research/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pending)});
      pending = null; try { localStorage.removeItem(pendingKey); } catch (_) {}
      answerSignature = ""; candidatesSignature = ""; render(data);
      try { localStorage.setItem("paper-assistant-research-job", jobId); } catch (_) {}
      note("研究已启动，刷新页面可重新连接进度。");
      refreshDialogues();
      timer = setTimeout(poll, 300);
    } catch (error) {
      if (error.status >= 400 && error.status < 500) { pending = null; try { localStorage.removeItem(pendingKey); } catch (_) {} }
      note(error.message + (pending ? " 提交结果未确认；可显式重试本次提交，刷新不会自动重发。" : ""), true);
    }
    finally { submitting = false; controls(); }
  });
  get("research-stop").addEventListener("click", async () => {
    if (!active || !jobId) return;
    get("research-stop").disabled = true;
    try { render(await request(`/api/research/jobs/${encodeURIComponent(jobId)}/stop`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})); }
    catch (error) { note(error.message, true); controls(); }
  });
  get("research-reconnect").addEventListener("click", poll);
  get("research-download").addEventListener("click", () => {
    if (!latest) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(latest, null, 2)], {type: "application/json;charset=utf-8"}));
    const link = make("a"); link.href = url; link.download = `${jobId}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  async function refreshDialogues() {
    try {
      const data = await request("/api/research/jobs");
      const empty = make("option", "新建研究对话"); empty.value = "";
      get("research-dialogue-select").replaceChildren(empty, ...data.dialogues.map(job => {
        const option = make("option", `${job.request.topic} · ${job.request.questions.join("；")}`);
        option.value = job.task_id; return option;
      }));
      get("research-dialogue-select").value = jobId || "";
      get("research-dialogue-status").textContent = data.unreadable_dialogue_ids?.length
        ? "部分本地记录暂不可读，请保留文件并检查终端。" : "各研究对话独立保存论文、历史与用量；切换不会调用模型。";
    } catch (error) { get("research-dialogue-status").textContent = error.message; }
  }
  get("research-dialogue-refresh").addEventListener("click", refreshDialogues);
  get("research-dialogue-select").addEventListener("change", async () => {
    if (submitting || discussionBusy || pending || deleteTarget) return;
    const selected = get("research-dialogue-select").value;
    if (!selected) { get("research-dialogue-select").value = jobId || ""; return; }
    clearTimeout(timer);
    jobId = selected; controls();
    try { localStorage.setItem("paper-assistant-research-job", jobId); } catch (_) {}
    await poll();
  });
  function clearView() {
    deleteTarget = null; get("research-delete-confirmation").hidden = true;
    clearTimeout(timer); jobId = null; latest = null; active = false;
    answerSignature = ""; candidatesSignature = "";
    for (const id of ["research-topic", "research-questions", "research-start", "research-end"]) get(id).value = "";
    get("research-source").value = "online"; get("research-papers").value = "2"; sourceNote();
    get("research-editor").open = true; get("research-result").hidden = true;
    get("research-dialogue-select").value = "";
    globalThis.paperResearchClear?.();
    try { localStorage.removeItem("paper-assistant-research-job"); } catch (_) {}
    controls();
  }
  get("research-dialogue-new").addEventListener("click", () => {
    if (active || submitting || discussionBusy || pending || deleteTarget) return;
    clearView(); note("请填写新的研究领域、研究问题和日期范围，再开始研究。");
  });
  get("research-dialogue-delete").addEventListener("click", () => {
    if (!jobId || latest?.job.task_id !== jobId || active || discussionBusy || submitting || pending || deleteTarget) return;
    deleteTarget = jobId;
    get("research-delete-scope").textContent = `永久删除“${latest.job.request.topic}”的研究问题、全部历史追问线程、专属索引、回答及执行记录。其他研究对话和公共模型、PDF 缓存保留。运行中的研究或追问须先停止并等待结束。此操作无法撤销。`;
    get("research-delete-confirmation").hidden = false; controls();
  });
  get("research-delete-cancel").addEventListener("click", () => {
    if (deleting) return;
    deleteTarget = null; get("research-delete-confirmation").hidden = true; controls();
  });
  get("research-delete-confirm").addEventListener("click", async () => {
    if (!deleteTarget || deleting || active || discussionBusy) return;
    const target = deleteTarget; deleting = true; controls();
    try {
      await request(`/api/research/jobs/${encodeURIComponent(target)}/delete`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({confirmed: true})});
      deleteTarget = null; get("research-delete-confirmation").hidden = true;
      try {
        localStorage.removeItem(`paper-assistant-chat-pending:${target}`);
        localStorage.removeItem(`paper-assistant-chat-action:${target}`);
      } catch (_) {}
      if (jobId === target) clearView();
      await refreshDialogues(); note("研究对话及其专属数据已永久删除。");
    } catch (error) { note(error.message, true); }
    finally { deleting = false; controls(); }
  });
  async function init() {
    try {
      try { pending = JSON.parse(localStorage.getItem(pendingKey) || "null"); } catch (_) {}
      if (pending) {
        get("research-topic").value = pending.topic; get("research-start").value = pending.start_date; get("research-end").value = pending.end_date;
        get("research-questions").value = pending.questions.join("\n"); get("research-papers").value = String(pending.target_papers);
        get("research-source").value = pending.source_mode;
      }
      const status = await request("/api/research/status"); keyReady = status.key_ready; available = status.cached_example_ready;
      get("research-cache-option").disabled = !available;
      note(keyReady ? "研究服务已就绪。"
        : "请在项目根目录 .env 中填写 OPENAI_API_KEY 后刷新页面。页面不会接收或保存密钥。", !keyReady);
      controls(); sourceNote();
      refreshDialogues();
      try { jobId = localStorage.getItem("paper-assistant-research-job"); } catch (_) {}
      if (!jobId && status.active_job_id) jobId = status.active_job_id;
      if (jobId && /^web-\d{8}T\d{6}-[a-f0-9]{12}$/.test(jobId)) await poll();
    } catch (_) { note("无法连接研究服务，请确认终端中的页面服务仍在运行。", true); controls(); }
  }
  init();
})();
