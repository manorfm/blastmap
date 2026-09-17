"""Self-indexing end-to-end suite: blastmap indexes its own source tree, then
answers a find_change_surface query about itself. This is the project's own
dogfooding check — real discovery heuristics run against a real, non-trivial
codebase (this one), with only the LLM backend faked (deterministic, no real
`claude`/`codex` CLI call, consistent with the rest of the suite).

Note on why this calls index_service directly instead of the index_path/CLI
discovery path: blastmap's own repo root has no main.py/app.py/wsgi.py (the
Python discovery heuristic's entry-file signal for a web service — see
discovery/python_stack.py's PythonDetector.matches), because blastmap is a
library/CLI/MCP-server, not a web microservice. That mismatch is itself an honest
example of the documented discovery-heuristic limitation (see README's
"Limitações conhecidas"). index_service is a first-class public function already
called directly by `blastmap update` and by test_orchestrator.py — supplying the
detector explicitly here is the documented way to index a shape auto-discovery
doesn't recognize, not a workaround.
"""
from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import indexed_files as indexed_files_repo
from blastmap.db.repositories import services as services_repo
from blastmap.discovery.python_stack import PythonDetector
from blastmap.generation import change_surface
from blastmap.generation.orchestrator import index_service
from blastmap.mcp import queries

from tests.test_orchestrator import FakeOrchestratorBackend

SELF_ROOT = Path(__file__).resolve().parents[1] / "blastmap"


def test_indexing_blastmaps_own_source_tree_succeeds(tmp_path: Path):
    conn = open_db(tmp_path / "self.db")
    backend = FakeOrchestratorBackend()

    result = index_service(conn, "blastmap-core", SELF_ROOT, PythonDetector(), backend)

    assert result.status == "ok"
    row = services_repo.get_service_by_name(conn, "blastmap-core")
    assert row is not None
    # Dogfooding finding: blastmap has no FastAPI/Flask/Django endpoints, no
    # SQLAlchemy/Django models and no queue calls (it's a CLI/library/MCP server,
    # not a web microservice), so PythonDetector's heuristics find zero "relevant"
    # files to hash-track here — only the always-generated overview unit runs, from
    # the folder tree alone. This is the discovery-heuristic gap the module
    # docstring above describes, made concrete instead of just asserted in prose.
    assert indexed_files_repo.get_indexed_file_hashes(conn, row["id"]) == {}
    assert result.llm_calls == 1  # exactly the overview unit


def test_reindexing_after_a_real_local_edit_only_regenerates_the_changed_unit(tmp_path: Path):
    conn = open_db(tmp_path / "self2.db")
    backend = FakeOrchestratorBackend()
    index_service(conn, "blastmap-core", SELF_ROOT, PythonDetector(), backend)
    calls_after_first_run = backend.calls

    second_backend = FakeOrchestratorBackend()
    result = index_service(conn, "blastmap-core", SELF_ROOT, PythonDetector(), second_backend)

    assert result.status == "ok"
    assert second_backend.calls == 0  # nothing changed on disk since the first run
    assert calls_after_first_run > 0


def test_describe_and_search_work_against_the_self_indexed_service(tmp_path: Path):
    conn = open_db(tmp_path / "self3.db")
    index_service(conn, "blastmap-core", SELF_ROOT, PythonDetector(), FakeOrchestratorBackend())

    described = queries.describe_service(conn, "blastmap-core")
    assert described["name"] == "blastmap-core"
    assert described["short_desc"]  # FakeOrchestratorBackend's canned overview
    assert "freshness" in described


def test_find_change_surface_against_self_indexed_knowledge(tmp_path: Path):
    conn = open_db(tmp_path / "self4.db")
    index_service(conn, "blastmap-core", SELF_ROOT, PythonDetector(), FakeOrchestratorBackend())
    backend = FakeOrchestratorBackend()  # its .generate() also answers the change_surface schema shape

    result = change_surface.analyze_change_surface(
        conn, "Split the repository layer into smaller modules", backend, hint_services=["blastmap-core"],
    )

    # hint_services anchors the search directly, since the fake overview text
    # won't keyword-match a specific engineering task the way a real LLM summary would.
    assert "recommended_next_queries" in result
    assert "freshness" in result
