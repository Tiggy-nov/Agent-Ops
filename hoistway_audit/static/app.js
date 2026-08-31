const $ = (selector) => document.querySelector(selector);

const formatTime = (milliseconds) => {
  if (milliseconds >= 3_600_000) return `${(milliseconds / 3_600_000).toFixed(2)} h`;
  if (milliseconds >= 60_000) return `${(milliseconds / 60_000).toFixed(1)} min`;
  if (milliseconds >= 1_000) return `${(milliseconds / 1_000).toFixed(2)} s`;
  return `${Math.round(milliseconds)} ms`;
};

const decisionLabel = {
  eligible: "Eligible",
  mutating: "Mutating",
  output_changed: "Output changed",
  single_session: "Single session",
  outside_safe_window: "Outside safe window",
};

const formatRate = (value) => value == null ? "—" : `${(value * 100).toFixed(0)}%`;

async function loadReport() {
  const response = await fetch("/audit/report", {cache: "no-store"});
  if (!response.ok) throw new Error("The audit report could not be loaded");
  render(await response.json());
}

function render(report) {
  const {audit, coverage, answer, repetition, timing_context: timing, stability, canonicalisation, candidates, verdicts} = report;
  $("#status-text").textContent = audit.complete ? "Audit complete" : "Collecting";
  $("#elapsed").textContent = `${audit.elapsed_hours.toFixed(1)} / ${audit.target_hours} hours`;
  $("#progress").style.width = `${audit.progress * 100}%`;
  $("#cross-session-rate").textContent = `${(repetition.cross_session.repeat_rate * 100).toFixed(1)}%`;
  $("#eligible-repeats").textContent = answer.eligible_repeated_calls.toLocaleString();
  $("#repeat-share").textContent = `${(answer.eligible_repeat_share * 100).toFixed(1)}%`;
  $("#cross-repeat").textContent = `${(repetition.cross_session.repeat_rate * 100).toFixed(1)}%`;
  $("#within-repeat").textContent = `${(repetition.within_session.repeat_rate * 100).toFixed(1)}%`;
  $("#total-repeat").textContent = `${(repetition.total.repeat_rate * 100).toFixed(1)}%`;
  $("#tool-calls").textContent = coverage.tool_calls.toLocaleString();
  $("#sessions").textContent = coverage.sessions.toLocaleString();
  $("#tools").textContent = coverage.tools.toLocaleString();
  $("#unscoped-calls").textContent = coverage.calls_dropped_missing_session_identity.toLocaleString();
  $("#lower-bound").textContent = formatTime(timing.repeated_tool_boundary_ms_lower_bound);
  $("#upper-bound").textContent = formatTime(timing.repeated_tool_boundary_ms_upper_bound);
  $("#multi-batch-share").textContent = `${(timing.eligible_calls_in_multi_call_batches_share * 100).toFixed(1)}%`;
  $("#batch-distribution").textContent = `Observed batch sizes: ${timing.batch_size_distribution.map((item) => `${item.batch_size} call${item.batch_size === 1 ? "" : "s"} × ${item.turns} turn${item.turns === 1 ? "" : "s"}`).join(" · ") || "none"}`;
  $("#timing-caveat").textContent = timing.caveat;

  const stabilityRows = [];
  stability.rows.forEach((row) => {
    [["Exact", "exact_match_rate"], ["Near-identical", "near_identical_rate"]].forEach(([label, key]) => {
      const tr = document.createElement("tr");
      const rates = row.buckets.map((bucket) => `<td>${formatRate(bucket[key])}</td>`).join("");
      tr.innerHTML = `<td>${escapeHtml(row.scope === "all_tools" ? "All tools" : row.scope)}</td><td>${label}</td>${rates}<td>${formatTime(row.observed_safe_reuse_window_ms)}</td>`;
      stabilityRows.push(tr);
    });
  });
  $("#stability-body").replaceChildren(...stabilityRows);

  const ruleNodes = [];
  Object.entries(canonicalisation).forEach(([key, value]) => {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = key.replaceAll("_", " ");
    detail.textContent = Array.isArray(value) ? value.join(", ") : value;
    ruleNodes.push(term, detail);
  });
  $("#rules").replaceChildren(...ruleNodes);

  const body = $("#candidate-body");
  const empty = $("#empty");
  empty.hidden = candidates.length > 0;
  body.replaceChildren(...candidates.map((candidate) => {
    const row = document.createElement("tr");
    const eligible = candidate.classification === "eligible";
    row.innerHTML = `
      <td>${escapeHtml(candidate.tool_name)} <small>${candidate.fingerprint_id}</small></td>
      <td>${candidate.calls}</td>
      <td>${candidate.sessions}</td>
      <td>${(candidate.output_stability * 100).toFixed(0)}%</td>
      <td>${formatTime(candidate.median_observed_tool_boundary_ms)}</td>
      <td>${formatTime(candidate.repeated_observed_tool_boundary_ms)}</td>
      <td><span class="decision ${eligible ? "eligible" : "excluded"}">${decisionLabel[candidate.classification]}</span></td>
    `;
    return row;
  }));

  $("#verdict-body").replaceChildren(...verdicts.map((item) => {
    const row = document.createElement("tr");
    const passed = item.verdict === "PASS";
    row.innerHTML = `
      <td>${escapeHtml(item.criterion)}</td>
      <td>${(item.measured * 100).toFixed(1)}%</td>
      <td>≥ ${(item.threshold * 100).toFixed(0)}%</td>
      <td><span class="decision ${passed ? "eligible" : "excluded"}">${item.verdict}</span></td>
    `;
    return row;
  }));
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value);
  return element.innerHTML;
}

$("#seed").addEventListener("click", async () => {
  $("#seed").disabled = true;
  const response = await fetch("/audit/demo/seed", {method: "POST"});
  if (!response.ok) throw new Error("Could not load demonstration data");
  render(await response.json());
});

loadReport().catch((error) => {
  $("#status-text").textContent = error.message;
});
