"""Fixture builders for benchmark tasks, seeded without discovery or an LLM."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import messages as messages_repo
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


def build_pix_fixture(db_path: Path) -> sqlite3.Connection:
    """A reviewed four-service payment graph used by change-surface goldens.

    The retrieval envelope intentionally contains all four indexed services because
    two-hop expansion reaches the complete small graph; that is a recorded baseline,
    not a claim that each service must change for every payment task.
    """
    conn = open_db(db_path)
    checkout_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    order_id = services_repo.ensure_service(conn, "order-service", "/tmp/order", "python")
    notification_id = services_repo.ensure_service(conn, "notification-service", "/tmp/notification", "python")

    services_repo.update_service_overview(conn, checkout_id, "Owns the checkout entry point and forwards payment method.", "L")
    services_repo.update_service_overview(conn, payments_id, "Owns payment method resolution and payment authorization.", "L")
    services_repo.update_service_overview(conn, order_id, "Consumes payment confirmation to create orders.", "L")
    services_repo.update_service_overview(conn, notification_id, "Sends emails when an order ships.", "L")

    checkout_api = apis_repo.upsert_api(conn, checkout_id, "POST", "/checkout", "starts checkout", "desc", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, checkout_id, checkout_api,
        [{
            "to_service_name": "payments-service", "call_kind": "http",
            "reason": "authorize the pix payment for the order", "data_needed": ["amount", "pix_key"],
            "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    order_api = apis_repo.upsert_api(conn, order_id, "POST", "/orders", "creates order", "desc", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, order_id, order_api,
        [
            {
                "to_service_name": "payments-service", "call_kind": "http",
                "reason": "check payment confirmation status", "data_needed": ["order_id"],
                "purpose_kind": "data_fetch", "confidence": 0.7,
            },
            {
                "to_service_name": "shipping-service", "call_kind": "http",
                "reason": "schedule delivery once the order is confirmed", "data_needed": ["order_id"],
                "purpose_kind": "other", "confidence": 0.6, "target_kind": "unknown",
            },
        ],
        [],
    )
    payments_api = apis_repo.upsert_api(conn, payments_id, "POST", "/charge", "charges a card", "desc", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, payments_id, payments_api,
        [{
            "to_service_name": "Stripe API", "call_kind": "http",
            "reason": "charge the customer's card via the vendor gateway", "data_needed": ["amount", "pix_key"],
            "purpose_kind": "data_fetch", "confidence": 0.85, "target_kind": "external",
        }],
        [],
    )
    service_calls_repo.reconcile_service_call_targets(conn)
    messages_repo.replace_messages(
        conn, payments_id,
        [{"direction": "publishes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    messages_repo.replace_messages(
        conn, notification_id,
        [{"direction": "consumes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    search_repo.rebuild_search_index(conn)
    return conn
