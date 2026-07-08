import os
import sys
import logging
import io
import tempfile
import json
import re
import asyncio
import django

from telegram import Update
from extract_info.ocr.tesseract_ocr import extract_text_from_receipt
from extract_info.services import  extract_receipt_text
from extract_info.tasks import process_file_task
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from asgiref.sync import sync_to_async
from handle_files.services.upload import UploadServiceFactory
from receipt.models import STATUS_CHOICES, Receipt, ReceiptItem, Category
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptData, ReceiptItem as ReceiptItemData
from jose import jwt
from datetime import datetime, timedelta
from decimal import Decimal

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# Initialize the services
upload_service = UploadServiceFactory.create('local')  # Use local volume uploads
ALLOWED_USERS = [int(user_id) for user_id in os.environ.get("ALLOWED_USERS").split(",")]
BANNED_FILE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "banned.txt"))
_auth_lock = asyncio.Lock()

async def authenticate_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    async with _auth_lock:
        banned_ids = _load_banned_ids()
        if user_id in banned_ids:
            await update.message.reply_text("User banned.")
            return False

        if user_id not in ALLOWED_USERS:
            # Permanently ban and notify
            _append_banned_id(user_id)
            await update.message.reply_text("User not authorized. You have been banned.")
            return False

        return True
  # Authenticate the user (permanent ban list stored in banned.txt)
def _load_banned_ids() -> set[int]:
    if not os.path.exists(BANNED_FILE_PATH):
        return set()
    with open(BANNED_FILE_PATH, "r", encoding="utf-8") as fh:
        return {int(line.strip()) for line in fh if line.strip().isdigit()}


def _append_banned_id(user_id: int) -> None:
    # Append once per new ban; caller ensures it is needed.
    with open(BANNED_FILE_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"{user_id}\n")

  
    
async def process_receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process receipt photo upload with OCR extraction and status tracking.
    
    Workflow:
    1. Upload photo to S3
    2. Create receipt with PENDING status
    3. Notify user of successful upload
    4. Extract data via GPT-4 Vision (status: PROCESSING)
    5. Update receipt with extracted data (status: COMPLETED or FAILED)
    6. Notify user of extraction results
    """
    if not await authenticate_user(update, context):
        await update.message.reply_text("⛔You are not authorized to use this bot.")
        return
    
    receipt_id = None
    temp_file_path = None
    
    try:
        # Get the uploaded document/file
        print(update.message)
        document = update.message.photo
        if not document:
            await update.message.reply_text("❌ No file found in the message.")
            return
        
        document_file = await context.bot.get_file(document[-1].file_id)
        
        file_data = io.BytesIO()
        await document_file.download_to_memory(out=file_data)
        file_data.seek(0)
        
        # Create a temporary photo to store the uploaded file
        file_extension = ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
            temp_file.write(file_data.read())
            temp_file_path = temp_file.name
        
        # Upload file 
        file_name = f"{document[-1].file_id}{file_extension}"
        url = upload_service.upload_file(temp_file_path, file_name)
        logger.info(f"Photo uploaded: {url}")
        
        # Get user identifier
        if update.message:
            user = (
                update.message.from_user.username or
                update.message.from_user.first_name or
                f"user_{update.message.from_user.id}"
            )
        else:
            user = 'anonymous'
        
        # Phase 1: Create receipt with PENDING status
        receipt_data = ReceiptData(
            user_id=user,
            image_url=url,
            status='pending'
        )
        # Wrap Django ORM call in sync_to_async for async context
        created_receipt = await sync_to_async(receipt_services.create_receipt)(receipt_data)
        receipt_id = getattr(created_receipt, 'receipt_id', None)
        
        logger.info(f"Receipt {receipt_id} created with PENDING status")
        
        # Notify user immediately - upload successful
        await update.message.reply_text(
            f"✅ Receipt uploaded successfully!\n\n"
            f"Receipt ID: `{receipt_id}`\n"
            f"Status: pending\n\n"
            f"Processing receipt data...",
            parse_mode="Markdown"
        )
        
        # Phase 2: Hand off processing to Celery background task
        chat_id = update.effective_chat.id if update.effective_chat else update.message.chat_id
        process_file_task.delay(
            receipt_id=receipt_id,
            file_path=url,
            chat_id=chat_id,
            file_type='image'
        )
        logger.info(f"Handed off receipt {receipt_id} processing to Celery.")
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        # Update receipt status to FAILED if it was created
        if receipt_id:
            try:
                await sync_to_async(receipt_services.update_receipt)(receipt_id, status='failed')
            except Exception as update_error:
                logger.error(f"Failed to update receipt status: {update_error}")
        
        await update.message.reply_text(
            f"❌ Error initiating receipt processing: {str(e)}\n\n"
            f"Receipt ID: `{receipt_id if receipt_id else 'N/A'}`\n"
            f"Status: failed",
            parse_mode="Markdown"
        )



async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Processing your voice message... 🎙️")
    # get audio file from telegram
    document = update.message.audio or update.message.voice
    if not document:
        await update.message.reply_text("❌ No file found in the message.")
        return
    
    document_file = await context.bot.get_file(document.file_id)
    file_data = io.BytesIO()
    await document_file.download_to_memory(out=file_data)
    file_data.seek(0)
    # upload to locally now 
    file_name = f"{document.file_id}.{document.mime_type.split('/')[-1]}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{document.mime_type.split('/')[-1]}") as temp_file:
        temp_file.write(file_data.read())
        temp_file_path = temp_file.name
    
    url = upload_service.upload_file(temp_file_path, file_name)
    logger.info(f"Voice message uploaded: {url}")
    
    # create receipt in db with status pending
    receipt_data = ReceiptData(
        user_id=update.effective_user.id,
        image_url=url,
        status='pending'
    )
    created_receipt = await sync_to_async(receipt_services.create_receipt)(receipt_data)
    receipt_id = getattr(created_receipt, 'receipt_id', None)
    logger.info(f"Receipt {receipt_id} created with PENDING status")
    await update.message.reply_text(
        f"✅ Receipt uploaded successfully!\n\n"
        f"Receipt ID: `{receipt_id}`\n"
        f"Status: pending\n\n"
        f"Processing receipt data...",
        parse_mode="Markdown"
    )
    chat_id = update.effective_chat.id if update.effective_chat else update.message.chat_id
    process_file_task.delay(
        receipt_id=receipt_id,
        file_path=url,
        chat_id=chat_id,
        file_type='audio'
    )
    logger.info(f"Handed off receipt {receipt_id} processing to Celery.")



async def process_bank_statement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Not enabled yet. 🏦")