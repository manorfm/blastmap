from pathlib import Path

from orbitkb.security.findings import find_security_findings
from orbitkb.security.redaction import redact_sensitive_values


def test_hardcoded_secret_is_reported_without_exposing_its_value(tmp_path: Path):
    source = tmp_path / "settings.py"
    source.write_text('PAYMENT_API_KEY = "do-not-index-this"\n', encoding="utf-8")

    findings = find_security_findings(tmp_path)

    assert findings[0].kind == "hardcoded_secret"
    assert findings[0].file_path == "settings.py"
    assert "do-not-index-this" not in findings[0].reason


def test_tracked_dotenv_is_a_security_finding_but_an_untracked_one_is_not(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("API_KEY=value\n", encoding="utf-8")

    assert find_security_findings(tmp_path, is_tracked=lambda _: True)[0].kind == "tracked_dotenv"
    assert find_security_findings(tmp_path, is_tracked=lambda _: False) == []


def test_redaction_removes_secret_values_from_source_evidence():
    text = 'token = "private-value"\nAuthorization: Bearer abc.def.ghi\n'

    redacted = redact_sensitive_values(text)

    assert "private-value" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "[REDACTED]" in redacted
