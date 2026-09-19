"""Order aggregate root.

Pure domain logic: no framework imports, no adapter imports. This module must
stay importable with nothing installed but the standard library.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class InvalidOrderStateError(Exception):
    """Raised when an operation is attempted from an illegal order state."""


@dataclass
class Order:
    order_id: str
    sku: str
    qty: int
    amount: float
    currency: str
    payment_token: str
    status: OrderStatus = OrderStatus.PENDING_PAYMENT
    cancel_reason: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError("qty must be a positive integer")
        if self.amount <= 0:
            raise ValueError("amount must be a positive number")
        if not self.currency or len(self.currency) != 3:
            raise ValueError("currency must be a 3-letter ISO code")

    def confirm(self) -> None:
        if self.status != OrderStatus.PENDING_PAYMENT:
            raise InvalidOrderStateError(
                f"cannot confirm order {self.order_id} from status {self.status}"
            )
        self.status = OrderStatus.CONFIRMED

    def cancel(self, reason: str) -> None:
        if self.status == OrderStatus.CANCELLED:
            raise InvalidOrderStateError(f"order {self.order_id} is already cancelled")
        self.status = OrderStatus.CANCELLED
        self.cancel_reason = reason
