import base64
import json
import logging
import os
import re
from typing import Any, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from receipt import services as receipt_services
from receipt.models import Category, ReceiptItem

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
EXTRACTION_VALIDATION_MAX_ATTEMPTS = 3
REPAIR_ERROR_LIMIT = 8
CATEGORY_OPTIONS_PROMPT = "\n".join(
    f"- {value}: {label}" for value, label in Category.choices
)
EXTRACTION_PROMPT=f"""
Extract all readable text from this grocery receipt and structure it as Ticket object.
The tickets are from Mexico so are in Spanish.
First identify the product table header and its columns before extracting item rows.
Common item columns:
- CANT, CANTIDAD, CANT. -> quantity. This may be a decimal weight such as 0.545, 2.065, or 3.865.
- DESCRIPCION, ARTICULO, PRODUCTO -> raw item name
- PRECIO, PRICE, P.U., UNIT PRICE -> unit_price
- TOTAL, IMPORTE -> line_total
- The item name and receipt context -> category
For each product row, extract values by matching the row values to the detected column positions.
The quantity data can be in a column with names like: CANT, CANTIDAD. If a quantity value is visible in the row, use the exact numeric value, including decimals below 1. Never round, truncate, or coerce quantity to an integer. Only return 1 when no quantity is readable for that specific row.
Always extract the raw item name, do not hallucinate or correct it; use exactly what appears on the receipt.
For the store name, prefer the canonical commercial name that appears on the receipt. If the receipt includes legal suffixes such as SA DE CV, S.A. DE C.V., S.A. DE C.V, SOCIEDAD ANONIMA, or similar, normalize them away in the final store_name value.
For product items, line_total means the row total for that item. If the TOTAL or IMPORTE column is present, always use it as line_total.
If TOTAL/IMPORTE is not present but unit price and quantity are visible, calculate line_total as quantity multiplied by unit_price and use the full row as source_text with lower confidence.
unit_price means the single-item price from PRECIO, PRICE, P.U., or UNIT PRICE. If no unit price column is present, return null for unit_price.
If a row has quantity, unit_price, and line_total, verify line_total is approximately quantity multiplied by unit_price.
Examples:
- "0.545 AGUACATE KG ... PRECIO 39.80 TOTAL 21.69" -> quantity 0.545, unit_price 39.80, line_total 21.69.
- "2 CORAZON LECHUGA ... PRECIO 15.80 TOTAL 31.60" -> quantity 2, unit_price 15.80, line_total 31.60.
Do not extract items where a minus sign appears in front or after the amount, as those are likely discounts or returns.
For item category, choose exactly one category key from this list. Return the key, not the Spanish label and not a translated word:
{CATEGORY_OPTIONS_PROMPT}
Use "other" only when no specific category fits. For category source_text, use the raw item name or visible row text that supports the category decision.
Examples:
- "TIENDAS CHEDRAUI SA DE CV" -> "chedraui"
- "Soriana S.A. de C.V." -> "soriana"
- "Walmart de México S.A. de C.V." -> "walmart"
If the store name is unclear, return null.
Try to extract the subtotal, discount, store name and total amount if possible. If you cannot find them, return null for those fields.
For every receipt-level field and every item field, return an object with:
- value: the normalized extracted value
- source_text: the exact visible text snippet used as evidence
- confidence: a number from 0 to 1 indicating how confident you are
Use lower confidence when the receipt image is blurry, the amount is ambiguous, the source text does not clearly support the value, or you are guessing.
Return ONLY valid JSON, no markdown formatting.
"""

class TextExtractionField(BaseModel):
    value: Optional[str] = Field(description="Normalized extracted text value")
    source_text: str = Field(default="", description="Exact source text used as evidence")
    confidence: float = Field(ge=0, le=1, description="Confidence score from 0 to 1")


class AmountExtractionField(BaseModel):
    value: Optional[float] = Field(description="Normalized numeric amount")
    source_text: str = Field(default="", description="Exact source text used as evidence")
    confidence: float = Field(ge=0, le=1, description="Confidence score from 0 to 1")


class CategoryExtractionField(BaseModel):
    value: Category = Field(description="Exact category key from the allowed receipt item categories")
    source_text: str = Field(default="", description="Exact source text used as category evidence")
    confidence: float = Field(ge=0, le=1, description="Confidence score from 0 to 1")


class QuantityExtractionField(BaseModel):
    value: Optional[float] = Field(description="Exact positive quantity from CANT or CANTIDAD, including decimal weights")
    source_text: str = Field(default="", description="Exact source text used as quantity evidence")
    confidence: float = Field(ge=0, le=1, description="Confidence score from 0 to 1")


class Item(BaseModel):
    name: TextExtractionField = Field(description="Raw name of the item exactly as printed on the receipt")
    unit_price: Optional[AmountExtractionField] = Field(
        default=None,
        description="Single-item price from PRECIO, PRICE, P.U., or UNIT PRICE. Return null when no unit price is visible.",
    )
    line_total: AmountExtractionField = Field(
        description="Line total for this item row from TOTAL or IMPORTE. This is quantity multiplied by unit price.",
    )
    quantity: QuantityExtractionField = Field(description="Quantity from CANT or CANTIDAD. Preserve decimal weights exactly. Use 1 only when quantity is not visible.")
    category: CategoryExtractionField = Field(description="Best category key for the item")

class Ticket(BaseModel):
    items: List[Item] = Field(description="List of items found in the ticket")
    subtotal: Optional[AmountExtractionField] = Field(default=None, description="Subtotal amount of the ticket")
    discount: Optional[AmountExtractionField] = Field(default=None, description="Discount amount of the ticket")
    store_name: Optional[TextExtractionField] = Field(default=None, description="Name of the store where the ticket was issued")
    total: Optional[AmountExtractionField] = Field(default=None, description="Total amount of the ticket")


class ModelRefusalError(ValueError):
    pass


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


def normalize_category_key(value: Any) -> str | None:
    if value in (None, ""):
        return None

    raw_value = str(value).strip().strip("'\"`").lower()
    if not raw_value:
        return None

    key_lookup = {category_value.lower(): category_value for category_value, _ in Category.choices}
    label_lookup = {label.lower(): category_value for category_value, label in Category.choices}
    if raw_value in key_lookup:
        return key_lookup[raw_value]
    if raw_value in label_lookup:
        return label_lookup[raw_value]

    key_prefix = re.match(r"^([a-z_]+)\s*\(", raw_value)
    if key_prefix and key_prefix.group(1) in key_lookup:
        return key_lookup[key_prefix.group(1)]

    return None


# Set your API key

# Load the image and encode it as base64
def encode_image_to_base64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_ticket_from_messages(
    messages: list[dict[str, Any]],
    *,
    max_attempts: int = EXTRACTION_VALIDATION_MAX_ATTEMPTS,
) -> Ticket:
    request_messages = messages
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.parse(
                model="gpt-4o-mini",
                temperature=0.0,
                seed=0,
                messages=request_messages,
                response_format=Ticket,
            )
            return _parsed_ticket_from_response(response)
        except (ValidationError, json.JSONDecodeError) as exc:
            if attempt >= max_attempts:
                logger.error(
                    "Ticket extraction validation failed after %s attempts: %s",
                    max_attempts,
                    exc,
                )
                raise
            logger.warning(
                "Ticket extraction validation failed on attempt %s/%s; retrying with repair instruction: %s",
                attempt,
                max_attempts,
                exc,
            )
            request_messages = _messages_with_repair_instruction(messages, exc)

    raise RuntimeError("Ticket extraction retry loop exited unexpectedly")


def _parsed_ticket_from_response(response: Any) -> Ticket:
    message = response.choices[0].message
    parsed_ticket = message.parsed
    if parsed_ticket is None:
        raise ModelRefusalError(f"Model refused: {message.refusal}")
    return parsed_ticket


def _messages_with_repair_instruction(
    messages: list[dict[str, Any]],
    exc: ValidationError | json.JSONDecodeError,
) -> list[dict[str, Any]]:
    return [
        *messages,
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _repair_instruction(exc),
                }
            ],
        },
    ]


def _repair_instruction(exc: ValidationError | json.JSONDecodeError) -> str:
    return (
        "Previous response failed schema validation for the Ticket response_format.\n"
        "Return the same receipt extraction again as valid JSON matching the Ticket schema. "
        "Fix only the fields, types, and constraints listed below. Do not include markdown.\n\n"
        f"{_format_validation_error(exc)}"
    )


def _format_validation_error(exc: ValidationError | json.JSONDecodeError) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        lines = []
        for error in errors[:REPAIR_ERROR_LIMIT]:
            location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
            error_type = error.get("type", "validation_error")
            message = error.get("msg", str(exc))
            lines.append(f"- {location}: {message} ({error_type})")
        remaining = len(errors) - REPAIR_ERROR_LIMIT
        if remaining > 0:
            lines.append(f"- {remaining} more validation errors omitted")
        return "\n".join(lines)
    return str(exc)


# Prepare the API request
def extract_receipt_text(image_path:str)->Ticket:
    base64_image = encode_image_to_base64(image_path)
    logger.info(f"Extracting text from image: {image_path}")
    try:
        messages = [
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
        ]
        parsed_ticket = extract_ticket_from_messages(messages)

        if getattr(parsed_ticket, "store_name", None) and parsed_ticket.store_name.value:
            parsed_ticket.store_name.value = normalize_store_name(parsed_ticket.store_name.value)

        logger.info(f"GPT extraction successful")
        return parsed_ticket
    except (ValidationError, json.JSONDecodeError) as e:
        logger.error(f"Validation or parsing error: {e}")
        raise
    except ModelRefusalError as e:
        logger.error(f"Model refusal: {e}")
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
        temperature=0.0,
        seed=0.0,
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
    category = normalize_category_key(raw_category) or category_lookup.get(raw_category.lower(), "other")
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

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT + f"\n\nTranscription:\n{transcribed_text}\n\n"
                    }
                ]
            }
        ]
        parsed_ticket = extract_ticket_from_messages(messages)

        if getattr(parsed_ticket, "store_name", None) and parsed_ticket.store_name.value:
            parsed_ticket.store_name.value = normalize_store_name(parsed_ticket.store_name.value)
        
        logger.info(f"Extraction successful: {len(parsed_ticket.items)} items found")
        return parsed_ticket
        
    except (ValidationError, json.JSONDecodeError) as e:
        logger.error(f"Validation or parsing error: {e}")
        raise
    except ModelRefusalError as e:
        logger.error(f"Model refusal: {e}")
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
