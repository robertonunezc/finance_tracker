from openai import OpenAI
import os
from dotenv import load_dotenv
import logging
from typing import List
from pydantic import BaseModel, Field
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
def extract_receipt_text(image_path:str, max_attempts:int = 3)->Ticket:
    base64_image = encode_image_to_base64(image_path)
    logger.info(f"Extracting text from image: {image_path}")
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract all readable text from this grocery receipt and structure it as a JSON object with the following format: {\"items\": [{\"name\": \"item name\", \"price\": 0.00, \"quantity\": 1, \"category\": \"category name\"}], \"total\": 0.00}. "
                            "Categorize each item using one of these categories: groceries, beverages, dairy, produce, meat, bakery, frozen, pantry, snacks, medication, health, personal_care, toiletries, household, cleaning, paper_products, pet_supplies, baby_products, electronics, restaurant, clothing, school_supplies, transportation, entertainment, utilities, gas, taxes, other. "
                            "The tickets are from Mexico so are in spanish. "
                            "If you dont find a good match for an item, do not hallucinate, just use 'other' as category."
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
            max_tokens=1000,
            response_format=Ticket)
            result = response.choices[0].message.content
            logger.info(f"GPT extraction successful: {len(result)} characters")
            return Ticket(**json.loads(result))
        except (ValidationError, json.JSONDecodeError) as e:
            if attempt == max_attempts:
                raise
            print(f"Attempt {attempt} failed: {e}. Retrying...")
        except Exception as e:
            logger.error(f"GPT extraction failed: {str(e)}")
        raise

# Example usage
if __name__ == "__main__":
    receipt_text = extract_receipt_text("tickets/w2.jpg")
    print(receipt_text)
21