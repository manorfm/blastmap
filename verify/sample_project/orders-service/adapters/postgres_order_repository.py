"""SQLAlchemy-backed persistence adapter for orders."""
from __future__ import annotations

import os

from sqlalchemy import Column, Integer, Numeric, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from domain.order import Order as DomainOrder
from domain.order import OrderStatus
from domain.ports import OrderRepositoryPort

Base = declarative_base()

_DATABASE_URL = os.environ.get(
    "ORDERS_DATABASE_URL", "postgresql+psycopg2://orders:orders@localhost:5432/orders"
)


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(String, primary_key=True)
    sku = Column(String, nullable=False)
    qty = Column(Integer, nullable=False)
    amount = Column(Numeric, nullable=False)
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)


class PostgresOrderRepository(OrderRepositoryPort):
    def __init__(self) -> None:
        self._engine = create_engine(_DATABASE_URL, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, future=True)

    def save(self, order: DomainOrder) -> None:
        with self._session_factory() as session:
            row = session.get(Order, order.order_id)
            if row is None:
                row = Order(order_id=order.order_id)
                session.add(row)
            row.sku = order.sku
            row.qty = order.qty
            row.amount = order.amount
            row.currency = order.currency
            row.status = order.status.value
            session.commit()

    def get(self, order_id: str) -> DomainOrder | None:
        with self._session_factory() as session:
            row = session.get(Order, order_id)
            if row is None:
                return None
            return DomainOrder(
                order_id=row.order_id,
                sku=row.sku,
                qty=row.qty,
                amount=float(row.amount),
                currency=row.currency,
                payment_token="",
                status=OrderStatus(row.status),
            )
