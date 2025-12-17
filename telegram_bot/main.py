import os
# Add the parent directory to the Python path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from handle_files.services.upload import UploadServiceFactory
from services.store_data.store_data import ServiceType
from receipt.models import STATUS_CHOICES, Receipt, ReceiptItem
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptData
from repositories.repository_factory import RepositoryFactory
from jose import jwt
from datetime import datetime, timedelta
from extract_info.services import extract_receipt_text
import os
import logging
import io
import tempfile
import json
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_USERS = [int(user_id) for user_id in os.environ.get("ALLOWED_USERS").split(",")]
BANNED_FILE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "banned.txt"))
_auth_lock = asyncio.Lock()

# Initialize the services
upload_service = UploadServiceFactory.create()

# Initialize repository (using PostgreSQL as configured in the original code)
receipt_repository = RepositoryFactory.create_receipt_repository(service_type=ServiceType.POSTGRES)

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
        "You can also send me photos to upload them to S3."
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
    
    receipt = None
    temp_file_path = None
    
    try:
        # Get the photo file
        photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
        
        file_data = io.BytesIO()
        await photo_file.download_to_memory(out=file_data)
        file_data.seek(0)
        
        # Create a temporary file to store the photo
        file_extension = os.path.splitext(photo_file.file_path)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
            temp_file.write(file_data.read())
            temp_file_path = temp_file.name
        
        # Upload to S3
        file_name = f"{update.message.photo[-1].file_id}{file_extension}"
        url = upload_service.upload_file(temp_file_path, file_name)
        logger.info(f"Photo uploaded to S3: {url}")
        
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
        receipt = receipt_services.create_receipt(Receipt(user_id=user, image_url=url, status=STATUS_CHOICES[0][0]))

        logger.info(f"Receipt {receipt.receipt_id} created with PENDING status")
        
        # Notify user immediately - upload successful
        await update.message.reply_text(
            f"✅ Receipt uploaded successfully!\n\n"
            f"Receipt ID: `{receipt.receipt_id}`\n"
            f"Status: {receipt.status.value}\n\n"
            f"Processing receipt data...",
            parse_mode="Markdown"
        )
        
        # Phase 2: Update status to PROCESSING and extract data
        receipt_services.update_receipt()
        receipt_repository.update(receipt.receipt_id, status=ReceiptStatus.PROCESSING)
        logger.info(f"Receipt {receipt.receipt_id} status updated to PROCESSING")
        
        # Extract text from the receipt image using GPT-4 Vision
        file_full_path = os.path.join(os.getcwd(), temp_file_path)
        logger.info(f"Extracting text from receipt: {file_full_path}")
        
        extracted_receipt = extract_receipt_text(file_full_path)
        logger.info(f"GPT-4 extraction result: {extracted_receipt}")
        
        # Clean and parse JSON response with multiple strategies
        receipt_formatted = None
        
        # Strategy 1: Remove markdown code blocks
        cleaned = extracted_receipt.replace('```json', '').replace('```', '').strip()
        
        # Strategy 2: Try to find JSON in the response
        try:
            receipt_formatted = json.loads(cleaned)
            logger.info(f"Parsed JSON successfully: {receipt_formatted}")
        except json.JSONDecodeError:
            # Strategy 3: Try to extract JSON from within the text
            import re
            json_match = re.search(r'\{[\s\S]*\}', cleaned)
            if json_match:
                try:
                    receipt_formatted = json.loads(json_match.group())
                    logger.info(f"Extracted JSON from text: {receipt_formatted}")
                except json.JSONDecodeError:
                    pass
        
        # If still no valid JSON, raise error with the raw response
        if not receipt_formatted:
            logger.error(f"Could not parse JSON from GPT response. Raw response: {extracted_receipt[:500]}")
            raise json.JSONDecodeError("No valid JSON found in GPT response", extracted_receipt, 0)
        
        # Parse extracted items
        items: list[ReceiptItem] = []
        if 'items' in receipt_formatted:
            for item in receipt_formatted['items']:
                item_name = item.get('name', 'Unknown Item')
                item_price = float(item.get('price', 0.0))
                item_quantity = int(item.get('quantity', 1)) if 'quantity' in item else 1
                item_category = item.get('category', 'other') if 'category' in item else 'other'
                
                items.append(ReceiptItem(
                    name=item_name,
                    price=item_price,
                    quantity=item_quantity,
                    category=item_category
                ))
        else:
            logger.warning("No items found in extracted data")
        
        # Phase 3: Update receipt with extracted data and COMPLETED status
        total_amount = float(receipt_formatted.get('total', 0.0))
        receipt_repository.update(
            receipt.receipt_id,
            purchase_date=datetime.now(),
            total_amount=total_amount,
            items=items,
            status=ReceiptStatus.COMPLETED
        )
        logger.info(f"Receipt {receipt.receipt_id} completed with {len(items)} items")
        
        # Cleanup temp file
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        
        # Notify user of successful extraction
        items_summary = "\n".join([f"• {item.name}: ${item.price:.2f}" for item in items[:5]])
        if len(items) > 5:
            items_summary += f"\n... and {len(items) - 5} more items"
        
        await update.message.reply_text(
            f"🎉 Receipt processed successfully!\n\n"
            f"📊 Summary:\n"
            f"Total: ${total_amount:.2f}\n"
            f"Items: {len(items)}\n\n"
            f"{items_summary}\n\n"
            f"Status: ✅ {ReceiptStatus.COMPLETED.value}",
            parse_mode="Markdown"
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GPT response: {e}")
        # Update receipt status to FAILED
        if receipt:
            receipt_repository.update(receipt.receipt_id, status=ReceiptStatus.FAILED)
        await update.message.reply_text(
            f"❌ Failed to parse receipt data.\n\n"
            f"Receipt ID: `{receipt.receipt_id if receipt else 'N/A'}`\n"
            f"Status: {ReceiptStatus.FAILED.value}\n\n"
            f"The image was saved but data extraction failed. Please try again.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        # Update receipt status to FAILED if it was created
        if receipt:
            try:
                receipt_repository.update(receipt.receipt_id, status=ReceiptStatus.FAILED)
            except Exception as update_error:
                logger.error(f"Failed to update receipt status: {update_error}")
        
        await update.message.reply_text(
            f"❌ Error processing receipt: {str(e)}\n\n"
            f"Receipt ID: `{receipt.receipt_id if receipt else 'N/A'}`\n"
            f"Status: {ReceiptStatus.FAILED.value if receipt else 'Not created'}",
            parse_mode="Markdown"
        )
    finally:
        # Ensure temp file cleanup
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup temp file: {cleanup_error}")

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
    application.add_handler(CommandHandler("generate_token", generate_token))
    application.add_handler(CommandHandler("verify_token", verify_token))
    
    # Add the receipt processing handler
    application.add_handler(MessageHandler(filters.PHOTO, process_receipt_upload))
    
    # Start the bot
    logger.info("Starting the bot...")
    application.run_polling()

if __name__ == "__main__":
    main()