from celery import shared_task
import logging
import os
import asyncio
import tempfile
from decimal import Decimal, InvalidOperation
from telegram import Bot

from extract_info import services as extract_info_service
from receipt import extraction_review
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptItem as ReceiptItemData
from receipt.models import Category

logger = logging.getLogger(__name__)

def mark_receipt_failed(receipt_id: str, bot_token: str | None = None, chat_id: int | None = None) -> None:
    receipt_services.update_receipt(receipt_id, status='failed')
    if bot_token and chat_id:
        bot = Bot(token=bot_token)
        asyncio.run(bot.send_message(
            chat_id=chat_id,
            text=f"❌ Failed to process receipt {receipt_id}. Please try again."
        ))


@shared_task(bind=True, max_retries=3, autoretry_for=(Exception,), retry_backoff=True)
def process_file_task(self, receipt_id: str, file_path: str, chat_id: int | None = None, file_type: str = "image"):
    """
    Background task to process a receipt file (image, audio, or pdf).
    file_path is the relative path in the media volume.
    file_type should be 'image', 'audio', or 'pdf'.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
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
                item_price = extraction_review.field_value(item.price) or 0.0
                item_quantity = _positive_item_quantity(extraction_review.field_value(item.quantity)) or 1
                category_confidence = None
                vector_data = None
                try:
                    matched_category, vector_data = extract_info_service.find_nearest_category(item_name_string=item_name)
                    if matched_category:
                        category = matched_category
                    else:
                        category = extract_info_service.categorize_item(item_name)
                except Exception as exc:
                    logger.warning("Category enrichment failed for receipt %s item %s: %s", receipt_id, item_name, exc)
                    category = Category.OTHER
                    category_confidence = 0.0
                receipt_item = ReceiptItemData(
                    name=item_name,
                    price=float(item_price),
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
        total_amount = float(application_result.total_amount)
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
                
        # Send Success Message to Telegram
        if bot_token and chat_id:
            bot = Bot(token=bot_token)
            
            if application_result.status == "needs_review":
                message_text = (
                    f"⚠️ Receipt {receipt_id} needs manual review.\n"
                    f"Total: ${total_amount:,.2f}\n"
                    f"Issues: {len(application_result.validation.issues)}\n"
                    f"Items Extracted: {len(items)}\n\nItems:\n"
                )
            else:
                message_text = f"✅ Receipt {receipt_id} processed successfully!\nTotal: ${total_amount:,.2f}\nItems Extracted: {len(items)}\n\nItems:\n"
            for item in items:
                message_text += f"- {item.name} (x{item.quantity}): ${item.price:,.2f}\n"
                
            asyncio.run(bot.send_message(
                chat_id=chat_id,
                text=message_text
            ))

        return True

    except Exception as e:
        logger.error(f"Task process_file_task failed for {file_path}: {e}")
        
        # If we exhausted retries, mark as error and notify user
        if self.request.retries >= self.max_retries:
            try:
                mark_receipt_failed(receipt_id, bot_token=bot_token, chat_id=chat_id)
            except Exception as inner_e:
                logger.error(f"Failed to update failed status for {receipt_id}: {inner_e}")

        raise


def _positive_item_quantity(value):
    if value in (None, ""):
        return None
    try:
        quantity = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if quantity < 1 or quantity != quantity.to_integral_value():
        return None
    return int(quantity)


def should_cleanup_processed_file(file_path: str | None) -> bool:
    if not file_path or file_path.startswith(("http://", "https://")):
        return False

    file_realpath = os.path.realpath(file_path)
    temp_realpath = os.path.realpath(tempfile.gettempdir())
    try:
        return os.path.commonpath([file_realpath, temp_realpath]) == temp_realpath
    except ValueError:
        return False
