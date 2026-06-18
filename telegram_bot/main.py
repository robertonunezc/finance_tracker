import os
import sys
import logging
import io
import tempfile
import json
import re
import asyncio
import django
# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure Django settings before importing models
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finance_tracker.settings')
django.setup()
from extract_info.ocr.tesseract_ocr import extract_text_from_receipt
from extract_info.services import  extract_receipt_text
from extract_info.tasks import process_receipt_task
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
from routing_messages import handle_routing_menu_selection, route_incoming_message
# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ALLOWED_USERS = [int(user_id) for user_id in os.environ.get("ALLOWED_USERS").split(",")]
BANNED_FILE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "banned.txt"))
_auth_lock = asyncio.Lock()

# Initialize the services
upload_service = UploadServiceFactory.create('local')  # Use local volume uploads

# Define the start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the Spends App Bot! 👋\n\n"
        "I can help you with:\n"
        "• Uploading photos to S3\n"
        "• Managing authentication\n\n"
        "Use /help to see all available commands."
    )

# Define the help command handler
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        ""
    )

# Define the generate token handler
async def generate_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Create a payload with user information
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=7)  # Token expires in 7 days
    }
    
    # Generate the token
    token = jwt.encode(payload, os.getenv("JWT_SECRET"), algorithm="HS256")
    
    # Send the token to the user
    await update.message.reply_text(
        f"Here's your JWT token (valid for 7 days):\n\n`{token}`\n\n"
        "Keep this token secure and use it to authenticate your requests.",
        parse_mode="Markdown"
    )

# Define the verify token handler
async def verify_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if the user provided a token
    if not context.args:
        await update.message.reply_text(
            "Please provide a token to verify.\n\n"
            "Usage: /verify_token YOUR_TOKEN"
        )
        return
    
    # Get the token from the command arguments
    token = context.args[0]
    
    try:
        # Verify the token
        payload = auth_service.authenticate(token)
        
        # Send the verification result to the user
        await update.message.reply_text(
            f"✅ Token is valid!\n\n"
            f"User ID: {payload.get('sub')}\n"
            f"Username: {payload.get('username')}\n"
            f"Expires: {datetime.fromtimestamp(payload.get('exp')).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        # Send the error message to the user
        await update.message.reply_text(f"❌ Token verification failed: {str(e)}")

# Define the receipt upload and processing handler

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
    
    

# Define the main function
def main():
    # Initialize the application
    application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    # application.add_handler(CommandHandler("generate_token", generate_token))
    # application.add_handler(CommandHandler("verify_token", verify_token))
    
    # Add the receipt processing handler (handles document/file uploads)
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VOICE | filters.AUDIO,route_incoming_message ))
    # 2. Callback query handler to catch the menu button clicks
    application.add_handler(CallbackQueryHandler(handle_routing_menu_selection))
    
    # Start the bot
    logger.info("Starting the bot...")
    application.run_polling()

if __name__ == "__main__":
    main()