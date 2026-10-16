/**
 * ProofFix's thin OmniRush/OpenCode boundary.
 *
 * The localhost server remains the only owner of run state.  These tools only
 * validate a small JSON argument shape, fetch the authoritative snapshot when
 * an action is state-gated, and delegate to the existing HTTP API.
 */

const LOCALHOST_BASE = "http://127.0.0.1:8000";
const RUN_ID_PATTERN = /^run-[A-Za-z0-9][A-Za-z0-9._-]*$/;
const SHA_PATTERN = /^[0-9a-fA-F]{7,64}$/;
const ACTOR_PATTERN = /^[^\x00\r\n]{1,256}$/;
const TIMESTAMP_PATTERN = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/;
const TOKEN_PATTERN = /^[A-Za-z0-9_./:=,@+%~-]+$/;
const MAX_TEXT_LENGTH = 4096;
const MAX_LIST_ITEMS = 128;
const MAX_TIMEOUT_SECONDS = 300;

type JsonObject = Record<string, unknown>;

function assertObject(value: unknown, label: string): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as JsonObject;
}

function assertRunId(value: unknown): string {
  if (typeof value !== "string" || value.length > 128 || !RUN_ID_PATTERN.test(value)) {
    throw new Error("run_id must be an opaque local run ID");
  }
  return value;
}

function assertActor(value: unknown): string {
  if (typeof value !== "string" || value.length > 256 || !ACTOR_PATTERN.test(value) || !value.trim()) {
    throw new Error("actor is required");
  }
  return value.trim();
}

function assertTimestamp(value: unknown): string {
  if (typeof value !== "string" || value.length > 30 || !TIMESTAMP_PATTERN.test(value)) {
    throw new Error("timestamp must be a UTC timestamp");
  }
  const parsed = Date.parse(value);
  const canonical = value.length === 20 ? `${value.slice(0, -1)}.000Z` : value;
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString() !== canonical) {
    throw new Error("timestamp must be a canonical UTC timestamp");
  }
  return value;
}

function assertDigest(value: unknown): string {
  if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error("patch_digest must be a sha256 digest");
  }
  return value;
}

function assertFormat(value: unknown): "markdown" | "json" {
  if (value === undefined || value === "markdown" || value === "md") return "markdown";
  if (value === "json") return "json";
  throw new Error("format must be markdown or json");
}

function assertKeys(value: JsonObject, allowed: readonly string[], label: string): void {
  for (const key of Object.keys(value)) {
    if (!allowed.includes(key)) throw new Error(`${label} contains an unsupported field: ${key}`);
  }
}

function assertBoundedText(value: unknown, label: string, max = MAX_TEXT_LENGTH, required = true): string {
  if (typeof value !== "string" || value.length > max || value.includes("\x00") || (required && !value.trim())) {
    throw new Error(`${label} must be bounded text`);
  }
  return value;
}

function assertCommand(value: unknown, label: string): string {
  const command = assertBoundedText(value, label);
  const tokens = command.trim().split(/\s+/);
  if (!tokens.length || tokens.some((token) => !TOKEN_PATTERN.test(token))) {
    throw new Error(`${label} has an unsafe command shape`);
  }
  return command;
}

function assertStringList(value: unknown, label: string, max = MAX_LIST_ITEMS, validator: (item: unknown, label: string) => string = assertBoundedText): string[] {
  if (!Array.isArray(value) || value.length > max) throw new Error(`${label} must be a bounded list`);
  return value.map((item, index) => validator(item, `${label}[${index}]`));
}

function assertScopePaths(value: unknown): string[] {
  return assertStringList(value, "scope_files", MAX_LIST_ITEMS, (item, label) => {
    const path = assertBoundedText(item, label, 512);
    if (path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path) || path.includes("\\") || path.split("/").includes("..")) {
      throw new Error(`${label} must be a repository-relative path`);
    }
    return path;
  });
}

function assertTimeout(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > MAX_TIMEOUT_SECONDS) {
    throw new Error("timeout_seconds must be a finite positive number no greater than 300");
  }
  return value;
}

function assertOptionalCommand(value: unknown, label: string): string | undefined {
  return value === undefined || value === null ? undefined : assertCommand(value, label);
}

function assertPlan(raw: unknown): JsonObject {
  const plan = assertObject(raw, "verification plan");
  assertKeys(plan, ["original_failure_command", "original_command", "regression_test", "relevant_tests", "broader_suite", "timeout_seconds", "scope_files"], "verification plan");
  assertCommand(plan.original_failure_command ?? plan.original_command, "original_failure_command");
  assertCommand(plan.regression_test, "regression_test");
  if (plan.relevant_tests !== undefined) assertStringList(plan.relevant_tests, "relevant_tests", 64, assertCommand);
  if (plan.broader_suite !== undefined && plan.broader_suite !== null) assertCommand(plan.broader_suite, "broader_suite");
  if (plan.timeout_seconds !== undefined) assertTimeout(plan.timeout_seconds);
  if (plan.scope_files !== undefined) assertScopePaths(plan.scope_files);
  return plan;
}

function localPath(runId: string, suffix = ""): string {
  return `/api/runs/${encodeURIComponent(runId)}${suffix}`;
}

async function request(path: string, method: "GET" | "POST", payload?: JsonObject): Promise<unknown> {
  const response = await fetch(`${LOCALHOST_BASE}${path}`, {
    method,
    headers: method === "POST" ? { "Content-Type": "application/json" } : undefined,
    body: method === "POST" ? JSON.stringify(payload ?? {}) : undefined,
  });
  const body = await response.text();
  let decoded: unknown;
  try {
    decoded = body ? JSON.parse(body) : {};
  } catch {
    throw new Error("ProofFix returned a malformed localhost response");
  }
  if (!response.ok) {
    const error = assertObject(decoded, "ProofFix error").error;
    throw new Error(typeof error === "string" ? error : `ProofFix request failed (${response.status})`);
  }
  return decoded;
}

async function authoritativeRun(runId: string): Promise<JsonObject> {
  const run = assertObject(await request(localPath(runId), "GET"), "run");
  return run;
}

function requireState(run: JsonObject, expected: string): void {
  if (run.state !== expected && run.status !== expected) {
    throw new Error(`run is not ready for this operation: ${String(run.state ?? run.status ?? "unknown")}`);
  }
}

async function startRun(raw: unknown): Promise<unknown> {
  const args = assertObject(raw, "start_run arguments");
  assertKeys(args, ["repository_path", "command", "failure_text", "constraints", "issue_url", "pr_url", "commit_sha", "branch"], "start_run arguments");
  assertBoundedText(args.repository_path, "repository_path");
  assertCommand(args.command, "command");
  assertBoundedText(args.failure_text, "failure_text");
  if (args.constraints !== undefined) assertStringList(args.constraints, "constraints", 16);
  if (args.issue_url !== undefined && args.issue_url !== null) assertBoundedText(args.issue_url, "issue_url");
  if (args.pr_url !== undefined && args.pr_url !== null) assertBoundedText(args.pr_url, "pr_url");
  if (args.commit_sha !== undefined && (typeof args.commit_sha !== "string" || !SHA_PATTERN.test(args.commit_sha))) {
    throw new Error("commit_sha must be a hexadecimal commit reference");
  }
  if (args.branch !== undefined && (typeof args.branch !== "string" || args.branch.length > 256 || !/^[A-Za-z0-9._/-]+$/.test(args.branch) || args.branch.startsWith("/") || args.branch.startsWith("-") || args.branch.includes(".."))) {
    throw new Error("branch is invalid");
  }
  return request("/api/runs", "POST", args);
}

async function getRun(raw: unknown): Promise<unknown> {
  const args = assertObject(raw, "get_run arguments");
  assertKeys(args, ["run_id"], "get_run arguments");
  return authoritativeRun(assertRunId(args.run_id));
}

async function approvalAction(raw: unknown, action: "approve" | "reject"): Promise<unknown> {
  const args = assertObject(raw, `${action}_patch arguments`);
  assertKeys(args, ["run_id", "actor", "timestamp", "patch_digest"], `${action}_patch arguments`);
  const runId = assertRunId(args.run_id);
  const actor = assertActor(args.actor);
  const timestamp = assertTimestamp(args.timestamp);
  const patchDigest = assertDigest(args.patch_digest);
  const run = await authoritativeRun(runId);
  requireState(run, "awaiting_approval");
  return request(`${localPath(runId)}/${action}`, "POST", {
    actor,
    timestamp,
    patch_digest: patchDigest,
  });
}

async function verifyRun(raw: unknown): Promise<unknown> {
  const args = assertObject(raw, "verify_run arguments");
  assertKeys(args, ["run_id", "plan"], "verify_run arguments");
  const runId = assertRunId(args.run_id);
  const plan = assertPlan(args.plan);
  requireState(await authoritativeRun(runId), "applying");
  return request(`${localPath(runId)}/verify`, "POST", { plan });
}

async function exportReport(raw: unknown): Promise<unknown> {
  const args = assertObject(raw, "export_report arguments");
  assertKeys(args, ["run_id", "format", "filename"], "export_report arguments");
  const runId = assertRunId(args.run_id);
  const format = assertFormat(args.format);
  if (args.filename !== undefined && (typeof args.filename !== "string" || !/^[A-Za-z0-9._-]{1,128}$/.test(args.filename))) {
    throw new Error("filename must be a simple relative filename");
  }
  await authoritativeRun(runId);
  return request(`${localPath(runId)}/export`, "POST", { format, filename: args.filename });
}

export const operations = {
  "proofix.start_run": startRun,
  "proofix.get_run": getRun,
  "proofix.approve_patch": (args: unknown) => approvalAction(args, "approve"),
  "proofix.reject_patch": (args: unknown) => approvalAction(args, "reject"),
  "proofix.verify_run": verifyRun,
  "proofix.export_report": exportReport,
};

export default async function ProofFixPlugin() {
  return {
    tool: {
      "proofix.start_run": { description: "Start a guarded ProofFix run.", execute: startRun },
      "proofix.get_run": { description: "Read the authoritative ProofFix run.", execute: getRun },
      "proofix.approve_patch": { description: "Approve a persisted patch after inspecting its evidence.", execute: (args: unknown) => approvalAction(args, "approve") },
      "proofix.reject_patch": { description: "Reject a persisted patch after inspecting its evidence.", execute: (args: unknown) => approvalAction(args, "reject") },
      "proofix.verify_run": { description: "Verify an approved patch through the orchestrator.", execute: verifyRun },
      "proofix.export_report": { description: "Export the server-owned evidence report.", execute: exportReport },
    },
  };
}
