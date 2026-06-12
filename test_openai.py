import os
from openai import OpenAI
from pydantic import BaseModel

class Item(BaseModel):
    name: str

class Ticket(BaseModel):
    items: list[Item]

client = OpenAI(api_key="fake-key")

try:
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        response_format=Ticket
    )
    print(response)
except Exception as e:
    import traceback
    traceback.print_exc()

print("Now testing client.chat.completions.parse")
try:
    response = client.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        response_format=Ticket
    )
    print(response)
except Exception as e:
    import traceback
    traceback.print_exc()

