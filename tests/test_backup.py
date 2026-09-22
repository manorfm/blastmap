from orbitkb.db.backup import backup_database, restore_database
from orbitkb.db.connection import open_db


def test_backup_and_restore_preserve_a_consistent_database(tmp_path):
    source = tmp_path / "source.db"
    conn = open_db(source)
    conn.execute("INSERT INTO schema_meta (key, value) VALUES ('marker', 'saved')")
    conn.commit()
    backup = tmp_path / "backup.db"
    backup_database(source, backup)
    restored = tmp_path / "restored.db"
    restore_database(backup, restored)
    assert open_db(restored).execute("SELECT value FROM schema_meta WHERE key = 'marker'").fetchone()["value"] == "saved"
