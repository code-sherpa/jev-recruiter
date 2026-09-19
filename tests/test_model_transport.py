"""Offline transport retries preserve the model input and never run browser actions."""

import httpx
import pytest

from jev_ultrafast import model


@pytest.mark.parametrize('timeout', [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout])
def test_timeout_retries_identical_request_then_returns_success(monkeypatch, caplog, timeout):
    requests, sleeps = [], []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise timeout('private provider failure', request=request)
        return httpx.Response(200, json={'answers': {'operation': 'DONE'}})

    monkeypatch.setattr(model.time, 'sleep', sleeps.append)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(model, 'CLIENT', client)
        result = model.post_json('https://provider.example/choices', 'private-key', {'state': 'observed'})
    assert result == {'answers': {'operation': 'DONE'}}
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert requests[0].url == requests[1].url
    assert requests[0].headers['Authorization'] == requests[1].headers['Authorization'] == 'Bearer private-key'
    assert sleeps == [0.5]
    assert 'private provider failure' not in caplog.text
    assert 'private-key' not in caplog.text


def test_exhausted_timeouts_stop_after_three_attempts(monkeypatch):
    requests, sleeps = [], []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout('private provider failure', request=request)

    monkeypatch.setattr(model.time, 'sleep', sleeps.append)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(model, 'CLIENT', client)
        with pytest.raises(RuntimeError, match='Model connection failed; no action executed') as error:
            model.post_json('https://provider.example/choices', 'private-key', {'state': 'observed'})
    assert len(requests) == 3
    assert sleeps == [0.5, 1.0]
    assert isinstance(error.value.__context__, httpx.ReadTimeout)
    assert 'private provider failure' not in str(error.value)


def test_http_status_and_timeout_share_one_attempt_budget(monkeypatch):
    requests, sleeps = [], []

    def handle(request):
        requests.append(request)
        if len(requests) == 2:
            raise httpx.ReadTimeout('private provider failure', request=request)
        return httpx.Response(503)

    monkeypatch.setattr(model.time, 'sleep', sleeps.append)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(model, 'CLIENT', client)
        with pytest.raises(RuntimeError, match='HTTP 503'):
            model.post_json('https://provider.example/choices', 'private-key', {'state': 'observed'})
    assert len(requests) == 3
    assert sleeps == [0.5, 1.0]
