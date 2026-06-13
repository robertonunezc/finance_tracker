from celery import shared_task
import logging
import os
import asyncio
from datetime import datetime
from decimal import Decimal
from telegram import Bot

from extract_info.services import extract_receipt_text
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptItem as ReceiptItemData

logger = logging.getLogger(__name__)

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_receipt_task(self, receipt_id: int, image_path: str, chat_id: int):
    """
    Celery task to execute extract_receipt_text asynchronously.
    """
    logger.info(f"Starting task: extract_receipt_text for receipt {receipt_id}. Attempt: {self.request.retries + 1}")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    try:
        # Phase 2: Update status to PROCESSING and extract data
        receipt_services.update_receipt(receipt_id, status='processing')
        logger.info(f"Receipt {receipt_id} status updated to PROCESSING")
        
        ticket = extract_receipt_text(image_path)
        
        # Parse extracted items
        items = []
        if hasattr(ticket, 'items') and ticket.items:
            for item in ticket.items:
                items.append(ReceiptItemData(
                    name=item.name,
                    price=float(item.price),
                    quantity=int(item.quantity),
                    category=item.category
                ))
        else:
            logger.warning("No items found in extracted data")
            
        # Phase 3: Update receipt with extracted data and COMPLETED status
        total_amount = float(ticket.total) if hasattr(ticket, 'total') else 0.0
        receipt_services.update_receipt(
            receipt_id,
            purchase_date=datetime.now(),
            total_amount=Decimal(str(total_amount)),
            items=items,
            status='completed'
        )
        logger.info(f"Receipt {receipt_id} completed with {len(items)} items")
        
        # Cleanup temp file
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except Exception as e:
                logger.error(f"Failed to cleanup temp file {image_path}: {e}")
                
        # Send Success Message to Telegram
        if bot_token and chat_id:
            bot = Bot(token=bot_token)
            asyncio.run(bot.send_message(
                chat_id=chat_id,
                text=f"✅ Receipt {receipt_id} processed successfully!\nTotal: ${total_amount:,.2f}\nItems Extracted: {len(items)}"
            ))

        return True

    except Exception as e:
        logger.error(f"Task process_receipt_task failed for {image_path}: {e}")
        
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
