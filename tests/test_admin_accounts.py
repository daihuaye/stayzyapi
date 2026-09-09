from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
import pytest
from sqlalchemy import select
from app.admin_models import Administrator, AdministratorReset, AdministratorSession
from app.admin_security import hash_password, verify_password
from app.security import sha256
from conftest import sign_in

PASSWORD = 'temporary password 123'
NEW_PASSWORD = 'my permanent password 456'

async def add_account(factory, email='owner@example.com', role='owner', temporary=False):
    async with factory() as db:
        account=Administrator(email=email,role=role,password_hash=await hash_password(PASSWORD),must_change_password=temporary,active=True)
        db.add(account); await db.commit(); return account.id

async def login(client,email='owner@example.com',password=PASSWORD):
    response=await client.post('/v1/admin/auth/login',json={'email':email,'password':password})
    assert response.status_code==200, response.text
    return {'Authorization':f"Bearer {response.json()['session_token']}"}

@pytest.mark.asyncio
async def test_first_login_password_change_and_customer_isolation(api_client,session_factory,caplog):
    client,_,email,*_=api_client
    account_id=await add_account(session_factory,temporary=True)
    headers=await login(client)
    assert (await client.get('/v1/admin/auth/me',headers=headers)).json()['must_change_password']
    for path in ['/v1/admin/accounts','/v1/admin/experiments']:
        assert (await client.get(path,headers=headers)).status_code==403
    changed=await client.post('/v1/admin/auth/change-password',headers=headers,json={'current_password':PASSWORD,'new_password':NEW_PASSWORD})
    assert changed.status_code==204
    assert (await client.get('/v1/admin/auth/me',headers=headers)).status_code==401
    headers=await login(client,password=NEW_PASSWORD)
    assert (await client.get('/v1/admin/experiments',headers=headers)).status_code==200
    async with session_factory() as db:
        account=await db.get(Administrator,account_id)
        assert account.password_hash.startswith('$argon2id$') and PASSWORD not in account.password_hash
        assert await verify_password(account.password_hash,NEW_PASSWORD)
        sessions=(await db.scalars(select(AdministratorSession))).all()
        assert all(s.token_hash != headers['Authorization'][7:] for s in sessions)
    customer=await sign_in(client,email)
    assert (await client.get('/v1/admin/experiments',headers={'Authorization':f"Bearer {customer['access_token']}"})).status_code==401
    assert (await client.get('/v1/admin/experiments',headers={'Authorization':'Bearer old-shared-admin-token'})).status_code==401
    assert PASSWORD not in caplog.text and NEW_PASSWORD not in caplog.text

@pytest.mark.asyncio
async def test_owner_management_roles_deactivation_and_duplicates(api_client,session_factory):
    client,*_=api_client
    owner=await add_account(session_factory)
    headers=await login(client)
    body={'email':' Admin@Example.com ','role':'admin','temporary_password':PASSWORD}
    response=await client.post('/v1/admin/accounts',headers=headers,json=body)
    assert response.status_code==201
    account=response.json(); assert account['email']=='admin@example.com'
    assert 'password_hash' not in account
    assert (await client.post('/v1/admin/accounts',headers=headers,json=body)).status_code==409
    admin=await login(client,'admin@example.com')
    assert (await client.post('/v1/admin/auth/change-password',headers=admin,json={'current_password':PASSWORD,'new_password':NEW_PASSWORD})).status_code==204
    admin=await login(client,'admin@example.com',NEW_PASSWORD)
    assert (await client.get('/v1/admin/accounts',headers=admin)).status_code==403
    assert (await client.get('/v1/admin/experiments',headers=admin)).status_code==200
    assert (await client.patch(f'/v1/admin/accounts/{owner}',headers=headers,json={'active':False})).status_code==409
    assert (await client.patch(f'/v1/admin/accounts/{owner}',headers=headers,json={'role':'admin'})).status_code==409
    assert (await client.patch(f"/v1/admin/accounts/{account['id']}",headers=headers,json={'active':False})).status_code==200
    assert (await client.get('/v1/admin/auth/me',headers=admin)).status_code==401
    assert (await client.patch(f"/v1/admin/accounts/{account['id']}",headers=headers,json={'active':True})).status_code==200
    assert (await client.get('/v1/admin/auth/me',headers=admin)).status_code==401

@pytest.mark.asyncio
async def test_concurrent_owner_demotions_keep_one_owner(api_client,session_factory):
    client,*_=api_client
    a=await add_account(session_factory)
    b=await add_account(session_factory,'second@example.com')
    ha,hb=await login(client),await login(client,'second@example.com')
    responses=await asyncio.gather(client.patch(f'/v1/admin/accounts/{b}',headers=ha,json={'role':'admin'}),client.patch(f'/v1/admin/accounts/{a}',headers=hb,json={'role':'admin'}))
    assert sorted(r.status_code for r in responses)==[200,403]
    async with session_factory() as db:
        owners=(await db.scalars(select(Administrator).where(Administrator.role=='owner',Administrator.active.is_(True)))).all()
        assert len(owners)==1

@pytest.mark.asyncio
async def test_reset_links_single_use_superseded_and_delivery_failure(api_client,session_factory,settings):
    client,_,sender,*_=api_client
    await add_account(session_factory)
    headers=await login(client)
    settings.admin_web_url='https://stayzyweb.example.test'
    async def request_reset(address='owner@example.com'):
        return await client.post('/v1/admin/auth/forgot-password',json={'email':address})
    assert (await request_reset('unknown@example.com')).json()=={'status':'accepted'}
    await request_reset()
    first=parse_qs(urlsplit(sender.admin_deliveries[-1][1]).fragment)['token'][0]
    await request_reset()
    second=parse_qs(urlsplit(sender.admin_deliveries[-1][1]).fragment)['token'][0]
    assert first!=second
    assert (await client.post('/v1/admin/auth/reset-password',json={'token':first,'new_password':NEW_PASSWORD})).status_code==400
    responses=await asyncio.gather(*[client.post('/v1/admin/auth/reset-password',json={'token':second,'new_password':NEW_PASSWORD}) for _ in range(2)])
    assert sorted(r.status_code for r in responses)==[204,400]
    assert (await client.get('/v1/admin/auth/me',headers=headers)).status_code==401
    assert (await login(client,password=NEW_PASSWORD))
    async def fail(*args): raise RuntimeError('private provider error')
    sender.send_admin_reset=fail
    assert (await request_reset()).json()=={'status':'accepted'}
    assert (await request_reset()).json()=={'status':'accepted'}

@pytest.mark.asyncio
async def test_expiry_logout_and_reset_deactivation(api_client,session_factory,settings):
    client,_,sender,*_=api_client
    owner_id=await add_account(session_factory)
    admin_id=await add_account(session_factory,'admin@example.com',role='admin')
    headers=await login(client)
    settings.admin_web_url='https://stayzyweb.example.test'
    await client.post('/v1/admin/auth/forgot-password',json={'email':'admin@example.com'})
    token=parse_qs(urlsplit(sender.admin_deliveries[-1][1]).fragment)['token'][0]
    await client.patch(f'/v1/admin/accounts/{admin_id}',headers=headers,json={'active':False})
    assert (await client.post('/v1/admin/auth/reset-password',json={'token':token,'new_password':NEW_PASSWORD})).status_code==400
    await client.post('/v1/admin/auth/forgot-password',json={'email':'owner@example.com'})
    expired=parse_qs(urlsplit(sender.admin_deliveries[-1][1]).fragment)['token'][0]
    async with session_factory() as db:
        reset=await db.scalar(select(AdministratorReset).where(AdministratorReset.token_hash==sha256(expired)))
        reset.expires_at=datetime.now(UTC)-timedelta(seconds=1)
        session=await db.scalar(select(AdministratorSession).where(AdministratorSession.administrator_id==owner_id))
        session.expires_at=datetime.now(UTC)-timedelta(seconds=1)
        await db.commit()
    assert (await client.post('/v1/admin/auth/reset-password',json={'token':expired,'new_password':NEW_PASSWORD})).status_code==400
    assert (await client.get('/v1/admin/auth/me',headers=headers)).status_code==401
    headers=await login(client)
    assert (await client.post('/v1/admin/auth/logout',headers=headers)).status_code==204
    assert (await client.get('/v1/admin/auth/me',headers=headers)).status_code==401

@pytest.mark.asyncio
async def test_generic_errors_throttling_and_password_policy(api_client,session_factory,settings):
    client,*_=api_client
    await add_account(session_factory)
    unknown=await client.post('/v1/admin/auth/login',json={'email':'unknown@example.com','password':'wrong'})
    wrong=await client.post('/v1/admin/auth/login',json={'email':'owner@example.com','password':'wrong'})
    assert unknown.json()['detail']['code']==wrong.json()['detail']['code']=='invalid_credentials'
    for _ in range(4): await client.post('/v1/admin/auth/login',json={'email':'owner@example.com','password':'wrong'})
    assert (await client.post('/v1/admin/auth/login',json={'email':'owner@example.com','password':PASSWORD})).status_code==429
    settings.admin_login_budget=1
    assert (await client.post('/v1/admin/auth/login',json={'email':'another@example.com','password':'wrong'})).status_code==429

@pytest.mark.asyncio
async def test_bootstrap_and_recovery_commands(api_client,session_factory,monkeypatch):
    from app.jobs import admin_accounts
    monkeypatch.setattr(admin_accounts,'SessionFactory',session_factory)
    await admin_accounts.manage_owner('bootstrap-owner','Owner@Example.com',PASSWORD)
    with pytest.raises(ValueError): await admin_accounts.manage_owner('bootstrap-owner','other@example.com',PASSWORD)
    client,*_=api_client
    headers=await login(client)
    await admin_accounts.manage_owner('recover-owner','owner@example.com',NEW_PASSWORD)
    assert (await client.get('/v1/admin/auth/me',headers=headers)).status_code==401
    new=await login(client,password=NEW_PASSWORD)
    assert (await client.get('/v1/admin/auth/me',headers=new)).json()['must_change_password'] is True

@pytest.mark.asyncio
async def test_creation_password_boundaries_and_immutable_email(api_client,session_factory):
    client,*_=api_client
    await add_account(session_factory)
    headers=await login(client)
    for password in ['short', 'a'*129]:
        response=await client.post('/v1/admin/accounts',headers=headers,json={'email':'new@example.com','role':'admin','temporary_password':password})
        assert response.status_code==422
        assert password not in response.text
    for index,password in enumerate(['a'*15,'b'*128,'  meaningful spaces  ']):
        address=f'valid{index}@example.com'
        response=await client.post('/v1/admin/accounts',headers=headers,json={'email':address,'role':'admin','temporary_password':password})
        assert response.status_code==201
        assert (await client.post('/v1/admin/auth/login',json={'email':address,'password':password})).status_code==200
        assert (await client.patch(f"/v1/admin/accounts/{response.json()['id']}",headers=headers,json={'email':'different@example.com'})).status_code==422

@pytest.mark.asyncio
async def test_disabled_and_unknown_login_match(api_client,session_factory):
    client,*_=api_client
    account_id=await add_account(session_factory)
    async with session_factory() as db:
        account=await db.get(Administrator,account_id);account.active=False;await db.commit()
    disabled=await client.post('/v1/admin/auth/login',json={'email':'owner@example.com','password':PASSWORD})
    unknown=await client.post('/v1/admin/auth/login',json={'email':'unknown@example.com','password':PASSWORD})
    assert disabled.status_code==unknown.status_code==401
    assert disabled.json()['detail']['message']==unknown.json()['detail']['message']

@pytest.mark.asyncio
async def test_reset_rate_limits_and_global_budget(api_client,session_factory,settings):
    client,_,sender,*_=api_client
    await add_account(session_factory)
    settings.admin_web_url='https://stayzyweb.example.test'
    for _ in range(4):
        assert (await client.post('/v1/admin/auth/forgot-password',json={'email':'owner@example.com'})).status_code==202
    assert len(sender.admin_deliveries)==3
    await add_account(session_factory,'other@example.com')
    settings.admin_reset_budget=1
    assert (await client.post('/v1/admin/auth/forgot-password',json={'email':'other@example.com'})).status_code==202
    assert len(sender.admin_deliveries)==3

@pytest.mark.asyncio
async def test_sendgrid_admin_payload_disables_tracking(settings):
    import httpx
    import json
    from app.services.email import SendGridEmailSender
    captured=[]
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(202,headers={'x-message-id':'test-message'})
    settings.sendgrid_api_key='not-a-real-key'
    settings.sendgrid_admin_reset_template_id='test-admin-template'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        sender=SendGridEmailSender(settings,client=client)
        result=await sender.send_admin_reset('owner@example.com','https://web.example/admin/reset-password#token=synthetic','test')
    assert result.accepted
    assert captured[0]['template_id']=='test-admin-template'
    assert captured[0]['tracking_settings']=={'click_tracking':{'enable':False,'enable_text':False},'open_tracking':{'enable':False}}
