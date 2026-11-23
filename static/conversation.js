"use strict";

const byId = id => document.getElementById(id);
const threadKey = "nexora.conversation.id";
const tokenKey = "nexora.conversation.original";
let threadId = sessionStorage.getItem(threadKey) || crypto.randomUUID();
sessionStorage.setItem(threadKey, threadId);
let state = {stage: "new"};
let originalDraft = "";
let originalToken = sessionStorage.getItem(tokenKey) || "";
let pendingDraft = "";
let busy = false;
let retry = null;

function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function renderMatches(items) {
  const list = byId("matches");
  list.replaceChildren();
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "dashboard-match";
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "dashboard";
    input.checked = item.dashboard_id === state.selected;
    const select = () => sendTurn("select", "", item.dashboard_id);
    input.addEventListener("change", select);
    const coverage = element("span", item.coverage === "exact" ? "Exact answer" : "Related information");
    coverage.className = "coverage " + item.coverage;
    label.append(input, element("strong", item.title), coverage);
    card.append(label, element("p", item.reason));
    if (item.url_status === "configured") {
      const link = element("a", "Open dashboard ↗");
      link.href = item.dashboard_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.addEventListener("click", () => { if (!input.checked) select(); });
      card.append(link);
    } else {
      card.append(element("small", item.url_status === "placeholder" ? "Dashboard link not configured" : "Dashboard link unavailable"));
    }
    list.append(card);
  }
  if (!items.length) {
    const empty = state.discovery_mode === "invalid_ranking" ? "The model’s ranking could not be verified."
      : "No dashboard match found.";
    list.append(element("p", empty + " Choose Answer Here to investigate."));
  }
}

function renderEvidence(evidence) {
  byId("insight-answer").textContent = evidence.answer;
  const filters = state.filters;
  const applied = [...(filters.cities || []), filters.team, filters.role];
  for (const rule of filters.filters || []) {
    applied.push(rule.column.split(".")[1] + " " + rule.operator + " " + rule.values.join(", "));
  }
  if (filters.start_date) applied.push("From " + filters.start_date);
  if (filters.end_date) applied.push("Through " + filters.end_date);
  byId("scope").textContent = applied.filter(Boolean).join(" · ");
  byId("definition").textContent = evidence.definition;
  byId("sources").textContent = "Sources · " + evidence.sources.join(", ");
  byId("query").textContent = evidence.sql;
  byId("parameters").textContent = "Parameters · " + JSON.stringify(evidence.parameters);
  const chart = inspectChartRows(evidence.rows);
  const chartPanel = byId("answer-chart");
  chartPanel.hidden = !chart;
  byId("answer-bars").replaceChildren();
  if (chart) {
    const measure = evidence.unit || chart.valueKey.replaceAll("_", " ");
    byId("chart-caption").textContent = measure + " by " + chart.labelKey.replaceAll("_", " ");
    byId("answer-bars").append(renderBarChart(chart));
  } else {
    chartPanel.open = false;
  }
  const table = byId("evidence-rows");
  table.replaceChildren();
  if (!evidence.rows.length) {
    table.createCaption().textContent = "No matching records";
    return;
  }
  const columns = Object.keys(evidence.rows[0]);
  const header = table.createTHead().insertRow();
  for (const name of columns) header.append(element("th", name));
  const body = table.createTBody();
  for (const row of evidence.rows) {
    const line = body.insertRow();
    for (const name of columns) line.insertCell().textContent = row[name] ?? "—";
  }
}

function renderState(snapshot) {
  state = snapshot;
  const fresh = state.stage === "new";
  const original = state.original_question || originalDraft;
  byId("original").textContent = original;
  byId("original").hidden = fresh || !original;
  const needsDetail = state.stage === "discover" && Boolean(state.discovery_clarification);
  byId("discovery").hidden = state.stage !== "discover" || needsDetail;
  byId("conversation-form").hidden = state.stage === "discover" && !needsDetail;
  if (!byId("discovery").hidden) {
    const methods = {semantic: "Semantic match", keyword_fallback: "Keyword fallback · language service unavailable",
      invalid_ranking: "Dashboard ranking unavailable"};
    byId("match-method").textContent = methods[state.discovery_mode] || "";
    renderMatches(state.dashboards);
  }
  byId("insight").hidden = !state.evidence;
  if (state.evidence) renderEvidence(state.evidence);
  byId("notice").hidden = !state.notice;
  byId("notice").textContent = state.notice || "";
  byId("suggestions").hidden = !fresh;
  byId("recent").hidden = fresh;
  byId("history").replaceChildren(...(state.history || []).map(q => element("li", q)));
  byId("composer-label").textContent = needsDetail ? state.discovery_clarification : (fresh ? "Your question" : "Continue the conversation");
  byId("message").placeholder = fresh ? "Ask about your offices, workforce or portfolio" : "Ask a follow-up or clarify your question";
  byId("send").textContent = needsDetail ? "Answer Here" : (fresh ? "Find dashboards" : "Send follow-up");
}

async function readEvents(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let finished = false;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    buffer = (buffer + value).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (frame.startsWith("data:")) {
        const event = JSON.parse(frame.slice(5));
        if (event.type === "CUSTOM" && event.name === "original_question_token") {
          originalToken = event.value;
          try { sessionStorage.setItem(tokenKey, originalToken); } catch { /* The current page still has the token. */ }
        }
        if (event.type === "STEP_STARTED") {
          const labels = {discover_dashboards: "Finding dashboards…", select_sources: "Selecting data sources…",
            plan_query: "Planning the analysis…", query_sources: "Reading evidence…"};
          if (labels[event.stepName]) byId("run-status").textContent = labels[event.stepName];
        }
        if (event.type === "STATE_SNAPSHOT") renderState(event.snapshot);
        if (event.type === "RUN_ERROR") throw new Error(event.message);
        if (event.type === "RUN_FINISHED") finished = true;
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
  if (!finished) throw new Error("The connection ended before this turn finished. Please retry.");
}

async function sendTurn(action, text = "", dashboard = "", clarification = "") {
  if (busy) return;
  busy = true;
  const signature = JSON.stringify([threadId, action, text, dashboard, clarification]);
  const runId = retry?.signature === signature ? retry.runId : crypto.randomUUID();
  const previousState = state;
  retry = {signature, runId};
  byId("run-status").textContent = action === "restore" ? "" : "Working…";
  const controls = document.querySelectorAll(".conversation button, .conversation input, #message");
  controls.forEach(control => { control.disabled = true; });
  try {
    const payload = {
      threadId, runId, state: action === "select" ? {selected: dashboard} : {}, tools: [], context: [],
      messages: text ? [{id: crypto.randomUUID(), role: "user", content: text}] : [],
      forwardedProps: {action},
    };
    if (originalToken && ((action === "answer_here" && !text) || action === "followup")) {
      payload.forwardedProps.original_token = originalToken;
    }
    if (clarification) payload.forwardedProps.clarification = clarification;
    if (action === "followup" && state.needs_prior_question) payload.forwardedProps.prior_question = pendingDraft;
    const response = await fetch("/ag-ui", {method: "POST",
      headers: {"Content-Type": "application/json", Accept: "text/event-stream"}, body: JSON.stringify(payload)});
    if (!response.ok) throw new Error("The server could not accept this turn.");
    await readEvents(response);
    if (action === "followup" || action === "answer_here") {
      pendingDraft = state.needs_prior_question ? text : "";
    }
    retry = null;
    if (action !== "restore" && action !== "select") byId("message").value = "";
    byId("run-status").textContent = "";
  } catch (error) {
    renderState(previousState);
    byId("run-status").textContent = error.message;
  } finally {
    busy = false;
    controls.forEach(control => { control.disabled = false; });
  }
}

byId("conversation-form").addEventListener("submit", event => {
  event.preventDefault();
  const text = byId("message").value.trim();
  if (!text || busy) return;
  if (state.stage === "new") {
    originalToken = "";
    sessionStorage.removeItem(tokenKey);
    originalDraft = text;
    sendTurn("discover", text);
  } else if (state.stage === "discover") {
    if (state.discovery_clarification) sendTurn("answer_here", originalDraft || state.original_question, "", text);
    else sendTurn("answer_here", text);
  } else {
    sendTurn("followup", text);
  }
});
byId("answer-here").addEventListener("click", () => {
  const original = originalDraft || state.original_question || byId("message").value.trim();
  if (state.needs_original && !original && !originalToken) {
    byId("run-status").textContent = "Re-enter the original question, then choose Answer Here.";
    byId("conversation-form").hidden = false;
    byId("composer-label").textContent = "Original question";
    byId("send").textContent = "Answer Here";
    byId("message").focus();
    return;
  }
  sendTurn("answer_here", original);
});
byId("new-thread").addEventListener("click", () => {
  threadId = crypto.randomUUID();
  sessionStorage.setItem(threadKey, threadId);
  originalDraft = "";
  originalToken = "";
  sessionStorage.removeItem(tokenKey);
  pendingDraft = "";
  retry = null;
  byId("message").value = "";
  byId("run-status").textContent = "";
  renderState({stage: "new"});
  byId("message").focus();
});

async function start() {
  await sendTurn("restore");
  const response = await fetch("/conversation/examples");
  if (!response.ok) return;
  for (const text of await response.json()) {
    const button = element("button", text);
    button.addEventListener("click", () => {
      byId("message").value = text;
      byId("conversation-form").requestSubmit();
    });
    byId("example-list").append(button);
  }
}
start().catch(() => { byId("run-status").textContent = "Could not load the suggested questions."; });
