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
                        "text": "Extract all readable text from this grocery receipt and structure it as Ticket object"
                        "Categorize each item using one of these categories: groceries, beverages, dairy, produce, meat, bakery, frozen, pantry, snacks, medication, health, personal_care, toiletries, household, cleaning, paper_products, pet_supplies, baby_products, electronics, restaurant, clothing, school_supplies, transportation, entertainment, utilities, gas, taxes, other. "
                        "The tickets are from Mexico so are in spanish. "
                        "The quantity data can be in a column with names like: CANT, CANTIDAD"
                        "Always extract the raw name, do not halluciante or correct it, just use what it says in the receipt"
                        "If you dont find a good match for an item category, do not hallucinate, just use 'other' as category."
                        "Return ONLY valid JSON, no markdown formatting."
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
if __name__ == "__main__":
    text = 'Cerveza Victoria 12 pzs 355ml'
    embedding = generate_embedding(text)
    print(embedding)