from openai import OpenAI
import os
from dotenv import load_dotenv
import logging
import asyncio
import json
from typing import List
from pydantic import BaseModel, Field, ValidationError
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
EXTRACTION_PROMPT="""
Extract all readable text from this grocery receipt and structure it as Ticket object
The tickets are from Mexico so are in spanish. 
The quantity data can be in a column with names like: CANT, CANTIDAD
Always extract the raw name, do not halluciante or correct it, just use what it says in the receipt
Return ONLY valid JSON, no markdown formatting.
"""

class Item(BaseModel):
    name: str = Field(description="Name of the item")
    price: float = Field(description="Price of the item")
    quantity: int = Field(description="Quantity of the item")
    category: str = Field(description="Category of the item")

class Ticket(BaseModel):
    items: List[Item] = Field(description="List of items found in the ticket")
    total: float = Field(description="Total amount of the ticket")

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
import base64

# Set your API key

# Load the image and encode it as base64
def encode_image_to_base64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# Prepare the API request
def extract_receipt_text(image_path:str)->Ticket:
    base64_image = encode_image_to_base64(image_path)
    logger.info(f"Extracting text from image: {image_path}")
    try:
        response = client.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        response_format=Ticket)
        parsed_ticket = response.choices[0].message.parsed
        if parsed_ticket is None:
            raise ValueError(f"Model refused: {response.choices[0].message.refusal}")
        logger.info(f"GPT extraction successful")
        return parsed_ticket
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Validation or parsing error: {e}")
        raise
    except Exception as e:
        logger.error(f"GPT extraction failed: {str(e)}")
        raise
# Example usage

def generate_embedding(text:str):
    response = client.embeddings.create(
    input=text,
    model="text-embedding-3-small"
    )
    return response.data[0].embedding

def categorize_item(item:str)->str:
    """
    Categorize an item using OpenAI.
    """
    response = client.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Categorize this item {item} as one of the following categories: groceries, beverages, dairy, produce, meat, bakery, frozen, pantry, snacks, medication, health, personal_care, toiletries, household, cleaning, paper_products, pet_supplies, baby_products, electronics, restaurant, clothing, school_supplies, transportation, entertainment, utilities, gas, taxes, other. "
                    "Do not return any additional text, just the category name."
                    "If you dont know the category, return 'other'"
                }
            ]
        }
    ])
    category = response.choices[0].message.parsed
    logger.info(f"GPT categorization successful {category}")
    return category

def extract_bank_statement_text(pdf_path:str):
    return "Bank accounts are not implemented yet"

def transcribe_and_extract_text(audio_path:str):
    """
    Transcribe audio file and extract structured data.
    """
    logger.info(f"Starting transcription and extraction for: {audio_path}")
    
    try:
        # 1. Transcribe the audio
        logger.info("Step 1: Transcribing audio...")
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=open(audio_path, "rb"),
            language="es"  # Specify language for better accuracy
        )
        transcribed_text = transcription.text
        logger.info(f"Transcription complete: {transcribed_text[:100]}...")
        
        # 2. Extract structured data from transcription
        logger.info("Step 2: Extracting structured data from transcription...")

        response = client.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT + f"\n\nTranscription:\n{transcribed_text}\n\n"
                    }
                ]
            }
        ],
        response_format=Ticket)
        
        parsed_ticket = response.choices[0].message.parsed
        if parsed_ticket is None:
            raise ValueError(f"Model refused: {response.choices[0].message.refusal}")
        
        logger.info(f"Extraction successful: {len(parsed_ticket.items)} items found")
        return parsed_ticket
        
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Validation or parsing error: {e}")
        raise
    except Exception as e:
        logger.error(f"Transcription or extraction failed: {str(e)}")
        raise

from django.db.models.functions import CosineDistance
from .models import ReceiptItem
import openai

def find_nearest_category(item_name_string):
    # 1. Generate embedding for the newly extracted item
    new_vector = generate_embedding(item_name_string)
    # 2. Query Postgres for the closest match in your history
    closest_match = ReceiptItem.objects.annotate(
        distance=CosineDistance('embedding', new_vector)
    ).order_by('distance').first() # Get the single closest item

    # 3. Set a strict similarity threshold (0.0 means identical, 1.0 means opposite)
    # 0.3 to 0.4 is generally a safe spot for semantic similarity
    if closest_match and closest_match.distance < 0.35:
        logger.info(f"Found similar category: {closest_match.category} with distance {closest_match.distance}")
        logger.info(f"Item name: {item_name_string}")
        logger.info(f"Closest match: {closest_match.name}")
        return closest_match.category, new_vector
        
    return None, new_vector
