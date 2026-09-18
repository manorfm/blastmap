"""The benchmark task set: hand-picked, not mined from real history (there isn't a
real corpus yet — see README's "Desenvolvimento" section on growing this over time).
Each task's expected_services is a floor, not a ceiling: retrieval is allowed to
return extra candidates (that's what the LLM step is for), so a benchmark run checks
expected_services ⊆ candidates, not equality.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from benchmark.fixtures import build_catalog_fixture
from tests.test_change_surface import _build_pix_fixture


@dataclass
class BenchmarkTask:
    id: str
    description: str
    build_fixture: Callable[[Path], sqlite3.Connection]
    expected_services: set[str]
    hint_services: list[str] | None = None


TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        id="pix-checkout",
        description="Add support for Pix in checkout",
        build_fixture=_build_pix_fixture,
        expected_services={"checkout-service", "payments-service"},
    ),
    BenchmarkTask(
        id="payment-authorization",
        description="Improve payment authorization flow",
        build_fixture=_build_pix_fixture,
        expected_services={"payments-service"},
    ),
    BenchmarkTask(
        id="payment-notification",
        description="Notify customers when payment is authorized",
        build_fixture=_build_pix_fixture,
        expected_services={"payments-service", "notification-service"},
    ),
    BenchmarkTask(
        id="order-confirmation",
        description="Change order confirmation logic",
        build_fixture=_build_pix_fixture,
        expected_services={"order-service"},
    ),
    BenchmarkTask(
        id="pix-hint-anchored",
        description="xyz unrelated internal cleanup",
        build_fixture=_build_pix_fixture,
        expected_services={"notification-service"},
        hint_services=["notification-service"],
    ),
    BenchmarkTask(
        id="search-two-hop",
        description="Fix search requests",
        build_fixture=build_catalog_fixture,
        expected_services={"search-service", "catalog-service", "recommendation-service"},
    ),
    BenchmarkTask(
        id="billing-one-hop",
        description="Record billing transactions",
        build_fixture=build_catalog_fixture,
        expected_services={"billing-service", "ledger-service"},
    ),
    BenchmarkTask(
        id="catalog-hint-anchored",
        description="reorganize internal structure",
        build_fixture=build_catalog_fixture,
        expected_services={"recommendation-service"},
        hint_services=["recommendation-service"],
    ),
]
