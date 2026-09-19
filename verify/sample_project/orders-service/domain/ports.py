"""Abstract ports (hexagonal architecture boundaries).

Application use cases depend only on these interfaces, never on the concrete
adapters that implement them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.order import Order


@dataclass
class ChargeResult:
    transaction_id: str
    status: str


class PaymentGatewayPort(ABC):
    @abstractmethod
    def charge(self, amount: float, currency: str, payment_token: str, order_id: str) -> ChargeResult:
        ...


class InventoryPort(ABC):
    @abstractmethod
    def check_stock(self, sku: str, qty: int) -> bool:
        ...


class OrderRepositoryPort(ABC):
    @abstractmethod
    def save(self, order: Order) -> None:
        ...

    @abstractmethod
    def get(self, order_id: str) -> Order | None:
        ...


class EventPublisherPort(ABC):
    @abstractmethod
    def publish(self, topic: str, payload: dict) -> None:
        ...
