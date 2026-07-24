import hashlib
import uuid
from pathlib import Path
from urllib.parse import urlparse

from handle_files.services.upload import UploadServiceFactory
from .models import Receipt, ReceiptExtractionReview, ReceiptItem
from typing import List, Optional
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.utils import timezone
from .dataclasses import ReceiptData, ReceiptLookupResult, ReceiptUploadRequest, ReceiptUploadResult
from pgvector.django import CosineDistance


def _receipt_to_lookup_result(receipt: Receipt, created: bool = False) -> ReceiptLookupResult:
    return ReceiptLookupResult(
        receipt_id=str(receipt.receipt_id),
        user_id=receipt.user_id,
        image_url=receipt.image_url,
        status=receipt.status,
        created=created,
        file_hash=receipt.file_hash,
        source_type=receipt.source_type,
        source_metadata=receipt.source_metadata or {},
    )


def compute_file_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_receipt_duplicate_action(status: str) -> str:
    if status == "completed":
        return "skip_completed"
    if status in {"pending", "processing"}:
        return "skip_in_progress"
    if status == "needs_review":
        return "skip_needs_review"
    if status == "failed":
        return "retry"
    return "retry"


def prepare_receipt_upload(request: ReceiptUploadRequest, upload_service=None) -> ReceiptUploadResult:
    if request.file_type not in {"image", "pdf"}:
        raise ValueError(f"Unsupported receipt upload file type: {request.file_type}")

    file_hash = compute_file_sha256(request.source_file_path)
    existing_receipt = get_receipt_by_user_and_file_hash(request.user_id, file_hash)
    if existing_receipt:
        action = get_receipt_duplicate_action(existing_receipt.status)
        if action != "retry":
            return _receipt_upload_result(existing_receipt, action, request.file_type, should_enqueue=False)
        return _retry_receipt_upload(request, existing_receipt, file_hash, upload_service)

    uploaded_url = _upload_receipt_source_file(request, upload_service)
    created_receipt = create_receipt_with_file_hash(
        ReceiptData(
            user_id=request.user_id,
            image_url=uploaded_url,
            status="pending",
            source_type=request.source_type,
            source_metadata=request.source_metadata or {},
        ),
        file_hash,
    )
    if created_receipt.created:
        return _receipt_upload_result(created_receipt, "created", request.file_type, should_enqueue=True)

    action = get_receipt_duplicate_action(created_receipt.status)
    if action != "retry":
        return _receipt_upload_result(created_receipt, action, request.file_type, should_enqueue=False)
    return _retry_receipt_upload(request, created_receipt, file_hash, upload_service)


def _retry_receipt_upload(
    request: ReceiptUploadRequest,
    receipt: ReceiptLookupResult,
    file_hash: str,
    upload_service=None,
) -> ReceiptUploadResult:
    uploaded_url = _upload_receipt_source_file(request, upload_service)
    update_receipt(
        receipt.receipt_id,
        image_url=uploaded_url,
        status="pending",
        source_type=request.source_type,
        source_metadata=request.source_metadata or {},
    )
    return ReceiptUploadResult(
        receipt_id=receipt.receipt_id,
        user_id=receipt.user_id,
        image_url=uploaded_url,
        status="pending",
        action="retry",
        file_hash=file_hash,
        file_type=request.file_type,
        should_enqueue=True,
        source_type=request.source_type,
        source_metadata=request.source_metadata or {},
    )


def _receipt_upload_result(
    receipt: ReceiptLookupResult,
    action: str,
    file_type: str,
    *,
    should_enqueue: bool,
) -> ReceiptUploadResult:
    return ReceiptUploadResult(
        receipt_id=receipt.receipt_id,
        user_id=receipt.user_id,
        image_url=receipt.image_url,
        status=receipt.status,
        action=action,
        file_hash=receipt.file_hash or "",
        file_type=file_type,
        should_enqueue=should_enqueue,
        source_type=receipt.source_type,
        source_metadata=receipt.source_metadata or {},
    )


def _upload_receipt_source_file(request: ReceiptUploadRequest, upload_service=None) -> str:
    service = upload_service or UploadServiceFactory.create("local")
    return service.upload_file(request.source_file_path, _receipt_upload_object_name(request.original_filename))


def _receipt_upload_object_name(original_filename: str) -> str:
    suffix = Path(original_filename or "").suffix.lower()
    if not suffix:
        suffix = ".bin"
    return f"{uuid.uuid4().hex}{suffix}"


def get_receipt_by_user_and_file_hash(user_id: str, file_hash: str) -> Optional[ReceiptLookupResult]:
    if not file_hash:
        return None

    try:
        receipt = Receipt.objects.get(user_id=user_id, file_hash=file_hash, is_active=True)
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
                source_type=receipt_data.source_type,
                source_metadata=receipt_data.source_metadata or {},
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
        source_type=receipt_data.source_type,
        source_metadata=receipt_data.source_metadata or {},
        status=receipt_data.status or 'pending'
    )
    # Return with receipt_id for tracking
    saved_data = ReceiptData(
        user_id=receipt.user_id,
        purchase_date=receipt.purchase_date,
        total_amount=receipt.total_amount,
        image_url=receipt.image_url,
        status=receipt.status,
        items=list(receipt.items.all()),
        source_type=receipt.source_type,
        source_metadata=receipt.source_metadata or {},
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
                unit_price=item.unit_price,
                line_total=item.line_total,
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
        items=list(receipt.items.all()),
        source_type=receipt.source_type,
        source_metadata=receipt.source_metadata or {},
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
            items=list(receipt.items.all()),
            source_type=receipt.source_type,
            source_metadata=receipt.source_metadata or {},
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
    receipts = Receipt.objects.filter(user_id=user_id, is_active=True).order_by('-purchase_date')
    receipt_list = []
    for receipt in receipts:
        receipt_list.append(
            ReceiptData(
                user_id=receipt.user_id,
                purchase_date=receipt.purchase_date,
                total_amount=receipt.total_amount,
                image_url=receipt.image_url,
                status=receipt.status,
                items=list(receipt.items.all()),
                source_type=receipt.source_type,
                source_metadata=receipt.source_metadata or {},
            )
        )
    return receipt_list

def get_closest_match_receipt_item(item_name: str,  new_vector: List[float]) -> Optional[ReceiptItem]:
    return ReceiptItem.objects.annotate(
        distance=CosineDistance('embedding', new_vector)
    ).order_by('distance').first()


def reset_receipt_for_reprocessing(receipt_id: str) -> Receipt:
    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id, is_active=True)
        receipt.items.all().delete()
        ReceiptExtractionReview.objects.filter(receipt=receipt).delete()
        receipt.status = "pending"
        receipt.purchase_date = timezone.now()
        receipt.total_amount = Decimal("0.00")
        receipt.subtotal_amount = None
        receipt.discount_amount = None
        receipt.store_name = None
        receipt.extracted_text = None
        receipt.extraction_result = None
        receipt.save(
            update_fields=[
                "status",
                "purchase_date",
                "total_amount",
                "subtotal_amount",
                "discount_amount",
                "store_name",
                "extracted_text",
                "extraction_result",
                "updated_at",
            ]
        )
        return receipt


def deactivate_receipt(receipt_id: str) -> Receipt:
    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id, is_active=True)
        receipt.is_active = False
        receipt.save(update_fields=["is_active", "updated_at"])
        return receipt


def infer_receipt_file_type(image_url: str) -> str:
    return "pdf" if Path(urlparse(image_url).path).suffix.lower() == ".pdf" else "image"
