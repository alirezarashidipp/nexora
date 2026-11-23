const MAX_BAR_ITEMS = 20;
const MAX_PIE_ITEMS = 8;

function chartElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderBarChart(data) {
  const chart = chartElement("div", "bar-chart");
  const maximum = Math.max(...data.items.map(item => item.value));
  const format = new Intl.NumberFormat();
  for (const item of data.items) {
    const row = chartElement("div", "bar-row");
    const track = chartElement("span", "bar-track");
    const fill = chartElement("span", "bar-fill");
    fill.style.width = `${100 * item.value / maximum}%`;
    track.append(fill);
    row.append(chartElement("span", "bar-label", item.label), track,
      chartElement("strong", "bar-value", format.format(item.value)));
    chart.append(row);
  }
  return chart;
}

function inspectChartRows(rows) {
  if (!Array.isArray(rows) || rows.length < 2 || rows.length > MAX_BAR_ITEMS) {
    return null;
  }

  const columns = Object.keys(rows[0] || {});
  const sameShape = columns.length === 2 && rows.every((row) => (
    row
    && typeof row === "object"
    && !Array.isArray(row)
    && Object.keys(row).length === 2
    && columns.every((column) => Object.hasOwn(row, column))
  ));
  if (!sameShape) {
    return null;
  }

  const labelColumns = columns.filter((column) => rows.every((row) => (
    typeof row[column] === "string" && row[column].trim()
  )));
  const numberColumns = columns.filter((column) => rows.every((row) => (
    typeof row[column] === "number" && Number.isFinite(row[column])
  )));
  if (labelColumns.length !== 1 || numberColumns.length !== 1) {
    return null;
  }

  const labelKey = labelColumns[0];
  const valueKey = numberColumns[0];
  const items = rows.map((row) => ({
    label: row[labelKey].trim(),
    value: row[valueKey],
  }));
  if (
    new Set(items.map((item) => item.label)).size !== items.length
    || items.some((item) => item.value < 0)
    || !items.some((item) => item.value > 0)
  ) {
    return null;
  }

  return {
    labelKey,
    valueKey,
    items,
    types: [
      "bar",
      ...(items.length <= MAX_PIE_ITEMS
        && items.every((item) => item.value > 0)
        && Number.isFinite(items.reduce((sum, item) => sum + item.value, 0))
        ? ["pie"]
        : []),
    ],
  };
}

if (typeof window !== "undefined") {
  window.inspectChartRows = inspectChartRows;
}

if (typeof module !== "undefined") {
  module.exports = {inspectChartRows};
}
