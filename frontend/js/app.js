import { streamUpload } from "./sse-client.js";

const STAGES = ["security", "reader", "prompt", "ai", "validate", "save"];

const STAGE_TITLES = {
  security: "Security gate",
  reader: "Read document",
  prompt: "Safe prompt",
  ai: "AI call",
  validate: "Validate output",
  save: "Save result",
};

const STATUS_LABELS = {
  idle: "Waiting",
  running: "Running",
  done: "Done",
  attention: "Needs a look",
  failed: "Failed",
};

const ATTEMPT_LABELS = {
  trying: "Trying",
  success: "Answered",
  failed: "Failed",
  skipped: "Skipped",
};

const STATE_LABELS = {
  ok: "All checks passed",
  needs_review: "Needs review",
  reviewed: "Reviewed",
};

const RISK_TEXT = {
  none: "No instruction-like text was found in this document.",
  low: "One instruction-like phrase was found. It was treated as data only.",
  high: "Several instruction-like phrases were found. They were treated as data only, so please review the result.",
};

const ITEM_COLUMNS = [
  { field: "description", type: "text", label: "Description", placeholder: "Description" },
  { field: "quantity", type: "number", label: "Quantity", placeholder: "Qty" },
  { field: "unit_price", type: "number", label: "Unit price", placeholder: "Price" },
  { field: "amount", type: "number", label: "Amount", placeholder: "Amount" },
];

const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  config: {
    max_file_size_mb: 5,
    allowed_extensions: ["pdf", "png", "jpg", "jpeg"],
    data_retention_days: 30,
  },
  file: null,
  previewUrl: null,
  busy: false,
  controller: null,
  gotFinal: false,
  stageInfo: {},
  userPicked: false,
  selectedStage: null,
  attemptItems: new Map(),
  current: null,
};

const stageEls = {};

const $ = (id) => document.getElementById(id);

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = text;
  }
  return node;
}

function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function formatMs(ms) {
  if (ms === null || ms === undefined) {
    return "";
  }
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function formatSize(bytes) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function getExtension(name) {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index + 1).toLowerCase();
}

function setMessage(node, message) {
  node.textContent = message || "";
  node.hidden = !message;
}

function errorMessage(body) {
  const detail = body && body.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (detail && typeof detail.message === "string") {
    return detail.message;
  }
  if (Array.isArray(detail)) {
    return "Some of the values were not accepted.";
  }
  return "Something went wrong. Please try again.";
}

/* ---------- Setup data ---------- */

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const body = await response.json();
    state.config = { ...state.config, ...body };
  } catch (error) {
    // We keep the default limits if the server cannot be reached.
  }

  const types = state.config.allowed_extensions.map((item) => item.toUpperCase()).join(", ");
  $("limits-note").textContent =
    `${types}, up to ${state.config.max_file_size_mb} MB. Use sample documents only. ` +
    `Records are deleted after ${state.config.data_retention_days} days.`;
}

async function loadProviders() {
  const box = $("provider-chips");
  try {
    const response = await fetch("/api/providers");
    const body = await response.json();
    box.replaceChildren();
    for (const provider of body.providers) {
      const ready = provider.text_ready;
      const chip = make("span", ready ? "chip on" : "chip off", `${provider.label} ${ready ? "ready" : "off"}`);
      if (ready && provider.vision_ready) {
        chip.title = "Can read images";
      }
      box.append(chip);
    }
  } catch (error) {
    box.replaceChildren(make("span", "chip off", "Providers unknown"));
  }
}

/* ---------- File choosing ---------- */

function updateButtons() {
  $("extract-btn").disabled = !state.file || state.busy;
  $("extract-btn").textContent = state.busy ? "Reading..." : "Extract data";
  $("cancel-btn").hidden = !state.busy;
  $("clear-file").disabled = state.busy;
}

function setScanner(stateName, text) {
  $("scanner-badge").dataset.state = stateName;
  $("scanner-badge").textContent = text;
  $("paper").classList.toggle("scanning", stateName === "running");
}

function showFile(file) {
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }

  const isImage = file.type.startsWith("image/");
  if (isImage) {
    state.previewUrl = URL.createObjectURL(file);
    $("paper-img").src = state.previewUrl;
  } else {
    $("paper-img").removeAttribute("src");
  }
  $("paper-img").hidden = !isImage;
  $("paper").classList.toggle("has-image", isImage);

  $("file-type").textContent = getExtension(file.name).toUpperCase().slice(0, 4) || "FILE";
  $("file-name").textContent = file.name;
  $("file-size").textContent = formatSize(file.size);
  $("file-chip").hidden = false;
  $("drop-title").textContent = "File ready";
  $("drop-sub").textContent = "Choose another file to replace it";
  setScanner("idle", "Ready");
}

function clearFile() {
  state.file = null;
  $("file-input").value = "";
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
  $("paper-img").hidden = true;
  $("paper-img").removeAttribute("src");
  $("paper").classList.remove("has-image");
  $("file-chip").hidden = true;
  $("drop-title").textContent = "Drop a PDF or photo here";
  $("drop-sub").textContent = "or tap to choose a file";
  setMessage($("upload-error"), "");
  updateButtons();
}

function chooseFile(file) {
  setMessage($("upload-error"), "");
  if (!file) {
    return;
  }

  const allowed = state.config.allowed_extensions;
  if (!allowed.includes(getExtension(file.name))) {
    setMessage($("upload-error"), `That file type is not allowed. Use one of: ${allowed.join(", ")}.`);
    return;
  }
  if (file.size === 0) {
    setMessage($("upload-error"), "That file is empty.");
    return;
  }
  if (file.size > state.config.max_file_size_mb * 1024 * 1024) {
    setMessage($("upload-error"), `That file is larger than ${state.config.max_file_size_mb} MB.`);
    return;
  }

  state.file = file;
  showFile(file);
  updateButtons();
}

function bindUpload() {
  const zone = $("dropzone");

  $("file-input").addEventListener("change", (event) => {
    chooseFile(event.target.files[0]);
  });

  ["dragenter", "dragover"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    zone.addEventListener(name, () => zone.classList.remove("dragover"));
  });

  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer && event.dataTransfer.files.length > 0) {
      chooseFile(event.dataTransfer.files[0]);
    }
  });

  ["dragover", "drop"].forEach((name) => {
    window.addEventListener(name, (event) => event.preventDefault());
  });

  $("clear-file").addEventListener("click", clearFile);
  $("extract-btn").addEventListener("click", startExtraction);
  $("cancel-btn").addEventListener("click", () => {
    if (state.controller) {
      state.controller.abort();
    }
  });
}

/* ---------- Pipeline view ---------- */

function setStage(stage, status, detail, ms) {
  const els = stageEls[stage];
  if (!els) {
    return;
  }

  els.button.dataset.status = status;
  const label = STATUS_LABELS[status] || status;
  els.state.textContent = ms !== null && ms !== undefined ? `${label} · ${formatMs(ms)}` : label;
  els.detail.textContent = detail || els.defaultDetail;

  if (status === "attention") {
    els.num.textContent = "!";
  } else if (status === "failed") {
    els.num.textContent = "\u00d7";
  } else if (status === "running") {
    els.num.textContent = "";
  } else {
    els.num.textContent = els.index;
  }

  const anyRunning = STAGES.some((id) => stageEls[id].button.dataset.status === "running");
  $("track").dataset.running = String(anyRunning);
}

function setPipelineBadge(stateName, text) {
  $("pipeline-badge").dataset.state = stateName;
  $("pipeline-badge").textContent = text;
}

function resetPipeline() {
  state.stageInfo = {};
  state.userPicked = false;
  state.selectedStage = null;
  state.attemptItems = new Map();

  STAGES.forEach((id) => setStage(id, "idle", null, null));
  STAGES.forEach((id) => stageEls[id].button.setAttribute("aria-pressed", "false"));

  $("attempts").replaceChildren();
  $("attempts-empty").hidden = false;
  $("detail-body").replaceChildren();
  $("detail-empty").hidden = false;
  $("detail-title").textContent = "Stage details";
  setPipelineBadge("idle", "Waiting");
  setMessage($("run-error"), "");
}

function selectStage(stage) {
  state.selectedStage = stage;
  STAGES.forEach((id) => {
    stageEls[id].button.setAttribute("aria-pressed", String(id === stage));
  });
  renderDetails(stage);
}

function buildChecks(checks) {
  const list = make("ul", "check-list");
  for (const check of checks) {
    const status = check.status || (check.passed ? "passed" : "failed");
    const item = make("li");
    item.dataset.status = status;
    item.append(make("span", "ico"), make("span", "check-name", check.name));
    if (check.detail) {
      item.append(make("span", "check-detail", check.detail));
    }
    list.append(item);
  }
  return list;
}

function buildFindings(findings) {
  const list = make("ul", "finding-list");
  for (const finding of findings) {
    const item = make("li");
    item.append(make("strong", "", finding.name));
    if (finding.snippet) {
      item.append(document.createTextNode(" "), make("code", "", finding.snippet));
    }
    list.append(item);
  }
  return list;
}

function buildNotes(notes) {
  const list = make("ul", "note-list");
  for (const note of notes) {
    list.append(make("li", "", note));
  }
  return list;
}

function renderDetails(stage) {
  const info = state.stageInfo[stage];
  const body = $("detail-body");
  body.replaceChildren();
  $("detail-title").textContent = STAGE_TITLES[stage] || "Stage details";

  if (!info) {
    $("detail-empty").hidden = false;
    $("detail-empty").textContent = "This stage has not started yet.";
    return;
  }

  $("detail-empty").hidden = true;

  if (info.detail) {
    body.append(make("p", "detail-line", info.detail));
  }

  const checks = info.checks || (info.validation && info.validation.checks);
  if (checks && checks.length > 0) {
    body.append(buildChecks(checks));
  }
  if (info.findings && info.findings.length > 0) {
    body.append(buildFindings(info.findings));
  }
  if (info.notes && info.notes.length > 0) {
    body.append(buildNotes(info.notes));
  }
}

function onStage(payload) {
  if (!stageEls[payload.stage]) {
    return;
  }

  state.stageInfo[payload.stage] = payload;
  setStage(payload.stage, payload.status, payload.detail, payload.elapsed_ms);
  $("live-status").textContent = `${STAGE_TITLES[payload.stage]}: ${STATUS_LABELS[payload.status] || payload.status}`;

  if (!state.userPicked) {
    selectStage(payload.stage);
  }
}

function onAttempt(attempt) {
  let item = state.attemptItems.get(attempt.provider);

  if (!item) {
    item = {
      row: make("li", "attempt"),
      name: make("strong"),
      badge: make("span", "badge"),
      detail: make("span", "attempt-detail"),
    };
    item.row.append(item.name, item.badge, item.detail);
    $("attempts").append(item.row);
    state.attemptItems.set(attempt.provider, item);
    $("attempts-empty").hidden = true;
  }

  item.name.textContent = attempt.label;
  item.badge.dataset.state = attempt.status;
  item.badge.textContent = ATTEMPT_LABELS[attempt.status] || attempt.status;
  item.detail.textContent = attempt.detail;
}

function settleRunning(status, detail) {
  STAGES.forEach((id) => {
    if (stageEls[id].button.dataset.status === "running") {
      setStage(id, status, detail, null);
    }
  });
}

/* ---------- Running a document ---------- */

function resetRun() {
  resetPipeline();
  $("results").hidden = true;
  state.current = null;
}

async function startExtraction() {
  if (!state.file || state.busy) {
    return;
  }

  resetRun();
  state.busy = true;
  state.gotFinal = false;
  state.controller = new AbortController();
  updateButtons();
  setScanner("running", "Scanning");
  setPipelineBadge("running", "Running");
  $("pipeline").scrollIntoView({ behavior: REDUCE_MOTION ? "auto" : "smooth", block: "start" });

  try {
    await streamUpload(
      state.file,
      { stage: onStage, attempt: onAttempt, result: onResult, failure: onFailure },
      state.controller.signal
    );

    if (!state.gotFinal) {
      settleRunning("failed", "The connection ended early");
      setPipelineBadge("failed", "Failed");
      setMessage($("run-error"), "The connection ended before the result arrived. Please try again.");
    }
  } catch (error) {
    if (error.name === "AbortError") {
      settleRunning("idle", "Cancelled");
      setPipelineBadge("idle", "Cancelled");
      setMessage($("run-error"), "Cancelled. Nothing was saved.");
    } else {
      settleRunning("failed", "Connection problem");
      setPipelineBadge("failed", "Failed");
      const message = error instanceof TypeError ? "Could not reach the server. Check your connection." : error.message;
      setMessage($("run-error"), message || "Something went wrong.");
    }
  } finally {
    state.busy = false;
    state.controller = null;
    setScanner("idle", "Ready");
    updateButtons();
  }
}

function onFailure(payload) {
  state.gotFinal = true;

  const failedStage = stageEls[payload.stage];
  if (failedStage && failedStage.button.dataset.status !== "failed") {
    setStage(payload.stage, "failed", payload.message, null);
  }

  setPipelineBadge("failed", "Failed");
  setMessage($("run-error"), payload.message || "Something went wrong.");
}

function onResult(payload) {
  state.gotFinal = true;

  const view = {
    id: payload.id,
    saved: payload.saved,
    data: payload.data,
    validation: payload.validation,
    status: payload.validation.status,
    risk: payload.safety.risk,
    findings: payload.safety.findings || [],
    safetyNotes: payload.safety.notes || [],
    provider: payload.ai.provider,
    model: payload.ai.model,
    duration_ms: payload.duration_ms,
  };

  const needsLook = view.status !== "ok";
  setPipelineBadge(needsLook ? "attention" : "done", needsLook ? "Needs a look" : "Complete");

  showResult(view, true);
  loadHistory();
  loadStats();
}

/* ---------- Results ---------- */

function textInput(id, value) {
  $(id).value = value === null || value === undefined ? "" : String(value);
}

function setConfidence(key, value) {
  const chip = $(`c-${key}`);
  if (typeof value === "number") {
    chip.textContent = `AI ${Math.round(value * 100)}%`;
    chip.hidden = false;
  } else {
    chip.hidden = true;
  }
}

function makeItemCell(column, lineNumber, value) {
  const cell = make("label", "cell");
  cell.append(make("span", "cell-label", column.label));

  const input = document.createElement("input");
  input.type = column.type;
  if (column.type === "number") {
    input.step = "any";
    input.inputMode = "decimal";
  } else {
    input.maxLength = 200;
  }
  input.placeholder = column.placeholder;
  input.setAttribute("aria-label", `Line ${lineNumber} ${column.label.toLowerCase()}`);
  input.dataset.field = column.field;
  input.value = value === null || value === undefined ? "" : String(value);

  cell.append(input);
  return cell;
}

function renderLineItems(items) {
  const box = $("line-items");
  box.replaceChildren();
  $("no-items").hidden = items.length > 0;

  if (items.length === 0) {
    return;
  }

  const head = make("div", "items-head");
  head.setAttribute("aria-hidden", "true");
  ITEM_COLUMNS.forEach((column) => head.append(make("span", "", column.label)));
  box.append(head);

  items.forEach((item, index) => {
    const row = make("div", "item");
    ITEM_COLUMNS.forEach((column) => {
      row.append(makeItemCell(column, index + 1, item[column.field]));
    });
    box.append(row);
  });
}

function fillForm(data) {
  textInput("f-vendor", data.vendor);
  textInput("f-invoice_number", data.invoice_number);
  textInput("f-invoice_date", data.invoice_date);
  textInput("f-currency", data.currency);
  textInput("f-subtotal", data.subtotal);
  textInput("f-tax", data.tax);
  textInput("f-total", data.total);

  const confidence = data.confidence || {};
  ["vendor", "invoice_number", "invoice_date", "total"].forEach((key) => setConfidence(key, confidence[key]));

  renderLineItems(data.line_items || []);
}

function textOrNull(id) {
  const value = $(id).value.trim();
  return value === "" ? null : value;
}

function numberOrNull(value) {
  const text = String(value).trim();
  if (text === "") {
    return null;
  }
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function readForm() {
  const items = Array.from(document.querySelectorAll("#line-items .item")).map((row) => {
    const get = (field) => row.querySelector(`[data-field="${field}"]`).value;
    const description = get("description").trim();
    return {
      description: description === "" ? null : description,
      quantity: numberOrNull(get("quantity")),
      unit_price: numberOrNull(get("unit_price")),
      amount: numberOrNull(get("amount")),
    };
  });

  return {
    vendor: textOrNull("f-vendor"),
    invoice_number: textOrNull("f-invoice_number"),
    invoice_date: textOrNull("f-invoice_date"),
    currency: textOrNull("f-currency"),
    subtotal: numberOrNull($("f-subtotal").value),
    tax: numberOrNull($("f-tax").value),
    total: numberOrNull($("f-total").value),
    line_items: items,
    confidence: {},
  };
}

function renderStatus(view) {
  const checks = view.validation.checks || [];
  const failed = checks.filter((check) => check.status === "failed");
  const banner = $("status-banner");
  banner.dataset.state = view.status;

  if (view.status === "ok") {
    banner.textContent = "All checks passed. Still worth a quick look at the important numbers.";
  } else if (view.status === "reviewed") {
    banner.textContent =
      failed.length === 0
        ? "Reviewed and corrected by you. All checks pass."
        : `Reviewed by you. ${failed.length} check(s) still fail, see the list.`;
  } else {
    const reasons = view.validation.reasons || [];
    banner.textContent = `Needs review: ${reasons.join(", ") || "please check the values"}.`;
  }

  $("result-badge").dataset.state = view.status;
  $("result-badge").textContent = STATE_LABELS[view.status] || view.status;
}

function renderSafety(view) {
  const box = $("safety-box");
  box.replaceChildren();

  const head = make("div", "");
  const badge = make("span", "badge", `Risk: ${view.risk}`);
  badge.dataset.state = view.risk;
  head.append(badge);
  box.append(head, make("p", "muted", RISK_TEXT[view.risk] || ""));

  if (view.findings.length > 0) {
    box.append(buildFindings(view.findings));
  }
  if (view.safetyNotes.length > 0) {
    box.append(buildNotes(view.safetyNotes));
  }
}

function showResult(view, scroll) {
  state.current = view;
  $("results").hidden = false;

  const parts = [];
  if (view.provider) {
    parts.push(`Read by ${capitalize(view.provider)}${view.model ? ` (${view.model})` : ""}`);
  }
  if (view.duration_ms !== null && view.duration_ms !== undefined) {
    parts.push(`in ${formatMs(view.duration_ms)}`);
  }
  parts.push(view.saved ? "saved in your history" : "not saved");
  $("result-meta").textContent = `${parts.join(", ")}.`;

  fillForm(view.data);
  renderStatus(view);

  $("validation-summary").textContent = view.validation.summary || "";
  const list = $("validation-list");
  list.replaceChildren();
  for (const item of buildChecks(view.validation.checks || []).children) {
    list.append(item.cloneNode(true));
  }

  renderSafety(view);
  $("json-view").textContent = JSON.stringify(view.data, null, 2);

  $("save-btn").disabled = !view.id;
  $("save-note").textContent = view.id ? "" : "This result was not saved, so corrections cannot be stored.";

  if (scroll) {
    $("results").scrollIntoView({ behavior: REDUCE_MOTION ? "auto" : "smooth", block: "start" });
  }
}

function viewFromDocument(doc) {
  const trace = doc.trace || {};
  return {
    id: doc.id,
    saved: true,
    data: doc.data,
    validation: doc.validation,
    status: doc.status,
    risk: doc.risk,
    findings: (trace.findings || []).map((name) => ({ name })),
    safetyNotes: trace.safety || [],
    provider: doc.provider,
    model: doc.model,
    duration_ms: doc.duration_ms,
  };
}

function applyTrace(doc) {
  const trace = doc.trace || {};
  resetPipeline();

  const info = {
    security: { detail: "Passed when this file was uploaded", checks: trace.security },
    reader: { detail: "The document was read", notes: trace.reader },
    prompt: {
      detail: `Injection risk: ${doc.risk}`,
      notes: trace.safety,
      findings: (trace.findings || []).map((name) => ({ name })),
    },
    ai: { detail: `Answered by ${capitalize(doc.provider)} (${doc.model})`, notes: trace.notes },
    validate: { detail: doc.validation.summary, validation: doc.validation },
    save: { detail: "Saved in your history" },
  };

  STAGES.forEach((stage) => {
    state.stageInfo[stage] = { stage, status: "done", title: STAGE_TITLES[stage], ...info[stage] };
    setStage(stage, "done", info[stage].detail, null);
  });

  (trace.ai_attempts || []).forEach(onAttempt);
  setPipelineBadge("done", "Loaded from history");
  selectStage("validate");
}

async function saveCorrections(event) {
  event.preventDefault();
  if (!state.current || !state.current.id) {
    return;
  }

  const button = $("save-btn");
  button.disabled = true;
  $("save-note").textContent = "Saving...";

  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(state.current.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readForm()),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(errorMessage(body));
    }

    showResult(viewFromDocument(body), false);
    $("save-note").textContent = "Saved. Your corrected version is stored.";
    loadHistory();
    loadStats();
  } catch (error) {
    $("save-note").textContent = error.message || "Could not save your corrections.";
    button.disabled = false;
  }
}

function bindResults() {
  $("data-form").addEventListener("submit", saveCorrections);

  $("copy-json").addEventListener("click", async () => {
    const button = $("copy-json");
    try {
      await navigator.clipboard.writeText($("json-view").textContent);
      button.textContent = "Copied";
    } catch (error) {
      button.textContent = "Copy failed";
    }
    setTimeout(() => {
      button.textContent = "Copy JSON";
    }, 1500);
  });

  $("download-json").addEventListener("click", () => {
    const blob = new Blob([$("json-view").textContent], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "doclens-result.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

/* ---------- History and stats ---------- */

function formatTotal(total, currency) {
  if (typeof total !== "number") {
    return "no total";
  }
  return `${total.toFixed(2)}${currency ? ` ${currency}` : ""}`;
}

async function openDocument(id) {
  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(id)}`);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(errorMessage(body));
    }
    setMessage($("run-error"), "");
    showResult(viewFromDocument(body), true);
    applyTrace(body);
  } catch (error) {
    setMessage($("run-error"), error.message || "Could not open that document.");
  }
}

async function removeDocument(id, filename) {
  if (!window.confirm(`Delete "${filename}" from your history? This cannot be undone.`)) {
    return;
  }

  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!response.ok) {
      throw new Error(errorMessage(await response.json()));
    }
    if (state.current && state.current.id === id) {
      $("results").hidden = true;
      state.current = null;
    }
    loadHistory();
    loadStats();
  } catch (error) {
    setMessage($("run-error"), error.message || "Could not delete that document.");
  }
}

function renderHistory(documents) {
  const list = $("history-list");
  list.replaceChildren();
  $("history-empty").hidden = documents.length > 0;

  for (const doc of documents) {
    const item = make("li", "history-item");

    const main = make("button", "history-main");
    main.type = "button";
    main.append(
      make("strong", "", doc.filename),
      make(
        "span",
        "",
        `${doc.vendor || "Unknown vendor"} · ${formatTotal(doc.total, doc.currency)} · ${formatDate(doc.created_at)}`
      )
    );
    main.addEventListener("click", () => openDocument(doc.id));

    const badge = make("span", "badge", STATE_LABELS[doc.status] || doc.status);
    badge.dataset.state = doc.status;

    const remove = make("button", "icon-btn danger", "\u00d7");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete ${doc.filename}`);
    remove.addEventListener("click", () => removeDocument(doc.id, doc.filename));

    item.append(main, badge, remove);
    list.append(item);
  }
}

async function loadHistory() {
  try {
    const response = await fetch("/api/documents?limit=20");
    const body = await response.json();
    renderHistory(body.documents || []);
  } catch (error) {
    $("history-empty").textContent = "Could not load your history.";
    $("history-empty").hidden = false;
  }
}

async function loadStats() {
  try {
    const response = await fetch("/api/stats");
    const body = await response.json();
    $("st-total").textContent = String(body.total_documents);
    $("st-ok").textContent = String(body.ok);
    $("st-review").textContent = String(body.needs_review);
    $("st-corrected").textContent = String(body.corrected_by_users);
    $("st-ms").textContent = formatMs(body.average_duration_ms) || "0 s";
  } catch (error) {
    // Statistics are optional, so we leave the numbers as they are.
  }
}

/* ---------- Start ---------- */

function init() {
  STAGES.forEach((id, index) => {
    const button = document.querySelector(`.step[data-stage="${id}"]`);
    stageEls[id] = {
      button,
      num: button.querySelector(".node-num"),
      detail: button.querySelector(".step-detail"),
      state: button.querySelector(".step-state"),
      defaultDetail: button.querySelector(".step-detail").textContent,
      index: String(index + 1),
    };
    button.addEventListener("click", () => {
      state.userPicked = true;
      selectStage(id);
    });
  });

  bindUpload();
  bindResults();
  $("refresh-history").addEventListener("click", () => {
    loadHistory();
    loadStats();
  });

  updateButtons();
  loadConfig();
  loadProviders();
  loadHistory();
  loadStats();
}

init();