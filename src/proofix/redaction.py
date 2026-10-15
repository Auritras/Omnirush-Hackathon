"""Conservative redaction for model, browser, and report-visible text."""

from __future__ import annotations

import re
from typing import Mapping


_PRIVATE_KEY = re.compile(
    r"(?ms)(?P<header>-----BEGIN [^-\r\n]*PRIVATE KEY-----)(?P<body>.*?)(?P<footer>-----END [^-\r\n]*PRIVATE KEY-----)"
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{6,})")
_GITHUB_TOKEN = re.compile(r"(?i)\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_\-]{10,}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)(?P<prefix>^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_.-]*"
    r"(?:password|passwd|pwd|api[_-]?key|api[_-]?token|access[_-]?token|secret|credential|private[_-]?key|auth[_-]?token|github[_-]?token)"
    r"[ \t]*(?:=|:)[ \t]*)(?P<quote>[\"']?)(?P<value>[^\"'\r\n#]*?)(?P=quote)(?P<tail>[ \t]*(?:#.*)?$)"
)
_URL_CREDENTIAL = re.compile(r"(?i)(://[^\s:/]+:)([^@\s/]+)(@)")
_SENSITIVE_NAME = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|api[_-]?token|access[_-]?token|private[_-]?key|credential|auth)"
)


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact common credentials while retaining surrounding text and newlines.

    The second return value contains non-sensitive category labels, never the
    matched secret itself.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    findings: list[str] = []

    def mark(label: str) -> None:
        if label not in findings:
            findings.append(label)

    def private_replacement(match: re.Match[str]) -> str:
        mark("private_key")
        body = match.group("body")
        line_count = body.count("\n")
        if line_count == 0:
            return f"{match.group('header')}[REDACTED]{match.group('footer')}"
        return f"{match.group('header')}" + "\n".join(
            "[REDACTED]" if line else "" for line in body.split("\n")
        ) + f"{match.group('footer')}"

    result = _PRIVATE_KEY.sub(private_replacement, text)

    def bearer_replacement(match: re.Match[str]) -> str:
        mark("bearer_token")
        return f"{match.group(1)}[REDACTED]"

    result = _BEARER.sub(bearer_replacement, result)

    def github_replacement(match: re.Match[str]) -> str:
        mark("github_token")
        return "[REDACTED]"

    result = _GITHUB_TOKEN.sub(github_replacement, result)

    def assignment_replacement(match: re.Match[str]) -> str:
        if not match.group("value"):
            return match.group(0)
        mark("credential_assignment")
        return f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}{match.group('tail')}"

    result = _SECRET_ASSIGNMENT.sub(assignment_replacement, result)

    def url_replacement(match: re.Match[str]) -> str:
        mark("url_credential")
        return f"{match.group(1)}[REDACTED]{match.group(3)}"

    result = _URL_CREDENTIAL.sub(url_replacement, result)
    return result, findings


def redact_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Return an environment mapping with sensitive values masked by default."""

    if not isinstance(environ, Mapping):
        raise TypeError("environ must be a mapping")
    result: dict[str, str] = {}
    for name, value in environ.items():
        name_text = str(name)
        value_text = str(value)
        if _SENSITIVE_NAME.search(name_text):
            result[name_text] = "[REDACTED]"
            continue
        cleaned, _ = redact_text(value_text)
        result[name_text] = cleaned
    return result
