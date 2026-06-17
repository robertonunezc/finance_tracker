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
    category: Optional[str] = 'other'
    embedding: Optional[List[float]] = None 

@dataclass
class ReceiptData:
    user_id: str
    image_url: str
    purchase_date: Optional[datetime] = None
    total_amount: Optional[Decimal] = None
    status: Optional[str] = 'pending'
    items: Optional[List[ReceiptItem]] = None
