from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, Integer, Numeric

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True)
    sku = Column(String, nullable=False)
    qty = Column(Integer, nullable=False)
    amount = Column(Numeric, nullable=False)
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)
