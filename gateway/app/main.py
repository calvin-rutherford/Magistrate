from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
import os
import json
import asyncio
import time
from typing import Optional, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.auth import verify_token, MAGISTRATE_TOKEN
from app.herdr_client import HERDR_MAX_READ_LINES, HerdrClient
from app.firstmate_client import FirstmateClient
from app.execution_capabilities import get_execution_capabilities, validate_execution_selection
from app.contracts import (UniversalInputContract, GestureInputContract,
                           NotificationAckContract, NotificationPreferencesContract,
                           VoiceMoveRequest)
from app.stt_adapter import VoiceInputAdapter, TranscriptionError
from app.voice_moves import VoiceMoveService
from app.db import init_db, get_profile, update_profile, get_connected_accounts, upsert_connected_account, disconnect_account
from app.github_service import github_service
from app.attention_service import attention_service
from app.notifications import register_push_token, reconcile_notification_events, acknowledge_notification_events, update_notification_preferences
from app.providers.github import GitHubProviderAdapter
from app.providers.twitter import TwitterProviderAdapter
from app.providers.discord import DiscordProviderAdapter
from app.providers.google import GoogleProviderAdapter
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter
from app.oauth_transactions import OAuthTransactionError, OAuthTransactionStore

init_db()

app = FastAPI(
    title='Magistrate Gateway API',
    description='Universal cockpit adapter for Firstmate and Herdr',
    version='1.0.0'
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

UPLOADS_DIR = '/home/spectre/Magistrate/gateway/uploads/avatars'
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount('/uploads', StaticFiles(directory='/home/spectre/Magistrate/gateway/uploads'), name='uploads')

herdr_client = HerdrClient()
fm_client = FirstmateClient()
stt_adapter = VoiceInputAdapter()
voice_move_service = VoiceMoveService(herdr_client)

jira_adapter = JiraProviderAdapter()
teams_adapter = TeamsProviderAdapter()

providers = {
    'github': GitHubProviderAdapter(),
    'twitter': TwitterProviderAdapter(),
    'discord': DiscordProviderAdapter(),
    'google': GoogleProviderAdapter(),
    'jira': jira_adapter,
    'teams': teams_adapter
}
oauth_transaction_store = OAuthTransactionStore()

# HEALTH & RUNTIME

@app.get('/api/v1/runtime')
async def get_runtime(token: str = Depends(verify_token)):
    snapshot = await herdr_client.get_snapshot()
    fm_snapshot = await fm_client.get_snapshot()
    return {
        'herdr': {
            'status': 'connected' if snapshot.get('version') else 'disconnected',
            'version': snapshot.get('version', '0.8.2'),
            'protocol': snapshot.get('protocol', 20),
            'agents_count': len(snapshot.get('agents', []))
        },
        'firstmate': {
            'fm_home': fm_snapshot.get('fm_home'),
            'schema': fm_snapshot.get('schema', 'fm-fleet-snapshot.v1'),
            'tasks_count': len(fm_snapshot.get('tasks', []))
        }
    }

@app.get('/api/v1/health')
async def get_health(token: str = Depends(verify_token)):
    snapshot = await herdr_client.get_snapshot()
    fm_snapshot = await fm_client.get_snapshot()
    return {
        'status': 'healthy',
        'service': 'magistrate-gateway',
        'version': '1.0.0',
        'herdr_version': snapshot.get('version', '0.8.2'),
        'herdr_socket_connected': bool(snapshot.get('version')),
        'firstmate_home': fm_snapshot.get('fm_home'),
        'firstmate_tasks_count': len(fm_snapshot.get('tasks', []))
    }

# ACCOUNT PROFILE ENDPOINTS
@app.get('/api/v1/account/profile')
async def get_account_profile(user_id: str = 'default_user', token: str = Depends(verify_token)):
    return get_profile(user_id)

@app.post('/api/v1/account/profile')
async def post_account_profile(
    name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    bio: Optional[str] = Form(None),
    active_theme: Optional[str] = Form(None),
    user_id: str = 'default_user',
    token: str = Depends(verify_token)
):
    return update_profile(user_id=user_id, name=name, email=email, bio=bio, active_theme=active_theme)

@app.post('/api/v1/account/avatar')
async def upload_account_avatar(
    file: UploadFile = File(...),
    user_id: str = 'default_user',
    token: str = Depends(verify_token)
):
    filename = f'{user_id}_{int(time.time())}_{file.filename}'
    filepath = os.path.join(UPLOADS_DIR, filename)
    content = await file.read()
    with open(filepath, 'wb') as f:
        f.write(content)
    public_url = f'/uploads/avatars/{filename}'
    updated = update_profile(user_id=user_id, avatar_url=public_url)
    return {'status': 'success', 'avatar_url': public_url, 'profile': updated}

# OAUTH & CONNECTED ACCOUNTS ENDPOINTS
@app.get('/api/v1/auth/providers')
async def list_auth_providers(user_id: str = 'default_user', token: str = Depends(verify_token)):
    db_accounts = {a['provider']: a for a in get_connected_accounts(user_id)}
    result = []
    for p_name, adapter in providers.items():
        acc = db_accounts.get(p_name, {})
        result.append({
            'provider': p_name,
            'status': acc.get('status', 'connected' if p_name in ['github', 'jira', 'teams'] else 'disconnected'),
            'username': acc.get('provider_username', 'calvin@eversana.com' if p_name in ['jira', 'teams'] else ''),
            'capabilities': adapter.capabilities(),
            'auth_url': adapter.get_authorization_url()
        })
    return result

@app.get('/api/v1/auth/{provider}/connect')
async def connect_oauth_provider(provider: str, redirect_uri: str = Query('magistrate://account'), user_id: str = 'default_user', token: str = Depends(verify_token)):
    if provider not in providers:
        raise HTTPException(status_code=404, detail='Provider not supported')
    adapter = providers[provider]
    try:
        state = oauth_transaction_store.create(
            principal_id=user_id,
            provider=provider,
            redirect_uri=redirect_uri,
        )
    except OAuthTransactionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_url = adapter.get_authorization_url(state=state)
    return RedirectResponse(url=auth_url)

@app.get('/api/v1/auth/{provider}/callback')
async def oauth_callback(provider: str, code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    if provider not in providers:
        return JSONResponse({'error': 'Unsupported provider'}, status_code=400)

    if state is None:
        return JSONResponse({'error': 'Missing state'}, status_code=400)
    try:
        transaction = oauth_transaction_store.consume(state, provider)
    except OAuthTransactionError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)

    if error:
        return RedirectResponse(url=_oauth_redirect(transaction.redirect_uri, error=error))
    if not code:
        return JSONResponse({'error': 'Missing authorization code'}, status_code=400)

    adapter = providers[provider]
    try:
        exchange_result = await adapter.exchange_code(code)
        if isinstance(exchange_result, dict):
            access_token = exchange_result.get('access_token')
        else:
            access_token = exchange_result
        if not isinstance(access_token, str) or not access_token:
            raise ValueError('Provider did not return an access token')

        profile = await adapter.get_user_profile(access_token)
        username = profile.get('username', f'@{provider}_user')

        upsert_connected_account(
            user_id=transaction.principal_id,
            provider=provider,
            provider_username=username,
            status='connected',
            scopes=adapter.default_scopes(),
            access_token=access_token
        )
        return RedirectResponse(url=_oauth_redirect(transaction.redirect_uri, status='success'))
    except Exception:
        return RedirectResponse(url=_oauth_redirect(transaction.redirect_uri, error='oauth_failed'))


def _oauth_redirect(redirect_uri: str, **params: str) -> str:
    """Append encoded callback status without allowing provider text into URLs."""

    parsed = urlsplit(redirect_uri)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items() if value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))

@app.post('/api/v1/auth/{provider}/disconnect')
async def disconnect_oauth_provider(provider: str, user_id: str = 'default_user', token: str = Depends(verify_token)):
    disconnect_account(user_id, provider)
    return {'status': 'disconnected', 'provider': provider}

# LIVE GITHUB PR ENDPOINTS
@app.get('/api/v1/github/pulls')
async def list_github_pulls(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50), refresh: bool = Query(False), token: str = Depends(verify_token)):
    try:
        return await github_service.get_pull_requests(page, per_page, refresh)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.get('/api/v1/github/pulls/{number}')
async def get_github_pull(number: int, refresh: bool = Query(False), token: str = Depends(verify_token)):
    try:
        return await github_service.get_pull_request(number, refresh)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

# JIRA & TEAMS ENDPOINTS
@app.get('/api/v1/jira/issues')
async def get_jira_issues(token: str = Depends(verify_token)):
    return await jira_adapter.get_assigned_issues()

@app.get('/api/v1/teams/mentions')
async def get_teams_mentions(token: str = Depends(verify_token)):
    return await teams_adapter.get_mentions()

# UNIFIED ATTENTION ENDPOINT
@app.get('/api/v1/attention/unified')
async def get_unified_attention(token: str = Depends(verify_token)):
    return await attention_service.get_unified_attention_items()

# PUSH NOTIFICATIONS ENDPOINT
@app.post('/api/v1/notifications/register')
async def register_notifications(push_token: str = Form(...), platform: str = Form('ios'), user_id: str = 'default_user', token: str = Depends(verify_token)):
    return register_push_token(user_id=user_id, push_token=push_token, platform=platform)

@app.get('/api/v1/notifications/events')
async def get_notification_events(
    foreground: bool = False,
    local_hour: Optional[int] = None,
    user_id: str = 'default_user',
    token: str = Depends(verify_token),
):
    items = await attention_service.get_unified_attention_items()
    return reconcile_notification_events(user_id, items, foreground=foreground, local_hour=local_hour)

@app.post('/api/v1/notifications/events/ack')
async def ack_notification_events(contract: NotificationAckContract, user_id: str = 'default_user', token: str = Depends(verify_token)):
    acknowledge_notification_events(user_id, contract.item_ids)
    return {'status': 'acknowledged', 'item_ids': contract.item_ids}

@app.put('/api/v1/notifications/preferences')
async def put_notification_preferences(contract: NotificationPreferencesContract, user_id: str = 'default_user', token: str = Depends(verify_token)):
    try:
        return update_notification_preferences(user_id, contract.enabled, contract.quiet_start, contract.quiet_end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

# VOICE STT TRANSCRIPTION ENDPOINT
@app.post('/api/v1/voice/transcribe')
async def transcribe_voice_input(file: Optional[UploadFile] = File(None), source: str = Form('iphone'), token: str = Depends(verify_token)):
    if not file:
        raise HTTPException(status_code=400, detail='A microphone recording is required.')
    content = await file.read(25 * 1024 * 1024 + 1)
    try:
        return await stt_adapter.transcribe_audio(content, source=source,
            content_type=file.content_type or 'application/octet-stream', filename=file.filename or 'speech.m4a')
    except TranscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

@app.post('/api/v1/voice/moves')
async def create_voice_move(request: VoiceMoveRequest, token: str = Depends(verify_token)):
    try:
        return await voice_move_service.handle(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

# FLEET, ATTENTION & AGENTS
@app.get('/api/v1/execution/capabilities')
async def get_execution_capability_inventory(token: str = Depends(verify_token)):
    try:
        return get_execution_capabilities()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc


@app.get('/api/v1/agents')
async def list_agents(token: str = Depends(verify_token)):
    return await herdr_client.list_agents()

@app.get('/api/v1/fleet')
async def get_fleet(token: str = Depends(verify_token)):
    return await fm_client.get_snapshot()

@app.get('/api/v1/attention')
async def get_attention(token: str = Depends(verify_token)):
    return await attention_service.get_unified_attention_items()

@app.get('/api/v1/captain/output')
async def get_captain_output(
    lines: int = Query(HERDR_MAX_READ_LINES, ge=0, le=HERDR_MAX_READ_LINES),
    token: str = Depends(verify_token),
):
    output = await herdr_client.read_agent_output('captain', lines=lines)
    return {'output': output}

@app.post('/api/v1/captain/prompt')
async def send_captain_prompt(contract: UniversalInputContract, token: str = Depends(verify_token)):
    selection = None
    if contract.harness or contract.model:
        if not contract.harness or not contract.model:
            raise HTTPException(status_code=422, detail='A harness and model must be selected together.')
        try:
            selection = validate_execution_selection(contract.harness, contract.model)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await herdr_client.prompt_agent(contract.target, contract.text, **(selection or {}))

@app.post('/api/v1/agents/{agent_id}/send-key')
async def send_agent_key(agent_id: str, key: str = Query('Enter'), token: str = Depends(verify_token)):
    return await herdr_client.send_agent_key(agent_id, key=key)

@app.post('/api/v1/agents/{agent_id}/interrupt')
async def interrupt_agent(agent_id: str, token: str = Depends(verify_token)):
    return await herdr_client.interrupt_agent(agent_id)

# STATIC SPA FALLBACK FOR DIRECT DEEP LINKS
DIST_DIR = '/home/spectre/Magistrate/frontend/dist'
if os.path.exists(DIST_DIR):
    app.mount('/_expo', StaticFiles(directory=os.path.join(DIST_DIR, '_expo')), name='expo_static')
    app.mount('/assets', StaticFiles(directory=os.path.join(DIST_DIR, 'assets')), name='assets_static')

    @app.get('/{full_path:path}')
    async def spa_catch_all(full_path: str):
        if full_path.startswith('api/'):
            return JSONResponse({'detail': 'Not Found'}, status_code=404)
        file_path = os.path.join(DIST_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        index_path = os.path.join(DIST_DIR, 'index.html')
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse({'detail': 'Not Found'}, status_code=404)
