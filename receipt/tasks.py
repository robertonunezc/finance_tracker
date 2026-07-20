import logging

from celery import shared_task

from receipt.models import Receipt, ReceiptSource

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, autoretry_for=(Exception,), retry_backoff=True)
def notify_receipt_processed_task(self, receipt_id: str):
    return notify_receipt_processed(receipt_id)


def notify_receipt_processed(receipt_id: str) -> bool:
    try:
        receipt = (
            Receipt.objects.prefetch_related("items")
            .select_related("extraction_review")
            .get(receipt_id=receipt_id)
        )
    except Receipt.DoesNotExist:
        logger.warning("Receipt %s not found for processing notification", receipt_id)
        return False

    if receipt.source_type == ReceiptSource.TELEGRAM:
        from telegram_bot.notifications import send_receipt_processed_notification

        return send_receipt_processed_notification(receipt)

    if receipt.source_type == ReceiptSource.MANUAL_UPLOAD:
        logger.info("Skipping external notification for manual upload receipt %s", receipt_id)
        return False

    logger.info("Skipping notification for receipt %s with source %s", receipt_id, receipt.source_type)
    return False
