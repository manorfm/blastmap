from fastapi import APIRouter

router = APIRouter()


def format_total(amount, tax):
    return amount + tax


class OrdersController:
    @router.post("/orders")
    def create_order(self, payload: dict):
        total = format_total(payload["amount"], payload["tax"])
        return {"total": total}
