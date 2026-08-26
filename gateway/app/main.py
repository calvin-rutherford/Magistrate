from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
import os
import json
import asyncio
import time
from typing import Optional, List

from app.auth import verify_token, MAGISTRATE_TOKEN
from app.herdr_client import HERDR_MAX_READ_LINES, HerdrClient
from app.firstmate_client import FirstmateClient
from app.contracts import UniversalInputContract, GestureInputContract, NotificationAckContract, NotificationPreferencesContract
from app.stt_adapter import VoiceInputAdapter
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
    # For a real flow, you generate a state parameter to prevent CSRF, and append the mobile app's redirect_uri to it.
    state = f"{user_id}::{redirect_uri}"
    auth_url = adapter.get_authorization_url() + f"&state={state}" if '?' in adapter.get_authorization_url() else adapter.get_authorization_url() + f"?state={state}"
    return RedirectResponse(url=auth_url)

@app.get('/api/v1/auth/{provider}/callback')
async def oauth_callback(provider: str, code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    if not state:
        return JSONResponse({'error': 'Missing state'}, status_code=400)
    
    parts = state.split('::')
    user_id = parts[0]
    app_redirect_uri = parts[1] if len(parts) > 1 else 'magistrate://account'

    if error:
        return RedirectResponse(url=f"{app_redirect_uri}?error={error}")

    if provider not in providers:
        return RedirectResponse(url=f"{app_redirect_uri}?error=unsupported_provider")
    
    adapter = providers[provider]
    
    # In a fully real flow, adapter.exchange_code(code) makes an HTTP request to the provider.
    # We will simulate the exchange here to allow the UI to work seamlessly if .env keys are missing.
    try:
        if hasattr(adapter, 'exchange_code'):
            access_token = await adapter.exchange_code(code)
        else:
            access_token = f"mock_token_{code}"
            
        profile = await adapter.get_user_profile(access_token)
        username = profile.get('username', f'@{provider}_user')
        
        upsert_connected_account(
            user_id=user_id, 
            provider=provider, 
            provider_username=username, 
            status='connected', 
            scopes=adapter.default_scopes(), 
            access_token=access_token
        )
        return RedirectResponse(url=f"{app_redirect_uri}?status=success")
    except Exception as e:
        return RedirectResponse(url=f"{app_redirect_uri}?error={str(e)}")

@app.post('/api/v1/auth/{provider}/disconnect')
async def disconnect_oauth_provider(provider: str, user_id: str = 'default_user', token: str = Depends(verify_token)):
    disconnect_account(user_id, provider)
    return {'status': 'disconnected', 'provider': provider}

# LIVE GITHUB PR ENDPOINTS
@app.get('/api/v1/github/pulls')
async def list_github_pulls(token: str = Depends(verify_token)):
    return await github_service.get_pull_requests()

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
    content = b''
    if file:
        content = await file.read()
    result = await stt_adapter.transcribe_audio(content, source=source)
    return result

# FLEET, ATTENTION & AGENTS
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
    return await herdr_client.prompt_agent(contract.target, contract.text)

@app.post('/api/v1/agents/{agent_id}/send-key')
async def send_agent_key(agent_id: str, key: str = Query('Enter'), token: str = Depends(verify_token)):
    return await herdr_client.send_agent_key(agent_id, key=key)

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
