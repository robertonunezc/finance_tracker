import asyncio
import logging
import os

from telegram import Bot

from receipt.models import ReceiptExtractionReview

logger = logging.getLogger(__name__)


def send_receipt_processed_notification(receipt) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = (receipt.source_metadata or {}).get("chat_id")
    if not bot_token or chat_id in (None, ""):
        logger.warning("Skipping Telegram notification for receipt %s: missing token or chat_id", receipt.receipt_id)
        return False

    bot = Bot(token=bot_token)
    asyncio.run(
        bot.send_message(
            chat_id=chat_id,
            text=build_receipt_processed_message(receipt),
        )
    )
    return True


def build_receipt_processed_message(receipt) -> str:
    if receipt.status == "failed":
        return f"❌ Failed to process receipt {receipt.receipt_id}. Please try again."

    total_amount = float(receipt.total_amount or 0)
    items = list(receipt.items.all())
    if receipt.status == "needs_review":
        message_text = (
            f"⚠️ Receipt {receipt.receipt_id} needs manual review.\n"
            f"Total: ${total_amount:,.2f}\n"
            f"Issues: {_receipt_issue_count(receipt)}\n"
            f"Items Extracted: {len(items)}\n\nItems:\n"
        )
    else:
        message_text = (
            f"✅ Receipt {receipt.receipt_id} processed successfully!\n"
            f"Total: ${total_amount:,.2f}\n"
            f"Items Extracted: {len(items)}\n\nItems:\n"
        )

    for item in items:
        message_text += f"- {item.name} (x{item.quantity}): ${item.price:,.2f}\n"
    return message_text


def _receipt_issue_count(receipt) -> int:
    try:
        return len(receipt.extraction_review.issues or [])
    except ReceiptExtractionReview.DoesNotExist:
        return 0
