from celery import shared_task
import logging
import os
import asyncio
from django.utils import timezone
from decimal import Decimal
from telegram import Bot

from extract_info import services as extract_info_service
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptItem as ReceiptItemData

logger = logging.getLogger(__name__)

import tempfile
from handle_files.services.upload import UploadServiceFactory

@shared_task(bind=True, max_retries=3, autoretry_for=(Exception,), retry_backoff=True)
def process_file_task(self, receipt_id: str, file_path: str, chat_id: int, file_type: str):
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
                # Look up if you've bought something similar before
                matched_category, vector_data = extract_info_service.find_nearest_category(item.name)
                if matched_category:
                    category = matched_category
                else:
                    category = extract_info_service.categorize_item(item.name)
                receipt_item = ReceiptItemData(
                    name=item.name,
                    price=float(item.price),
                    quantity=int(item.quantity),
                    category=category,
                    embedding=vector_data
                )
                items.append(receipt_item)
        else:
            logger.warning("No items found in extracted data")
            
        # Phase 3: Update receipt with extracted data and COMPLETED status
        total_amount = float(ticket.total) if hasattr(ticket, 'total') else 0.0
        receipt_services.update_receipt(
            receipt_id,
            purchase_date=timezone.now(),
            total_amount=Decimal(str(total_amount)),
            items=items,
            status='completed'
        )
        logger.info(f"Receipt {receipt_id} completed with {len(items)} items")
        
        # Cleanup temp file
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception as e:
                logger.error(f"Failed to cleanup temp file {file_path}: {e}")
                
        # Send Success Message to Telegram
        if bot_token and chat_id:
            bot = Bot(token=bot_token)
            
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
                receipt_services.update_receipt(receipt_id, status='error')
                if bot_token and chat_id:
                    bot = Bot(token=bot_token)
                    asyncio.run(bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Failed to process receipt {receipt_id}. Please try again."
                    ))
            except Exception as inner_e:
                logger.error(f"Failed to update error status for {receipt_id}: {inner_e}")

        raise

