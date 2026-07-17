import hashlib

from .models import Receipt, ReceiptItem
from typing import List, Optional
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.utils import timezone
from .dataclasses import ReceiptData, ReceiptLookupResult
from pgvector.django import CosineDistance


def _receipt_to_lookup_result(receipt: Receipt, created: bool = False) -> ReceiptLookupResult:
    return ReceiptLookupResult(
        receipt_id=str(receipt.receipt_id),
        user_id=receipt.user_id,
        image_url=receipt.image_url,
        status=receipt.status,
        created=created,
        file_hash=receipt.file_hash,
    )


def compute_file_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_receipt_by_user_and_file_hash(user_id: str, file_hash: str) -> Optional[ReceiptLookupResult]:
    if not file_hash:
        return None

    try:
        receipt = Receipt.objects.get(user_id=user_id, file_hash=file_hash)
    except Receipt.DoesNotExist:
        return None

    return _receipt_to_lookup_result(receipt, created=False)


def create_receipt_with_file_hash(receipt_data: ReceiptData, file_hash: str) -> ReceiptLookupResult:
    try:
        with transaction.atomic():
            receipt = Receipt.objects.create(
                user_id=receipt_data.user_id,
                file_hash=file_hash,
                purchase_date=receipt_data.purchase_date or timezone.now(),
                total_amount=receipt_data.total_amount or Decimal(0.0),
                image_url=receipt_data.image_url,
                status=receipt_data.status or 'pending'
            )
        return _receipt_to_lookup_result(receipt, created=True)
    except IntegrityError:
        existing_receipt = get_receipt_by_user_and_file_hash(receipt_data.user_id, file_hash)
        if existing_receipt is None:
            raise
        return existing_receipt

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
        purchase_date=receipt_data.purchase_date or timezone.now(),
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

def get_closest_match_receipt_item(item_name: str,  new_vector: List[float]) -> Optional[ReceiptItem]:
    return ReceiptItem.objects.annotate(
        distance=CosineDistance('embedding', new_vector)
    ).order_by('distance').first()
