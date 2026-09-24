from pathlib import Path

from orbitkb.db.connection import open_db


def test_schema_initializes(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == "20"


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
    assert upgraded.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()["value"] == "20"


def test_schema_adds_selected_decisions_to_an_existing_change_plan(tmp_path: Path):
    path = tmp_path / "legacy-plan.db"
    conn = open_db(path)
    conn.execute("ALTER TABLE change_plan_runs DROP COLUMN selected_decisions_json")
    conn.execute("UPDATE schema_meta SET value = '17' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    upgraded = open_db(path)

    columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(change_plan_runs)")}
    assert "selected_decisions_json" in columns
    assert upgraded.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()["value"] == "20"


def test_schema_adds_change_units_to_an_existing_change_plan(tmp_path: Path):
    path = tmp_path / "legacy-plan-units.db"
    conn = open_db(path)
    conn.execute("ALTER TABLE change_plan_runs DROP COLUMN change_units_json")
    conn.execute("UPDATE schema_meta SET value = '17' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    upgraded = open_db(path)

    columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(change_plan_runs)")}
    assert "change_units_json" in columns
    assert upgraded.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()["value"] == "20"


def test_schema_upgrades_legacy_entrypoint_constraint_without_losing_contracts(tmp_path: Path):
    path = tmp_path / "legacy-entrypoints.db"
    conn = open_db(path)
    conn.execute("INSERT INTO services (name, root_path, updated_at) VALUES ('orders', '/repos/orders', 'now')")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        DROP TABLE entrypoints;
        CREATE TABLE entrypoints (
            id          INTEGER PRIMARY KEY,
            service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('http', 'graphql', 'message', 'cli', 'job', 'rpc')),
            method      TEXT NOT NULL,
            name        TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            start_line  INTEGER NOT NULL,
            end_line    INTEGER NOT NULL,
            updated_at  TEXT NOT NULL,
            UNIQUE(service_id, kind, method, name, symbol)
        );
        INSERT INTO entrypoints VALUES (7, 1, 'http', 'GET', '/orders', 'Orders.get', 'Orders.java', 2, 4, 'now');
        INSERT INTO entrypoint_contracts (entrypoint_id, contract_json) VALUES (7, '{"source":"legacy"}');
        """
    )
    conn.execute("UPDATE schema_meta SET value = '19' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    upgraded = open_db(path)

    entrypoint = upgraded.execute("SELECT * FROM entrypoints WHERE id = 7").fetchone()
    contract = upgraded.execute("SELECT contract_json FROM entrypoint_contracts WHERE entrypoint_id = 7").fetchone()
    assert entrypoint["kind"] == "http"
    assert contract["contract_json"] == '{"source":"legacy"}'
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema_preserves_old_architecture_findings_while_removing_kind_constraint(tmp_path: Path):
    path = tmp_path / "legacy.db"
    conn = open_db(path)
    conn.executescript(
        """
        DROP TABLE architecture_findings;
        CREATE TABLE architecture_findings (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES architecture_runs(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('cycle', 'fan_in', 'fan_out', 'shared_database', 'duplicate_external_integration')),
            severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')) DEFAULT 'info',
            services_json TEXT NOT NULL,
            detail_json TEXT,
            reason TEXT NOT NULL
        );
        INSERT INTO architecture_runs (created_at, services_indexed) VALUES ('2026-01-01', 1);
        INSERT INTO architecture_findings (run_id, kind, severity, services_json, detail_json, reason)
        VALUES (1, 'cycle', 'warning', '[\"orders\"]', '{}', 'old finding');
        """
    )
    conn.close()

    upgraded = open_db(path)

    assert upgraded.execute("SELECT kind FROM architecture_findings").fetchone()["kind"] == "cycle"
    upgraded.execute(
        """INSERT INTO architecture_findings (run_id, kind, severity, services_json, detail_json, reason)
           VALUES (1, 'possible_bff_domain_leakage', 'warning', '[\"orders\"]', '{}', 'new finding')"""
    )
    upgraded.execute(
        """INSERT INTO architecture_findings (run_id, kind, severity, services_json, detail_json, reason)
           VALUES (1, 'possible_read_entrypoint_side_effect', 'warning', '[\"orders\"]', '{}', 'newer finding')"""
    )
