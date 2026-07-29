from decimal import Decimal, InvalidOperation
from typing import Any


def format_quantity(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        quantity = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return str(value)

    if quantity == quantity.to_integral_value():
        return format(quantity.quantize(Decimal("1")), "f")

    text = format(quantity.normalize(), "f")
    return text.rstrip("0").rstrip(".")
