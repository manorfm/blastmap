from pathlib import Path

from orbitkb.db.connection import open_db


def test_schema_initializes(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == "3"


def test_schema_adds_message_version_to_an_existing_static_contract_table(tmp_path: Path):
    path = tmp_path / "legacy.db"
    conn = open_db(path)
    conn.execute("ALTER TABLE static_message_contracts DROP COLUMN message_version")
    conn.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    upgraded = open_db(path)

    columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(static_message_contracts)")}
    assert "message_version" in columns
    assert upgraded.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()["value"] == "3"
