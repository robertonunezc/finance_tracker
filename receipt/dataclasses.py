from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from receipt.models import Category


@dataclass
class ReceiptItem:
    name: str
    price: float
    quantity: Optional[int] = 1
    category: Optional[Category] = Category.OTHER

@dataclass
class ReceiptData:
    user_id: str
    purchase_date: Optional[datetime] = None
    total_amount: Optional[Decimal] = None
    image_url: str
    status: Optional[str] = 'pending'
    items: Optional[List[ReceiptItem]] = None
