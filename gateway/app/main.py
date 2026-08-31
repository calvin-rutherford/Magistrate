from fastapi import FastAPI, Depends, Header, Response, Request, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
import os
import json
import asyncio
import secrets
import time
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.auth import Principal, issue_session, revoke_session, require_scope, verify_token
from app.herdr_client import DEFAULT_HISTORY_LINES, HERDR_MAX_READ_LINES, HerdrClient
from app.firstmate_client import FirstmateClient
from app.execution_capabilities import get_execution_capabilities, validate_execution_selection, profile_selection
from app.contracts import (UniversalInputContract, ExecutionSettingsContract, ExecutionCredentialContract, GestureInputContract,
                           NotificationAckContract, NotificationPreferencesContract, AttentionActionContract,
                           AttentionActionExecuteContract,
                           RenameAgentContract, VoiceMoveRequest)
from app.stt_adapter import VoiceInputAdapter, TranscriptionError
from app.voice_moves import VoiceMoveService
from app.conversation_store import (CONVERSATION_SCHEMA, MAX_MESSAGE_WINDOW, ingest_terminal_rows,
                                    list_messages as list_conversation_messages, record_primary_reply,
                                    record_prompt, reset_conversation, set_turn_status, turn_messages)
from app.db import (init_db, get_profile, update_profile, get_connected_accounts, upsert_connected_account,
                    disconnect_account, get_execution_preferences, get_execution_credential_status,
                    save_execution_preferences, save_execution_credential, delete_execution_credential)
from app.github_service import github_service
from app.recent_activity import RecentActivityService
from app.attention_service import attention_service
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
from app.ar_glasses import router as ar_router
from app.uploads import (MAX_UPLOAD_BYTES, MAX_UPLOAD_COUNT, MAX_UPLOAD_TOTAL_BYTES,
                         associate_uploads, save_upload, get_upload, validate_upload_metadata)

init_db()

app = FastAPI(
    title='Magistrate Gateway API',
    description='Universal cockpit adapter for Firstmate and Herdr',
    version='1.0.0'
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
    if request.url.path == '/api/v1/uploads' and length > MAX_UPLOAD_REQUEST_BYTES:
        return JSONResponse({'detail': 'The upload request is too large.'}, status_code=413)
    if request.url.path == '/api/v1/captain/prompt' and length > MAX_PROMPT_REQUEST_BYTES:
        return JSONResponse({'detail': 'The prompt request is too large.'}, status_code=413)
    return await call_next(request)

herdr_client = HerdrClient()
fm_client = FirstmateClient()
recent_activity_service = RecentActivityService(fm_client, github_service)
stt_adapter = VoiceInputAdapter()
voice_move_service = VoiceMoveService(herdr_client)
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
            items = await attention_service.get_unified_attention_items()
            for user_id in list_registered_push_users():
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
    if os.getenv('MAGISTRATE_DISABLE_NOTIFICATION_RECONCILER', '').lower() not in {'1', 'true', 'yes'}:
        _notification_reconciler_task = asyncio.create_task(_reconcile_registered_notifications())


@app.on_event('shutdown')
async def stop_notification_reconciler():
    global _notification_reconciler_task
    if _notification_reconciler_task:
        _notification_reconciler_task.cancel()
        await asyncio.gather(_notification_reconciler_task, return_exceptions=True)
        _notification_reconciler_task = None


# The captain thread is the conversation Magistrate owns end to end, and the
# only one whose transcript comes from the canonical record. Worker panes also
# contain autonomous and Firstmate-authored turns with no Magistrate submission
# id, so they keep reading terminal history until every turn has durable identity.
CANONICAL_CONVERSATION_TARGET = 'captain'


async def _ingest_target_snapshot(user_id: str, target: str, lines: int = DEFAULT_HISTORY_LINES) -> Optional[str]:
    """Fold the current terminal snapshot into the canonical record.

    This is the only place terminal output enters the conversation. A working
    agent can expose a transiently empty snapshot, and Herdr may be unreachable
    entirely; neither may hide the record that already exists. The failure is
    returned rather than swallowed so the caller reports it instead of
    presenting a stale transcript as a current one.
    """
    try:
        snapshot = await herdr_client.read_typed_rows(target, lines=lines)
        ingest_terminal_rows(user_id, target, snapshot.get('rows', []))
        return None
    except Exception as exc:
        return f'{type(exc).__name__}: {exc}'[:200]


@app.websocket('/api/v1/events')
async def agent_events(websocket: WebSocket):
    """Authenticate in the first frame; credentials never travel in a URL."""
    principal = None
    requested_target = None
    await websocket.accept()
    try:
        if principal is None:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            message = json.loads(raw)
            token = message.get('token') if isinstance(message, dict) and message.get('type') == 'auth' else None
            requested_target = message.get('target') if isinstance(message, dict) else None
            if not isinstance(token, str):
                await websocket.close(code=1008)
                return
            # Browser WebSocket APIs cannot set an Authorization header. The
            # first application frame is the equivalent bearer transport.
            from app.auth import _principal_from_token
            try:
                principal = _principal_from_token(token)
            except HTTPException:
                await websocket.close(code=1008)
                return
        if not principal.has('read'):
            await websocket.close(code=1008)
            return
        target = requested_target if principal is not None and isinstance(requested_target, str) and requested_target else 'captain'
        seen: set[str] = set()
        revisions: Dict[str, int] = {}
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
                    revisions.clear()
                    await websocket.send_json({'type': 'subscribed', 'target': target})
            except asyncio.TimeoutError:
                pass
            if target == CANONICAL_CONVERSATION_TARGET:
                # Canonical delivery: ingest the snapshot, then send only the
                # messages whose revision this connection has not seen. A
                # re-read that changed nothing sends nothing, and a revised
                # message arrives with the id the client already rendered.
                await _ingest_target_snapshot(principal.user_id, target)
                payload = list_conversation_messages(principal.user_id, target)
                fresh = [item for item in payload['messages'] if revisions.get(item['id']) != item['revision']]
                # Rebuilt rather than accumulated: the delivered window slides,
                # so this stays bounded by the window instead of by session age.
                revisions = {item['id']: item['revision'] for item in payload['messages']}
                if fresh:
                    await websocket.send_json({
                        'type': 'conversation_messages', 'schema_version': CONVERSATION_SCHEMA,
                        'target': target, 'messages': fresh,
                    })
                continue
            # Worker panes still read their transcript from the terminal; see
            # CHAT_ARCHITECTURE_FIX.md for why that path is transitional.
            history = await herdr_client.get_agent_history(target, lines=DEFAULT_HISTORY_LINES)
            fresh = []
            for item in history.get('messages', []):
                # Stable history ids keep two legitimate identical turns
                # distinct while repeated snapshots remain idempotent.
                key = f"id:{item.get('id')}" if item.get('id') else f"{item.get('role')}|{item.get('kind')}|{item.get('text')}"
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(item)
            if fresh:
                await websocket.send_json({'type': 'agent_history', 'target': history.get('target', target), 'messages': fresh})
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        try:
            await websocket.close(code=1008)
        except Exception:
            pass


class SessionRequest(BaseModel):
    bootstrap_secret: Optional[str] = None


@app.post('/api/v1/auth/session')
async def create_session(request: SessionRequest, response: Response):
    # Bearer issuance must never be cached by a browser, proxy, or shared CDN.
    response.headers['Cache-Control'] = 'no-store'
    return issue_session(request.bootstrap_secret)


@app.get('/api/v1/auth/session')
async def inspect_session(response: Response, principal: Principal = Depends(verify_token)):
    """Small protected validation endpoint independent of Herdr/Firstmate."""
    response.headers['Cache-Control'] = 'no-store'
    return {
        'authenticated': True,
        'user_id': principal.user_id,
        'scopes': sorted(principal.scopes),
        'expires_at': principal.expires_at,
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
    snapshot = await herdr_client.get_snapshot()
    fm_snapshot = await fm_client.get_snapshot()
    # A missing snapshot means Herdr is unreachable. Reporting a placeholder
    # version/protocol here would be an invented metric, so report null instead.
    herdr_connected = bool(snapshot.get('version'))
    return {
        'herdr': {
            'status': 'connected' if herdr_connected else 'disconnected',
            'version': snapshot.get('version') if herdr_connected else None,
            'protocol': snapshot.get('protocol') if herdr_connected else None,
            'agents_count': len(snapshot.get('agents', []))
        },
        'firstmate': {
            'fm_home': fm_snapshot.get('fm_home'),
            'schema': fm_snapshot.get('schema', 'fm-fleet-snapshot.v1'),
            'tasks_count': len(fm_snapshot.get('tasks', []))
        }
    }

@app.get('/api/v1/health')
async def get_health(principal: Principal = Depends(require_scope('read'))):
    snapshot = await herdr_client.get_snapshot()
    fm_snapshot = await fm_client.get_snapshot()
    herdr_connected = bool(snapshot.get('version'))
    firstmate_available = bool(fm_snapshot.get('fm_home'))
    # The gateway process answering is not the same claim as the product being
    # healthy. Degrade explicitly when a live source is missing, and never
    # substitute a placeholder Herdr version for one we did not observe.
    degraded = [name for name, ok in (('herdr', herdr_connected), ('firstmate', firstmate_available)) if not ok]
    return {
        'status': 'degraded' if degraded else 'healthy',
        'degraded_sources': degraded,
        'service': 'magistrate-gateway',
        'version': '1.0.0',
        'herdr_version': snapshot.get('version') if herdr_connected else None,
        'herdr_socket_connected': herdr_connected,
        'firstmate_home': fm_snapshot.get('fm_home'),
        'firstmate_available': firstmate_available,
        'firstmate_tasks_count': len(fm_snapshot.get('tasks', []))
    }

# ACCOUNT PROFILE ENDPOINTS
@app.get('/api/v1/account/profile')
async def get_account_profile(principal: Principal = Depends(require_scope('account'))):
    return get_profile(principal.user_id)

@app.post('/api/v1/account/profile')
async def post_account_profile(
    name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    bio: Optional[str] = Form(None),
    active_theme: Optional[str] = Form(None),
    principal: Principal = Depends(require_scope('account'))
):
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
        return await recent_activity_service.get_recent_activity(limit, refresh)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

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
    return await attention_service.get_unified_attention_items()


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
        items = await attention_service.get_unified_attention_items()
        return prepare_confirmation(items, action_key, contract.action, contract.target_id, principal.user_id, principal.session_id)
    except AttentionActionError as exc:
        raise _action_error(exc) from exc


@app.post('/api/v1/attention/actions/{action_key}/execute')
async def execute_attention_action(action_key: str, contract: AttentionActionExecuteContract, principal: Principal = Depends(require_scope('command'))):
    _require_owner(principal)
    if contract.action_key != action_key:
        raise HTTPException(status_code=409, detail={'code': 'mismatch', 'message': 'The action key in the request does not match the route.'})
    try:
        items = await attention_service.get_unified_attention_items()
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
    items = await attention_service.get_unified_attention_items()
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
    items = await attention_service.get_unified_attention_items()
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
    items = await attention_service.get_unified_attention_items()
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

@app.post('/api/v1/voice/moves')
async def create_voice_move(request: VoiceMoveRequest, principal: Principal = Depends(require_scope('voice'))):
    try:
        result = await voice_move_service.handle(request, principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Voice Mode shares the captain thread, so a completed voice turn is
    # recorded canonically exactly like a typed one. Without this the chat
    # transcript and the voice transcript would be two different records.
    if result.get('status') == 'completed':
        client_message_id = request.client_message_id or result.get('move_id')
        if client_message_id:
            turn = record_prompt(
                principal.user_id, CANONICAL_CONVERSATION_TARGET, client_message_id,
                result.get('utterance') or request.utterance, source='voice',
            )
            response = result.get('response')
            if isinstance(response, str) and response.strip():
                record_primary_reply(
                    principal.user_id, CANONICAL_CONVERSATION_TARGET, turn['turn_id'], response,
                    source='voice',
                )
            return {
                **result,
                'conversation': {
                    'schema_version': CONVERSATION_SCHEMA,
                    'target': CANONICAL_CONVERSATION_TARGET,
                    'conversation_id': turn['conversation_id'],
                    'turn_id': turn['turn_id'],
                    'messages': turn_messages(turn['turn_id']),
                },
            }
    return result

# FLEET, ATTENTION & AGENTS
@app.get('/api/v1/execution/capabilities')
async def get_execution_capability_inventory(principal: Principal = Depends(require_scope('read'))):
    try:
        return get_execution_capabilities(principal.user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc


@app.get('/api/v1/execution/settings')
async def get_execution_settings(principal: Principal = Depends(require_scope('account'))):
    return {**get_execution_preferences(principal.user_id), 'migration_supported': False,
            'credential_storage': 'encrypted', 'credentials': [
                {'credential_key': key, 'configured': configured}
                for key, configured in get_execution_credential_status(principal.user_id).items()
            ]}


@app.put('/api/v1/execution/settings')
async def put_execution_settings(contract: ExecutionSettingsContract, principal: Principal = Depends(require_scope('account'))):
    current = get_execution_preferences(principal.user_id)
    profile_id = contract.profile_id if 'profile_id' in contract.model_fields_set else current['profile_id']
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
    return {**save_execution_preferences(principal.user_id, profile_id=profile_id, switching_behavior=switching, unavailable_behavior=unavailable),
            'migration_supported': False}


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
    agents, fleet = await asyncio.gather(herdr_client.list_agents(), fm_client.get_snapshot())
    return fm_client.apply_agent_display_names(agents, fleet)

@app.get('/api/v1/fleet')
async def get_fleet(principal: Principal = Depends(require_scope('read'))):
    return await fm_client.get_snapshot()

@app.get('/api/v1/attention')
async def get_attention(principal: Principal = Depends(require_scope('read'))):
    return await attention_service.get_unified_attention_items()

@app.get('/api/v1/captain/output')
async def get_captain_output(
    lines: int = Query(DEFAULT_HISTORY_LINES, ge=0, le=HERDR_MAX_READ_LINES),
    principal: Principal = Depends(require_scope('read')),
):
    output = await herdr_client.read_agent_output('captain', lines=lines)
    return {'output': output}

@app.get('/api/v1/agents/{agent_id}/history')
async def get_agent_history(
    agent_id: str,
    lines: int = Query(DEFAULT_HISTORY_LINES, ge=0, le=HERDR_MAX_READ_LINES),
    before: Optional[str] = Query(None, min_length=1, max_length=64),
    after: Optional[str] = Query(None, min_length=1, max_length=64),
    principal: Principal = Depends(require_scope('read')),
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
async def send_captain_prompt(contract: UniversalInputContract, principal: Principal = Depends(require_scope('command'))):
    selection = None
    if contract.profile_id:
        try:
            selection = profile_selection(contract.profile_id, principal.user_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            if get_execution_preferences(principal.user_id)['unavailable_behavior'] != 'fallback':
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            # Fallback is explicit user policy, never the default. The response
            # remains a current-session prompt and does not pretend migration ran.
            selection = None
    elif contract.harness or contract.model:
        if not contract.harness or not contract.model:
            raise HTTPException(status_code=422, detail='A harness and model must be selected together.')
        try:
            selection = validate_execution_selection(contract.harness, contract.model, user_id=principal.user_id, provider=contract.provider, variant=contract.variant)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
        except ValueError as exc:
            if get_execution_preferences(principal.user_id)['unavailable_behavior'] == 'fallback':
                selection = None
            else:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif 'profile_id' not in contract.model_fields_set:
        preference = get_execution_preferences(principal.user_id)
        if preference['profile_id']:
            try:
                selection = profile_selection(preference['profile_id'], principal.user_id)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
            except ValueError as exc:
                if preference['unavailable_behavior'] != 'fallback':
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                selection = None
    prompt_text = contract.text or ''
    if contract.attachments:
        # Attachment ids are opaque and must belong to this principal. Verify
        # every piece of client metadata against the stored record before the
        # provider sees a manifest; names and sizes from JSON are never trusted.
        stored_uploads = []
        for attachment in contract.attachments:
            stored = get_upload(principal.user_id, attachment.upload_id)
            if not stored:
                raise HTTPException(status_code=404, detail='One or more attached files are unavailable.')
            try:
                validate_upload_metadata(stored, attachment.filename, attachment.media_type, attachment.size)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            stored_uploads.append(stored)
        if contract.message_id:
            try:
                associate_uploads(principal.user_id, contract.message_id, [item['upload_id'] for item in stored_uploads])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Herdr's current prompt contract is text-only. Forward a bounded,
        # human-readable manifest through the normal Herdr -> Firstmate path;
        # never put bytes, local paths, credentials, or bearer tokens in the
        # prompt/history. A provider that cannot accept even this manifest must
        # return its real error, which the client surfaces instead of claiming
        # delivery.
        attachment_summary = ', '.join(
            f"{item['filename']} ({item['media_type']}, {item['size']} bytes)" for item in stored_uploads
        )
        prompt_text = prompt_text + ('\n\n' if prompt_text else '') + 'Attached files: ' + attachment_summary
    # The canonical turn is created before the provider sees the prompt: the
    # frontend's message_id is the submission identity, so a retry or replay
    # reuses this turn instead of minting a second user message, and the
    # terminal adapter can attribute the reply even when it arrives much later.
    client_message_id = contract.message_id or 'srv-' + secrets.token_hex(8)
    turn = record_prompt(
        principal.user_id, contract.target, client_message_id, contract.text or '',
        source='text', submitted_text=prompt_text,
    )
    result = await herdr_client.prompt_agent(contract.target, prompt_text, **(selection or {}))
    if result.get('status') == 'error':
        set_turn_status(principal.user_id, contract.target, client_message_id, 'failed')
    else:
        response = result.get('response')
        if isinstance(response, str) and response.strip():
            # A harness that answers synchronously is recorded here, so the
            # reply has canonical identity from the start and the poll that
            # later re-reads the same turn revises it instead of adding a row.
            record_primary_reply(principal.user_id, contract.target, turn['turn_id'], response)
    return {
        **result,
        'message_id': client_message_id,
        'conversation': {
            'schema_version': CONVERSATION_SCHEMA,
            'target': contract.target,
            'conversation_id': turn['conversation_id'],
            'turn_id': turn['turn_id'],
            'messages': turn_messages(turn['turn_id']),
        },
    }

# CANONICAL CONVERSATION RECORD
# The chat transcript is the gateway's own record (see app/conversation_store.py
# and CHAT_ARCHITECTURE_FIX.md). Terminal snapshots only feed it.
@app.get('/api/v1/conversations/{target}/messages')
async def get_conversation_messages(
    target: str,
    lines: int = Query(DEFAULT_HISTORY_LINES, ge=0, le=HERDR_MAX_READ_LINES),
    limit: int = Query(MAX_MESSAGE_WINDOW, ge=1, le=MAX_MESSAGE_WINDOW),
    principal: Principal = Depends(require_scope('read')),
):
    ingest_error = await _ingest_target_snapshot(principal.user_id, target, lines=lines)
    # The record is still authoritative when the live snapshot could not be
    # read; the failure travels with it rather than being presented as success.
    return {**list_conversation_messages(principal.user_id, target, limit=limit), 'ingest_error': ingest_error}


@app.post('/api/v1/conversations/{target}/reset')
async def post_conversation_reset(target: str, principal: Principal = Depends(require_scope('command'))):
    """Discard this conversation's canonical record for a genuinely fresh thread."""
    return reset_conversation(principal.user_id, target)


@app.post('/api/v1/conversations/{target}/turns/{client_message_id}/cancel')
async def post_conversation_turn_cancel(
    target: str, client_message_id: str, principal: Principal = Depends(require_scope('command')),
):
    set_turn_status(principal.user_id, target, client_message_id, 'cancelled')
    return {'status': 'cancelled', 'target': target, 'client_message_id': client_message_id}

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
