from fastapi import FastAPI, Depends, Header, Response, Request, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, ConfigDict, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
import os
import json
import asyncio
import secrets
import time
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.auth import (Principal, friend_beta_onboarding_required, issue_friend_beta_session,
                      issue_session, revoke_session, require_any_scope, require_scope,
                      validate_friend_beta_configuration, verify_token)
from app.herdr_client import HerdrClient
from app.firstmate_client import FirstmateClient
from app.execution_capabilities import get_execution_capabilities, validate_execution_selection, profile_selection
from app.contracts import (ExecutionSettingsContract, ExecutionCredentialContract,
                           NotificationAckContract, NotificationPreferencesContract, AttentionActionContract,
                           AttentionActionExecuteContract, RoutingPreferenceContract,
                           AgentMigrationRequestContract, AgentMigrationTransitionContract,
                           ActivityCatchUpContract, MAGI_MAX_RESPONSE_BYTES,
                           RenameAgentContract)
from app.stt_adapter import VoiceInputAdapter, TranscriptionError
from app.db import (init_db, get_profile, update_profile, get_connected_accounts, upsert_connected_account,
                    disconnect_account, get_execution_preferences, get_execution_credential_status,
                    save_execution_preferences, save_execution_credential, delete_execution_credential,
                    create_agent_migration, get_agent_migration, get_agent_migration_by_idempotency, transition_agent_migration)
from app.github_service import github_service
from app.recent_activity import RecentActivityService
from app.activity_store import SourceEventConflict, list_activity, snapshot_activity, source_diagnostics
from app.structured_runtime import StructuredRuntimeProjection
from app.attention_service import attention_service
from app.ar_glasses import router as ar_router
from app.attention_actions import (AttentionActionError, action_for_item, execute_confirmation,
                                   prepare_confirmation, outcome_for_item, _outcome_row, _public_outcome)
from app.notifications import (register_push_token, revoke_push_token, get_registered_push_token,
                               list_registered_push_users, registered_local_hour,
                               reconcile_notification_events, dispatch_notification_events,
                               mark_notification_events_delivered, acknowledge_notification_events, get_notification_preferences,
                               update_notification_preferences)
from app.providers.github import GitHubProviderAdapter
from app.providers.twitter import TwitterProviderAdapter
from app.providers.discord import DiscordProviderAdapter
from app.providers.google import GoogleProviderAdapter
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter
from app.oauth_transactions import OAuthTransactionError, OAuthTransactionStore
from app.usage import get_usage
from app.uploads import (MAX_UPLOAD_BYTES, MAX_UPLOAD_COUNT, MAX_UPLOAD_TOTAL_BYTES,
                         associate_uploads, save_upload, get_upload)
from app.magi_chat_api import (magi_chat_readiness, magi_chat_service,
                               router as magi_chat_router,
                               validate_magi_chat_configuration)
from app.magi_chat_store import MagiChatStore
from app.firstmate_execution import MAX_FIRSTMATE_EXECUTION_EVENT_BYTES
from app.firstmate_execution_api import (
    firstmate_execution_service, router as firstmate_execution_router,
)
from app.firstmate_decision_api import router as firstmate_decision_router

init_db()

app = FastAPI(
    title='Magistrate Gateway API',
    description='Authenticated Gateway for provider-native Magi chat and governed execution',
    version='1.1.0'
)

def _cors_origins() -> list[str]:
    configured = os.getenv('MAGISTRATE_CORS_ORIGINS')
    if configured is not None:
        origins = [item.strip() for item in configured.split(',') if item.strip()]
        if '*' in origins:
            raise RuntimeError('Wildcard CORS is not permitted.')
        if os.getenv('MAGISTRATE_ENV', '').lower() not in {'dev', 'development', 'test', 'testing'}:
            for origin in origins:
                if origin.startswith('http://') and not origin.startswith(('http://localhost', 'http://127.0.0.1', 'http://[::1]')):
                    raise RuntimeError('Production CORS origins must use HTTPS.')
        return origins
    if os.getenv('MAGISTRATE_ENV', '').lower() in {'dev', 'development', 'test', 'testing'}:
        return ['http://localhost:8081', 'http://localhost:19006']
    raise RuntimeError('MAGISTRATE_CORS_ORIGINS is required outside explicit development/test mode.')


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type'],
)

GATEWAY_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = GATEWAY_DIR / 'uploads' / 'avatars'
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount('/uploads', StaticFiles(directory=str(GATEWAY_DIR / 'uploads')), name='uploads')
app.include_router(ar_router)
app.include_router(magi_chat_router)
app.include_router(firstmate_execution_router)
app.include_router(firstmate_decision_router)

# Bound request envelopes before Starlette parses multipart/JSON bodies. The
# per-file and aggregate checks below remain authoritative because multipart
# overhead makes a precise Content-Length limit impossible.
MAX_PROMPT_REQUEST_BYTES = 1_000_000
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_TOTAL_BYTES + (MAX_UPLOAD_COUNT * 4096) + 1_000_000

@app.middleware('http')
async def enforce_bounded_request_size(request: Request, call_next):
    content_length = request.headers.get('content-length')
    try:
        length = int(content_length) if content_length is not None else 0
    except ValueError:
        return JSONResponse({'detail': 'Invalid request size.'}, status_code=400)
    if length < 0:
        return JSONResponse({'detail': 'Invalid request size.'}, status_code=400)
    if request.url.path == '/api/v1/uploads' and length > MAX_UPLOAD_REQUEST_BYTES:
        return JSONResponse({'detail': 'The upload request is too large.'}, status_code=413)
    if request.url.path == '/api/v1/magi/messages' and length > MAX_PROMPT_REQUEST_BYTES:
        return JSONResponse({'detail': 'The prompt request is too large.'}, status_code=413)
    firstmate_execution_contract = request.method == 'POST' and bool(re.fullmatch(
        r'/api/v1/firstmate/execution-events(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/wake)?',
        request.url.path,
    ))
    firstmate_decision_contract = (
        request.method == 'POST'
        and request.url.path == '/api/v1/firstmate/decision-events'
    )
    firstmate_structured_contract = firstmate_execution_contract or firstmate_decision_contract
    if firstmate_structured_contract and length > MAX_FIRSTMATE_EXECUTION_EVENT_BYTES:
        return JSONResponse({'detail': 'The Firstmate execution event is too large.'}, status_code=413)
    bounded_contract_path = request.method == 'POST' and (
        request.url.path == '/api/v1/activity/catch-up'
        or firstmate_structured_contract
    )
    if bounded_contract_path and content_length is None:
        # Semantic/catch-up contracts are small JSON records, never streaming
        # uploads. A declared size makes the bound effective before JSON parse.
        return JSONResponse({'detail': 'A semantic response request size is required.'}, status_code=411)
    if bounded_contract_path and length > MAGI_MAX_RESPONSE_BYTES:
        return JSONResponse({'detail': 'The semantic response request is too large.'}, status_code=413)
    if bounded_contract_path:
        # Content-Length is an early rejection aid, not authority: a peer can
        # lie or send a differently framed body. Buffer only through this
        # contract's hard cap and let Starlette reuse the verified cached bytes.
        body_cap = (
            MAX_FIRSTMATE_EXECUTION_EVENT_BYTES
            if firstmate_structured_contract else MAGI_MAX_RESPONSE_BYTES
        )
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > body_cap:
                detail = (
                    'The Firstmate execution event is too large.'
                    if firstmate_structured_contract
                    else 'The semantic response request is too large.'
                )
                return JSONResponse({'detail': detail}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
    return await call_next(request)

herdr_client = HerdrClient()
fm_client = FirstmateClient()
structured_runtime = StructuredRuntimeProjection()
recent_activity_service = RecentActivityService(structured_runtime, github_service)
stt_adapter = VoiceInputAdapter()
_notification_reconciler_task = None


async def _reconcile_registered_notifications() -> None:
    """Poll source-of-truth attention server-side for background push delivery."""
    try:
        interval = max(15, int(os.getenv('MAGISTRATE_NOTIFICATION_POLL_SECONDS', '30')))
    except ValueError:
        interval = 30
    while True:
        await asyncio.sleep(interval)
        try:
            for user_id in list_registered_push_users():
                items = await attention_service.get_unified_attention_items(user_id)
                await dispatch_notification_events(user_id, items, local_hour=registered_local_hour(user_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A source/provider outage must not kill the reconciler; the next
            # interval retries and the Attention tab remains the fallback.
            print('Notification reconciler unavailable:', exc)


@app.on_event('startup')
async def start_notification_reconciler():
    global _notification_reconciler_task
    validate_friend_beta_configuration()
    validate_magi_chat_configuration()
    # A process cannot resume an in-flight provider socket. Preserve the
    # reserved pair and expose a truthful, explicitly retryable failure.
    await asyncio.to_thread(MagiChatStore().recover_orphaned_pending)
    await firstmate_execution_service.recover_pending()
    if fm_client.captain_producer_required:
        producer = fm_client.get_producer_readiness()
        if producer['status'] != 'ready':
            raise RuntimeError('The required pinned Firstmate producer is unavailable.')
    if os.getenv('MAGISTRATE_DISABLE_NOTIFICATION_RECONCILER', '').lower() not in {'1', 'true', 'yes'}:
        _notification_reconciler_task = asyncio.create_task(_reconcile_registered_notifications())


@app.on_event('shutdown')
async def stop_notification_reconciler():
    global _notification_reconciler_task
    if _notification_reconciler_task:
        _notification_reconciler_task.cancel()
        await asyncio.gather(_notification_reconciler_task, return_exceptions=True)
    _notification_reconciler_task = None


async def _bounded_event_frame(websocket: WebSocket, timeout: float) -> str:
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
    try:
        if len(raw.encode('utf-8', errors='strict')) > 4096:
            await websocket.close(code=1009)
            raise WebSocketDisconnect(code=1009)
    except UnicodeEncodeError:
        await websocket.close(code=1008)
        raise WebSocketDisconnect(code=1008)
    return raw


@app.websocket('/api/v1/events')
async def application_events(websocket: WebSocket):
    """Deliver only Native Magi messages and structured Activity changes."""
    await websocket.accept()
    try:
        raw = await _bounded_event_frame(websocket, 10)
        message = json.loads(raw)
        if not isinstance(message, dict) or set(message) != {'type', 'token', 'activity_after'}:
            await websocket.close(code=1008)
            return
        token = message.get('token')
        activity_after = message.get('activity_after')
        if (message.get('type') != 'auth' or not isinstance(token, str)
                or type(activity_after) is not int
                or not 0 <= activity_after <= 9_007_199_254_740_991):
            await websocket.close(code=1008)
            return
        from app.auth import _principal_from_token
        try:
            principal = _principal_from_token(token)
        except HTTPException:
            await websocket.close(code=1008)
            return
        if not principal.has('read'):
            await websocket.close(code=1008)
            return
        activity_cursor = activity_after
        revisions: Dict[str, tuple[Any, ...]] = {}
        await websocket.send_json({
            'type': 'connected', 'schema_version': 'magistrate.events.v2',
        })
        while True:
            try:
                control = json.loads(await _bounded_event_frame(websocket, 0.75))
                if not isinstance(control, dict) or set(control) != {'activity_after'}:
                    await websocket.close(code=1008)
                    return
                requested_cursor = control['activity_after']
                if (type(requested_cursor) is not int
                        or not activity_cursor <= requested_cursor <= 9_007_199_254_740_991):
                    await websocket.close(code=1008)
                    return
                activity_cursor = requested_cursor
            except asyncio.TimeoutError:
                pass

            payload = await magi_chat_service.current_conversation(principal.user_id)
            fresh = [
                item for item in payload['messages']
                if revisions.get(item['id']) != (item['revision'], item['status'])
            ]
            revisions = {
                item['id']: (item['revision'], item['status'])
                for item in payload['messages']
            }
            if fresh:
                await websocket.send_json({
                    'type': 'magi_messages',
                    'schema_version': 'magistrate.events.v2',
                    'payload_schema_version': payload['schema_version'],
                    'conversation_id': payload['conversation_id'],
                    'messages': fresh,
                })
            activity_page = list_activity(
                principal.user_id, after=activity_cursor, limit=100,
            )
            if activity_page['records']:
                activity_cursor = activity_page['next_cursor']
                await websocket.send_json({
                    **activity_page,
                    'type': 'activity_records',
                    'schema_version': 'magistrate.events.v2',
                    'payload_schema_version': activity_page['schema_version'],
                })
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        try:
            await websocket.close(code=1008)
        except Exception:
            pass


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bootstrap_secret: Optional[str] = Field(None, max_length=512)


class FriendBetaSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    access_code: str = Field(min_length=1, max_length=128)


@app.post('/api/v1/auth/session')
async def create_session(request: SessionRequest, response: Response):
    # Bearer issuance must never be cached by a browser, proxy, or shared CDN.
    response.headers['Cache-Control'] = 'no-store'
    return issue_session(request.bootstrap_secret)


@app.post('/api/v1/auth/friend-beta/session')
async def create_friend_beta_session(request: FriendBetaSessionRequest, response: Response):
    """Exchange one operator-issued beta grant; never echo its access code."""
    response.headers['Cache-Control'] = 'no-store'
    return issue_friend_beta_session(request.access_code)


@app.get('/api/v1/auth/session')
async def inspect_session(response: Response, principal: Principal = Depends(verify_token)):
    """Small protected validation endpoint independent of Herdr/Firstmate."""
    response.headers['Cache-Control'] = 'no-store'
    return {
        'authenticated': True,
        'user_id': principal.user_id,
        'scopes': sorted(principal.scopes),
        'expires_at': principal.expires_at,
        'auth_method': 'friend-beta-access' if principal.access_grant_id else 'operator-bootstrap',
        'onboarding_required': friend_beta_onboarding_required(principal),
    }


@app.post('/api/v1/auth/session/revoke')
async def revoke_current_session(response: Response, principal: Principal = Depends(verify_token), authorization: Optional[str] = Header(None)):
    response.headers['Cache-Control'] = 'no-store'
    if not authorization:
        raise HTTPException(status_code=400, detail='Bearer session required')
    _, _, token = authorization.partition(' ')
    if not token:
        raise HTTPException(status_code=400, detail='Bearer session required')
    revoke_session(token)
    return {'status': 'revoked', 'session_id': principal.session_id}

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
async def get_runtime(principal: Principal = Depends(require_scope('read'))):
    fleet, runtime = await asyncio.gather(
        asyncio.to_thread(structured_runtime.fleet, principal.user_id),
        asyncio.to_thread(structured_runtime.runtime, principal.user_id),
    )
    execution_interface = fm_client.get_execution_interface_readiness()
    event_ingress = {
        'status': 'ready',
        'schemas': ['firstmate.execution-event.v1', 'firstmate.decision-events.v1'],
        'live_probe_performed': False,
    }
    return {
        'gateway': {'status': 'connected'},
        'provider': magi_chat_readiness(),
        # Herdr exposes no durable process observation contract. A normal read
        # therefore reports that it was not probed instead of opening its socket
        # or invoking its CLI.
        'herdr': {
            'status': 'not-observed',
            'required': False,
            'version': None,
            'protocol': None,
            'agents_count': None,
            'live_probe_performed': False,
        },
        'firstmate': {
            'schema': fleet['schema'],
            'status': execution_interface['status'],
            'tasks_count': fleet['tasks_count'],
            'last_event_at': fleet['last_event_at'],
            'persisted_runtime_status': fleet['persisted_runtime_status'],
            'live_probe_performed': False,
        },
        'execution_interface': execution_interface,
        'event_ingress': event_ingress,
        'persisted_runtime': runtime,
    }

@app.get('/api/v1/health')
async def get_health(principal: Principal = Depends(require_scope('read'))):
    runtime = await asyncio.to_thread(structured_runtime.runtime, principal.user_id)
    execution_interface = fm_client.get_execution_interface_readiness()
    event_ingress = {
        'status': 'ready',
        'schemas': ['firstmate.execution-event.v1', 'firstmate.decision-events.v1'],
        'live_probe_performed': False,
    }
    provider = magi_chat_readiness()
    producer = fm_client.get_producer_readiness()
    degraded: List[str] = []
    if provider['enabled'] and provider['status'] != 'configured':
        degraded.append('magi-provider')
    if execution_interface['status'] != 'configured':
        degraded.append('firstmate-execution-interface')
    if producer['required'] and producer['status'] != 'ready':
        degraded.append('firstmate-producer')
    return {
        'status': 'degraded' if degraded else 'healthy',
        'degraded_sources': degraded,
        'service': 'magistrate-gateway',
        'version': '1.1.0',
        'gateway_ready': True,
        'magi_provider': provider,
        'execution_interface': execution_interface,
        'event_ingress': event_ingress,
        'persisted_runtime': runtime,
        'last_execution_event_at': runtime['last_event_at'],
        # Backward-compatible fields deliberately make no live Herdr claim.
        'herdr_version': None,
        'herdr_socket_connected': False,
        'herdr_observation': 'not-probed',
        'firstmate_home': None,
        'firstmate_available': execution_interface['status'] == 'configured',
        'firstmate_tasks_count': runtime['known_objectives'],
        'firstmate_producer': producer,
    }


@app.get('/api/v1/diagnostics/soak')
async def get_soak_diagnostics(principal: Principal = Depends(require_scope('read'))):
    """Bounded P0 evidence without prompts, terminal bytes, or source payloads."""
    return {
        'schema_version': 'soak-diagnostics.v1',
        'target': 'captain',
        'activity_sources': source_diagnostics(principal.user_id),
        'firstmate_producer': fm_client.get_producer_readiness(),
        'native_chat': await magi_chat_service.diagnostics(principal.user_id),
    }

# ACCOUNT PROFILE ENDPOINTS
@app.get('/api/v1/account/profile')
async def get_account_profile(principal: Principal = Depends(require_scope('account'))):
    return get_profile(principal.user_id)

@app.post('/api/v1/account/profile')
async def post_account_profile(
    name: Optional[str] = Form(None, max_length=80),
    email: Optional[str] = Form(None, max_length=254),
    bio: Optional[str] = Form(None, max_length=1000),
    active_theme: Optional[str] = Form(None, max_length=64),
    principal: Principal = Depends(require_scope('account'))
):
    if name is not None:
        name = name.strip()
        if not name or any(
            unicodedata.category(character).startswith('C')
            or unicodedata.category(character) in {'Zl', 'Zp'}
            for character in name
        ):
            raise HTTPException(status_code=422, detail='Display name is invalid.')
    return update_profile(user_id=principal.user_id, name=name, email=email, bio=bio, active_theme=active_theme)

@app.post('/api/v1/account/avatar')
async def upload_account_avatar(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_scope('account'))
):
    safe_name = Path(file.filename or 'avatar').name
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', safe_name)[:128] or 'avatar'
    filename = f'{principal.user_id}_{int(time.time())}_{safe_name}'
    filepath = os.path.join(UPLOADS_DIR, filename)
    content = await file.read()
    with open(filepath, 'wb') as f:
        f.write(content)
    public_url = f'/uploads/avatars/{filename}'
    updated = update_profile(user_id=principal.user_id, avatar_url=public_url)
    return {'status': 'success', 'avatar_url': public_url, 'profile': updated}

# OAUTH & CONNECTED ACCOUNTS ENDPOINTS
def _provider_connection_state(adapter, account: dict) -> Dict[str, Any]:
    """Resolve a provider row that can never claim an unbacked connection.

    'connected' requires all three of: operator OAuth configuration, a stored
    credential, and an unexpired credential. Any missing piece downgrades to the
    specific honest state, so a stale database row, a revoked deployment
    credential, or a deferred provider can never render as connected.
    """
    available = bool(adapter.is_configured())
    deferred = bool(adapter.is_deferred())
    stored_status = account.get('status') or 'disconnected'
    username = account.get('provider_username') or ''
    expires_at = account.get('credential_expires_at')
    expired = isinstance(expires_at, int) and expires_at <= int(time.time())

    if not available:
        reason = adapter.unavailable_reason()
        # The identity is withheld too: showing a username beside an
        # unavailable provider reads as a connection that does not exist.
        return {'status': 'unavailable', 'username': '', 'available': False,
                'deferred': deferred, 'unavailable_reason': reason}
    if stored_status != 'connected':
        return {'status': 'disconnected', 'username': '', 'available': True,
                'deferred': deferred, 'unavailable_reason': None}
    if not account.get('has_credential'):
        return {'status': 'disconnected', 'username': '', 'available': True, 'deferred': deferred,
                'unavailable_reason': 'The stored credential for this account is missing. Reconnect to restore access.'}
    if expired:
        return {'status': 'expired', 'username': username, 'available': True, 'deferred': deferred,
                'unavailable_reason': 'The stored credential has expired. Reconnect to restore access.'}
    return {'status': 'connected', 'username': username, 'available': True,
            'deferred': deferred, 'unavailable_reason': None}


@app.get('/api/v1/auth/providers')
async def list_auth_providers(principal: Principal = Depends(require_scope('providers'))):
    db_accounts = {a['provider']: a for a in get_connected_accounts(principal.user_id)}
    result = []
    for p_name, adapter in providers.items():
        state = _provider_connection_state(adapter, db_accounts.get(p_name, {}))
        result.append({
            'provider': p_name,
            'status': state['status'],
            'username': state['username'],
            'capabilities': adapter.capabilities(),
            'available': state['available'],
            'deferred': state['deferred'],
            'unavailable_reason': state['unavailable_reason'],
            # Listing integrations must not create a state-less OAuth URL. The
            # authenticated connect route creates the real, one-time transaction.
            'auth_url': None,
            'configuration': 'available' if state['available'] else 'unavailable'
        })
    return result

@app.get('/api/v1/auth/{provider}/connect')
async def connect_oauth_provider(provider: str, redirect_uri: str = Query('magistrate://account'), principal: Principal = Depends(require_scope('providers'))):
    if provider not in providers:
        raise HTTPException(status_code=404, detail='Provider not supported')
    adapter = providers[provider]
    try:
        state = oauth_transaction_store.create(
            principal_id=principal.user_id,
            provider=provider,
            redirect_uri=redirect_uri,
        )
        if not adapter.is_configured():
            raise HTTPException(status_code=503, detail='Provider OAuth is unavailable or not configured.')
        auth_url = adapter.get_authorization_url(state=state)
    except OAuthTransactionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='Provider OAuth is unavailable or not configured.') from exc
    return {'provider': provider, 'auth_url': auth_url, 'expires_in': 600}

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
        username = profile.get('username') or profile.get('login') or profile.get('email')
        raw_identity = profile.get('id') or profile.get('account_id')
        # GitHub and several other providers issue a numeric account id. Requiring
        # a string here previously rejected every real GitHub identity, so the
        # only truthful outcome was a failure; accept int and normalize instead.
        provider_user_id = str(raw_identity) if isinstance(raw_identity, (str, int)) and not isinstance(raw_identity, bool) else ''
        if not isinstance(username, str) or not username or not provider_user_id:
            raise ValueError('Provider profile did not return an authenticated identity.')

        upsert_connected_account(
            user_id=transaction.principal_id,
            provider=provider,
            provider_user_id=provider_user_id,
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
async def disconnect_oauth_provider(provider: str, principal: Principal = Depends(require_scope('providers'))):
    if provider not in providers:
        raise HTTPException(status_code=404, detail='Provider not supported')
    disconnect_account(principal.user_id, provider)
    return {'status': 'disconnected', 'provider': provider}

# LIVE GITHUB PR ENDPOINTS
@app.get('/api/v1/github/pulls')
async def list_github_pulls(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50), refresh: bool = Query(False), principal: Principal = Depends(require_scope('providers'))):
    try:
        return await github_service.get_pull_requests(page, per_page, refresh)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.get('/api/v1/github/pulls/{number}')
async def get_github_pull(number: int, refresh: bool = Query(False), principal: Principal = Depends(require_scope('providers'))):
    try:
        return await github_service.get_pull_request(number, refresh)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.get('/api/v1/recent-activity')
async def get_recent_activity(limit: int = Query(20, ge=1, le=50), refresh: bool = Query(False), principal: Principal = Depends(require_scope('read'))):
    try:
        return await recent_activity_service.get_recent_activity(
            principal.user_id, limit, refresh,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _activity_catch_up(user_id: str, *, after: int, limit: int, reconcile: bool) -> Dict[str, Any]:
    # ``reconcile`` remains accepted for older clients but observation is now a
    # pure durable replay. Producers push execution/decision/completion events
    # through their authenticated structured seams.
    del reconcile
    return {
        **list_activity(user_id, after=after, limit=limit),
        'sources': source_diagnostics(user_id),
        'reconciliation': 'persisted-only',
    }


@app.get('/api/v1/activity/snapshot')
async def get_canonical_activity_snapshot(
    before: Optional[int] = Query(None, ge=1, le=9_007_199_254_740_991),
    limit: int = Query(100, ge=1, le=200),
    reconcile: bool = Query(True),
    principal: Principal = Depends(require_scope('read')),
):
    """Return a bounded projection without polling execution runtime."""
    try:
        del reconcile
        return {
            **snapshot_activity(principal.user_id, before=before, limit=limit),
            'sources': source_diagnostics(principal.user_id),
            'reconciliation': 'persisted-only',
        }
    except (ValueError, SourceEventConflict) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail='Structured activity reconciliation is unavailable.',
        ) from exc


@app.get('/api/v1/activity')
async def get_canonical_activity(
    after: int = Query(0, ge=0, le=9_007_199_254_740_991),
    limit: int = Query(100, ge=1, le=200),
    reconcile: bool = Query(True),
    principal: Principal = Depends(require_scope('read')),
):
    """Replay tenant-owned canonical activity from durable structured events."""
    try:
        return await _activity_catch_up(
            principal.user_id, after=after, limit=limit, reconcile=reconcile,
        )
    except (ValueError, SourceEventConflict) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='Structured activity reconciliation is unavailable.') from exc


@app.get('/api/v1/activity/replay')
async def replay_canonical_activity(
    after: int = Query(0, ge=0, le=9_007_199_254_740_991),
    limit: int = Query(100, ge=1, le=200),
    principal: Principal = Depends(require_scope('read')),
):
    """Replay durable rows without touching Firstmate or terminal state."""
    try:
        return await _activity_catch_up(
            principal.user_id, after=after, limit=limit, reconcile=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post('/api/v1/activity/catch-up')
async def post_canonical_activity_catch_up(
    contract: ActivityCatchUpContract,
    principal: Principal = Depends(require_scope('read')),
):
    try:
        return await _activity_catch_up(
            principal.user_id,
            after=contract.after,
            limit=contract.limit,
            reconcile=contract.reconcile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='Structured activity reconciliation is unavailable.') from exc

# JIRA & TEAMS ENDPOINTS
@app.get('/api/v1/jira/issues')
async def get_jira_issues(principal: Principal = Depends(require_scope('providers'))):
    return await jira_adapter.get_assigned_issues()

@app.get('/api/v1/teams/mentions')
async def get_teams_mentions(principal: Principal = Depends(require_scope('providers'))):
    return await teams_adapter.get_mentions()

# UNIFIED ATTENTION ENDPOINT
@app.get('/api/v1/attention/unified')
async def get_unified_attention(principal: Principal = Depends(require_scope('read'))):
    return await attention_service.get_unified_attention_items(principal.user_id)


def _require_owner(principal: Principal) -> None:
    # Command-capable sessions are still checked against the configured owner;
    # a future observer/read-only session must never gain action authority by
    # merely receiving an action-shaped payload.
    owner_id = os.getenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'default_user').strip()
    if principal.user_id != owner_id:
        raise HTTPException(status_code=403, detail='Only the authenticated owner may execute Attention actions.')


def _action_error(exc: AttentionActionError) -> HTTPException:
    status = exc.code if exc.code in {'stale', 'rejected', 'pending'} else ('rejected' if exc.code in {'unsupported', 'unsupported_risk', 'confirmation_invalid', 'replay_mismatch'} else 'failed')
    return HTTPException(status_code=exc.status_code, detail={'code': exc.code, 'status': status, 'message': exc.detail})


@app.post('/api/v1/attention/actions/{action_key}/prepare')
async def prepare_attention_action(action_key: str, contract: AttentionActionContract, principal: Principal = Depends(require_scope('command'))):
    _require_owner(principal)
    if contract.action_key != action_key:
        raise HTTPException(status_code=409, detail={'code': 'mismatch', 'message': 'The action key in the request does not match the route.'})
    try:
        items = await attention_service.get_unified_attention_items(principal.user_id)
        return prepare_confirmation(items, action_key, contract.action, contract.target_id, principal.user_id, principal.session_id)
    except AttentionActionError as exc:
        raise _action_error(exc) from exc


@app.post('/api/v1/attention/actions/{action_key}/execute')
async def execute_attention_action(action_key: str, contract: AttentionActionExecuteContract, principal: Principal = Depends(require_scope('command'))):
    _require_owner(principal)
    if contract.action_key != action_key:
        raise HTTPException(status_code=409, detail={'code': 'mismatch', 'message': 'The action key in the request does not match the route.'})
    try:
        items = await attention_service.get_unified_attention_items(principal.user_id)
        return await execute_confirmation(
            items, action_key, contract.action, contract.target_id, contract.confirmation_token,
            principal.user_id, principal.session_id, fm_client.fm_home,
        )
    except AttentionActionError as exc:
        raise _action_error(exc) from exc


@app.get('/api/v1/attention/actions/by-item/{item_id}')
async def get_attention_action_for_item(item_id: str, principal: Principal = Depends(require_scope('read'))):
    """Reload-safe lookup for a detail route whose source item has resolved."""
    existing = outcome_for_item(item_id, principal.user_id)
    if existing:
        return _public_outcome(existing)
    items = await attention_service.get_unified_attention_items(principal.user_id)
    for item in items:
        if item.get('id') == item_id:
            action = action_for_item(item)
            if action:
                return action
            break
    raise HTTPException(status_code=404, detail={'code': 'stale', 'message': 'Attention action is no longer available.'})


@app.get('/api/v1/attention/actions/{action_key}')
async def get_attention_action(action_key: str, principal: Principal = Depends(require_scope('read'))):
    """Reload-safe action/outcome state without exposing execution internals."""
    existing = _outcome_row(action_key, principal.user_id)
    if existing:
        return _public_outcome(existing)
    items = await attention_service.get_unified_attention_items(principal.user_id)
    for item in items:
        action = action_for_item(item)
        if action and action['action_key'] == action_key:
            return action
    raise HTTPException(status_code=404, detail={'code': 'stale', 'message': 'Attention action is no longer available.'})

@app.get('/api/v1/usage')
async def get_usage_summary(provider: Optional[str] = None, principal: Principal = Depends(require_scope('read'))):
    try:
        return await get_usage(provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

# PUSH NOTIFICATIONS ENDPOINT
@app.post('/api/v1/notifications/register')
async def register_notifications(
    request: Request,
    push_token: Optional[str] = Form(None),
    platform: str = Form('ios'),
    timezone_offset_minutes: Optional[int] = Form(None),
    principal: Principal = Depends(require_scope('notifications')),
):
    # Native clients use multipart FormData; JSON keeps the authenticated
    # contract convenient for device-registration integrations and tests.
    if push_token is None and request.headers.get('content-type', '').startswith('application/json'):
        payload = await request.json()
        if isinstance(payload, dict):
            push_token = payload.get('push_token')
            platform = payload.get('platform', platform)
            timezone_offset_minutes = payload.get('timezone_offset_minutes', timezone_offset_minutes)
    try:
        if timezone_offset_minutes is not None:
            timezone_offset_minutes = int(timezone_offset_minutes)
        return register_push_token(user_id=principal.user_id, push_token=push_token, platform=platform, timezone_offset_minutes=timezone_offset_minutes)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.delete('/api/v1/notifications/register')
async def unregister_notifications(principal: Principal = Depends(require_scope('notifications'))):
    return revoke_push_token(principal.user_id)

@app.get('/api/v1/notifications/preferences')
async def get_notifications_preferences(principal: Principal = Depends(require_scope('notifications'))):
    return get_notification_preferences(principal.user_id)

@app.get('/api/v1/notifications/status')
async def get_notifications_status(principal: Principal = Depends(require_scope('notifications'))):
    registered = get_registered_push_token(principal.user_id)
    return {'native_push': 'registered' if registered else 'unavailable', 'platform': registered['platform'] if registered else None}

@app.get('/api/v1/notifications/events')
async def get_notification_events(
    foreground: bool = False,
    local_hour: Optional[int] = None,
    principal: Principal = Depends(require_scope('notifications')),
):
    # Foreground is intentionally ignored for server delivery. A client poll
    # must never consume a transition before the gateway has sent the remote
    # push; web clients still receive the returned feed for browser fallback.
    del foreground
    items = await attention_service.get_unified_attention_items(principal.user_id)
    return await dispatch_notification_events(principal.user_id, items, local_hour=local_hour)

@app.post('/api/v1/notifications/events/delivered')
async def delivered_notification_events(contract: NotificationAckContract, principal: Principal = Depends(require_scope('notifications'))):
    mark_notification_events_delivered(principal.user_id, contract.item_ids)
    return {'status': 'delivered', 'item_ids': contract.item_ids}

@app.post('/api/v1/notifications/events/ack')
async def ack_notification_events(contract: NotificationAckContract, principal: Principal = Depends(require_scope('notifications'))):
    acknowledge_notification_events(principal.user_id, contract.item_ids)
    return {'status': 'acknowledged', 'item_ids': contract.item_ids}

@app.put('/api/v1/notifications/preferences')
async def put_notification_preferences(contract: NotificationPreferencesContract, principal: Principal = Depends(require_scope('notifications'))):
    try:
        return update_notification_preferences(principal.user_id, contract.enabled, contract.quiet_start, contract.quiet_end, contract.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

# VOICE STT TRANSCRIPTION ENDPOINT
@app.get('/api/v1/voice/capabilities')
async def get_voice_capabilities(principal: Principal = Depends(require_scope('voice'))):
    """Return speech capability flags without exposing server credentials."""
    capability = stt_adapter.capabilities()
    return {
        'schema_version': 'voice-capabilities.v1',
        'provider': capability['provider'],
        'configured': capability['configured'],
        'model': capability['model'],
        'reason': capability['reason'],
        'modes': [{
            'id': 'openai', 'label': 'Gateway OpenAI',
            'available': capability['configured'], 'reason': capability['reason'],
        }],
    }

async def _read_bounded_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(1024 * 1024, MAX_UPLOAD_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail='Files must be smaller than 25 MB.')
        chunks.append(chunk)
    return b''.join(chunks)


@app.post('/api/v1/uploads')
async def upload_chat_files(
    files: List[UploadFile] = File(...),
    message_id: Optional[str] = Form(None),
    principal: Principal = Depends(require_scope('command')),
):
    if not files:
        raise HTTPException(status_code=400, detail='At least one file is required.')
    if len(files) > MAX_UPLOAD_COUNT:
        raise HTTPException(status_code=413, detail='A message may include at most 10 attachments.')
    if message_id and not re.fullmatch(r'^[A-Za-z0-9_-]{8,128}$', message_id):
        raise HTTPException(status_code=422, detail='Invalid chat message id.')
    uploaded = []
    total = 0
    try:
        for file in files:
            content = await _read_bounded_upload(file)
            total += len(content)
            if total > MAX_UPLOAD_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail='The attachments in one message are too large.')
            uploaded.append(save_upload(principal.user_id, file.filename or 'upload', file.content_type, content))
        if message_id:
            associate_uploads(principal.user_id, message_id, [item['upload_id'] for item in uploaded])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The client must not infer processing success from a 200 alone. Report the
    # state the server actually reached: bytes stored and validated, and whether
    # the upload is already associated with a chat message.
    attached = bool(message_id)
    return {'uploads': [{**item, 'attached': attached} for item in uploaded]}


@app.get('/api/v1/uploads/{upload_id}')
async def download_chat_file(upload_id: str, principal: Principal = Depends(require_scope('command'))):
    if not re.fullmatch(r'^[A-Za-z0-9_-]{16,64}$', upload_id):
        raise HTTPException(status_code=404, detail='Upload not found.')
    upload = get_upload(principal.user_id, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail='Upload not found.')
    return FileResponse(upload['path'], media_type=upload['media_type'], filename=upload['filename'])


@app.post('/api/v1/voice/transcribe')
async def transcribe_voice_input(file: Optional[UploadFile] = File(None), source: str = Form('iphone'), principal: Principal = Depends(require_scope('voice'))):
    if not file:
        raise HTTPException(status_code=400, detail='A microphone recording is required.')
    content = await file.read(25 * 1024 * 1024 + 1)
    try:
        return await stt_adapter.transcribe_audio(content, source=source,
            content_type=file.content_type or 'application/octet-stream', filename=file.filename or 'speech.m4a')
    except TranscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

# FLEET, ATTENTION & AGENTS
@app.get('/api/v1/execution/capabilities')
async def get_execution_capability_inventory(principal: Principal = Depends(require_scope('read'))):
    try:
        return get_execution_capabilities(principal.user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc


def _routing_delivery(preference: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    selected = None
    if preference.get('routing_profile_id'):
        try:
            selected = profile_selection(preference['routing_profile_id'], user_id)
        except (RuntimeError, ValueError):
            # A removed inventory entry does not erase the durable preference,
            # and it must not be reconstructed from a similar-looking model.
            selected = {'profile_id': preference['routing_profile_id'], 'harness': None, 'model': None, 'provider': None, 'variant': None}
    return {
        'default': selected,
        'applies_to': ['new', 'restarted'],
        'delivery': {
            'status': 'pending-firstmate-integration', 'automatic': False,
            'consumer': 'firstmate', 'seam': 'GET /api/v1/execution/routing-preference',
            'message': 'Preference is persisted, but Firstmate does not consume it automatically yet.',
        },
    }


@app.get('/api/v1/execution/settings')
async def get_execution_settings(principal: Principal = Depends(require_scope('account'))):
    preference = get_execution_preferences(principal.user_id)
    return {**preference, 'routing_preference': _routing_delivery(preference, principal.user_id),
            'migration_supported': False, 'credential_storage': 'encrypted', 'credentials': [
                {'credential_key': key, 'configured': configured}
                for key, configured in get_execution_credential_status(principal.user_id).items()
            ]}


@app.put('/api/v1/execution/settings')
async def put_execution_settings(contract: ExecutionSettingsContract, principal: Principal = Depends(require_scope('account'))):
    current = get_execution_preferences(principal.user_id)
    profile_id = contract.profile_id if 'profile_id' in contract.model_fields_set else current['profile_id']
    routing_profile_id = contract.routing_profile_id if 'routing_profile_id' in contract.model_fields_set else current['routing_profile_id']
    switching = contract.switching_behavior or current['switching_behavior']
    unavailable = contract.unavailable_behavior or current['unavailable_behavior']
    if profile_id and 'profile_id' in contract.model_fields_set:
        try:
            # Validate identity and availability separately. An unavailable profile
            # remains persisted so the configured error policy can explain it in UI.
            capabilities = get_execution_capabilities(principal.user_id)
            profile = next((item for item in capabilities['profiles'] if item['id'] == profile_id), None)
            if profile is None:
                raise ValueError('The selected execution profile is not available.')
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if routing_profile_id and 'routing_profile_id' in contract.model_fields_set:
        try:
            profile_selection(routing_profile_id, principal.user_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = save_execution_preferences(principal.user_id, profile_id=profile_id, routing_profile_id=routing_profile_id,
                                       switching_behavior=switching, unavailable_behavior=unavailable)
    return {**saved, 'routing_preference': _routing_delivery(saved, principal.user_id), 'migration_supported': False}


@app.get('/api/v1/execution/routing-preference')
async def get_spawn_routing_preference(principal: Principal = Depends(require_scope('account'))):
    preference = get_execution_preferences(principal.user_id)
    return _routing_delivery(preference, principal.user_id)


@app.put('/api/v1/execution/routing-preference')
async def put_spawn_routing_preference(contract: RoutingPreferenceContract, principal: Principal = Depends(require_scope('account'))):
    supplied_harness = 'harness' in contract.model_fields_set
    supplied_model = 'model' in contract.model_fields_set
    if not supplied_harness or not supplied_model or bool(contract.harness) != bool(contract.model):
        raise HTTPException(status_code=422, detail='A default harness and model must be supplied together; use null for both to clear.')
    current = get_execution_preferences(principal.user_id)
    selection = None
    if contract.harness and contract.model:
        try:
            selection = validate_execution_selection(contract.harness, contract.model, user_id=principal.user_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = save_execution_preferences(
        principal.user_id, profile_id=current['profile_id'],
        routing_profile_id=selection['profile_id'] if selection else None,
        switching_behavior=current['switching_behavior'], unavailable_behavior=current['unavailable_behavior'],
    )
    return _routing_delivery(saved, principal.user_id)


@app.put('/api/v1/execution/credentials/{credential_key:path}')
async def put_execution_credential(credential_key: str, contract: ExecutionCredentialContract, principal: Principal = Depends(require_scope('account'))):
    if not re.fullmatch(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$', credential_key):
        raise HTTPException(status_code=422, detail='Invalid credential key.')
    try:
        capabilities = get_execution_capabilities(principal.user_id)
        allowed_keys = {profile['auth']['credential_key'] for profile in capabilities['profiles']}
        if capabilities['configured'] and credential_key not in allowed_keys:
            raise HTTPException(status_code=422, detail='That credential is not used by a verified execution profile.')
        return save_execution_credential(principal.user_id, credential_key, contract.credential)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete('/api/v1/execution/credentials/{credential_key}')
async def remove_execution_credential(credential_key: str, principal: Principal = Depends(require_scope('account'))):
    delete_execution_credential(principal.user_id, credential_key)
    return {'credential_key': credential_key, 'configured': False}


@app.get('/api/v1/agents')
async def list_agents(principal: Principal = Depends(require_scope('read'))):
    """Return process-free objective/run projections for the Fleet UI."""
    return await asyncio.to_thread(structured_runtime.agents, principal.user_id)

@app.get('/api/v1/fleet')
async def get_fleet(principal: Principal = Depends(require_scope('read'))):
    return await asyncio.to_thread(structured_runtime.fleet, principal.user_id)


@app.post('/api/v1/agents/{agent_id}/migration-requests')
async def request_agent_migration(agent_id: str, contract: AgentMigrationRequestContract, principal: Principal = Depends(require_scope('command'))):
    existing = get_agent_migration_by_idempotency(principal.user_id, contract.idempotency_key)
    if existing:
        if existing['agent_id'] != agent_id or existing['target']['profile_id'] != contract.profile_id:
            raise HTTPException(status_code=409, detail='That idempotency key was already used for a different migration request.')
        return existing
    try:
        target = profile_selection(contract.profile_id, principal.user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    observed_agents = await asyncio.to_thread(structured_runtime.agents, principal.user_id)
    agent = next((item for item in observed_agents if item.get('id') == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail='The structured worker run is no longer available.')
    if str(agent.get('status') or '').lower() not in {'working', 'blocked'}:
        raise HTTPException(status_code=409, detail='Migration is available only for a structured active worker run.')
    context = await asyncio.to_thread(
        structured_runtime.migration_context, principal.user_id, agent_id,
    )
    if context is None:
        raise HTTPException(status_code=404, detail='The structured worker run is no longer available.')
    context['current_runtime'] = {
        'harness': agent.get('harness'), 'model': agent.get('model'),
    }
    try:
        return create_agent_migration(principal.user_id, agent_id, contract.idempotency_key, target, context)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get('/api/v1/agents/{agent_id}/migration-requests/{request_id}')
async def inspect_agent_migration(agent_id: str, request_id: str, principal: Principal = Depends(require_scope('read'))):
    migration = get_agent_migration(principal.user_id, request_id)
    if not migration or migration['agent_id'] != agent_id:
        raise HTTPException(status_code=404, detail='Migration request not found.')
    return migration


@app.post('/api/v1/agents/{agent_id}/migration-requests/{request_id}/operator-transition')
async def report_agent_migration_transition(agent_id: str, request_id: str, contract: AgentMigrationTransitionContract, principal: Principal = Depends(require_scope('command'))):
    migration = get_agent_migration(principal.user_id, request_id)
    if not migration or migration['agent_id'] != agent_id:
        raise HTTPException(status_code=404, detail='Migration request not found.')
    if migration['idempotency_key'] != contract.idempotency_key:
        raise HTTPException(status_code=409, detail='The transition does not match this migration idempotency key.')
    try:
        return transition_agent_migration(principal.user_id, request_id, contract.state, contract.evidence)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.get('/api/v1/attention')
async def get_attention(principal: Principal = Depends(require_scope('read'))):
    return await attention_service.get_unified_attention_items(principal.user_id)

@app.post('/api/v1/agents/{agent_id}/send-key')
async def send_agent_key(agent_id: str, key: str = Query('Enter'), principal: Principal = Depends(require_scope('command'))):
    return await herdr_client.send_agent_key(agent_id, key=key)

@app.post('/api/v1/agents/{agent_id}/interrupt')
async def interrupt_agent(agent_id: str, principal: Principal = Depends(require_scope('command'))):
    return await herdr_client.interrupt_agent(agent_id)

@app.post('/api/v1/agents/{agent_id}/rename')
async def rename_agent(agent_id: str, contract: RenameAgentContract, principal: Principal = Depends(require_scope('command'))):
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
