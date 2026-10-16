/* ProofFix control room: render durable snapshots, never invent workflow state. */
(function () {
  "use strict";

  const STAGES = [
    ["triage", "Triage"],
    ["reproduction", "Reproduce"],
    ["diagnosis", "Diagnose"],
    ["experiment", "Experiment"],
    ["patch", "Patch"],
    ["verification", "Verify"]
  ];
  const TERMINAL_COPY = {
    not_reproduced: "The reported failure was not reproduced. No success claim is available.",
    blocked: "The evidence chain is blocked. The cause and outcome remain unverified.",
    verification_failed: "Verification failed. The patch is not verified and must not be treated as fixed.",
    verification_blocked: "Verification is blocked. The patch remains unverified.",
    verified: "All server-recorded verification checks passed. The patch is verified for this run."
  };
  const ACTION_ENDPOINTS = { approve: "/approve", reject: "/reject", diagnosis: "/request-diagnosis", verify: "/verify", export: "/export" };
  let currentRun = null;
  let currentEvents = [];

  const $ = (id) => document.getElementById(id);
  const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
  const setText = (id, value, fallback) => { const node = $(id); if (node) node.textContent = text(value, fallback); return node; };
  const clear = (node) => { while (node && node.firstChild) node.removeChild(node.firstChild); };
  const create = (tag, value, className) => { const node = document.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = text(value, ""); return node; };
  const listValue = (value) => Array.isArray(value) ? value : [];
  const mapping = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};

  async function api(path, options) {
    const response = await fetch(path, Object.assign({ headers: { "Accept": "application/json" } }, options || {}));
    let body = {};
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) throw new Error(text(body.error, "Request failed"));
    return body;
  }

  function runIdFromUrl() {
    return new URLSearchParams(window.location.search).get("run") || new URLSearchParams(window.location.search).get("run_id");
  }

  async function loadRun() {
    const runId = runIdFromUrl();
    if (!runId) {
      currentRun = null;
      currentEvents = [];
      renderEmpty();
      announce("No run selected. Add a run ID to the URL.");
      return;
    }
    setText("connection-label", "Reading server snapshot…");
    try {
      const encoded = encodeURIComponent(runId);
      const result = await Promise.all([api(`/api/runs/${encoded}`), api(`/api/runs/${encoded}/events`)]);
      currentRun = result[0];
      currentEvents = listValue(result[1].events);
      render(currentRun, currentEvents);
      announce(`Snapshot refreshed for ${runId}.`);
      setText("connection-label", "Snapshot current");
    } catch (error) {
      currentRun = null;
      currentEvents = [];
      renderEmpty();
      setText("connection-label", "Snapshot unavailable");
      announce(`Snapshot unavailable: ${error.message}`);
    }
  }

  function announce(message) { const node = $("live-status"); if (node) node.textContent = message; }

  function renderEmpty() {
    setText("run-state", "No run selected");
    setText("terminal-copy", "Load a run ID with ?run=run-… to inspect server-owned evidence.");
    setText("provenance-badge", "Waiting for data");
    $("provenance-badge").className = "badge badge-neutral";
    ["repository-value", "branch-value", "commit-value", "run-id-value", "provenance-value"].forEach((id) => setText(id, "—"));
    ["export-markdown", "export-json", "approve-button", "reject-button", "diagnosis-button", "verify-button"].forEach((id) => { if ($(id)) $(id).disabled = true; });
    setText("connection-label", "Awaiting run snapshot");
    if ($("actor-input")) $("actor-input").value = "";
    if ($("timestamp-input")) $("timestamp-input").value = "";
    setText("patch-action-status", "Actions unlock only when the server reports an awaiting approval state.");
    renderReproduction({ state: "created", input: {} }, []);
    renderHypotheses({ hypotheses: [] });
    renderPatch({ state: "created", patch: null });
    renderVerification({ state: "created", verification: null });
    renderEvidence({}, []);
    renderStages("created");
  }

  function snapshotInput(run) { return mapping(run.input || run.intake); }
  function repositorySnapshot(run) {
    const evidence = listValue(run.evidence).find((item) => mapping(item).kind === "repository_snapshot");
    return mapping(run.repository || run.repository_snapshot || run.snapshot || mapping(evidence).details || snapshotInput(run));
  }
  function eventDetails(event) { return mapping(mapping(event).details); }
  function reproductionRecord(run, events) {
    const evidence = listValue(run.evidence).find((item) => mapping(item).kind === "reproduction");
    const candidates = listValue(events).filter((event) => mapping(event).stage === "reproduction" || String(mapping(event).kind).includes("reproduction"));
    const event = candidates[candidates.length - 1] || {};
    const detail = mapping(mapping(event).details);
    const result = mapping(detail.result);
    const evidenceDetail = mapping(mapping(evidence).details);
    return Object.assign({}, evidenceDetail, detail, result, {
      status: mapping(mapping(evidence).details).status || text(detail.outcome, run.state),
      reason: mapping(evidence).summary || detail.reason || ""
    });
  }

  function render(run, events) {
    const input = snapshotInput(run);
    const repo = repositorySnapshot(run);
    const state = text(run.state, "unknown");
    setText("run-state", state.replaceAll("_", " "));
    setText("terminal-copy", TERMINAL_COPY[state] || `The run is ${state.replaceAll("_", " ")}. Continue only through server-authorized transitions.`);
    setText("repository-value", repo.repository || repo.path || input.repository_path);
    setText("branch-value", repo.branch || input.branch);
    setText("commit-value", repo.commit || repo.commit_sha || input.commit_sha);
    setText("run-id-value", run.run_id);
    const provenance = input.provenance || input.mode || run.provenance || run.mode || (state === "verified" ? "live OmniRush" : "—");
    setText("provenance-value", provenance);
    setText("provenance-badge", provenance);
    $("provenance-badge").className = `badge ${String(provenance).toLowerCase().includes("recorded") || String(provenance).toLowerCase().includes("demo") ? "badge-recorded" : "badge-live"}`;
    ["export-markdown", "export-json"].forEach((id) => { $(id).disabled = !run.run_id; });
    renderStages(state);
    renderReproduction(run, events);
    renderHypotheses(run);
    renderPatch(run);
    renderVerification(run);
    renderEvidence(run, events);
  }

  function statusClass(status) {
    return status === "Complete" || status === "Passed" ? "status-complete" : status === "Active" || status === "Running" ? "status-active" : status === "Blocked" ? "status-blocked" : status === "Failed" || status === "Failed / not reproduced" ? "status-failed" : "status-pending";
  }

  function stageStatus(stage, state) {
    const order = { triage: 0, reproduction: 1, diagnosis: 2, experiment: 3, patch: 4, verification: 5 };
    const index = order[stage];
    if (state === "blocked") return stage === "triage" ? "Complete" : stage === "reproduction" ? "Blocked" : "Pending";
    if (state === "verification_blocked") return stage === "verification" ? "Blocked" : index < 5 ? "Complete" : "Pending";
    if (state === "not_reproduced") return stage === "reproduction" ? "Failed" : index < 1 ? "Complete" : "Pending";
    if (state === "verification_failed") return stage === "verification" ? "Failed" : index < 5 ? "Complete" : "Pending";
    if (state === "verified") return "Complete";
    const active = { triaging: "triage", reproducing: "reproduction", diagnosing: "diagnosis", experimenting: "experiment", patch_proposed: "patch", awaiting_approval: "patch", applying: "patch", verifying: "verification" }[state];
    if (active === stage) return "Active";
    if (active && index < order[active]) return "Complete";
    return "Pending";
  }

  function renderStages(state) {
    STAGES.forEach(([key]) => {
      const item = document.querySelector(`[data-stage="${key}"]`);
      if (!item) return;
      const status = stageStatus(key, state);
      item.className = `stage-${status.toLowerCase().replaceAll(" ", "-")}`;
      const label = item.querySelector(".stage-status");
      if (label) label.textContent = status;
    });
  }

  function renderReproduction(run, events) {
    const record = reproductionRecord(run, events);
    setStatus("reproduction-status", record.status || run.state, record.status === "reproduced" ? "Complete" : record.status === "not_reproduced" ? "Failed" : record.status === "blocked" ? "Blocked" : "Pending");
    const input = snapshotInput(run);
    setText("reproduction-command", record.command || input.command, "No reproduction command recorded.");
    setText("reproduction-exit", record.exit_code);
    setText("reproduction-duration", record.duration_ms === undefined || record.duration_ms === null ? "—" : `${record.duration_ms} ms`);
    setText("reproduction-assessment", record.reason || record.outcome || record.status);
    setText("stdout-log", record.stdout || record.stdout_summary, "No bounded stdout captured.");
    setText("stderr-log", record.stderr || record.stderr_summary, "No bounded stderr captured.");
    setText("stdout-ref", record.stdout_ref || record.stdout_artifact_ref, "—");
    setText("stderr-ref", record.stderr_ref || record.stderr_artifact_ref, "—");
    chips($("reproduction-artifacts"), [record.stdout_ref, record.stderr_ref, record.command_ref].filter(Boolean));
  }

  function setStatus(id, raw, label) {
    const node = $(id); if (!node) return;
    const normalized = label || (raw ? String(raw).replaceAll("_", " ") : "Pending");
    node.textContent = normalized;
    node.className = `status-pill ${statusClass(normalized)}`;
  }

  function chips(node, values) {
    if (!node) return; clear(node);
    if (!values.length) { node.appendChild(create("span", "None recorded", "muted")); return; }
    values.forEach((value) => node.appendChild(create("span", value, "chip")));
  }

  function renderHypotheses(run) {
    const list = $("hypotheses-list"); clear(list);
    const hypotheses = listValue(run.hypotheses);
    setText("hypothesis-count", `${hypotheses.length} specialist${hypotheses.length === 1 ? "" : "s"}`);
    if (!hypotheses.length) { list.appendChild(create("p", "No specialist hypotheses have been recorded. Diagnosis remains unproven.", "empty-state")); return; }
    hypotheses.forEach((hypothesis) => {
      const card = create("article", undefined, "hypothesis-card");
      card.appendChild(create("h3", hypothesis.hypothesis || "Unlabelled hypothesis", "hypothesis-title"));
      const meta = create("div", undefined, "hypothesis-meta");
      meta.appendChild(create("span", `Confidence: ${text(hypothesis.confidence)}`));
      meta.appendChild(create("span", `Role: ${text(hypothesis.role)}`));
      meta.appendChild(create("span", `Session: ${text(hypothesis.session_id)}`));
      card.appendChild(meta);
      card.appendChild(renderExperiment(hypothesis, listValue(run.experiments)));
      const pair = create("div", undefined, "evidence-pair");
      pair.appendChild(evidenceColumn("SUPPORTING EVIDENCE", hypothesis.supporting_evidence));
      pair.appendChild(evidenceColumn("CONTRADICTING EVIDENCE", hypothesis.contradicting_evidence));
      card.appendChild(pair); list.appendChild(card);
    });
  }

  function renderExperiment(hypothesis, experiments) {
    const block = create("div", undefined, "experiment-block");
    block.appendChild(create("span", "PROPOSED EXPERIMENT", "field-label"));
    block.appendChild(create("p", hypothesis.experiment || "No experiment proposed."));
    const matching = experiments.filter((result) => {
      const ids = [hypothesis.id, hypothesis.hypothesis_id, hypothesis.session_id].filter(Boolean).map(String);
      return (result.hypothesis_id && ids.includes(String(result.hypothesis_id))) || (result.hypothesis && result.hypothesis === hypothesis.hypothesis);
    });
    const observed = matching[matching.length - 1];
    block.appendChild(create("span", "OBSERVED RESULT", "field-label"));
    if (!observed) {
      block.appendChild(create("p", "No matching execution recorded yet.", "experiment-empty"));
      return block;
    }
    const resultLine = `${text(observed.status, "unknown")} · exit ${text(observed.exit_code)} · digest ${text(observed.output_digest)}`;
    block.appendChild(create("p", resultLine, "experiment-result"));
    if (observed.command) block.appendChild(create("code", Array.isArray(observed.command) ? observed.command.join(" ") : observed.command));
    const output = observed.stdout || observed.stderr;
    if (output) block.appendChild(create("p", output, "experiment-output"));
    return block;
  }

  function evidenceColumn(label, values) {
    const column = create("div"); column.appendChild(create("span", label, "field-label"));
    const items = listValue(values); const summary = items.map((item) => mapping(item).summary || item).join("; ");
    column.appendChild(create("p", summary || "None recorded.")); return column;
  }

  function renderPatch(run) {
    const patch = mapping(run.patch); const approval = mapping(patch.approval || patch.decision);
    const state = text(run.state);
    setStatus("patch-status", patch.digest ? state : "No proposal", patch.digest && state === "awaiting_approval" ? "Active" : patch.digest && ["applying", "verifying", "verified"].includes(state) ? "Complete" : patch.digest ? "Pending" : "Pending");
    setText("patch-digest", patch.digest);
    setText("patch-risk", patch.risk_summary);
    setText("patch-lines", changedLineCount(patch.diff));
    setText("patch-diff", patch.diff, "No patch proposal recorded.");
    chips($("changed-files"), listValue(patch.files));
    setText("regression-test", patch.regression_test);
    const enabled = state === "awaiting_approval" && Boolean(patch.digest);
    ["approve-button", "reject-button", "diagnosis-button"].forEach((id) => { $(id).disabled = !enabled; });
    if ($("verify-button")) $("verify-button").disabled = state !== "applying";
    if (approval.action) setText("patch-action-status", `${approval.action} by ${text(approval.actor)} at ${text(approval.timestamp)}.`);
  }

  function changedLineCount(diff) {
    if (!diff) return "—";
    const additions = String(diff).split("\n").filter((line) => line.startsWith("+") && !line.startsWith("+++" )).length;
    const removals = String(diff).split("\n").filter((line) => line.startsWith("-") && !line.startsWith("---" )).length;
    return `+${additions} / −${removals}`;
  }

  function renderVerification(run) {
    const verification = mapping(run.verification); const checks = listValue(verification.checks);
    const status = verification.status || (run.state === "verified" ? "verified" : run.state === "verification_failed" ? "verification_failed" : "not run");
    setStatus("verification-status", status, status === "verified" ? "Passed" : status === "verification_failed" ? "Failed" : status === "verification_blocked" ? "Blocked" : "Not run");
    const list = $("verification-list"); clear(list);
    if (!checks.length) { list.appendChild(create("li", "Verification checks will appear here in execution order.", "empty-state")); return; }
    checks.forEach((check) => {
      const item = create("li", undefined, "verification-item");
      const body = create("div"); body.appendChild(create("strong", check.name || "Unnamed check")); body.appendChild(create("code", check.command || "No command recorded."));
      item.appendChild(body); item.appendChild(create("span", text(check.status, "pending"), `check-status ${check.status === "passed" || check.status === "ok" ? "pass" : check.status === "blocked" ? "blocked" : check.status === "failed" ? "fail" : "pending"}`));
      if (check.exit_code !== undefined || check.duration_ms !== undefined) item.appendChild(create("span", `exit ${text(check.exit_code)} · ${text(check.duration_ms)} ms`, "verification-detail"));
      const detail = mapping(check.details).error || mapping(check.details).failure || check.failure_detail;
      if (detail) item.appendChild(create("p", detail, "verification-detail"));
      list.appendChild(item);
    });
  }

  function renderEvidence(run, events) {
    const allEvidence = listValue(run.evidence); const files = []; const commands = []; const artifacts = []; const sessions = new Set(listValue(run.omnirush_sessions));
    const addEvidence = (item) => {
      const record = mapping(item); const details = mapping(record.details);
      if (record.path || details.path) files.push(`${record.path || details.path}${record.line || details.line ? `:${record.line || details.line}` : ""}`);
      if (record.command || details.command) commands.push(record.command || details.command);
      if (record.artifact_ref || details.artifact_ref) artifacts.push(record.artifact_ref || details.artifact_ref);
      listValue(details.session_ids).forEach((session) => sessions.add(session));
    };
    allEvidence.forEach(addEvidence);
    listValue(run.hypotheses).forEach((hypothesis) => { if (hypothesis.session_id) sessions.add(hypothesis.session_id); listValue(hypothesis.supporting_evidence).concat(listValue(hypothesis.contradicting_evidence)).forEach(addEvidence); });
    listValue(events).forEach((event) => { const details = eventDetails(event); if (event.command || details.command) commands.push(event.command || details.command); if (event.stdout_ref) artifacts.push(event.stdout_ref); if (event.stderr_ref) artifacts.push(event.stderr_ref); });
    const unique = (items) => [...new Set(items.filter(Boolean).map(String))];
    renderEvidenceList($("file-evidence"), unique(files), "No file evidence recorded.");
    renderEvidenceList($("command-evidence"), unique(commands).concat(unique(artifacts).map((ref) => `artifact: ${ref}`)), "No command evidence recorded.");
    renderClaimsList($("claim-evidence"), collectClaims(run, events));
    renderEvidenceList($("session-evidence"), unique([...sessions]), "No sessions recorded.");
    setText("evidence-count", `${unique(files).length + unique(commands).length + unique(artifacts).length + sessions.size + collectClaims(run, events).length} records`);
  }

  function collectClaims(run, events) {
    const claims = [];
    const add = (value, inheritedRefs, explicit = false) => {
      if (Array.isArray(value)) { value.forEach((item) => add(item, inheritedRefs, explicit)); return; }
      if (!value || typeof value !== "object") { if (value) claims.push({ summary: value, refs: inheritedRefs || [] }); return; }
      const record = mapping(value);
      if (Array.isArray(record.claims)) add(record.claims, inheritedRefs, true);
      if (record.claim !== undefined || explicit) {
        const claim = mapping(record.claim);
        const summary = claim.summary || claim.claim || claim.text || claim.description || record.summary || record.text || record.description || (typeof record.claim === "string" ? record.claim : "Unlabelled claim");
        claims.push({ summary, refs: claimRefs(claim).concat(claimRefs(record), inheritedRefs || []) });
      }
    };
    add(run.claims, [], true);
    allClaimEvidence(run.evidence).forEach((entry) => add(entry, [], true));
    listValue(events).forEach((event) => add(mapping(event).claim || eventDetails(event).claims, [], true));
    const seen = new Set();
    return claims.filter((claim) => { const key = `${String(claim.summary)}|${claim.refs.join(",")}`; if (seen.has(key)) return false; seen.add(key); return true; });
  }

  function allClaimEvidence(evidence) {
    const result = [];
    listValue(evidence).forEach((item) => {
      const record = mapping(item); const details = mapping(record.details);
      if (record.claim !== undefined) result.push({ claim: record.claim, ...record });
      if (details.claim !== undefined) result.push({ claim: details.claim, ...details });
      if (Array.isArray(record.claims)) result.push(...record.claims);
      if (Array.isArray(details.claims)) result.push(...details.claims);
    });
    return result;
  }

  function claimRefs(value) {
    const record = mapping(value); const refs = [];
    if (record.path) refs.push(`${record.path}${record.line ? `:${record.line}` : ""}`);
    if (record.command) refs.push(`command=${record.command}`);
    if (record.artifact_ref) refs.push(`artifact=${record.artifact_ref}`);
    listValue(record.references || record.evidence).forEach((item) => refs.push(...claimRefs(item)));
    return refs;
  }

  function renderClaimsList(node, claims) {
    clear(node);
    if (!claims.length) { node.appendChild(create("p", "No claims recorded.", "empty-state")); return; }
    claims.forEach((claim) => {
      const record = create("div", undefined, "evidence-record claim-record");
      record.appendChild(create("strong", claim.summary));
      if (claim.refs.length) record.appendChild(create("span", `References: ${claim.refs.join(" · ")}`));
      else record.appendChild(create("span", "No supporting reference recorded."));
      node.appendChild(record);
    });
  }

  function renderEvidenceList(node, values, empty) {
    clear(node); if (!values.length) { node.appendChild(create("p", empty, "empty-state")); return; }
    values.forEach((value) => { const record = create("div", undefined, "evidence-record"); record.appendChild(create("strong", value)); record.appendChild(create("span", "Recorded by the server snapshot.")); node.appendChild(record); });
  }

  async function exportReport(format) {
    if (!currentRun || !currentRun.run_id) return;
    try {
      const result = await api(`/api/runs/${encodeURIComponent(currentRun.run_id)}/export`, { method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify({ format }) });
      setText("patch-action-status", `Exported ${format} report to ${text(result.path)}.`); announce(`${format} evidence export completed.`);
    } catch (error) { announce(`Export failed: ${error.message}`); setText("patch-action-status", `Export failed: ${error.message}`); }
  }

  async function patchAction(action) {
    if (!currentRun || !currentRun.run_id) return;
    const patch = mapping(currentRun.patch); const actor = $("actor-input").value.trim(); const timestamp = $("timestamp-input").value.trim();
    const payload = { actor, timestamp, patch_digest: patch.digest };
    try {
      const endpoint = action === "request-diagnosis" ? ACTION_ENDPOINTS.diagnosis : ACTION_ENDPOINTS[action];
      await api(`/api/runs/${encodeURIComponent(currentRun.run_id)}${endpoint}`, { method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify(payload) });
      await loadRun();
      announce(`Server accepted ${action.replaceAll("-", " ")}; snapshot and events refreshed.`);
    } catch (error) { setText("patch-action-status", `Action not accepted: ${error.message}`); announce(`Action not accepted: ${error.message}`); }
  }

  async function verifyPatch() {
    if (!currentRun || !currentRun.run_id) return;
    const input = snapshotInput(currentRun); const patch = mapping(currentRun.patch); const plan = mapping(currentRun.verification).plan || {};
    try {
      await api(`/api/runs/${encodeURIComponent(currentRun.run_id)}${ACTION_ENDPOINTS.verify}`, { method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify({ original_failure_command: plan.original_failure_command || input.command, regression_test: plan.regression_test || patch.regression_test || input.command, relevant_tests: listValue(plan.relevant_tests), broader_suite: plan.broader_suite }) });
      await loadRun();
      announce("Verification request accepted; snapshot and events refreshed.");
    } catch (error) { announce(`Verification request not accepted: ${error.message}`); }
  }

  $("refresh-button").addEventListener("click", loadRun);
  $("export-markdown").addEventListener("click", () => exportReport("markdown"));
  $("export-json").addEventListener("click", () => exportReport("json"));
  $("approve-button").addEventListener("click", () => patchAction("approve"));
  $("reject-button").addEventListener("click", () => patchAction("reject"));
  $("diagnosis-button").addEventListener("click", () => patchAction("request-diagnosis"));
  if ($("verify-button")) $("verify-button").addEventListener("click", verifyPatch);
  loadRun();
}());
