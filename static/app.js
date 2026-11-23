"use strict";

async function loadSampleQuestions() {
  const retryButton = byId("retry-samples");
  retryButton.disabled = true;
  try {
    const response = await fetch("/conversation/examples");
    if (!response.ok) throw new Error("Could not load sample questions.");
    const questions = await response.json();
    const list = byId("sample-list");
    list.replaceChildren();
    for (const question of questions) {
      const item = document.createElement("li");
      const button = element("button", question);
      button.type = "button";
      button.addEventListener("click", () => {
        if (busy || state.stage !== "new") return;
        byId("message").value = question;
        byId("message").focus();
        byId("conversation-form").requestSubmit(byId("send"));
      });
      item.append(button);
      list.append(item);
    }
    byId("sample-error").hidden = true;
  } catch {
    byId("sample-error").hidden = false;
  } finally {
    retryButton.disabled = false;
  }
}

byId("retry-samples").addEventListener("click", loadSampleQuestions);
loadSampleQuestions();

function renderDashboardList(dashboards) {
  const list = byId("dashboard-list");
  list.replaceChildren();
  for (const dashboard of dashboards) {
    const card = document.createElement("article");
    card.className = "dashboard-card";
    card.append(element("h2", dashboard.title), element("p", dashboard.description), dashboardDestination(dashboard));
    list.append(card);
  }
  if (!dashboards.length) list.append(element("p", "No dashboards are listed."));
}

function renderDatabaseList(databases) {
  const list = byId("database-list");
  list.replaceChildren();
  for (const database of databases) {
    const entry = document.createElement("article");
    entry.className = "database-entry";
    const header = document.createElement("header");
    header.append(element("h2", database.name), element("p", database.description));
    const tables = document.createElement("dl");
    for (const table of database.tables) {
      const columns = document.createElement("dd");
      for (const name of table.columns) columns.append(element("code", name));
      tables.append(element("dt", table.name), columns);
    }
    entry.append(header, tables);
    list.append(entry);
  }
  if (!databases.length) list.append(element("p", "No databases are listed."));
}

let catalogueStatus = "idle";

function showCatalogueStatus(message, failed = false) {
  for (const panel of document.querySelectorAll(".catalogue-panel")) {
    panel.querySelector(".catalogue-status").textContent = message;
    panel.querySelector(".catalogue-retry").hidden = !failed;
  }
}

async function loadCatalogue() {
  if (catalogueStatus === "loading" || catalogueStatus === "ready") return;
  catalogueStatus = "loading";
  showCatalogueStatus("Loading catalogue…");
  try {
    const response = await fetch("/catalogue");
    if (!response.ok) throw new Error("Catalogue unavailable.");
    const catalogue = await response.json();
    renderDashboardList(catalogue.dashboards);
    renderDatabaseList(catalogue.databases);
    catalogueStatus = "ready";
    showCatalogueStatus("");
  } catch {
    catalogueStatus = "idle";
    showCatalogueStatus("Could not load the catalogue.", true);
  }
}

const sectionTabs = Array.from(document.querySelectorAll(".top-nav [role=tab]"));

function showSection(selected) {
  for (const tab of sectionTabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    byId(tab.getAttribute("aria-controls")).hidden = !active;
  }
  document.title = selected.textContent + " | Nexora";
  if (selected.id !== "workspace-tab") loadCatalogue();
}

for (const [index, tab] of sectionTabs.entries()) {
  tab.addEventListener("click", () => showSection(tab));
  tab.addEventListener("keydown", event => {
    const last = sectionTabs.length - 1;
    let next;
    switch (event.key) {
      case "ArrowLeft": next = index === 0 ? last : index - 1; break;
      case "ArrowRight": next = index === last ? 0 : index + 1; break;
      case "Home": next = 0; break;
      case "End": next = last; break;
      default: return;
    }
    event.preventDefault();
    sectionTabs[next].focus();
    showSection(sectionTabs[next]);
  });
}

for (const button of document.querySelectorAll(".catalogue-retry")) {
  button.addEventListener("click", loadCatalogue);
}
document.querySelector(".brand").addEventListener("click", () => showSection(byId("workspace-tab")));
