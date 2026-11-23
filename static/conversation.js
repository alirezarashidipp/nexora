"use strict";

const byId = id => document.getElementById(id);
const threadKey = "nexora.conversation.id";
let threadId = sessionStorage.getItem(threadKey) || crypto.randomUUID();
sessionStorage.setItem(threadKey, threadId);
let state = {stage: "new"};
let originalDraft = "";
let followupDraft = "";
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
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "dashboard";
    input.checked = item.dashboard_id === state.selected;
    const select = () => sendTurn("select", state.choosing_followup ? followupDraft : "", item.dashboard_id);
    input.addEventListener("change", select);
    label.append(input, element("strong", item.title), element("small", item.description));
    if (item.url_status === "configured") {
      const link = element("a", "Open dashboard ↗");
      link.href = item.dashboard_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.addEventListener("click", select);
      label.append(link);
    }
    list.append(label);
  }
  if (!items.length) list.append(element("p", "No matching dashboard found."));
}

function renderEvidence(evidence) {
  byId("insight-answer").textContent = evidence.answer;
  const filters = state.filters;
  const applied = [...filters.cities, filters.team, filters.role];
  if (filters.start_date) applied.push("From " + filters.start_date);
  if (filters.end_date) applied.push("Through " + filters.end_date);
  byId("scope").textContent = applied.filter(Boolean).join(" · ");
  byId("definition").textContent = evidence.definition;
  byId("sources").textContent = "Sources · " + evidence.sources.join(", ");
  byId("query").textContent = evidence.sql;
  byId("parameters").textContent = "Parameters · " + JSON.stringify(evidence.parameters);
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
  byId("discovery").hidden = fresh || state.stage === "answer" || (Boolean(state.filters?.metric) && !state.choosing_followup);
  byId("answer-here").hidden = Boolean(state.choosing_followup);
  if (!byId("discovery").hidden) renderMatches(state.dashboards);
  byId("insight").hidden = !state.evidence;
  if (state.evidence) renderEvidence(state.evidence);
  byId("notice").hidden = !state.notice;
  byId("notice").textContent = state.notice || "";
  byId("suggestions").hidden = !fresh;
  byId("recent").hidden = fresh;
  byId("history").replaceChildren(...(state.history || []).map(q => element("li", q)));
  byId("composer-label").textContent = fresh ? "Your question" : "Continue the conversation";
  byId("message").placeholder = fresh ? "Ask about your offices, workforce or portfolio" : "Ask a follow-up or clarify your question";
  byId("send").textContent = fresh ? "Find dashboards" : "Send follow-up";
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
        if (event.type === "STATE_SNAPSHOT") renderState(event.snapshot);
        if (event.type === "RUN_ERROR") throw new Error(event.message);
        if (event.type === "RUN_FINISHED") finished = true;
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
  if (!finished) throw new Error("The connection ended before this turn finished. Please retry.");
}

async function sendTurn(action, text = "", dashboard = "") {
  if (busy) return;
  busy = true;
  const signature = JSON.stringify([threadId, action, text, dashboard]);
  const runId = retry?.signature === signature ? retry.runId : crypto.randomUUID();
  retry = {signature, runId};
  byId("run-status").textContent = action === "restore" ? "" : "Working…";
  const controls = document.querySelectorAll(".conversation button, .conversation input, #message");
  controls.forEach(control => { control.disabled = true; });
  try {
    const payload = {
      threadId, runId, state: {}, tools: [], context: [],
      messages: text ? [{id: crypto.randomUUID(), role: "user", content: text}] : [],
      forwardedProps: {action, dashboard},
    };
    const response = await fetch("/ag-ui", {method: "POST",
      headers: {"Content-Type": "application/json", Accept: "text/event-stream"}, body: JSON.stringify(payload)});
    if (!response.ok) throw new Error("The server could not accept this turn.");
    await readEvents(response);
    retry = null;
    if (action !== "restore" && action !== "select") byId("message").value = "";
    byId("run-status").textContent = "";
  } catch (error) {
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
    originalDraft = text;
    sendTurn("discover", text);
  } else {
    followupDraft = text;
    sendTurn("followup", text);
  }
});
byId("answer-here").addEventListener("click", () => {
  const original = originalDraft || state.original_question || byId("message").value.trim();
  if (state.needs_original && !original) {
    byId("run-status").textContent = "Re-enter the original question, then choose Answer Here.";
    byId("message").focus();
    return;
  }
  sendTurn("answer_here", original);
});
byId("new-thread").addEventListener("click", () => {
  threadId = crypto.randomUUID();
  sessionStorage.setItem(threadKey, threadId);
  originalDraft = "";
  followupDraft = "";
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
