# Structured LLM Responses with Pydantic

How to ensure LLM responses conform to expected structures using Pydantic validation.

## The Problem

LLMs return unstructured text by default. When building applications, you need predictable, typed responses that your code can reliably parse and use.

## The Solution

Use **structured outputs** with Pydantic models:

1. Define a Pydantic model describing the exact response shape
2. Pass it to the LLM API's structured output feature
3. Parse and validate the response with Pydantic
4. Retry on validation failures

---

## Step 1: Define Your Pydantic Response Model

```python
from typing import List
from pydantic import BaseModel, Field


class ExtractedPerson(BaseModel):
    name: str = Field(description="The person's full name")
    age: int | None = Field(default=None, description="The person's age if mentioned")
    occupation: str | None = Field(default=None, description="The person's job or role")


class ExtractionResult(BaseModel):
    people: List[ExtractedPerson] = Field(description="List of people mentioned in the text")
    summary: str = Field(description="A one-sentence summary of the text")
```

**Key points:**
- Use `Field(description=...)` to guide the LLM on what each field should contain
- Use `| None` with defaults for optional fields
- Nest models for complex structures

---

## Step 2: Call the LLM with Structured Output

```python
import json
import openai


async def extract_people_from_text(text: str) -> ExtractionResult:
    client = openai.AsyncOpenAI()

    messages = [
        {
            "role": "system",
            "content": "Extract information about people mentioned in the text.",
        },
        {
            "role": "user",
            "content": text,
        },
    ]

    # OpenAI's beta .parse() accepts Pydantic models directly
    response = await client.beta.chat.completions.parse(
        model="gpt-4o",
        messages=messages,
        response_format=ExtractionResult,  # Pass the Pydantic class
    )

    # Parse JSON and validate with Pydantic
    content = response.choices[0].message.content
    return ExtractionResult(**json.loads(content))
```

**What happens here:**
1. OpenAI converts your Pydantic model to a JSON schema internally
2. The LLM is constrained to output valid JSON matching that schema
3. You parse the JSON and instantiate your Pydantic model (validation happens automatically)

---

## Step 3: Add Retry on Validation Errors

Sometimes the LLM still produces invalid output. Wrap with retry logic:

```python
from pydantic import ValidationError


async def extract_with_retry(text: str, max_attempts: int = 3) -> ExtractionResult:
    client = openai.AsyncOpenAI()

    messages = [
        {"role": "system", "content": "Extract information about people."},
        {"role": "user", "content": text},
    ]

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.beta.chat.completions.parse(
                model="gpt-4o",
                messages=messages,
                response_format=ExtractionResult,
            )
            content = response.choices[0].message.content
            return ExtractionResult(**json.loads(content))

        except (ValidationError, json.JSONDecodeError) as e:
            if attempt == max_attempts:
                raise
            print(f"Attempt {attempt} failed: {e}. Retrying...")

    raise RuntimeError("Should not reach here")
```

---

## Dynamic Models with `create_model()`

When constraints are only known at runtime (e.g., allowed categories from a database):

```python
from pydantic import create_model, Field


def build_category_extractor(allowed_categories: list[str]):
    """Build a Pydantic model with dynamic enum constraint."""

    # json_schema_extra adds an "enum" constraint to the JSON schema
    CategoryResult = create_model(
        "CategoryResult",
        category=(
            str,
            Field(
                description="The document category",
                json_schema_extra={"enum": allowed_categories},
            ),
        ),
        confidence=(float, Field(description="Confidence score 0-1")),
    )

    return CategoryResult


# Usage
allowed = ["invoice", "receipt", "contract", "other"]
CategoryResult = build_category_extractor(allowed)

response = await client.beta.chat.completions.parse(
    model="gpt-4o",
    messages=messages,
    response_format=CategoryResult,
)
```

---

## Using with Google Vertex AI

Vertex AI requires a JSON schema instead of a Pydantic class directly:

```python
import json
from typing import Any, Dict, Type

from pydantic import BaseModel
from vertexai.generative_models import GenerationConfig, GenerativeModel


def get_resolved_pydantic_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Convert Pydantic model to resolved JSON schema."""
    schema = model.model_json_schema(mode="serialization").copy()
    definitions = schema.pop("$defs", {})
    return resolve_refs(schema, definitions)


def resolve_refs(schema: Dict[str, Any], definitions: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively replace $ref with actual definitions."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return resolve_refs(definitions[name], definitions)

    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        schema["properties"] = {
            k: resolve_refs(v, definitions) for k, v in properties.items()
        }

    if schema.get("type") == "array":
        schema["items"] = resolve_refs(schema["items"], definitions)

    return schema


async def extract_with_vertexai(text: str) -> ExtractionResult:
    schema = get_resolved_pydantic_schema(ExtractionResult)

    model = GenerativeModel("gemini-1.5-pro")

    response = await model.generate_content_async(
        text,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )

    return ExtractionResult(**json.loads(response.text))
```

---

## Reusable Retry Decorator

For cleaner code, use a decorator:

```python
import asyncio
from functools import wraps
from typing import Callable, Optional


def retry_async(
    retry_on: tuple = (ValidationError, json.JSONDecodeError),
    max_attempts: int = 3,
    delay: float = 0.5,
):
    """Decorator to retry async functions on specific exceptions."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error: Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retry_on as e:
                    last_error = e
                    if attempt < max_attempts:
                        await asyncio.sleep(delay)

            raise last_error

        return wrapper

    return decorator


# Usage
@retry_async(max_attempts=4)
async def extract_people_from_text(text: str) -> ExtractionResult:
    ...
```

---

## Full Working Example

```python
import asyncio
import json
from typing import List

import openai
from pydantic import BaseModel, Field, ValidationError


class ExtractedPerson(BaseModel):
    name: str = Field(description="The person's full name")
    age: int | None = Field(default=None, description="The person's age if mentioned")
    occupation: str | None = Field(default=None, description="The person's job or role")


class ExtractionResult(BaseModel):
    people: List[ExtractedPerson]
    summary: str


async def extract_people(text: str, max_attempts: int = 3) -> ExtractionResult:
    client = openai.AsyncOpenAI()

    messages = [
        {"role": "system", "content": "Extract information about people mentioned."},
        {"role": "user", "content": text},
    ]

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.beta.chat.completions.parse(
                model="gpt-4o",
                messages=messages,
                response_format=ExtractionResult,
            )
            content = response.choices[0].message.content
            return ExtractionResult(**json.loads(content))

        except (ValidationError, json.JSONDecodeError) as e:
            if attempt == max_attempts:
                raise
            print(f"Attempt {attempt} failed, retrying...")


async def main():
    text = """
    John Smith, a 35-year-old software engineer at Google, met with
    his colleague Sarah Johnson for lunch. Sarah works as a product manager.
    """

    result = await extract_people(text)

    print(f"Summary: {result.summary}")
    for person in result.people:
        print(f"  - {person.name}, age={person.age}, job={person.occupation}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Summary

| Step | What to do |
|------|------------|
| 1 | Define a Pydantic model with `Field(description=...)` |
| 2 | Pass it to `response_format` in OpenAI's `.parse()` |
| 3 | Parse JSON and instantiate your model |
| 4 | Wrap with retry for `ValidationError` / `JSONDecodeError` |
| 5 | Use `create_model()` for runtime constraints |

**Key benefits:**
- Type safety throughout your codebase
- Automatic validation of LLM outputs
- Clear contract between your code and the LLM
- Graceful handling of malformed responses
