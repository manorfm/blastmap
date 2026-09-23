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


def test_aws_access_key_literal_is_reported_without_a_nearby_secret_keyword(tmp_path: Path):
    """`accessKeyId` contains none of _SECRET_LITERAL's keywords (password,
    secret, token, api_key, credential) -- the key's own literal FORMAT is
    the proof here, not the variable name next to it."""
    source = tmp_path / "settings.py"
    source.write_text('accessKeyId = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")

    findings = find_security_findings(tmp_path)

    assert any(f.kind == "cloud_credential_literal" and f.severity == "error" for f in findings)


def test_azure_connection_string_literal_is_reported(tmp_path: Path):
    source = tmp_path / "settings.py"
    source.write_text(
        'CONN = "DefaultEndpointsProtocol=https;AccountName=orders;'
        'AccountKey=abcd1234ABCD5678efgh9012EFGH3456ijkl==;EndpointSuffix=core.windows.net"\n',
        encoding="utf-8",
    )

    findings = find_security_findings(tmp_path)

    assert any(f.kind == "cloud_credential_literal" for f in findings)


def test_pem_private_key_block_is_reported(tmp_path: Path):
    source = tmp_path / "settings.py"
    source.write_text(
        'KEY = "-----BEGIN PRIVATE KEY-----\\nMIIExampleKeyData\\n-----END PRIVATE KEY-----"\n',
        encoding="utf-8",
    )

    findings = find_security_findings(tmp_path)

    assert any(f.kind == "cloud_credential_literal" for f in findings)


def test_unrelated_source_has_no_cloud_credential_finding(tmp_path: Path):
    source = tmp_path / "settings.py"
    source.write_text("QUEUE_NAME = 'orders-queue'\n", encoding="utf-8")

    findings = find_security_findings(tmp_path)

    assert not any(f.kind == "cloud_credential_literal" for f in findings)


def test_redaction_removes_aws_access_key_and_azure_connection_string():
    text = (
        'accessKeyId = "AKIAIOSFODNN7EXAMPLE"\n'
        'conn = "AccountKey=abcd1234ABCD5678efgh9012EFGH3456ijkl=="\n'
    )

    redacted = redact_sensitive_values(text)

    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "abcd1234ABCD5678efgh9012EFGH3456ijkl==" not in redacted
    assert "[REDACTED]" in redacted
