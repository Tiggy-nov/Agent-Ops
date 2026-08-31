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
};

async function loadReport() {
  const response = await fetch("/audit/report", {cache: "no-store"});
  if (!response.ok) throw new Error("The audit report could not be loaded");
  render(await response.json());
}

function render(report) {
  const {audit, coverage, answer, candidates} = report;
  $("#status-text").textContent = audit.complete ? "Audit complete" : "Collecting";
  $("#elapsed").textContent = `${audit.elapsed_hours.toFixed(1)} / ${audit.target_hours} hours`;
  $("#progress").style.width = `${audit.progress * 100}%`;
  $("#removable-time").textContent = formatTime(answer.removable_latency_ms);
  $("#removable-share").textContent = `${(answer.removable_share * 100).toFixed(1)}%`;
  $("#tool-calls").textContent = coverage.tool_calls.toLocaleString();
  $("#sessions").textContent = coverage.sessions.toLocaleString();
  $("#tools").textContent = coverage.tools.toLocaleString();
  $("#eligible-repeats").textContent = answer.eligible_repeated_calls.toLocaleString();

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
      <td>${formatTime(candidate.median_latency_ms)}</td>
      <td>${formatTime(candidate.removable_latency_ms)}</td>
      <td><span class="decision ${eligible ? "eligible" : "excluded"}">${decisionLabel[candidate.classification]}</span></td>
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
