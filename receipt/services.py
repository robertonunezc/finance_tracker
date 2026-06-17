from .models import Receipt, ReceiptItem
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from .dataclasses import ReceiptData

def create_receipt(receipt_data: ReceiptData) -> ReceiptData:
    """
    Create a new receipt record.
    
    Args:
        receipt_data: ReceiptData containing user_id, image_url, and optional fields
        
    Returns:
        ReceiptData with the created receipt's data including receipt_id
    """
    receipt = Receipt.objects.create(
        user_id=receipt_data.user_id,
        purchase_date=receipt_data.purchase_date or datetime.now(),
        total_amount=receipt_data.total_amount or Decimal(0.0),
        image_url=receipt_data.image_url,
        status=receipt_data.status or 'pending'
    )
    # Return with receipt_id for tracking
    saved_data = ReceiptData(
        user_id=receipt.user_id,
        purchase_date=receipt.purchase_date,
        total_amount=receipt.total_amount,
        image_url=receipt.image_url,
        status=receipt.status,
        items=list(receipt.items.all())
    )
    # Attach receipt_id as an attribute for easy access
    saved_data.receipt_id = receipt.receipt_id
    return saved_data


def update_receipt(receipt_id: str, **kwargs) -> ReceiptData:
    """
    Update receipt fields.
    
    Args:
        receipt_id: The ID of the receipt to update
        **kwargs: Fields to update (status, total_amount, purchase_date, items, etc.)
        
    Returns:
        ReceiptData with the updated receipt's data
    """
    # Extract items if provided, as they need special handling
    items = kwargs.pop('items', None)
    
    # Update the receipt record
    Receipt.objects.filter(receipt_id=receipt_id).update(**kwargs)
    
    # If items are provided, create them
    if items is not None:
        receipt = Receipt.objects.get(receipt_id=receipt_id)
        # Clear existing items
        receipt.items.all().delete()
        # Create new items
        for item in items:
            ReceiptItem.objects.create(
                receipt=receipt,
                name=item.name,
                price=item.price,
                quantity=item.quantity,
                category=item.category,
                embedding=item.embedding
            )
    
    # Fetch and return the updated receipt
    receipt = Receipt.objects.get(receipt_id=receipt_id)
    return ReceiptData(
        user_id=receipt.user_id,
        purchase_date=receipt.purchase_date,
        total_amount=receipt.total_amount,
        image_url=receipt.image_url,
        status=receipt.status,
        items=list(receipt.items.all())
    )
def get_receipt_by_id(receipt_id: str) -> Optional[ReceiptData]:
    """
    Retrieve a receipt by its ID.
    Args:
        receipt_id: The ID of the receipt to retrieve
    Returns:
        ReceiptData if found, else None
    """
    try:
        receipt = Receipt.objects.get(receipt_id=receipt_id)
        return ReceiptData(
            user_id=receipt.user_id,
            purchase_date=receipt.purchase_date,
            total_amount=receipt.total_amount,
            image_url=receipt.image_url,
            status=receipt.status,
            items=list(receipt.items.all())
        )
    except Receipt.DoesNotExist:
        return None
def list_receipts_by_user(user_id: str) -> List[ReceiptData]:
    """
    List all receipts for a given user.
    
    Args:
        user_id: The ID of the user whose receipts to list 
    Returns:

        List of ReceiptData objects
    """
    receipts = Receipt.objects.filter(user_id=user_id).order_by('-purchase_date')
    receipt_list = []
    for receipt in receipts:
        receipt_list.append(
            ReceiptData(
                user_id=receipt.user_id,
                purchase_date=receipt.purchase_date,
                total_amount=receipt.total_amount,
                image_url=receipt.image_url,
                status=receipt.status,
                items=list(receipt.items.all())
            )
        )
    return receipt_list