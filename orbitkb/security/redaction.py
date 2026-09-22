"""Value redaction applied before code/configuration evidence reaches an LLM."""
from __future__ import annotations

import re

_ASSIGNMENT = re.compile(
    r"(?im)(?P<prefix>\b\w*(?:password|secret|token|api[_-]?key|credential|private[_-]?key)\w*\s*[:=]\s*)(?P<value>[^\s#;,]+)"
)
_BEARER = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_PRIVATE_KEY = re.compile(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")


def redact_sensitive_values(text: str) -> str:
    """Returns usable evidence while replacing values, never keys or topology."""
    text = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _ASSIGNMENT.sub(r"\g<prefix>[REDACTED]", text)
    return _BEARER.sub(r"\1[REDACTED]", text)


def redact_structured_values(value: object) -> object:
    """Return a schema-shaped value with sensitive text removed recursively.

    Generation is intentionally schema-first, but schema validity alone cannot keep a
    backend from echoing a credential into a descriptive string. Applying the same
    redaction at the structured-output boundary protects every downstream store,
    embedding and MCP response without coupling repositories to LLM details.
    """
    if isinstance(value, str):
        return redact_sensitive_values(value)
    if isinstance(value, list):
        return [redact_structured_values(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_structured_values(item) for key, item in value.items()}
    return value
