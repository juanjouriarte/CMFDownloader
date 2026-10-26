from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src import captcha

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(captcha,'GEMINI_API_KEY','test-only')
    monkeypatch.delenv('CMF_CAPTCHA_MODEL',raising=False)
    monkeypatch.delenv('CMF_CAPTCHA_FALLBACK_MODEL',raising=False)
    monkeypatch.setattr(captcha.time,'sleep',lambda _:None)
    client=Mock()
    monkeypatch.setattr(captcha.genai,'Client',Mock(return_value=client))
    return client

def test_success_uses_primary_without_fallback(client):
    client.models.generate_content.return_value=SimpleNamespace(text=' abC12 ')
    assert captcha.solve(b'image')=='ABC12'
    assert client.models.generate_content.call_count==1

def test_provider_failure_falls_back_after_bounded_primary_retries(client):
    client.models.generate_content.side_effect=[RuntimeError('provider down')]*3+[SimpleNamespace(text='xy98')]
    assert captcha.solve(b'image')=='XY98'
    assert [c.kwargs['model'] for c in client.models.generate_content.call_args_list]==['gemma-4-31b-it']*3+['gemini-3.1-flash-lite']

def test_empty_response_and_configurable_fallback(client,monkeypatch):
    monkeypatch.setenv('CMF_CAPTCHA_MODEL','primary')
    monkeypatch.setenv('CMF_CAPTCHA_FALLBACK_MODEL','backup')
    client.models.generate_content.side_effect=[SimpleNamespace(text=None),SimpleNamespace(text='1234')]
    assert captcha.solve(b'image',max_attempts=1)=='1234'
    assert [c.kwargs['model'] for c in client.models.generate_content.call_args_list]==['primary','backup']

def test_exhausted_failures_return_empty(client,monkeypatch):
    monkeypatch.setenv('CMF_CAPTCHA_FALLBACK_MODEL','')
    client.models.generate_content.side_effect=RuntimeError('failed')
    assert captcha.solve(b'image',max_attempts=2)==''
    assert client.models.generate_content.call_count==2
