"""Get-order use case.

NOTE (known architecture shortcut): this use case should receive an
OrderRepositoryPort through its constructor the same way CreateOrderUseCase
and CancelOrderUseCase do. Instead it reaches into the concrete
PostgresOrderRepository adapter directly, which violates the hexagonal
dependency rule. Left as-is intentionally; do not copy this pattern elsewhere.
"""
from __future__ import annotations

from adapters.postgres_order_repository import PostgresOrderRepository
from domain.order import Order


class GetOrderUseCase:
    def __init__(self) -> None:
        # Shortcut: bypasses OrderRepositoryPort and depends on the concrete adapter.
        self._repo = PostgresOrderRepository()

    def execute(self, order_id: str) -> Order | None:
        return self._repo.get(order_id)
