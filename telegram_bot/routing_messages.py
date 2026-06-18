from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram_bot.process_message import process_receipt_upload, process_voice_message, process_bank_statement

# 1. State/Route constants for clarity
ROUTE_TICKET = "ROUTE_TICKET"
ROUTE_STATEMENT = "ROUTE_STATEMENT"
ROUTE_VOICE = "ROUTE_VOICE"

async def route_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    # ─── ROUTE 1: VOICE MESSAGES (100% Deterministic & Free) ───
    if message.voice or message.audio:
        # Fast-track straight to your transcription/processing function
        await process_voice_message(update, context)
        return

    # ─── ROUTE 2: DOCUMENTS (PDFs, TXT, etc.) ───
    if message.document:
        mime_type = message.document.mime_type
        
        # If it's a PDF, we can check basic metadata or handle it as a statement
        if mime_type == "application/pdf":
            # OPTIONAL: Add a quick check here if you want to inspect page counts 
            # or names (e.g., 'statement' in filename), otherwise default to statement
            await process_bank_statement(update, context)
            return
            
        elif mime_type in ["image/jpeg", "image/png"]:
            await process_receipt_upload(update, context)
            return


    # ─── ROUTE 3: IMAGES / PHOTOS ───
    if message.photo:
        await process_receipt_upload(update, context)
        return
        
    # Fallback for plain text or unsupported types
    await message.reply_text("Please send an image of a ticket, a PDF bank statement, or a voice note.")

# ─── CALLBACK HANDLER FOR THE INTERACTIVE MENU ───
async def handle_routing_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    print(query.data)
    await query.answer() # Acknowledge the click
    
    selection = query.data
    file_id = context.user_data.get('pending_file_id')
    
    if selection == 'route_ticket':
        await query.edit_message_text("Processing your image as a **Ticket/Receipt**... ⏳")
        await process_receipt_upload(update, context)
        
    elif selection == 'route_statement':
        await query.edit_message_text("Processing your image as a **Bank Statement**... ⏳")
        await process_bank_statement(update, context)
