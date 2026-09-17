"""Deterministic fallback classifier for whether a call target is internal to this
system or a third-party integration, used ONLY when the LLM's own per-call judgment
(made with real code context at generation time) came back "unknown". This never
overrides a confident LLM or reconciliation (ground-truth) classification — it only
fills the gap for names neither of those could resolve.

Two signals, in precedence order:
1. A known-vendor keyword match (best-effort, extend the list as new vendors show up)
   — a stronger, more specific signal than the naming-convention check below.
2. Whether the name follows the same naming convention as services already indexed in
   this system (e.g. a shared "-service"/"-svc"/"-api" suffix) — the idea being that an
   unmapped dependency that looks like it belongs to this same architecture is more
   likely an internal service nobody has indexed yet than a stranger's product.
"""
from __future__ import annotations

import re

# Lowercase substrings of common third-party vendor/SaaS names. Best-effort and
# deliberately small — a false negative just falls through to "unknown", which is
# always safe; a false positive here would wrongly label an internal service external,
# so keep this to genuinely unambiguous vendor brand names only.
_KNOWN_VENDORS = (
    "stripe", "twilio", "sendgrid", "mailgun", "paypal", "braintree", "plaid",
    "aws", "amazon s3", "s3", "azure", "gcp", "google cloud", "firebase",
    "slack", "github", "gitlab", "salesforce", "hubspot", "zendesk", "shopify",
    "datadog", "segment", "mixpanel", "sentry", "auth0", "okta", "cloudflare",
    "algolia", "elasticsearch cloud", "openai", "anthropic",
)

_SUFFIX_RE = re.compile(r"[-_]([a-z]+)$")


def _matches_known_vendor(name: str) -> bool:
    lowered = name.lower()
    return any(vendor in lowered for vendor in _KNOWN_VENDORS)


def _matches_naming_convention(name: str, known_service_names: set[str]) -> bool:
    if not known_service_names:
        return False
    match = _SUFFIX_RE.search(name.lower())
    if not match:
        return False
    suffix = match.group(1)
    known_suffixes = {
        m.group(1) for known in known_service_names if (m := _SUFFIX_RE.search(known.lower()))
    }
    return suffix in known_suffixes


def classify_target_kind(name: str, known_service_names: set[str]) -> str:
    """Best-effort 'internal' | 'external' | 'unknown' guess from the name alone."""
    if not name or not name.strip():
        return "unknown"
    if name.lower() in {n.lower() for n in known_service_names}:
        return "internal"
    if _matches_known_vendor(name):
        return "external"
    if _matches_naming_convention(name, known_service_names):
        return "internal"
    return "unknown"
