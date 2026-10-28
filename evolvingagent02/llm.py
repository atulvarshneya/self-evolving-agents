"""Talking to the LLM: settings, the shared client, and parsing the LLM's JSON replies.

This is the only provider-specific module; it currently uses Anthropic's API.
"""
import os
import json
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)


# Initialize the Anthropic Client (Automatically picks up ANTHROPIC_API_KEY from environment)
client = Anthropic()
MODEL_NAME = os.getenv("DEFAULT_CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = int(os.getenv("DEFAULT_MAX_TOKENS", "16000"))


def _extract_text(response):
    message_text = "".join(block.text for block in response.content if block.type == "text")
    return message_text


def call_llm(system_prompt, user_prompt):

    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    message_text = _extract_text(response)

    return message_text


def parse_json(message_text):
    """Parse the LLM's JSON reply into a dict, tolerating a ```json code fence. Raises ValueError otherwise."""
    try:
        payload = json.loads(message_text)
    except ValueError:
        # Fallback if the LLM accidentally wraps the raw JSON in backticks
        text = message_text.replace("```json", "").replace("```", "").strip()
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object, got: {message_text!r}")
    return payload
