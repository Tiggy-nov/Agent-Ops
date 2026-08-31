const $ = (selector) => document.querySelector(selector);

const formatTime = (milliseconds) => {
  if (milliseconds == null) return "Not observed";
  if (milliseconds >= 3_600_000) return `${(milliseconds / 3_600_000).toFixed(2)} h`;
  if (milliseconds >= 60_000) return `${(milliseconds / 60_000).toFixed(1)} min`;
  if (milliseconds >= 1_000) return `${(milliseconds / 1_000).toFixed(2)} s`;
  return `${Math.round(milliseconds)} ms`;
};

const formatRate = (value, decimals = 1) =>
  value == null ? "Not observed" : `${(value * 100).toFixed(decimals)}%`;

const decisionLabel = {
  eligible: "Eligible",
  mutating: "Mutating",
  output_changed: "Output changed",
  single_session: "Single session",
  outside_safe_window: "Outside window",
};

const rateClass = (value) => {
  if (value == null) return "empty";
  if (value >= 0.95) return "good";
  if (value >= 0.8) return "mid";
  return "poor";
};

const setText = (selector, value) => {
  const element = $(selector);
  if (element) element.textContent = value;
};

const createCell = (text, className = "") => {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
};

function showError(error) {
  setText("#status-text", "Unavailable");
  setText("#error-message", error.message || "The local audit report could not be loaded.");
  $("#error-banner").hidden = false;
}

function clearError() {
  $("#error-banner").hidden = true;
}

async function loadReport() {
  clearError();
  const response = await fetch("/audit/report", {cache: "no-store"});
  if (!response.ok) throw new Error("The local audit report could not be loaded.");
  render(await response.json());
}

function renderStability(rows) {
  const output = [];
  rows.forEach((row) => {
    const signals = [
      ["Exact", "exact_match_rate"],
      ["Near-identical", "near_identical_rate"],
    ];

    signals.forEach(([label, key], index) => {
      const tr = document.createElement("tr");
      if (index === 0) {
        const scope = createCell(row.scope === "all_tools" ? "All tools" : row.scope);
        scope.rowSpan = 2;
        tr.append(scope);
      }
      tr.append(createCell(label));
      row.buckets.forEach((bucket) => {
        const cell = createCell(formatRate(bucket[key], 0), `rate ${rateClass(bucket[key])}`);
        tr.append(cell);
      });
      if (index === 0) {
        const windowCell = createCell(formatTime(row.observed_safe_reuse_window_ms), "mono");
        windowCell.rowSpan = 2;
        tr.append(windowCell);
      }
      output.push(tr);
    });
  });
  $("#stability-body").replaceChildren(...output);
}

function renderCandidates(candidates) {
  const body = $("#candidate-body");
  const empty = $("#empty");
  empty.hidden = candidates.length > 0;
  body.hidden = candidates.length === 0;

  const rows = candidates.map((candidate) => {
    const row = document.createElement("tr");
    const tool = createCell(candidate.tool_name);
    const fingerprint = document.createElement("span");
    fingerprint.className = "fingerprint";
    fingerprint.textContent = candidate.fingerprint_id;
    tool.append(fingerprint);
    row.append(tool);
    row.append(
      createCell(candidate.calls.toLocaleString()),
      createCell(candidate.sessions.toLocaleString()),
      createCell(formatRate(candidate.output_stability, 0)),
      createCell(formatTime(candidate.observed_safe_reuse_window_ms)),
      createCell(formatTime(candidate.repeated_observed_tool_boundary_ms)),
    );

    const decisionCell = document.createElement("td");
    const decision = document.createElement("span");
    const eligible = candidate.classification === "eligible";
    decision.className = `decision ${eligible ? "eligible" : "excluded"}`;
    decision.textContent = decisionLabel[candidate.classification] || candidate.classification;
    decisionCell.append(decision);
    row.append(decisionCell);
    return row;
  });
  body.replaceChildren(...rows);
}

function renderVerdicts(verdicts, auditComplete) {
  const passCount = verdicts.filter((item) => item.verdict === "PASS").length;
  const overall = $("#overall-verdict");

  if (!auditComplete) {
    overall.textContent = "Collecting";
    overall.className = "collecting";
    setText("#verdict-summary", `${passCount} of ${verdicts.length} thresholds currently pass.`);
  } else if (passCount === verdicts.length) {
    overall.textContent = "PASS";
    overall.className = "pass";
    setText("#verdict-summary", "All pre-registered thresholds are cleared.");
  } else {
    overall.textContent = "KILL";
    overall.className = "kill";
    setText("#verdict-summary", `${verdicts.length - passCount} of ${verdicts.length} thresholds were not cleared.`);
  }

  const rows = verdicts.map((item) => {
    const row = document.createElement("div");
    row.className = "verdict-row";

    const copy = document.createElement("div");
    const criterion = document.createElement("strong");
    criterion.textContent = item.criterion;
    const measurement = document.createElement("p");
    measurement.textContent = `Measured ${formatRate(item.measured)} / threshold ≥ ${formatRate(item.threshold, 0)}`;
    copy.append(criterion, measurement);

    const badge = document.createElement("span");
    badge.className = `status-badge ${item.verdict === "PASS" ? "pass" : "kill"}`;
    badge.textContent = item.verdict;
    row.append(copy, badge);
    return row;
  });
  $("#verdict-body").replaceChildren(...rows);
}

function renderRules(canonicalisation) {
  const nodes = [];
  Object.entries(canonicalisation).forEach(([key, value]) => {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = key.replaceAll("_", " ");
    detail.textContent = Array.isArray(value) ? value.join(", ") : String(value);
    nodes.push(term, detail);
  });
  $("#rules").replaceChildren(...nodes);
}

function render(report) {
  const {
    audit,
    coverage,
    answer,
    repetition,
    timing_context: timing,
    stability,
    canonicalisation,
    candidates,
    verdicts,
  } = report;

  clearError();
  setText("#status-text", audit.complete ? "Audit complete" : "Collecting evidence");
  setText("#elapsed", `${audit.elapsed_hours.toFixed(1)} / ${audit.target_hours} hours`);
  const progress = Math.min(1, Math.max(0, audit.progress));
  setText("#progress-value", `${Math.round(progress * 100)}%`);
  $("#progress").style.width = "100%";
  $("#progress").style.transform = `scaleX(${progress})`;

  setText("#cross-session-rate", formatRate(repetition.cross_session.repeat_rate));
  setText("#eligible-repeats", answer.eligible_repeated_calls.toLocaleString());
  setText("#repeat-share-copy", `${formatRate(answer.eligible_repeat_share)} of all observed tool calls`);
  setText("#cross-repeat", formatRate(repetition.cross_session.repeat_rate));
  setText("#within-repeat", formatRate(repetition.within_session.repeat_rate));
  setText("#total-repeat", formatRate(repetition.total.repeat_rate));

  setText("#tool-calls", coverage.tool_calls.toLocaleString());
  setText("#sessions", coverage.sessions.toLocaleString());
  setText("#tools", coverage.tools.toLocaleString());
  setText("#unscoped-calls", coverage.calls_dropped_missing_session_identity.toLocaleString());

  setText("#lower-bound", formatTime(timing.repeated_tool_boundary_ms_lower_bound));
  setText("#upper-bound", formatTime(timing.repeated_tool_boundary_ms_upper_bound));
  setText("#multi-batch-share", formatRate(timing.eligible_calls_in_multi_call_batches_share));
  const batchText = timing.batch_size_distribution
    .map((item) => `${item.batch_size} call${item.batch_size === 1 ? "" : "s"} in ${item.turns} turn${item.turns === 1 ? "" : "s"}`)
    .join(" · ");
  setText("#batch-distribution", batchText ? `Observed batches: ${batchText}` : "No batch evidence yet.");
  setText("#timing-caveat", timing.caveat || "No timing interpretation is available.");

  renderStability(stability.rows);
  renderCandidates(candidates);
  renderVerdicts(verdicts, audit.complete);
  renderRules(canonicalisation);
  document.body.classList.add("loaded");
}

$("#seed").addEventListener("click", async () => {
  const button = $("#seed");
  button.disabled = true;
  button.textContent = "Loading data";
  try {
    clearError();
    const response = await fetch("/audit/demo/seed", {method: "POST"});
    if (!response.ok) throw new Error("Demonstration data could not be loaded.");
    render(await response.json());
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = "Load demonstration data";
  }
});

$("#retry").addEventListener("click", () => {
  loadReport().catch(showError);
});

loadReport().catch(showError);
