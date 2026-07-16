import base64
import json
import logging
import os
import re
from typing import List

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from receipt import services as receipt_services
from receipt.models import Category, ReceiptItem

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
EXTRACTION_PROMPT="""
Extract all readable text from this grocery receipt and structure it as Ticket object.
The tickets are from Mexico so are in Spanish.
The quantity data can be in a column with names like: CANT, CANTIDAD. If you cannot extract the quantity, return 1 by default for all the items.
Always extract the raw item name, do not hallucinate or correct it; use exactly what appears on the receipt.
For the store name, prefer the canonical commercial name that appears on the receipt. If the receipt includes legal suffixes such as SA DE CV, S.A. DE C.V., S.A. DE C.V, SOCIEDAD ANONIMA, or similar, normalize them away in the final store_name value.
Examples:
- "TIENDAS CHEDRAUI SA DE CV" -> "chedraui"
- "Soriana S.A. de C.V." -> "soriana"
- "Walmart de México S.A. de C.V." -> "walmart"
If the store name is unclear, return null.
Try to extract the subtotal, discount, store name and total amount if possible. If you cannot find them, return null for those fields.
Return ONLY valid JSON, no markdown formatting.
"""

class Item(BaseModel):
    name: str = Field(description="Name of the item")
    price: float = Field(description="Price of the item")
    quantity: int = Field(description="Quantity of the item")
    category: str = Field(description="Category of the item")

class Ticket(BaseModel):
    items: List[Item] = Field(description="List of items found in the ticket")
    subtotal: float = Field(description="Subtotal amount of the ticket")
    discount: float = Field(description="Discount amount of the ticket")
    store_name: str = Field(description="Name of the store where the ticket was issued")
    total: float = Field(description="Total amount of the ticket")

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def normalize_store_name(value: str | None) -> str | None:
    if not value:
        return None

    cleaned = re.sub(r"\s+", " ", value).strip().lower()
    if not cleaned:
        return None

    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"\b(?:s\.a\.?|sa|sociedad anonima|sociedad anonima|de|del|la|los|las)\b", " ", cleaned)
    cleaned = re.sub(r"\b(?:cv|c\.v\.?|s\.a\.? de c\.v\.?|sa de cv|s\.a\. de c\.v|s\.a\. de c\.v\.)\b", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return None

    aliases = {
        "tiendas chedraui": "chedraui",
        "chedraui": "chedraui",
        "soriana": "soriana",
        "walmart": "walmart",
        "bodega aurrera": "bodega aurrera",
        "superama": "superama",
        "farmacias del ahorro": "farmacias del ahorro",
    }

    return aliases.get(cleaned, cleaned)


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

        if getattr(parsed_ticket, "store_name", None):
            parsed_ticket.store_name = normalize_store_name(parsed_ticket.store_name)

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
    logger.info(f"Categorizing item: {item}")

    category_options = [f"{value} ({label})" for value, label in Category.choices]
    categories_str = ", ".join(category_options)
    category_lookup = {
        value.lower(): value
        for value, _ in Category.choices
    }
    category_lookup.update({
        label.lower(): value
        for value, label in Category.choices
    })

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Categorize this item '{item}' as one of the following categories: {categories_str}. "
                    "Items will be in Spanish. "
                    "Return ONLY the exact category key, not the full label. "
                    "If you don't know the category, return 'other'."
                }
            ]
        }
    ])

    raw_category = response.choices[0].message.content.strip().strip("'\"")
    category = category_lookup.get(raw_category.lower(), "other")
    logger.info(f"GPT categorization successful: {category}")
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

        if getattr(parsed_ticket, "store_name", None):
            parsed_ticket.store_name = normalize_store_name(parsed_ticket.store_name)
        
        logger.info(f"Extraction successful: {len(parsed_ticket.items)} items found")
        return parsed_ticket
        
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Validation or parsing error: {e}")
        raise
    except Exception as e:
        logger.error(f"Transcription or extraction failed: {str(e)}")
        raise



def find_nearest_category(item_name_string:str)->tuple[str, list[float]]:
    # 1. Generate embedding for the newly extracted item
    new_vector = generate_embedding(item_name_string)
    # 2. Query Postgres for the closest match in your history
    closest_match = receipt_services.get_closest_match_receipt_item(item_name_string, new_vector)

    # 3. Set a strict similarity threshold (0.0 means identical, 1.0 means opposite)
    # 0.3 to 0.4 is generally a safe spot for semantic similarity
    if closest_match and closest_match.distance < 0.35:
        logger.info(f"Found similar category: {closest_match.category} with distance {closest_match.distance}")
        logger.info(f"Item name: {item_name_string}")
        logger.info(f"Closest match: {closest_match.name}")
        return closest_match.category, new_vector
        
    return None, new_vector
