from .models import Receipt, ReceiptItem
from user.models import User
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

class ReceiptData:
    user_id: str
    purchase_date: Optional[datetime] = None
    total_amount: Optional[Decimal] = None
    image_url: str
    status: Optional[str] = 'pending'
    items: Optional[List[ReceiptItem]] = None
