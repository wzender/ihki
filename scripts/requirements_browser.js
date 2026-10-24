"use strict";

const $ = id => document.getElementById(id);
const state = {data: null, selection: null, artifact: 0, query: ""};

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = String(text);
  if (className) el.className = className;
  return el;
}

function matches(value) {
  return !state.query || JSON.stringify(value).toLocaleLowerCase().includes(state.query);
}

function metric(value, label) {
  const box = node("div", null, "metric");
  box.append(node("strong", value), node("span", label));
  return box;
}

function navButton(kind, item, detail) {
  const button = node("button", null, "nav-button");
  button.type = "button";
  button.append(node("strong", item.title || item.id), node("small", detail));
  button.setAttribute("aria-current", state.selection?.kind === kind && state.selection.id === item.id ? "page" : "false");
  button.onclick = () => {
    state.selection = {kind, id: item.id};
    state.artifact = 0;
    render();
    $("content").focus();
  };
  return button;
}

function scenarioLines(raw) {
  return String(raw || "").split("\n").map(line => line.replace(/^-\s+/, "").replace(/\*\*/g, "").trim()).filter(Boolean);
}

function renderSpec(spec) {
  const main = $("content");
  const head = node("header", null, "document-head");
  head.append(node("span", "Durable specification", "type"), node("h2", spec.title || spec.id));
  if (spec.purpose) head.append(node("p", spec.purpose));
  main.append(head);
  const requirements = spec.requirements.filter(matches);
  if (!requirements.length) main.append(node("p", "No requirements match this search.", "no-results"));
  for (const requirement of requirements) {
    const details = node("details", null, "requirement");
    if (state.query) details.open = true;
    details.append(node("summary", requirement.name));
    const body = node("div", null, "requirement-body");
    body.append(node("p", requirement.text));
    for (const item of requirement.scenarios || []) {
      if (!matches(item) && state.query) continue;
      const scenario = node("section", null, "scenario");
      scenario.append(node("h4", item.name));
      for (const line of scenarioLines(item.rawText)) scenario.append(node("p", line));
      body.append(scenario);
    }
    details.append(body);
    main.append(details);
  }
}

function renderBlock(block, target) {
  if (block.type === "heading") target.append(node(`h${Math.min(6, Math.max(2, block.level || 2))}`, block.text));
  else if (block.type === "list") {
    const list = target.lastElementChild?.tagName === "UL" ? target.lastElementChild : node("ul");
    if (!list.isConnected) target.append(list);
    list.append(node("li", block.text));
  } else if (block.type === "task") target.append(node("p", block.text, `task${block.done ? " done" : ""}`));
  else if (block.type === "code-boundary") target.append(node("p", block.text || "code", "code-boundary"));
  else target.append(node("p", block.text));
}

function renderChange(change) {
  const main = $("content");
  const head = node("header", null, "document-head");
  head.append(node("span", `Active change · ${change.schema || "OpenSpec"}`, "type"), node("h2", change.id),
              node("p", `${change.completedTasks}/${change.totalTasks} implementation tasks complete`));
  main.append(head);
  if (!change.artifacts.length) {
    main.append(node("p", "No readable planning artifacts are available.", "no-results"));
    return;
  }
  state.artifact = Math.min(state.artifact, change.artifacts.length - 1);
  const tabs = node("div", null, "artifact-tabs");
  change.artifacts.forEach((artifact, index) => {
    const button = node("button", artifact.id);
    button.type = "button";
    button.setAttribute("aria-pressed", index === state.artifact ? "true" : "false");
    button.onclick = () => { state.artifact = index; render(); };
    tabs.append(button);
  });
  main.append(tabs);
  const artifact = change.artifacts[state.artifact];
  const article = node("article", null, "artifact");
  article.setAttribute("aria-label", artifact.path);
  const blocks = state.query ? artifact.blocks.filter(matches) : artifact.blocks;
  if (!blocks.length) article.append(node("p", "No artifact content matches this search.", "no-results"));
  for (const block of blocks) renderBlock(block, article);
  main.append(article);
}

function renderWarnings() {
  const box = $("warnings"), warnings = state.data.warnings || [];
  box.replaceChildren();
  box.hidden = !warnings.length;
  if (!warnings.length) return;
  box.append(node("h2", `${warnings.length} file warning${warnings.length === 1 ? "" : "s"}`));
  for (const warning of warnings) box.append(node("p", `${warning.file}: ${warning.message}`));
}

function render() {
  if (!state.data) return;
  const specs = state.data.specs.filter(matches), changes = state.data.changes.filter(matches);
  $("summary").replaceChildren(metric(state.data.summary.specifications, "Specifications"),
                                  metric(state.data.summary.requirements, "Requirements"),
                                  metric(state.data.summary.changes, "Active changes"));
  $("rootPath").textContent = state.data.root;
  $("specCount").textContent = specs.length;
  $("changeCount").textContent = changes.length;
  $("specNav").replaceChildren(...specs.map(item => navButton("spec", item, `${item.requirementCount} requirements`)));
  $("changeNav").replaceChildren(...changes.map(item => navButton("change", item, `${item.completedTasks}/${item.totalTasks} tasks`)));
  const found = specs.length + changes.length;
  $("searchStatus").textContent = state.query ? `${found} document${found === 1 ? "" : "s"} match` : "";
  const main = $("content");
  main.replaceChildren();
  let selected = state.selection?.kind === "spec" ? state.data.specs.find(item => item.id === state.selection.id)
               : state.selection?.kind === "change" ? state.data.changes.find(item => item.id === state.selection.id) : null;
  if (!selected || (state.query && !matches(selected))) {
    selected = specs[0] || changes[0];
    state.selection = selected ? {kind: specs.includes(selected) ? "spec" : "change", id: selected.id} : null;
  }
  if (!selected) main.append(node("p", "No specifications or changes match this search.", "no-results"));
  else if (state.selection.kind === "spec") renderSpec(selected);
  else renderChange(selected);
  renderWarnings();
}

async function load() {
  const refresh = $("refresh");
  refresh.disabled = true;
  refresh.textContent = "Refreshing…";
  try {
    const response = await fetch("/api/state", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    state.data = data;
    render();
  } catch (error) {
    const main = $("content");
    main.replaceChildren(node("h2", "Could not load OpenSpec"), node("p", error.message));
  } finally {
    refresh.disabled = false;
    refresh.textContent = "Refresh files";
  }
}

$("refresh").onclick = load;
$("search").addEventListener("input", event => {
  state.query = event.target.value.trim().toLocaleLowerCase();
  render();
});
load();
