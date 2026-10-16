# ProofFix skill boundary

ProofFix is a localhost-first, read-first failure investigation workflow. The
orchestrator and its append-only store own every run state transition. The
OmniRush plugin is only a guarded adapter over that existing API; it does not
maintain a second run state or edit the source checkout.

## Permissions and operating modes

- Plugin operations may call only the configured localhost ProofFix API.
- `proofix.start_run` accepts the bounded intake fields documented by the API.
- `proofix.get_run` reads the authoritative snapshot and event-derived state.
- Approval, rejection, verification, and report export are explicit API calls.
- The plugin has no unrestricted file access, shell access, process spawning, or
  direct generative-provider call.
- The default GitHub adapter is read-only and uses `gh` only after the user
  explicitly requests a GitHub read. GitHub writes and PR creation remain a
  separately approval-gated stretch feature.

The checked-in demo uses the `recorded-fixture` / `recorded/demo` responses and requires no
network, credentials, GitHub CLI, or model provider. Live GitHub reads may be
blocked when `gh` is unavailable, unauthenticated, or unable to reach GitHub;
the result includes safe next steps rather than pretending the read succeeded.

## Evidence requirements

Reproduction must run before diagnosis or patch work. Claims and hypotheses
must cite structured evidence (commands, files, tests, logs, issue text, or
artifact references). Large command output stays in bounded artifacts and is
redacted before persistence, browser display, or report export. A missing or
materially different reproduction stops the workflow as `not_reproduced` or
`blocked`.

## Approval boundary

Patch proposals remain in `awaiting_approval` until a human supplies the
current patch digest, actor, and UTC timestamp. The plugin validates that state
before delegating `approve_patch` or `reject_patch`; it cannot apply a patch by
itself. Verification is allowed only from `applying`, and the server records
the real command outcomes. Markdown and JSON reports are exported from the
server-owned run and event records.
