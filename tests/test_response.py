"""Mixed Anthropic content must work in both API consumers."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from radar import extract, plan


@pytest.fixture(params=[extract, plan], ids=['extraction', 'planning'])
def consumer(request):
    return request.param


def client_for(*contents):
    client = Mock()
    client.messages.create.side_effect = [
        SimpleNamespace(content=content) for content in contents
    ]
    return client


def run_consumer(consumer, client):
    if consumer is extract:
        return extract.extract_from_pdf(
            b'%PDF-1.4', 'test.pdf', client=client, fixtures_mode=False)
    return plan.plan_week(
        [], [], date(2026, 10, 15), client=client, fixtures_mode=False)


@pytest.mark.parametrize('content', [
    [SimpleNamespace(type='thinking', thinking='reasoning'),
     SimpleNamespace(type='text', text='[]')],
    [SimpleNamespace(type='text', text='['),
     SimpleNamespace(type='thinking', thinking='reasoning'),
     SimpleNamespace(type='text', text=']')],
])
def test_text_blocks_are_joined_and_thinking_ignored(consumer, content):
    client = client_for(content)
    result = run_consumer(consumer, client)
    assert result.source == 'api'
    assert client.messages.create.call_count == 1


@pytest.mark.parametrize('content', [
    [], [SimpleNamespace(type='thinking', thinking='reasoning')],
])
def test_no_text_raises_clear_error(consumer, content):
    with pytest.raises(ValueError, match='Anthropic response contained no text blocks'):
        consumer._call_model(client_for(content), b'pdf' if consumer is extract else 'prompt')


def test_no_text_uses_retry(consumer):
    client = client_for(
        [SimpleNamespace(type='thinking', thinking='reasoning')],
        [SimpleNamespace(type='text', text='[]')],
    )
    result = run_consumer(consumer, client)
    assert result.source == 'api'
    assert client.messages.create.call_count == 2


def test_no_text_twice_falls_back_with_reason(consumer):
    client = client_for([], [])
    result = run_consumer(consumer, client)
    assert result.source == 'fixture'
    assert 'Anthropic response contained no text blocks' in result.reason
    assert client.messages.create.call_count == 2
