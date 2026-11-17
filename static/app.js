const form = document.querySelector("#ask-form");
const question = document.querySelector("#question");
const submit = document.querySelector("#submit");
const buttonLabel = document.querySelector(".button-label");
const result = document.querySelector("#result");
const resultStatus = document.querySelector("#result-status");
const normalized = document.querySelector("#normalized");
const answer = document.querySelector("#answer");
const debug = document.querySelector("#debug");
const sql = document.querySelector("#sql");
const rows = document.querySelector("#rows");
const chartActions = document.querySelector("#chart-actions");
const chartPanel = document.querySelector("#chart-panel");
const chartTitle = document.querySelector("#chart-title");
const chartOutput = document.querySelector("#chart-output");
const chartButtons = [...document.querySelectorAll("[data-chart]")];
const steps = [...document.querySelectorAll(".workflow li")];
const schemaFields = document.querySelector("#schema-fields");
const examples = document.querySelector("#examples");
const viewSql = document.querySelector("#view-sql");
const workspaceSearch = document.querySelector("#workspace-search");
const fieldSearch = document.querySelector("#field-search");
const fieldCount = document.querySelector("#field-count");
const fieldsEmpty = document.querySelector("#fields-empty");
const examplesEmpty = document.querySelector("#examples-empty");
const viewButtons = [...document.querySelectorAll("[data-view]")];
const chartColors = Array.from(
  {length: 8},
  (_, index) => `var(--color-chart-${index + 1})`,
);
const numberFormat = new Intl.NumberFormat();
let chartData = null;

function setWorkflow(status) {
  const reviewOnly = status === "incomplete" || status === "invalid";
  steps.forEach((step, index) => {
    const isActive = status === "loading" && index === 0;
    step.classList.remove("is-current");
    step.classList.toggle("is-active", isActive);
    step.classList.toggle(
      "is-complete",
      status === "valid" || (reviewOnly && index === 0),
    );
    if (isActive) {
      step.setAttribute("aria-current", "step");
    } else {
      step.removeAttribute("aria-current");
    }
  });
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function filterWorkspace() {
  const workspaceTerm = workspaceSearch.value.trim().toLowerCase();
  const fieldTerm = fieldSearch.value.trim().toLowerCase();
  const fields = [...schemaFields.children];
  fields.forEach((field) => {
    field.hidden = !field.dataset.search.includes(workspaceTerm)
      || !field.dataset.search.includes(fieldTerm);
  });
  const visibleFields = fields.filter((field) => !field.hidden).length;
  fieldCount.textContent = visibleFields === fields.length
    ? `${fields.length} fields`
    : `${visibleFields} of ${fields.length} fields`;
  fieldsEmpty.hidden = visibleFields > 0;

  const suggestions = [...examples.children];
  suggestions.forEach((button) => {
    button.hidden = !button.dataset.question.toLowerCase().includes(workspaceTerm);
  });
  const hasSuggestions = suggestions.some((button) => !button.hidden);
  examples.hidden = !hasSuggestions;
  examplesEmpty.hidden = hasSuggestions;
}

workspaceSearch.addEventListener("input", filterWorkspace);
fieldSearch.addEventListener("input", filterWorkspace);

viewButtons.forEach((button) => {
  button.addEventListener("click", () => {
    schemaFields.classList.toggle("is-list", button.dataset.view === "list");
    viewButtons.forEach((option) => {
      option.setAttribute("aria-pressed", String(option === button));
    });
  });
});

function updateNavigation() {
  const current = window.location.hash || "#workspace";
  document.querySelectorAll(".top-nav a").forEach((link) => {
    if (link.getAttribute("href") === current) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

window.addEventListener("hashchange", updateNavigation);
updateNavigation();
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey
      && !event.target.matches("input, textarea, select, [contenteditable]")) {
    event.preventDefault();
    workspaceSearch.focus();
  }
});

function setStatus(status) {
  const labels = {
    valid: "Validated",
    incomplete: "Needs details",
    invalid: "Out of scope",
    error: "Error",
  };
  resultStatus.className = `status status-${status}`;
  resultStatus.textContent = labels[status] || "Processing";
}

function chartElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) {
    element.textContent = text;
  }
  return element;
}

function resetChart() {
  chartData = null;
  chartActions.hidden = true;
  chartPanel.hidden = true;
  chartOutput.replaceChildren();
  chartButtons.forEach((button) => {
    button.hidden = false;
    button.setAttribute("aria-pressed", "false");
  });
}

function offerCharts(dataRows) {
  chartData = window.inspectChartRows(dataRows);
  if (!chartData) {
    return;
  }

  chartButtons.forEach((button) => {
    button.hidden = !chartData.types.includes(button.dataset.chart);
  });
  chartActions.hidden = false;
  renderChart("bar");
}

function renderBarChart() {
  const chart = chartElement("div", "bar-chart");
  const maximum = Math.max(...chartData.items.map((item) => item.value));

  chartData.items.forEach((item) => {
    const row = chartElement("div", "bar-row");
    const label = chartElement("span", "bar-label", item.label);
    const track = chartElement("span", "bar-track");
    const fill = chartElement("span", "bar-fill");
    const value = chartElement("strong", "bar-value", numberFormat.format(item.value));
    fill.style.width = `${(item.value / maximum) * 100}%`;
    track.append(fill);
    row.append(label, track, value);
    chart.append(row);
  });

  return chart;
}

function renderPieChart() {
  const total = chartData.items.reduce((sum, item) => sum + item.value, 0);
  let start = 0;
  const stops = chartData.items.map((item, index) => {
    const end = index === chartData.items.length - 1
      ? 100
      : start + (item.value / total) * 100;
    const stop = `${chartColors[index]} ${start}% ${end}%`;
    start = end;
    return stop;
  });
  const chart = chartElement("div", "pie-chart");
  const graphic = chartElement("div", "pie-graphic");
  const legend = chartElement("div", "pie-legend");
  graphic.style.background = `conic-gradient(${stops.join(", ")})`;
  graphic.setAttribute("aria-hidden", "true");
  legend.setAttribute("role", "list");

  chartData.items.forEach((item, index) => {
    const row = chartElement("div", "pie-legend-row");
    row.setAttribute("role", "listitem");
    const swatch = chartElement("span", "pie-swatch");
    const label = chartElement("span", "pie-label", item.label);
    const percent = Math.round((item.value / total) * 100);
    const value = chartElement(
      "strong",
      "pie-value",
      `${numberFormat.format(item.value)} · ${percent}%`,
    );
    swatch.style.background = chartColors[index];
    row.append(swatch, label, value);
    legend.append(row);
  });

  chart.append(graphic, legend);
  return chart;
}

function renderChart(type) {
  if (!chartData || !chartData.types.includes(type)) {
    return;
  }

  chartTitle.textContent = `${type === "bar" ? "Bar" : "Pie"} chart · ${chartData.valueKey} by ${chartData.labelKey}`;
  chartOutput.replaceChildren(type === "bar" ? renderBarChart() : renderPieChart());
  chartPanel.hidden = false;
  chartButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.chart === type));
  });
}

chartActions.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-chart]");
  if (button && !button.hidden) {
    renderChart(button.dataset.chart);
  }
});

examples.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-question]");
  if (button && examples.contains(button)) {
    question.setCustomValidity("");
    question.value = button.dataset.question;
    question.focus();
  }
});

schemaFields.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-field]");
  if (!button || question.disabled) {
    return;
  }
  const start = question.selectionStart;
  const end = question.selectionEnd;
  const before = question.value.slice(0, start);
  const after = question.value.slice(end);
  let insertion = button.dataset.field;
  if (before && !/\s$/.test(before)) {
    insertion = ` ${insertion}`;
  }
  if (after && !/^\s/.test(after)) {
    insertion += " ";
  }
  if (before.length + insertion.length + after.length > question.maxLength) {
    question.setCustomValidity("This field would exceed the question length limit.");
    question.reportValidity();
    return;
  }
  question.setCustomValidity("");
  question.focus();
  question.setRangeText(insertion, start, end, "end");
});

question.addEventListener("input", () => question.setCustomValidity(""));

viewSql.addEventListener("click", () => {
  debug.open = true;
  debug.scrollIntoView({behavior: "smooth", block: "start"});
});

question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submit.disabled = true;
  question.disabled = true;
  submit.classList.add("is-loading");
  form.setAttribute("aria-busy", "true");
  document.querySelectorAll("[data-question], [data-field]").forEach((button) => {
    button.disabled = true;
  });
  buttonLabel.textContent = "Exploring";
  viewSql.disabled = true;
  result.hidden = false;
  normalized.hidden = true;
  debug.hidden = true;
  resetChart();
  answer.className = "";
  answer.textContent = "Checking your question against the available fields...";
  setWorkflow("loading");
  setStatus("loading");

  try {
    const response = await fetch("/ask", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: question.value}),
    });
    const data = await response.json();

    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg).join(". ")
        : data.detail;
      throw new Error(detail || "The question could not be processed. Try again.");
    }

    answer.textContent = data.answer;
    setStatus(data.status);
    setWorkflow(data.status);

    if (data.normalized_question !== data.question) {
      normalized.textContent = `↳ Understood as: ${data.normalized_question}`;
      normalized.hidden = false;
    }

    debug.hidden = !data.sql;
    viewSql.disabled = !data.sql;
    sql.textContent = data.sql || "";
    rows.textContent = JSON.stringify(data.rows, null, 2);
    offerCharts(data.status === "valid" ? data.rows : []);
  } catch (error) {
    answer.className = "error";
    answer.textContent = error.message;
    setStatus("error");
    setWorkflow("error");
    viewSql.disabled = true;
  } finally {
    submit.disabled = false;
    question.disabled = false;
    submit.classList.remove("is-loading");
    form.setAttribute("aria-busy", "false");
    document.querySelectorAll("[data-question], [data-field]").forEach((button) => {
      button.disabled = false;
    });
    buttonLabel.textContent = "Explore question";
  }
});

async function loadSchema() {
  const response = await fetch("/schema");
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Could not load the available fields.");
  }

  document.title = "Insight Studio | HSBC";
  question.maxLength = data.max_question_length;
  document.querySelector("#dataset-name").textContent = data.table;

  data.columns.forEach((column) => {
    const field = document.createElement("article");
    field.className = "field";
    field.dataset.search = [column.name, column.type, column.description, column.possible_values]
      .join(" ").toLowerCase();
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const numeric = /INT|REAL|FLOA|DOUB|NUM|DEC/i.test(column.type);
    const declaredType = column.type.split(" ")[0].toLowerCase();
    const typeLabel = numeric ? "Number" : declaredType.replace(/^./, (letter) => letter.toUpperCase());
    const symbol = chartElement("span", "field-icon");
    symbol.append(icon(numeric ? "number" : "field"));
    const copy = chartElement("span", "field-copy");
    copy.append(
      chartElement("strong", "", column.name),
      chartElement("small", "", typeLabel),
    );
    const descriptionPanel = chartElement("div", "field-description");
    const description = document.createElement("p");
    description.textContent = column.description;
    const values = document.createElement("small");
    values.textContent = `Possible values: ${column.possible_values}`;
    summary.append(symbol, copy);
    descriptionPanel.append(description, values);
    details.append(summary, descriptionPanel);
    const add = chartElement("button", "field-add", "+");
    add.type = "button";
    add.dataset.field = column.name;
    add.setAttribute("aria-label", `Add ${column.name} to your question`);
    add.title = `Add ${column.name} to your question`;
    field.append(details, add);
    schemaFields.append(field);
  });

  data.examples.forEach((example, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.question = example;
    button.className = "example-card";
    const symbol = chartElement("span", "example-symbol");
    symbol.append(icon(["bars", "users", "pie"][index % 3]));
    const copy = chartElement("span", "example-copy");
    copy.append(chartElement("strong", "example-text", example));
    const arrow = chartElement("span", "example-arrow");
    arrow.append(icon("arrow"));
    button.append(symbol, copy, arrow);
    examples.append(button);
  });
  filterWorkspace();
}

loadSchema().catch((error) => {
  schemaFields.textContent = "Available fields could not be loaded.";
  document.querySelector("#dataset-name").textContent = "Unavailable";
  fieldSearch.disabled = true;
  examplesEmpty.textContent = "Suggested questions could not be loaded.";
  examplesEmpty.hidden = false;
  console.error(error);
});
