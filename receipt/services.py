from .models import Receipt, ReceiptItem
from user.models import User
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from .dataclasses import ReceiptData

def create_receipt(receipt_data:ReceiptData) -> ReceiptData:
        """
        Factory method to create an instance of UploadService.
        """
        receipt =Receipt.objects.create(
            user_id=receipt_data.user_id,
            purchase_date=receipt_data.purchase_date or datetime.now(),
            total_amount=receipt_data.total_amount or Decimal(0.0),
            image_url=receipt_data.image_url,
            status=receipt_data.status or 'pending'
        )
        saved_data = ReceiptData(
            user_id=receipt.user_id,
            purchase_date=receipt.purchase_date,
            total_amount=receipt.total_amount,
            image_url=receipt.image_url,
            status=receipt.status,
            items=list(receipt.items.all())
        )
        return saved_data
        