"""Composition root for orders-service.

Creates the FastAPI app, constructs the concrete adapters, and wires them
into the use cases via their ports before the HTTP router is mounted.
"""
from __future__ import annotations

from fastapi import FastAPI

from adapters.http import orders_controller
from adapters.http.orders_controller import router
from adapters.inventory_client import InventoryClient
from adapters.kafka_event_publisher import KafkaEventPublisher
from adapters.postgres_order_repository import PostgresOrderRepository
from application.cancel_order_use_case import CancelOrderUseCase
from application.get_order_use_case import GetOrderUseCase

app = FastAPI(title="orders-service")

orders_controller.inventory_client = InventoryClient()
orders_controller.order_repository = PostgresOrderRepository()
orders_controller.event_publisher = KafkaEventPublisher()
orders_controller.get_order_use_case = GetOrderUseCase()
orders_controller.cancel_order_use_case = CancelOrderUseCase(
    repo=orders_controller.order_repository, events=orders_controller.event_publisher,
)

app.include_router(router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "orders-service"}
