"""Shared handling of Anthropic message content blocks."""

from typing import Any


def response_text(response: Any) -> str:
    """Join text blocks in order, ignoring thinking and other content blocks."""
    parts = [block.text for block in response.content if block.type == 'text']
    if not parts:
        raise ValueError('Anthropic response contained no text blocks')
    return ''.join(parts)
