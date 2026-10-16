"""The narrow, provider-neutral OmniRush session boundary."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Mapping

from .schemas import Hypothesis, SchemaError, validate_hypothesis


SPECIALIST_ROLES = (
    "stack-trace-analyst",
    "recent-change-analyst",
    "dependency-environment-analyst",
    "test-analyst",
    "code-path-analyst",
)


class OmniRushSessionClient:
    """Interface implemented by an OmniRush runtime adapter.

    This class intentionally contains no model-provider SDK or network logic.
    A host runtime may subclass it and provide ``request`` through its own
    OmniRush session transport.
    """

    mode = "live"

    def request(self, role: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError("an OmniRush host must provide the session transport")

    def run(self, role: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Compatibility spelling for hosts that expose a session ``run`` call."""
        return self.request(role, context)


class RecordedSessionClient(OmniRushSessionClient):
    """Offline client backed by checked-in, explicitly recorded responses."""

    mode = "recorded/demo"
    label = "recorded/demo"
    recorded = True

    def __init__(self, fixture_path: str | Path | Sequence[Mapping[str, Any]] | None = None, responses: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None):
        if isinstance(fixture_path, (list, tuple)) and responses is None:
            responses = fixture_path
            fixture_path = None
        if responses is not None:
            document: Mapping[str, Any] = {"mode": self.mode, "responses": responses}
        else:
            path = Path(fixture_path) if fixture_path is not None else Path(__file__).resolve().parents[2] / "fixtures" / "omnirush" / "reproducible-run.json"
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"could not load recorded OmniRush fixture: {path}") from exc
        if not isinstance(document, Mapping) or document.get("mode") != "recorded/demo":
            raise ValueError("recorded OmniRush fixtures must be labeled recorded/demo")
        raw = document.get("responses")
        if isinstance(raw, Mapping):
            self._responses = dict(raw)
        elif isinstance(raw, list):
            self._responses = {str(item.get("role")): item for item in raw if isinstance(item, Mapping) and item.get("role")}
        else:
            raise ValueError("recorded OmniRush fixture responses must be a list or object")
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.roles = tuple(self._responses)

    def request(self, role: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((role, context))
        if role not in self._responses:
            raise SchemaError(f"recorded response is missing specialist role: {role}")
        value = self._responses[role]
        if not isinstance(value, Mapping):
            raise SchemaError("recorded specialist response must be an object")
        return dict(value)


def run_specialists(context: Mapping[str, Any], client: OmniRushSessionClient) -> list[Hypothesis]:
    """Request and validate competing specialists without allowing status claims."""

    if not isinstance(context, Mapping):
        raise SchemaError("specialist context must be an object")
    if not isinstance(client, OmniRushSessionClient) and not hasattr(client, "request") and not hasattr(client, "run"):
        raise TypeError("client must provide an OmniRush request interface")
    hypotheses: list[Hypothesis] = []
    roles = tuple(getattr(client, "roles", SPECIALIST_ROLES))
    if not roles:
        raise SchemaError("no specialist roles are configured")
    for role in roles:
        if isinstance(client, OmniRushSessionClient) and type(client).request is OmniRushSessionClient.request and type(client).run is not OmniRushSessionClient.run:
            envelope = client.run(role, context)
        elif hasattr(client, "request"):
            envelope = client.request(role, context)
        else:
            envelope = client.run(role, context)
        if not isinstance(envelope, Mapping):
            raise SchemaError("specialist envelope must be an object")
        payload = envelope.get("response", envelope)
        if not isinstance(payload, Mapping):
            raise SchemaError("specialist response must be an object")
        candidate = dict(payload)
        candidate.setdefault("role", envelope.get("role", role))
        outer_session_id = envelope.get("session_id")
        nested_session_id = candidate.get("session_id")
        if outer_session_id is not None and not isinstance(outer_session_id, str):
            raise SchemaError("outer specialist session ID must be text")
        if nested_session_id is not None and not isinstance(nested_session_id, str):
            raise SchemaError("nested specialist session ID must be text")
        if outer_session_id is not None and nested_session_id is not None and outer_session_id != nested_session_id:
            raise SchemaError("nested specialist session ID does not match the outer session")
        if outer_session_id is not None:
            candidate["session_id"] = outer_session_id
        hypothesis = validate_hypothesis(candidate)
        if hypothesis.role != role:
            raise SchemaError("specialist role does not match requested role")
        hypotheses.append(hypothesis)
    return hypotheses
