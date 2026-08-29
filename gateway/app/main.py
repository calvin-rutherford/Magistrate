from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
import os
import json
import asyncio
import time
import re
from pathlib import Path
from typing import Optional, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.auth import verify_token, MAGISTRATE_TOKEN
from app.herdr_client import DEFAULT_HISTORY_LINES, HERDR_MAX_READ_LINES, HerdrClient
from app.firstmate_client import FirstmateClient
from app.execution_capabilities import get_execution_capabilities, validate_execution_selection, profile_selection
from app.contracts import (UniversalInputContract, ExecutionSettingsContract, ExecutionCredentialContract, GestureInputContract,
                           NotificationAckContract, NotificationPreferencesContract,
                           RenameAgentContract, VoiceMoveRequest)
from app.stt_adapter import VoiceInputAdapter, TranscriptionError
from app.voice_moves import VoiceMoveService
from app.db import (init_db, get_profile, update_profile, get_connected_accounts, upsert_connected_account,
                    disconnect_account, get_execution_preferences, get_execution_credential_status,
                    save_execution_preferences, save_execution_credential, delete_execution_credential)
from app.github_service import github_service
from app.recent_activity import RecentActivityService
from app.attention_service import attention_service
from app.notifications import register_push_token, reconcile_notification_events, acknowledge_notification_events, update_notification_preferences
from app.providers.github import GitHubProviderAdapter
from app.providers.twitter import TwitterProviderAdapter
from app.providers.discord import DiscordProviderAdapter
from app.providers.google import GoogleProviderAdapter
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter
from app.oauth_transactions import OAuthTransactionError, OAuthTransactionStore
from app.usage import get_usage

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

GATEWAY_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = GATEWAY_DIR / 'uploads' / 'avatars'
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount('/uploads', StaticFiles(directory=str(GATEWAY_DIR / 'uploads')), name='uploads')

herdr_client = HerdrClient()
fm_client = FirstmateClient()
recent_activity_service = RecentActivityService(fm_client, github_service)
stt_adapter = VoiceInputAdapter()
voice_move_service = VoiceMoveService(herdr_client)


@app.websocket('/api/v1/events')
async def agent_events(websocket: WebSocket):
    """Stream normalized agent history; clients retain HTTP polling as a fallback."""
    if websocket.query_params.get('token') != MAGISTRATE_TOKEN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    target = 'captain'
    seen: set[str] = set()
    try:
        await websocket.send_json({'type': 'connected', 'target': target})
        while True:
            try:
                control = await asyncio.wait_for(websocket.receive_text(), timeout=0.75)
                try:
                    message = json.loads(control)
                except json.JSONDecodeError:
                    message = {}
                if isinstance(message, dict) and isinstance(message.get('target'), str):
                    target = message['target']
                    seen.clear()
                    await websocket.send_json({'type': 'subscribed', 'target': target})
            except asyncio.TimeoutError:
                pass
            history = await herdr_client.get_agent_history(target, lines=DEFAULT_HISTORY_LINES)
            fresh = []
            for item in history.get('messages', []):
                key = f"{item.get('role')}|{item.get('kind')}|{item.get('text')}"
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(item)
            if fresh:
                await websocket.send_json({'type': 'agent_history', 'target': history.get('target', target), 'messages': fresh})
    except WebSocketDisconnect:
        return

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

@app.get('/api/v1/recent-activity')
async def get_recent_activity(limit: int = Query(20, ge=1, le=50), refresh: bool = Query(False), token: str = Depends(verify_token)):
    try:
        return await recent_activity_service.get_recent_activity(limit, refresh)
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

@app.get('/api/v1/usage')
async def get_usage_summary(provider: Optional[str] = None, token: str = Depends(verify_token)):
    try:
        return await get_usage(provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

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
async def get_execution_capability_inventory(user_id: str = 'default_user', token: str = Depends(verify_token)):
    try:
        return get_execution_capabilities(user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc


@app.get('/api/v1/execution/settings')
async def get_execution_settings(user_id: str = 'default_user', token: str = Depends(verify_token)):
    return {**get_execution_preferences(user_id), 'migration_supported': False,
            'credential_storage': 'encrypted', 'credentials': [
                {'credential_key': key, 'configured': configured}
                for key, configured in get_execution_credential_status(user_id).items()
            ]}


@app.put('/api/v1/execution/settings')
async def put_execution_settings(contract: ExecutionSettingsContract, user_id: str = 'default_user', token: str = Depends(verify_token)):
    current = get_execution_preferences(user_id)
    profile_id = contract.profile_id if 'profile_id' in contract.model_fields_set else current['profile_id']
    switching = contract.switching_behavior or current['switching_behavior']
    unavailable = contract.unavailable_behavior or current['unavailable_behavior']
    if profile_id and 'profile_id' in contract.model_fields_set:
        try:
            # Validate identity and availability separately. An unavailable profile
            # remains persisted so the configured error policy can explain it in UI.
            capabilities = get_execution_capabilities(user_id)
            profile = next((item for item in capabilities['profiles'] if item['id'] == profile_id), None)
            if profile is None:
                raise ValueError('The selected execution profile is not available.')
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**save_execution_preferences(user_id, profile_id=profile_id, switching_behavior=switching, unavailable_behavior=unavailable),
            'migration_supported': False}


@app.put('/api/v1/execution/credentials/{credential_key:path}')
async def put_execution_credential(credential_key: str, contract: ExecutionCredentialContract, user_id: str = 'default_user', token: str = Depends(verify_token)):
    if user_id != 'default_user':
        raise HTTPException(status_code=403, detail='Credential storage is scoped to the authenticated Magistrate account.')
    if not re.fullmatch(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$', credential_key):
        raise HTTPException(status_code=422, detail='Invalid credential key.')
    try:
        capabilities = get_execution_capabilities(user_id)
        allowed_keys = {profile['auth']['credential_key'] for profile in capabilities['profiles']}
        if capabilities['configured'] and credential_key not in allowed_keys:
            raise HTTPException(status_code=422, detail='That credential is not used by a verified execution profile.')
        return save_execution_credential(user_id, credential_key, contract.credential)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete('/api/v1/execution/credentials/{credential_key}')
async def remove_execution_credential(credential_key: str, user_id: str = 'default_user', token: str = Depends(verify_token)):
    delete_execution_credential(user_id, credential_key)
    return {'credential_key': credential_key, 'configured': False}


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
    lines: int = Query(DEFAULT_HISTORY_LINES, ge=0, le=HERDR_MAX_READ_LINES),
    token: str = Depends(verify_token),
):
    output = await herdr_client.read_agent_output('captain', lines=lines)
    return {'output': output}

@app.get('/api/v1/agents/{agent_id}/history')
async def get_agent_history(
    agent_id: str,
    lines: int = Query(DEFAULT_HISTORY_LINES, ge=0, le=HERDR_MAX_READ_LINES),
    before: Optional[str] = Query(None, min_length=1, max_length=64),
    after: Optional[str] = Query(None, min_length=1, max_length=64),
    token: str = Depends(verify_token),
):
    if before and after:
        raise HTTPException(status_code=422, detail='Use only one history cursor.')
    try:
        history_kwargs = {'lines': lines}
        if before is not None: history_kwargs['before'] = before
        if after is not None: history_kwargs['after'] = after
        return await herdr_client.get_agent_history(agent_id, **history_kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc

@app.post('/api/v1/captain/prompt')
async def send_captain_prompt(contract: UniversalInputContract, user_id: str = 'default_user', token: str = Depends(verify_token)):
    selection = None
    if contract.profile_id:
        try:
            selection = profile_selection(contract.profile_id, user_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            if get_execution_preferences(user_id)['unavailable_behavior'] != 'fallback':
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            # Fallback is explicit user policy, never the default. The response
            # remains a current-session prompt and does not pretend migration ran.
            selection = None
    elif contract.harness or contract.model:
        if not contract.harness or not contract.model:
            raise HTTPException(status_code=422, detail='A harness and model must be selected together.')
        try:
            selection = validate_execution_selection(contract.harness, contract.model, user_id=user_id, provider=contract.provider, variant=contract.variant)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            if get_execution_preferences(user_id)['unavailable_behavior'] == 'fallback':
                selection = None
            else:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif 'profile_id' not in contract.model_fields_set:
        preference = get_execution_preferences(user_id)
        if preference['profile_id']:
            try:
                selection = profile_selection(preference['profile_id'], user_id)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
            except ValueError as exc:
                if preference['unavailable_behavior'] != 'fallback':
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                selection = None
    return await herdr_client.prompt_agent(contract.target, contract.text, **(selection or {}))

@app.post('/api/v1/agents/{agent_id}/send-key')
async def send_agent_key(agent_id: str, key: str = Query('Enter'), token: str = Depends(verify_token)):
    return await herdr_client.send_agent_key(agent_id, key=key)

@app.post('/api/v1/agents/{agent_id}/interrupt')
async def interrupt_agent(agent_id: str, token: str = Depends(verify_token)):
    return await herdr_client.interrupt_agent(agent_id)

@app.post('/api/v1/agents/{agent_id}/rename')
async def rename_agent(agent_id: str, contract: RenameAgentContract, token: str = Depends(verify_token)):
    return await herdr_client.rename_agent(agent_id, contract.name)

# STATIC SPA FALLBACK FOR DIRECT DEEP LINKS
# Resolve the default from the checkout containing this gateway. Deployments may
# override it explicitly, but serving a sibling checkout must never be implicit.
PROJECT_DIR = Path(__file__).resolve().parents[2]
DIST_DIR = os.getenv('MAGISTRATE_DIST_DIR', str(PROJECT_DIR / 'frontend' / 'dist'))
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
