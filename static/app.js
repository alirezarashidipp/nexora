const schemaFields = document.querySelector("#schema-fields");
const workspaceSearch = document.querySelector("#workspace-search");
const fieldSearch = document.querySelector("#field-search");
const fieldCount = document.querySelector("#field-count");
const fieldsEmpty = document.querySelector("#fields-empty");
const viewButtons = document.querySelectorAll("[data-view]");

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

function directoryField(column) {
  const template = document.querySelector("#directory-field");
  const field = template.content.firstElementChild.cloneNode(true);
  const type = column.type.split(" ")[0].toLowerCase();
  const numeric = /INT|REAL|FLOA|DOUB|NUM|DEC/i.test(column.type);

  field.dataset.search = [column.name, column.type, column.description, column.possible_values]
    .join(" ").toLowerCase();
  field.querySelector("strong").textContent = column.name;
  field.querySelector(".field-copy small").textContent = numeric ? "Number" : type.charAt(0).toUpperCase() + type.slice(1);
  if (numeric) field.querySelector("use").setAttribute("href", "#i-number");
  field.querySelector(".field-description p").textContent = column.description;
  field.querySelector(".field-description small").textContent = `Possible values: ${column.possible_values}`;
  return field;
}

async function loadSchema() {
  const response = await fetch("/schema");
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Could not load the available fields.");
  }

  document.querySelector("#dataset-name").textContent = data.table;

  const fields = document.createDocumentFragment();
  data.columns.forEach(column => fields.append(directoryField(column)));
  schemaFields.replaceChildren(fields);
  filterWorkspace();
}

let schemaLoaded = false;
async function showFields() {
  if (schemaLoaded) return;
  schemaLoaded = true;
  try {
    await loadSchema();
    document.querySelector("#browse-fields").hidden = true;
  } catch {
    schemaLoaded = false;
    fieldCount.textContent = "Fields unavailable. Select Data sources to retry.";
  }
}
document.querySelector('a[href="#data-sources"]').addEventListener("click", showFields);
fieldSearch.addEventListener("focus", showFields);
workspaceSearch.addEventListener("focus", showFields);
if (location.hash === "#data-sources") showFields();
document.querySelector("#browse-fields").addEventListener("click", showFields);
