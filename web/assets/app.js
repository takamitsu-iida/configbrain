"use strict";

const form = document.querySelector("#generation-form");
const apiKeyInput = document.querySelector("#api-key");
const toggleApiKeyButton = document.querySelector("#toggle-api-key");
const submitButton = document.querySelector("#submit-button");
const emptyState = document.querySelector("#empty-state");
const jobState = document.querySelector("#job-state");
const statusBadge = document.querySelector("#status-badge");
const progressMessage = document.querySelector("#progress-message");
const progressText = document.querySelector("#progress-text");
const errorMessage = document.querySelector("#error-message");
const retryStatusButton = document.querySelector("#retry-status");
const resultContent = document.querySelector("#result-content");
const copyConfigurationButton = document.querySelector("#copy-configuration");
const productSelect = document.querySelector("#product");
const releaseSelect = document.querySelector("#release");
const targetError = document.querySelector("#target-error");
const requirementTemplateSelect = document.querySelector(
  "#requirement-template",
);
const reloadTemplatesButton = document.querySelector("#reload-templates");
const templateError = document.querySelector("#template-error");
const feedbackPanel = document.querySelector("#feedback-panel");
const feedbackForm = document.querySelector("#feedback-form");
const feedbackSubmitButton = document.querySelector("#feedback-submit");
const feedbackMessage = document.querySelector("#feedback-message");
const feedbackEditButton = document.querySelector("#feedback-edit");

const POLL_INTERVAL_MS = 2000;
let activeJobId = null;
let pollTimer = null;
let apiKey = "";
let configurationTargets = new Map();
let requirementTemplates = [];
let feedbackId = null;
let feedbackSaved = false;

function setHidden(element, hidden) {
  element.classList.toggle("hidden", hidden);
}

function setStatus(status) {
  const labels = {
    idle: "未実行",
    queued: "受付済み",
    running: "生成中",
    completed: "完了",
    failed: "失敗",
  };
  statusBadge.textContent = labels[status] || status;
  statusBadge.className = `status-badge ${status}`;
}

function clearTimer() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function productLabel(product) {
  const match = /^catalyst(\d+)$/i.exec(product);
  return match ? `Catalyst ${match[1]}` : product;
}

function updateReleaseOptions() {
  const releases = configurationTargets.get(productSelect.value) || [];
  releaseSelect.replaceChildren();
  for (const release of releases) {
    const option = document.createElement("option");
    option.value = release;
    option.textContent = `IOS XE ${release}`;
    releaseSelect.append(option);
  }
  releaseSelect.disabled = releases.length === 0;
}

async function loadConfigurationTargets() {
  try {
    const response = await fetch("/v1/configuration-targets");
    const body = await response.json();
    if (!response.ok) {
      throw new Error(
        formatApiError(body, "製品・リリース候補を取得できませんでした"),
      );
    }
    if (!Array.isArray(body.targets) || body.targets.length === 0) {
      throw new Error("利用可能な製品・リリースがありません");
    }
    configurationTargets = new Map(
      body.targets.map((target) => [
        target.product,
        Array.isArray(target.releases) ? target.releases : [],
      ]),
    );
    productSelect.replaceChildren();
    for (const product of configurationTargets.keys()) {
      const option = document.createElement("option");
      option.value = product;
      option.textContent = productLabel(product);
      productSelect.append(option);
    }
    productSelect.value = configurationTargets.has("catalyst9300")
      ? "catalyst9300"
      : productSelect.options[0].value;
    productSelect.disabled = false;
    updateReleaseOptions();
    setHidden(targetError, true);
    submitButton.disabled = false;
  } catch (error) {
    targetError.textContent = error.message;
    setHidden(targetError, false);
    productSelect.replaceChildren(
      new Option("候補を取得できませんでした", ""),
    );
    releaseSelect.replaceChildren(
      new Option("候補を取得できませんでした", ""),
    );
    productSelect.disabled = true;
    releaseSelect.disabled = true;
    submitButton.disabled = true;
  }
}

async function loadRequirementTemplates() {
  apiKey = apiKeyInput.value;
  requirementTemplateSelect.replaceChildren(
    new Option("直接入力", ""),
  );
  if (!apiKey) {
    templateError.textContent = "先にAPIキーを入力してください。";
    setHidden(templateError, false);
    return;
  }
  reloadTemplatesButton.disabled = true;
  try {
    const body = await requestJson("/v1/requirement-templates");
    if (!Array.isArray(body.templates) || body.templates.length === 0) {
      throw new Error("要件テンプレートがありません");
    }
    requirementTemplates = body.templates;
    for (const [index, template] of requirementTemplates.entries()) {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = template.label;
      requirementTemplateSelect.append(option);
    }
    setHidden(templateError, true);
  } catch (error) {
    requirementTemplates = [];
    templateError.textContent = error.message;
    setHidden(templateError, false);
  } finally {
    reloadTemplatesButton.disabled = !apiKeyInput.value;
  }
}

function formatApiError(responseBody, fallback) {
  if (!responseBody) {
    return fallback;
  }
  if (typeof responseBody.detail === "string") {
    return responseBody.detail;
  }
  if (Array.isArray(responseBody.detail)) {
    return responseBody.detail
      .map((item) => {
        const location = Array.isArray(item.loc)
          ? item.loc.slice(1).join(".")
          : "request";
        return `${location}: ${item.msg}`;
      })
      .join("\n");
  }
  return fallback;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "X-API-Key": apiKey,
      ...(options.headers || {}),
    },
  });
  let body = null;
  try {
    body = await response.json();
  } catch {
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: 応答を読み取れませんでした`);
    }
  }
  if (!response.ok) {
    throw new Error(
      formatApiError(body, `HTTP ${response.status}: APIリクエストに失敗しました`),
    );
  }
  return body;
}

function showError(message, allowRetry = false) {
  clearTimer();
  progressMessage.classList.add("hidden");
  errorMessage.textContent = message;
  setHidden(errorMessage, false);
  setHidden(retryStatusButton, !allowRetry || !activeJobId);
  submitButton.disabled = false;
}

function renderList(sectionId, listId, values) {
  const section = document.querySelector(sectionId);
  const list = document.querySelector(listId);
  list.replaceChildren();
  const items = Array.isArray(values) ? values : [];
  setHidden(section, items.length === 0);
  for (const value of items) {
    const item = document.createElement("li");
    item.textContent = String(value);
    list.append(item);
  }
}

function renderCitations(result) {
  const container = document.querySelector("#citations");
  container.replaceChildren();
  const evidenceById = new Map(
    (result.retrieval || []).map((item) => [item.citation_id, item]),
  );
  for (const citationId of result.citations || []) {
    const evidence = evidenceById.get(citationId);
    const item = document.createElement("div");
    item.className = "citation";
    const identifier = document.createElement("code");
    identifier.textContent = citationId;
    item.append(identifier);
    if (evidence) {
      const description = document.createElement("p");
      const heading = Array.isArray(evidence.heading_path)
        ? evidence.heading_path.join(" / ")
        : "";
      description.textContent = [
        `${evidence.product} ${evidence.release}`,
        heading,
        `score ${Number(evidence.score).toFixed(3)}`,
      ]
        .filter(Boolean)
        .join(" — ");
      item.append(description);
      if (
        typeof evidence.source_url === "string"
        && /^https?:\/\//i.test(evidence.source_url)
      ) {
        const sourceLink = document.createElement("a");
        sourceLink.href = evidence.source_url;
        sourceLink.target = "_blank";
        sourceLink.rel = "noopener noreferrer";
        sourceLink.textContent = "Cisco公式ドキュメントを開く";
        item.append(sourceLink);
      }
    }
    container.append(item);
  }
  if (container.childElementCount === 0) {
    const empty = document.createElement("p");
    empty.textContent = "引用はありません。";
    container.append(empty);
  }
}

function renderResult(job) {
  const result = job.result;
  if (!result) {
    showError("完了したジョブに生成結果がありません。");
    return;
  }
  document.querySelector("#configuration").textContent =
    result.configuration || "(空のコンフィグ)";
  document.querySelector("#explanation").textContent =
    result.explanation || "(説明なし)";
  renderList("#warnings-section", "#warnings", result.warnings);
  renderList(
    "#missing-section",
    "#missing-values",
    result.missing_required_values,
  );
  renderList(
    "#unsupported-section",
    "#unsupported-requirements",
    result.unsupported_requirements,
  );
  renderList(
    "#inferred-section",
    "#inferred-commands",
    result.inferred_commands,
  );
  renderCitations(result);
  setHidden(resultContent, false);
  setHidden(feedbackPanel, false);
}

function updateJobMetadata(job) {
  document.querySelector("#job-id").textContent = job.job_id || "-";
  document.querySelector("#request-id").textContent = job.request_id || "-";
  document.querySelector("#duration").textContent =
    job.duration_ms === null || job.duration_ms === undefined
      ? "-"
      : `${job.duration_ms.toLocaleString()} ms`;
}

function resetFeedbackForm() {
  const reviewer = document.querySelector("#reviewer").value;
  feedbackForm.reset();
  document.querySelector("#reviewer").value = reviewer;
  for (const control of feedbackForm.elements) {
    control.disabled = false;
  }
  feedbackId = crypto.randomUUID();
  feedbackSaved = false;
  feedbackSubmitButton.textContent = "フィードバックを保存";
  feedbackMessage.textContent = "";
  feedbackMessage.className = "feedback-message hidden";
  setHidden(feedbackEditButton, true);
  setHidden(feedbackPanel, true);
}

async function pollJob() {
  if (!activeJobId) {
    return;
  }
  try {
    const job = await requestJson(
      `/v1/config-generations/${encodeURIComponent(activeJobId)}`,
    );
    updateJobMetadata(job);
    setStatus(job.status);
    setHidden(errorMessage, true);
    setHidden(retryStatusButton, true);

    if (job.status === "completed") {
      clearTimer();
      setHidden(progressMessage, true);
      submitButton.disabled = false;
      renderResult(job);
      return;
    }
    if (job.status === "failed") {
      showError(
        `${job.error_code || "generation_failed"}: ${
          job.error || "生成に失敗しました"
        }`,
      );
      return;
    }
    progressText.textContent =
      job.status === "running"
        ? "根拠を検索し、コンフィグ案を生成しています..."
        : "実行待ちです...";
    setHidden(progressMessage, false);
    pollTimer = window.setTimeout(pollJob, POLL_INTERVAL_MS);
  } catch (error) {
    showError(
      `状態確認に失敗しました。\n${error.message}`,
      true,
    );
  }
}

async function submitGeneration(event) {
  event.preventDefault();
  clearTimer();
  apiKey = apiKeyInput.value;
  if (!apiKey) {
    apiKeyInput.focus();
    return;
  }

  const requestId = crypto.randomUUID();
  const payload = {
    requirement: document.querySelector("#requirement").value.trim(),
    product: productSelect.value,
    release: releaseSelect.value,
    knowledge_policy: document.querySelector("#knowledge-policy").value,
    top_k: Number(document.querySelector("#top-k").value),
  };

  submitButton.disabled = true;
  activeJobId = null;
  resetFeedbackForm();
  setHidden(emptyState, true);
  setHidden(jobState, false);
  setHidden(resultContent, true);
  setHidden(errorMessage, true);
  setHidden(retryStatusButton, true);
  setHidden(progressMessage, false);
  progressText.textContent = "ジョブを受け付けています...";
  setStatus("queued");
  updateJobMetadata({
    job_id: "-",
    request_id: requestId,
    duration_ms: null,
  });

  try {
    const accepted = await requestJson("/v1/config-generations", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": requestId,
        "X-Request-ID": requestId,
      },
      body: JSON.stringify(payload),
    });
    activeJobId = accepted.job_id;
    updateJobMetadata({
      job_id: accepted.job_id,
      request_id: accepted.request_id,
      duration_ms: null,
    });
    setStatus(accepted.status);
    await pollJob();
  } catch (error) {
    setStatus("failed");
    showError(error.message);
  }
}

async function submitFeedback(event) {
  event.preventDefault();
  if (!activeJobId || !feedbackId) {
    return;
  }
  feedbackSubmitButton.disabled = true;
  feedbackSubmitButton.setAttribute("aria-busy", "true");
  feedbackMessage.textContent = "保存しています...";
  feedbackMessage.className = "feedback-message";
  const issueCategories = Array.from(
    feedbackForm.querySelectorAll('input[name="issue"]:checked'),
    (input) => input.value,
  );
  const verdict = document.querySelector("#verdict").value;
  const dangerous = document.querySelector("#dangerous-error").checked;
  const comments = document.querySelector("#feedback-comments").value;
  if (
    ["major_changes", "unusable"].includes(verdict)
    && issueCategories.length === 0
  ) {
    feedbackMessage.textContent =
      "major_changesまたはunusableでは問題分類を選択してください。";
    feedbackMessage.className = "feedback-message error";
    feedbackSubmitButton.disabled = false;
    feedbackSubmitButton.removeAttribute("aria-busy");
    return;
  }
  if (dangerous && !comments.trim()) {
    feedbackMessage.textContent =
      "危険または破壊的な誤りにはコメントが必要です。";
    feedbackMessage.className = "feedback-message error";
    feedbackSubmitButton.disabled = false;
    feedbackSubmitButton.removeAttribute("aria-busy");
    return;
  }
  const payload = {
    feedback_id: feedbackId,
    reviewer: document.querySelector("#reviewer").value.trim(),
    requirement_anonymized: document.querySelector(
      "#requirement-anonymized",
    ).checked,
    verdict,
    expected_configuration: document.querySelector(
      "#expected-configuration",
    ).value,
    corrected_configuration: document.querySelector(
      "#corrected-configuration",
    ).value,
    citations_useful: document.querySelector("#citations-useful").value,
    requirement_classification_appropriate: document.querySelector(
      "#classification-appropriate",
    ).value,
    dangerous_or_destructive_error: dangerous,
    issue_categories: issueCategories,
    comments,
  };
  try {
    const feedbackUrl =
      `/v1/config-generations/${encodeURIComponent(activeJobId)}/feedback`
      + (feedbackSaved ? `/${encodeURIComponent(feedbackId)}` : "");
    const saved = await requestJson(
      feedbackUrl,
      {
        method: feedbackSaved ? "PUT" : "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": feedbackId,
        },
        body: JSON.stringify(payload),
      },
    );
    feedbackMessage.textContent =
      `保存しました。フィードバックID: ${saved.feedback_id}`;
    feedbackMessage.className = "feedback-message";
    feedbackSaved = true;
    for (const control of feedbackForm.elements) {
      control.disabled = true;
    }
    setHidden(feedbackEditButton, false);
  } catch (error) {
    feedbackMessage.textContent = `保存に失敗しました。\n${error.message}`;
    feedbackMessage.className = "feedback-message error";
    feedbackSubmitButton.disabled = false;
  } finally {
    feedbackSubmitButton.removeAttribute("aria-busy");
  }
}

function enableFeedbackEditing() {
  for (const control of feedbackForm.elements) {
    control.disabled = false;
  }
  feedbackSubmitButton.textContent = "訂正を保存";
  feedbackMessage.className = "feedback-message hidden";
  setHidden(feedbackEditButton, true);
}

toggleApiKeyButton.addEventListener("click", () => {
  const showing = apiKeyInput.type === "text";
  apiKeyInput.type = showing ? "password" : "text";
  toggleApiKeyButton.textContent = showing ? "表示" : "非表示";
  toggleApiKeyButton.setAttribute("aria-pressed", String(!showing));
});

apiKeyInput.addEventListener("input", () => {
  apiKey = apiKeyInput.value;
  reloadTemplatesButton.disabled = !apiKey;
});

apiKeyInput.addEventListener("change", async () => {
  if (apiKeyInput.value) {
    await loadRequirementTemplates();
  }
});

requirementTemplateSelect.addEventListener("change", () => {
  if (requirementTemplateSelect.value === "") {
    return;
  }
  const selected = requirementTemplates[
    Number(requirementTemplateSelect.value)
  ];
  if (selected) {
    document.querySelector("#requirement").value = selected.requirement;
  }
});

reloadTemplatesButton.addEventListener(
  "click",
  loadRequirementTemplates,
);

feedbackEditButton.addEventListener("click", enableFeedbackEditing);

copyConfigurationButton.addEventListener("click", async () => {
  const configuration = document.querySelector("#configuration").textContent;
  try {
    await navigator.clipboard.writeText(configuration);
    copyConfigurationButton.textContent = "コピー済み";
    window.setTimeout(() => {
      copyConfigurationButton.textContent = "コピー";
    }, 1400);
  } catch {
    showError("クリップボードへコピーできませんでした。");
  }
});

retryStatusButton.addEventListener("click", async () => {
  retryStatusButton.disabled = true;
  setHidden(errorMessage, true);
  setHidden(progressMessage, false);
  progressText.textContent = "状態を再確認しています...";
  await pollJob();
  retryStatusButton.disabled = false;
});

productSelect.addEventListener("change", updateReleaseOptions);
form.addEventListener("submit", submitGeneration);
feedbackForm.addEventListener("submit", submitFeedback);
loadConfigurationTargets();
