from celery import shared_task
import logging
import os
import tempfile
from decimal import Decimal, InvalidOperation

from extract_info import services as extract_info_service
from receipt import extraction_review
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptItem as ReceiptItemData
from receipt.models import Category
from receipt.tasks import notify_receipt_processed_task

logger = logging.getLogger(__name__)


def mark_receipt_failed(receipt_id: str) -> None:
    receipt_services.update_receipt(receipt_id, status='failed')


@shared_task(bind=True)
def process_file_task(self, receipt_id: str, file_path: str, file_type: str = "image"):
    """
    Background task to process a receipt file (image, audio, or pdf).
    file_path is the relative path in the media volume.
    file_type should be 'image', 'audio', or 'pdf'.
    """
    try:
        logger.info(f"Starting task: process_file_task for receipt {receipt_id} (type: {file_type}). Attempt: {self.request.retries + 1}")
        
        # Phase 2: Update status to PROCESSING and extract data
        receipt_services.update_receipt(receipt_id, status='processing')
        logger.info(f"Receipt {receipt_id} status updated to PROCESSING")
        
        if file_type == 'audio':
            ticket = extract_info_service.transcribe_and_extract_text(file_path)
        elif file_type == 'image':
            ticket = extract_info_service.extract_receipt_text(file_path)
        elif file_type == 'pdf':
            ticket = extract_info_service.extract_bank_statement_text(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
            
        # Parse extracted items
        items = []
        if hasattr(ticket, 'items') and ticket.items:
            for item in ticket.items:
                item_name = str(extraction_review.field_value(item.name) or "")
                item_quantity = _positive_item_quantity(extraction_review.field_value(item.quantity)) or Decimal("1.000")
                item_unit_price = _positive_item_amount(
                    extraction_review.field_value(getattr(item, "unit_price", None))
                )
                item_line_total = _positive_item_amount(
                    extraction_review.field_value(getattr(item, "line_total", None))
                )
                if item_line_total is None:
                    item_line_total = _positive_item_amount(
                        extraction_review.field_value(getattr(item, "price", None))
                    )
                if item_line_total is None and item_unit_price is not None:
                    item_line_total = item_unit_price * Decimal(str(item_quantity))
                vector_data = None
                extracted_category, category_confidence = _extracted_item_category(item)
                category = extracted_category or Category.OTHER
                try:
                    matched_category, vector_data = extract_info_service.find_nearest_category(item_name_string=item_name)
                    matched_category = extract_info_service.normalize_category_key(matched_category)
                    if matched_category and matched_category != Category.OTHER:
                        category = matched_category
                        category_confidence = None
                    elif extracted_category and extracted_category != Category.OTHER:
                        category = extracted_category
                    else:
                        category = extract_info_service.normalize_category_key(
                            extract_info_service.categorize_item(item_name)
                        ) or extracted_category or Category.OTHER
                        if category != extracted_category:
                            category_confidence = None
                except Exception as exc:
                    logger.warning("Category enrichment failed for receipt %s item %s: %s", receipt_id, item_name, exc)
                    if extracted_category:
                        category = extracted_category
                    else:
                        category = Category.OTHER
                        category_confidence = 0.0
                receipt_item = ReceiptItemData(
                    name=item_name,
                    unit_price=float(item_unit_price) if item_unit_price is not None else None,
                    line_total=float(item_line_total or Decimal("0.00")),
                    quantity=item_quantity,
                    category=category,
                    category_confidence=category_confidence,
                    embedding=vector_data
                )
                items.append(receipt_item)
        else:
            logger.warning("No items found in extracted data")
            
        # Phase 3: Validate and persist extracted data
        application_result = extraction_review.apply_extraction_result(
            receipt_id=receipt_id,
            ticket=ticket,
            items=items,
        )
        logger.info(
            "Receipt %s saved with status %s and %s items",
            receipt_id,
            application_result.status,
            len(items),
        )
        
        # Cleanup temp file
        if should_cleanup_processed_file(file_path) and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception as e:
                logger.error(f"Failed to cleanup temp file {file_path}: {e}")
                
        _schedule_receipt_notification(receipt_id)

        return True

    except Exception as e:
        logger.error(f"Task process_file_task failed for {file_path}: {e}")
        try:
            mark_receipt_failed(receipt_id)
            _schedule_receipt_notification(receipt_id)
        except Exception as inner_e:
            logger.error(f"Failed to update failed status for {receipt_id}: {inner_e}")

        raise


def _extracted_item_category(item):
    category_field = getattr(item, "category", None)
    category = extract_info_service.normalize_category_key(
        extraction_review.field_value(category_field)
    )
    if not category:
        return None, None
    return category, _field_confidence(category_field)


def _field_confidence(field):
    if isinstance(field, dict):
        value = field.get("confidence")
    else:
        value = getattr(field, "confidence", None)
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return None


def _positive_item_amount(value):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if amount <= Decimal("0.00"):
        return None
    return amount.quantize(Decimal("0.01"))


def _positive_item_quantity(value):
    if value in (None, ""):
        return None
    try:
        quantity = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if quantity <= Decimal("0.000"):
        return None
    return quantity.quantize(Decimal("0.001"))


def should_cleanup_processed_file(file_path: str | None) -> bool:
    if not file_path or file_path.startswith(("http://", "https://")):
        return False

    file_realpath = os.path.realpath(file_path)
    temp_realpath = os.path.realpath(tempfile.gettempdir())
    try:
        return os.path.commonpath([file_realpath, temp_realpath]) == temp_realpath
    except ValueError:
        return False


def _schedule_receipt_notification(receipt_id: str) -> None:
    try:
        notify_receipt_processed_task.delay(receipt_id)
    except Exception as exc:
        logger.error("Failed to schedule receipt processing notification for %s: %s", receipt_id, exc)
