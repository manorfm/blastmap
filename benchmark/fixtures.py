"""Fixture builders for benchmark tasks. Each returns an open sqlite3.Connection
seeded directly through the repository modules (no discovery, no LLM) — the same
technique tests.test_change_surface._build_pix_fixture already uses, just with a
different graph shape so the benchmark exercises more than one retrieval pattern.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo


def build_catalog_fixture(db_path: Path) -> sqlite3.Connection:
    """Two disconnected components, to exercise multi-hop graph expansion without
    any accidental keyword overlap between them:

      search-service --HTTP--> catalog-service --HTTP--> recommendation-service
      billing-service --HTTP--> ledger-service

    Vocabulary is deliberately non-overlapping and doesn't reuse the word "service"
    (every "-service" name already tokenizes to a "service" token, which would
    otherwise keyword-match everything and defeat the point of the benchmark).
    """
    conn = open_db(db_path)

    search_id = services_repo.ensure_service(conn, "search-service", "/tmp/search", "python")
    catalog_id = services_repo.ensure_service(conn, "catalog-service", "/tmp/catalog", "python")
    recommendation_id = services_repo.ensure_service(conn, "recommendation-service", "/tmp/recommendation", "python")
    billing_id = services_repo.ensure_service(conn, "billing-service", "/tmp/billing", "python")
    ledger_id = services_repo.ensure_service(conn, "ledger-service", "/tmp/ledger", "python")

    services_repo.update_service_overview(conn, search_id, "Handles search requests.", "L")
    services_repo.update_service_overview(conn, catalog_id, "Manages the product catalog.", "L")
    services_repo.update_service_overview(conn, recommendation_id, "Generates product recommendations.", "L")
    services_repo.update_service_overview(conn, billing_id, "Records billing transactions.", "L")
    services_repo.update_service_overview(conn, ledger_id, "Maintains the financial ledger.", "L")

    search_api = apis_repo.upsert_api(conn, search_id, "GET", "/search", "s", "d", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, search_id, search_api,
        [{"to_service_name": "catalog-service", "call_kind": "http", "reason": "fetch product details",
          "data_needed": [], "purpose_kind": "data_fetch", "confidence": 0.9}],
        [],
    )

    catalog_api = apis_repo.upsert_api(conn, catalog_id, "GET", "/products", "s", "d", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, catalog_id, catalog_api,
        [{"to_service_name": "recommendation-service", "call_kind": "http", "reason": "fetch related items",
          "data_needed": [], "purpose_kind": "enrichment", "confidence": 0.9}],
        [],
    )

    billing_api = apis_repo.upsert_api(conn, billing_id, "POST", "/bill", "s", "d", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, billing_id, billing_api,
        [{"to_service_name": "ledger-service", "call_kind": "http", "reason": "record the transaction",
          "data_needed": [], "purpose_kind": "notification", "confidence": 0.9}],
        [],
    )

    search_repo.rebuild_search_index(conn)
    return conn
