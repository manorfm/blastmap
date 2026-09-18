from pathlib import Path

from blastmap.db.connection import open_db


def test_schema_initializes(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == "2"
