from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.observability import install_diagnostics
from app.services.email import SendGridEmailSender


def events(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == 'stayzy.api']


async def test_request_ids_validation_and_safe_exception(caplog):
    app = FastAPI()
    install_diagnostics(app)

    @app.get('/broken/{value}')
    async def broken(value: str):
        raise RuntimeError('secret-email@example.com token=hidden sql-password')

    @app.get('/number')
    async def number(value: int):
        return value

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        broken = await client.get('/broken/private-value?token=hidden', headers={'X-Request-ID': 'untrusted'})
        invalid = await client.get('/number?value=secret-email@example.com')
        missing = await client.get('/unknown-secret')
    assert broken.status_code == 500
    assert broken.json()['detail']['code'] == 'internal_error'
    identifier = broken.headers['x-request-id']
    assert len(identifier) == 32 and identifier != 'untrusted'
    assert broken.json()['detail']['request_id'] == identifier
    assert invalid.status_code == 422
    assert invalid.json()['detail']['code'] == 'invalid_request'
    assert missing.status_code == 404
    logs = events(caplog)
    completed = [e for e in logs if e['event'] == 'request.completed']
    assert [e['status'] for e in completed] == [500, 422, 404]
    assert completed[0]['route'] == '/broken/{value}'
    assert completed[-1]['route'] == 'unmatched'
    assert len({e['request_id'] for e in completed}) == 3
    assert any(e['event'] == 'request.exception' and 'RuntimeError' in e['error_types'] for e in logs)
    text = json.dumps(logs) + broken.text + invalid.text
    for secret in ['secret-email@example.com', 'hidden', 'sql-password', 'private-value', 'unknown-secret']:
        assert secret not in text


@pytest.mark.parametrize('status,event', [(202,'email.accepted'), (400,'email.rejected'), (401,'email.rejected'), (403,'email.rejected'), (429,'email.rejected'), (500,'email.rejected')])
async def test_sendgrid_outcomes_are_safe(status, event, caplog):
    def handler(request):
        assert json.loads(request.content)['personalizations'][0]['dynamic_template_data']['magic_link'].endswith('token=secret-token')
        return httpx.Response(status, json={'errors':[{'message':'secret-email@example.com secret-token'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = SendGridEmailSender(Settings(_env_file=None, environment='test', sendgrid_api_key='secret-key', sendgrid_magic_link_template_id='secret-template'), client)
        result = await sender.send_magic_link('secret-email@example.com', 'https://example.com?token=secret-token', 'private-challenge')
    assert result.accepted == (status == 202)
    logs = events(caplog)
    assert any(e['event'] == event and e['status'] == status for e in logs)
    text = json.dumps(logs)
    for value in ['secret-email', 'secret-token', 'secret-key', 'secret-template', 'private-challenge']:
        assert value not in text


async def test_missing_email_configuration_and_transport_failure(caplog):
    def handler(request):
        raise httpx.ConnectError('secret-host secret-token', request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = SendGridEmailSender(Settings(_env_file=None, environment='test', sendgrid_api_key=None, sendgrid_magic_link_template_id=None), client)
        assert not (await sender.send_magic_link('private', 'private', 'private')).accepted
        assert any(e['event'] == 'email.skipped' and 'STAYZY_SENDGRID_API_KEY' in e['missing'] for e in events(caplog))
        sender.settings.sendgrid_api_key = 'secret-key'
        sender.settings.sendgrid_magic_link_template_id = 'secret-template'
        with pytest.raises(httpx.ConnectError):
            await sender.send_magic_link('private', 'private', 'private')
    logs = events(caplog)
    assert any(e['event'] == 'email.send_failed' for e in logs)
    assert 'secret-host' not in json.dumps(logs)


async def test_email_failure_stays_generic_and_is_correlated(api_client, caplog):
    client, app, _, storage, _ = api_client
    class FailedSender:
        async def send_magic_link(self, *args):
            raise RuntimeError('sensitive message')
    app.state.email_sender = FailedSender()
    response = await client.post('/v1/auth/magic-links', json={'email':'person@example.com'})
    assert response.status_code == 202
    assert response.json() == {'status':'accepted'}
    logs = events(caplog)
    assert any(e['event'] == 'auth.magic_link_processed' and e['send_state'] == 'failed' and e['request_id'] == response.headers['x-request-id'] for e in logs)
    assert 'sensitive message' not in json.dumps(logs)
    storage.available = False
    response = await client.get('/health/ready')
    assert response.status_code == 503
    assert any(e['event'] == 'readiness.failed' and e['dependency'] == 'bucket' for e in events(caplog))


async def test_throttling_is_visible_only_in_logs(api_client, caplog):
    client, _, _, _, _ = api_client
    for _ in range(4):
        response = await client.post('/v1/auth/magic-links', json={'email':'limited@example.com'})
        assert response.status_code == 202
        assert response.json() == {'status':'accepted'}
    assert any(e['event'] == 'auth.magic_link_throttled' for e in events(caplog))
    assert 'limited@example.com' not in json.dumps(events(caplog))


async def test_parallel_requests_have_separate_contexts(caplog):
    import asyncio
    from app.observability import emit
    app = FastAPI()
    install_diagnostics(app)
    @app.get('/parallel')
    async def parallel():
        emit('parallel.before')
        await asyncio.sleep(0)
        emit('parallel.after')
        return {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        responses = await asyncio.gather(*(client.get('/parallel') for _ in range(5)))
    identifiers = {r.headers['x-request-id'] for r in responses}
    assert len(identifiers) == 5
    for identifier in identifiers:
        request_events = [e['event'] for e in events(caplog) if e['request_id'] == identifier]
        assert request_events == ['request.started', 'parallel.before', 'parallel.after', 'request.completed']

@pytest.mark.parametrize('headers', [[], [('XCorrelationId', '')], [('XCorrelationId', 'secret-injected')],
    [('XCorrelationId', 'x' * 5000)], [('XCorrelationId', 'bad\nsecret')],
    [('XCorrelationId', 'a' * 32)],
    [('XCorrelationId', 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA'), ('xcorrelationid', 'BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB')]])
async def test_invalid_correlation_is_replaced_without_logging(headers, caplog):
    import uuid
    app = FastAPI()
    install_diagnostics(app)
    @app.get('/ok')
    async def ok():
        return {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        response = await client.get('/ok', headers=headers)
    identifier = response.headers['xcorrelationid']
    assert str(uuid.UUID(identifier)) == identifier
    assert response.status_code == 200
    assert all(e['correlation_id'] == identifier for e in events(caplog))
    for _, value in headers:
        if value:
            assert value not in json.dumps(events(caplog))


@pytest.mark.parametrize('status', [200, 401, 403, 404, 422, 500])
async def test_correlation_echo_and_error_contract(status, caplog):
    from app.errors import api_error
    from app.observability import correlation_id, request_id
    identifier = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    app = FastAPI()
    install_diagnostics(app)
    @app.get('/check')
    async def check(value: int = 1):
        if status == 500:
            raise RuntimeError('private-token')
        if status not in (200, 422):
            raise api_error(status, 'test_error', 'Test error')
        return {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        response = await client.get('/check', params={'value': 'bad' if status == 422 else '1'},
                                    headers={'xCoRrElAtIoNiD': identifier.upper()})
    assert response.status_code == status
    assert response.headers['xcorrelationid'] == identifier
    assert len(response.headers['x-request-id']) == 32
    if status != 200:
        assert response.json()['detail']['correlation_id'] == identifier
        assert response.json()['detail']['request_id'] == response.headers['x-request-id']
    assert all(e['correlation_id'] == identifier for e in events(caplog))
    assert correlation_id.get() is None
    assert request_id.get() is None
    assert 'private-token' not in json.dumps(events(caplog))


async def test_concurrent_shared_correlations_keep_distinct_request_ids(caplog):
    import asyncio
    from app.observability import emit, correlation_id
    ids = ['aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', '11111111-2222-3333-4444-555555555555']
    app = FastAPI()
    install_diagnostics(app)
    @app.get('/parallel')
    async def parallel():
        emit('work.before')
        await asyncio.sleep(0)
        emit('work.after')
        return {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        responses = await asyncio.gather(*(client.get('/parallel', headers={'XCorrelationId': value}) for value in ids * 3))
    assert len({r.headers['x-request-id'] for r in responses}) == 6
    for response in responses:
        logs = [e for e in events(caplog) if e['request_id'] == response.headers['x-request-id']]
        assert [e['event'] for e in logs] == ['request.started', 'work.before', 'work.after', 'request.completed']
        assert all(e['correlation_id'] == response.headers['xcorrelationid'] for e in logs)
    assert correlation_id.get() is None


async def test_voice_denial_has_correlated_decision(api_client, caplog):
    from conftest import sign_in
    client, _, email, _, _ = api_client
    tokens = await sign_in(client, email)
    identifier = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    response = await client.post('/v1/voice-packs/voice_harbor/download', json={'locale': 'en-US'},
        headers={'Authorization': f"Bearer {tokens['access_token']}", 'XCorrelationId': identifier})
    assert response.status_code == 403
    logs = [e for e in events(caplog) if e['correlation_id'] == identifier]
    assert [e['event'] for e in logs] == ['request.started', 'voice_download.entitlement_checked', 'request.rejected', 'request.completed']
    assert logs[1]['allowed'] is False
    assert logs[2]['code'] == 'premium_required'
    assert logs[3]['route'] == '/v1/voice-packs/{voice_id}/download'
