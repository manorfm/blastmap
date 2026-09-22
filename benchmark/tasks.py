"""The benchmark task set: hand-picked, not mined from real history (there isn't a
real corpus yet — see README's "Desenvolvimento" section on growing this over time).
Each task records two manually reviewed expectations: services that must be
available for impact analysis, and the exact deterministic candidate envelope. The
latter makes candidate precision/recall measurable without pretending that it is a
verdict on the later LLM synthesis step.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from benchmark.fixtures import build_catalog_fixture, build_pix_fixture


@dataclass
class BenchmarkTask:
    id: str
    description: str
    build_fixture: Callable[[Path], sqlite3.Connection]
    expected_impacted_services: set[str]
    expected_candidates: set[str]
    hint_services: list[str] | None = None


TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        id="pix-checkout",
        description="Add support for Pix in checkout",
        build_fixture=build_pix_fixture,
        expected_impacted_services={"checkout-service", "payments-service"},
        expected_candidates={"checkout-service", "notification-service", "order-service", "payments-service"},
    ),
    BenchmarkTask(
        id="payment-authorization",
        description="Improve payment authorization flow",
        build_fixture=build_pix_fixture,
        expected_impacted_services={"payments-service"},
        expected_candidates={"checkout-service", "notification-service", "order-service", "payments-service"},
    ),
    BenchmarkTask(
        id="payment-notification",
        description="Notify customers when payment is authorized",
        build_fixture=build_pix_fixture,
        expected_impacted_services={"payments-service", "notification-service"},
        expected_candidates={"checkout-service", "notification-service", "order-service", "payments-service"},
    ),
    BenchmarkTask(
        id="order-confirmation",
        description="Change order confirmation logic",
        build_fixture=build_pix_fixture,
        expected_impacted_services={"order-service"},
        expected_candidates={"checkout-service", "notification-service", "order-service", "payments-service"},
    ),
    BenchmarkTask(
        id="pix-hint-anchored",
        description="xyz unrelated internal cleanup",
        build_fixture=build_pix_fixture,
        expected_impacted_services={"notification-service"},
        expected_candidates={"checkout-service", "notification-service", "order-service", "payments-service"},
        hint_services=["notification-service"],
    ),
    BenchmarkTask(
        id="search-two-hop",
        description="Fix search requests",
        build_fixture=build_catalog_fixture,
        expected_impacted_services={"search-service", "catalog-service", "recommendation-service"},
        expected_candidates={"search-service", "catalog-service", "recommendation-service"},
    ),
    BenchmarkTask(
        id="billing-one-hop",
        description="Record billing transactions",
        build_fixture=build_catalog_fixture,
        expected_impacted_services={"billing-service", "ledger-service"},
        expected_candidates={"billing-service", "ledger-service"},
    ),
    BenchmarkTask(
        id="catalog-hint-anchored",
        description="reorganize internal structure",
        build_fixture=build_catalog_fixture,
        expected_impacted_services={"recommendation-service"},
        expected_candidates={"search-service", "catalog-service", "recommendation-service"},
        hint_services=["recommendation-service"],
    ),
]
