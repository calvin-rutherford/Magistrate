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
import hmac

from app.production_security import cors_origins, validate_production_configuration
from app.persistence import observation_connection
from app.request_boundary import RequestBoundary
from app.telemetry import prometheus_metrics, record
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.auth import (Principal, account_onboarding_required, authenticate_request,
                      issue_friend_beta_session, issue_session, revoke_session,
                      require_any_scope, require_scope,
                      validate_friend_beta_configuration, verify_token)
from app.provider_auth import (
    PROVIDER_COOKIE_NAME, PROVIDER_COOKIE_PATH, create_challenge,
    exchange_challenge, list_login_methods, provider_availability,
    public_session_payload, refresh_session as refresh_provider_session,
    unlink_login_method, validate_provider_auth_configuration,
)
from app.onboarding import acknowledge_welcome, onboarding_state
from app.herdr_client import HerdrClient
from app.firstmate_client import FirstmateClient
from app.execution_capabilities import get_execution_capabilities, validate_execution_selection, profile_selection
from app.execution_routing import ExecutionRequirements, select_execution_profile
from app.contracts import (ExecutionSettingsContract, ExecutionCredentialContract,
                           NotificationAckContract, NotificationPreferencesContract, AttentionActionContract,
                           AttentionActionExecuteContract, RoutingPreferenceContract,
                           ExecutionRouteRequirementsContract,
                           AgentMigrationRequestContract, AgentMigrationTransitionContract,
                           ActivityCatchUpContract, MAGI_MAX_RESPONSE_BYTES,
                           RenameAgentContract)
from app.stt_adapter import VoiceInputAdapter, TranscriptionError
from app.db import (database_health, init_db, get_profile, update_profile, get_connected_accounts, upsert_connected_account,
                    disconnect_account, get_execution_preferences, get_execution_credential_status,
                    save_execution_preferences, save_execution_credential, delete_execution_credential,
                    create_agent_migration, get_agent_migration, get_agent_migration_by_idempotency, transition_agent_migration)
from app.account_lifecycle import AccountDeletionError, delete_account
from app.projects import (ProjectError, bind_github_repository, create_project,
                          delete_project, get_project, list_projects,
                          unbind_repository, update_project)
from app.github_app import (MAX_WEBHOOK_BYTES as GITHUB_MAX_WEBHOOK_BYTES,
                            github_app_service, router as github_app_router,
                            validate_github_app_configuration)
from app.recent_activity import RecentActivityService
from app.activity_store import SourceEventConflict, list_activity, snapshot_activity, source_diagnostics
from app.structured_runtime import StructuredRuntimeProjection
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
from app.uploads import (MAX_UPLOAD_BYTES, MAX_UPLOAD_COUNT, MAX_UPLOAD_TOTAL_BYTES,
                         SIGNED_ACCESS_TTL_SECONDS, associate_uploads, delete_upload,
                         get_upload, save_upload, signed_access_token, validate_content,
                         verify_signed_access, discard_uploads, avatar_root)
from app.magi_chat_api import (magi_chat_readiness, magi_chat_service,
                               router as magi_chat_router,
                               validate_magi_chat_configuration)
from app.magi_chat_store import MagiChatStore
from app.firstmate_execution import MAX_FIRSTMATE_EXECUTION_EVENT_BYTES
from app.firstmate_intake import reconcile_pending_objective_intake
from app.firstmate_execution_api import (
    firstmate_execution_service, router as firstmate_execution_router,
)
from app.firstmate_decision_api import router as firstmate_decision_router
from app.billing import MAX_WEBHOOK_BYTES, validate_billing_configuration
from app.billing_api import router as billing_router
from app.project_memory_api import router as project_memory_router
from app.objective_cancellation import (
    ObjectiveCancellationError, objective_cancellation_service,
)
from app.hosted_execution import (
    HostedExecutionConfig, get_hosted_controller, router as hosted_execution_router,
)
from app.perception import MAX_PERCEPTION_EVENT_BYTES, router as perception_router

validate_production_configuration()
init_db()

app = FastAPI(
    title='Magistrate Gateway API',
    description='Authenticated Gateway for provider-native Magi chat and governed execution',
    version='1.1.0'
)

def _cors_origins() -> list[str]:
    return cors_origins()


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type'],
    expose_headers=['X-Request-ID'],
)

GATEWAY_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = avatar_root()
os.makedirs(UPLOADS_DIR, mode=0o700, exist_ok=True)
# Only avatars are public. Never mount the parent of private chat attachments.
app.mount('/uploads/avatars', StaticFiles(directory=str(UPLOADS_DIR)), name='avatars')
app.include_router(github_app_router)
app.include_router(magi_chat_router)
app.include_router(firstmate_execution_router)
app.include_router(firstmate_decision_router)
app.include_router(billing_router)
app.include_router(project_memory_router)
app.include_router(hosted_execution_router)
app.include_router(perception_router)

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
    if (
        request.url.path == '/api/v1/magi/messages'
        or request.url.path.startswith('/api/v1/magi/memory/entries/')
    ) and length > MAX_PROMPT_REQUEST_BYTES:
        return JSONResponse({'detail': 'The Magi request is too large.'}, status_code=413)
    perception_contract = request.method == 'POST' and (
        request.url.path == '/api/v1/perception/events'
        or bool(re.fullmatch(r'/api/v1/perception/events/pev_[A-Za-z0-9_-]{12,96}/confirm', request.url.path))
    )
    if perception_contract and content_length is None:
        return JSONResponse({'detail': 'A perception request size is required.'}, status_code=411)
    github_webhook = request.method == 'POST' and request.url.path == '/api/v1/github/webhooks'
    github_install = request.method == 'POST' and request.url.path == '/api/v1/github/app/install'
    stripe_webhook = request.method == 'POST' and request.url.path in {
        '/api/v1/billing/webhook', '/api/v1/billing/webhooks/stripe',
    }
    if perception_contract:
        body_cap, too_large = MAX_PERCEPTION_EVENT_BYTES, 'The perception request is too large.'
    elif github_webhook:
        body_cap, too_large = GITHUB_MAX_WEBHOOK_BYTES, 'Webhook payload is too large.'
    elif github_install:
        body_cap, too_large = 4096, 'The GitHub App request is too large.'
    elif stripe_webhook:
        body_cap, too_large = MAX_WEBHOOK_BYTES, 'The Stripe webhook is too large.'
    else:
        body_cap, too_large = 0, ''
    if body_cap and length > body_cap:
        return JSONResponse({'detail': too_large}, status_code=413)
    if body_cap:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > body_cap:
                return JSONResponse({'detail': too_large}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
    firstmate_execution_contract = request.method == 'POST' and bool(re.fullmatch(
        r'/api/v1/firstmate/execution-events(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/wake)?',
        request.url.path,
    ))
    firstmate_decision_contract = (
        request.method == 'POST'
        and request.url.path == '/api/v1/firstmate/decision-events'
    )
    hosted_worker_contract = request.method == 'POST' and bool(re.fullmatch(
        r'/api/v1/hosted-execution/objectives/mgo_[0-9a-f]{32}/(?:events|decision-events|decision-answers/ack)',
        request.url.path,
    ))
    firstmate_structured_contract = (
        firstmate_execution_contract or firstmate_decision_contract or hosted_worker_contract
    )
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

# Outermost user middleware: every HTTP parser is behind a measured-byte cap.
app.add_middleware(RequestBoundary)
app.state.startup_complete = False

herdr_client = HerdrClient()
fm_client = FirstmateClient()
structured_runtime = StructuredRuntimeProjection()
recent_activity_service = RecentActivityService(structured_runtime, github_app_service)
stt_adapter = VoiceInputAdapter()
_notification_reconciler_task = None
_firstmate_delivery_recovery_task = None
_hosted_execution_task = None
_PROCESS_STARTED_AT_MS = int(time.time() * 1000)


async def _recover_firstmate_deliveries_once() -> None:
    """Run one bounded write-side recovery pass, never from a read or timer."""
    try:
        intake_recovery = await reconcile_pending_objective_intake(
            updated_before_ms=_PROCESS_STARTED_AT_MS,
        )
        if intake_recovery["examined"]:
            record('recovery', outcome='ok')
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never log provider/subprocess exception text or row payloads.
        record('recovery', outcome='error')
    try:
        cancellation_recovery = await objective_cancellation_service.recover_pending(
            updated_before_ms=_PROCESS_STARTED_AT_MS,
        )
        if cancellation_recovery["examined"]:
            record('recovery', outcome='ok')
    except asyncio.CancelledError:
        raise
    except Exception:
        # Persisted pending rows remain eligible for the next explicit restart.
        record('recovery', outcome='error')


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
        except Exception:
            # A provider outage must not echo remote content into host logs.
            record('notifications', outcome='error')


@app.on_event('startup')
async def start_notification_reconciler():
    global _notification_reconciler_task, _firstmate_delivery_recovery_task, _hosted_execution_task
    validate_production_configuration()
    validate_friend_beta_configuration()
    validate_provider_auth_configuration()
    validate_billing_configuration()
    validate_magi_chat_configuration()
    validate_github_app_configuration()
    # Hosted mode is a fail-closed production boundary: validate every image,
    # identity, mTLS, network, and resource setting before serving.
    HostedExecutionConfig.from_env()
    # A process cannot resume an in-flight provider socket. Preserve the
    # reserved pair and expose a truthful, explicitly retryable failure.
    await asyncio.to_thread(MagiChatStore().recover_orphaned_pending)
    await firstmate_execution_service.recover_pending()
    if fm_client.captain_producer_required:
        producer = fm_client.get_producer_readiness()
        if producer['status'] != 'ready':
            raise RuntimeError('The required pinned Firstmate producer is unavailable.')
    _firstmate_delivery_recovery_task = asyncio.create_task(
        _recover_firstmate_deliveries_once()
    )
    hosted_controller = get_hosted_controller()
    if hosted_controller is not None:
        _hosted_execution_task = asyncio.create_task(hosted_controller.run())
    if os.getenv('MAGISTRATE_DISABLE_NOTIFICATION_RECONCILER', '').lower() not in {'1', 'true', 'yes'}:
        _notification_reconciler_task = asyncio.create_task(_reconcile_registered_notifications())
    app.state.startup_complete = True


@app.on_event('shutdown')
async def stop_notification_reconciler():
    global _notification_reconciler_task, _firstmate_delivery_recovery_task, _hosted_execution_task
    app.state.startup_complete = False
    tasks = [
        task for task in (_notification_reconciler_task, _firstmate_delivery_recovery_task, _hosted_execution_task)
        if task is not None
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _notification_reconciler_task = None
    _firstmate_delivery_recovery_task = None
    _hosted_execution_task = None


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


class ProviderChallengeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['apple', 'google']
    action: Literal['sign_in', 'link'] = 'sign_in'
    client_platform: Literal['native', 'web']
    redirect_uri: Optional[str] = Field(None, max_length=1024)


class ProviderExchangeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['apple', 'google']
    challenge_id: str = Field(min_length=1, max_length=64)
    nonce: str = Field(min_length=32, max_length=128)
    identity_token: Optional[str] = Field(None, max_length=24 * 1024)
    authorization_code: Optional[str] = Field(None, max_length=4096)
    redirect_uri: Optional[str] = Field(None, max_length=1024)
    display_name: Optional[str] = Field(None, max_length=120)


class ProviderRefreshRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    refresh_token: Optional[str] = Field(None, max_length=128)


class ObjectiveCancellationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idempotency_key: str = Field(min_length=8, max_length=128)


class AccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: str = Field(min_length=8, max_length=256)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    slug: Optional[str] = Field(None, min_length=1, max_length=64)
    description: str = Field('', max_length=2000)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[Literal['active', 'archived']] = None


class RepositoryBindingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str = Field(min_length=3, max_length=201)
    html_url: str = Field(min_length=19, max_length=500)
    provider_repository_id: Optional[str] = Field(None, max_length=128)
    default_branch: Optional[str] = Field(None, max_length=255)


def _optional_principal(request: Request, authorization: Optional[str]) -> Optional[Principal]:
    if authorization is None:
        return None
    return authenticate_request(request, authorization)


def _provider_cookie(response: Response, token: str, expires_at: int) -> None:
    production = os.getenv('MAGISTRATE_ENV', '').strip().lower() not in {
        'dev', 'development', 'test', 'testing',
    }
    response.set_cookie(
        PROVIDER_COOKIE_NAME, token, max_age=max(0, expires_at - int(time.time())),
        expires=expires_at, path=PROVIDER_COOKIE_PATH, secure=production,
        httponly=True, samesite='lax',
    )


def _clear_provider_cookie(response: Response) -> None:
    response.delete_cookie(PROVIDER_COOKIE_NAME, path=PROVIDER_COOKIE_PATH)


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


@app.get('/api/v1/auth/provider/configuration')
async def provider_auth_configuration(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return {'schema_version': 'provider-auth-configuration.v1', **provider_availability()}


@app.post('/api/v1/auth/provider/challenge')
async def create_provider_auth_challenge(
    contract: ProviderChallengeRequest,
    request: Request,
    response: Response,
    authorization: Optional[str] = Header(None),
):
    response.headers['Cache-Control'] = 'no-store'
    principal = _optional_principal(request, authorization)
    return create_challenge(
        contract.provider, contract.action, contract.client_platform,
        contract.redirect_uri, principal,
    )


@app.post('/api/v1/auth/provider/exchange')
async def exchange_provider_auth_challenge(
    contract: ProviderExchangeRequest,
    request: Request,
    response: Response,
    authorization: Optional[str] = Header(None),
):
    response.headers['Cache-Control'] = 'no-store'
    principal = _optional_principal(request, authorization)
    payload = await exchange_challenge(
        provider=contract.provider,
        challenge_id=contract.challenge_id,
        raw_nonce=contract.nonce,
        identity_token=contract.identity_token,
        authorization_code=contract.authorization_code,
        redirect_uri=contract.redirect_uri,
        display_name=contract.display_name,
        principal=principal,
    )
    if payload.get('status') == 'linked':
        return payload
    client_platform = payload.pop('_client_platform', 'native')
    include_refresh = client_platform == 'native'
    if not include_refresh:
        _provider_cookie(response, payload['_refresh_token'], int(payload['refresh_expires_at']))
    return public_session_payload(payload, include_refresh_token=include_refresh)


@app.post('/api/v1/auth/provider/refresh')
async def refresh_provider_auth_session(
    contract: ProviderRefreshRequest,
    request: Request,
    response: Response,
):
    response.headers['Cache-Control'] = 'no-store'
    cookie_token = request.cookies.get(PROVIDER_COOKIE_NAME)
    if contract.refresh_token and cookie_token and contract.refresh_token != cookie_token:
        raise HTTPException(status_code=400, detail='The provider refresh request is ambiguous.')
    token = contract.refresh_token or cookie_token
    if not token:
        raise HTTPException(status_code=401, detail='The provider session is invalid or expired.')
    client_platform = 'native' if contract.refresh_token else 'web'
    try:
        payload = refresh_provider_session(token, client_platform=client_platform)
    except HTTPException:
        if cookie_token:
            _clear_provider_cookie(response)
        raise
    native = bool(contract.refresh_token)
    if not native:
        _provider_cookie(response, payload['_refresh_token'], int(payload['refresh_expires_at']))
    return public_session_payload(payload, include_refresh_token=native)


@app.get('/api/v1/auth/session')
async def inspect_session(response: Response, principal: Principal = Depends(verify_token)):
    """Small protected validation endpoint independent of Herdr/Firstmate."""
    response.headers['Cache-Control'] = 'no-store'
    return {
        'authenticated': True,
        'user_id': principal.user_id,
        'scopes': sorted(principal.scopes),
        'expires_at': principal.expires_at,
        'auth_method': (
            'friend-beta-access' if principal.access_grant_id
            else principal.auth_provider or 'operator-bootstrap'
        ),
        'refresh_expires_at': principal.refresh_expires_at,
        'onboarding_required': account_onboarding_required(principal),
    }


@app.post('/api/v1/auth/session/revoke')
async def revoke_current_session(response: Response, principal: Principal = Depends(verify_token), authorization: Optional[str] = Header(None)):
    response.headers['Cache-Control'] = 'no-store'
    _clear_provider_cookie(response)
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

def _execution_interface_readiness() -> dict[str, Any]:
    if HostedExecutionConfig.from_env() is not None:
        return {
            'status': 'configured',
            'mode': 'hosted-isolation',
            'activation': 'not-observed',
            'live_probe_performed': False,
        }
    return fm_client.get_execution_interface_readiness()


@app.get('/api/v1/runtime')
async def get_runtime(principal: Principal = Depends(require_scope('read'))):
    fleet, runtime = await asyncio.gather(
        asyncio.to_thread(structured_runtime.fleet, principal.user_id),
        asyncio.to_thread(structured_runtime.runtime, principal.user_id),
    )
    execution_interface = _execution_interface_readiness()
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
    execution_interface = _execution_interface_readiness()
    event_ingress = {
        'status': 'ready',
        'schemas': ['firstmate.execution-event.v1', 'firstmate.decision-events.v1'],
        'live_probe_performed': False,
    }
    provider = magi_chat_readiness()
    producer = fm_client.get_producer_readiness()
    database = await asyncio.to_thread(database_health)
    degraded: List[str] = []
    if provider['enabled'] and provider['status'] != 'configured':
        degraded.append('magi-provider')
    if execution_interface['status'] != 'configured':
        degraded.append('firstmate-execution-interface')
    if producer['required'] and producer['status'] != 'ready':
        degraded.append('firstmate-producer')
    if database['status'] != 'healthy':
        degraded.append('database')
    return {
        'status': 'degraded' if degraded else 'healthy',
        'degraded_sources': degraded,
        'service': 'magistrate-gateway',
        'version': '1.1.0',
        'gateway_ready': True,
        'database': database,
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

@app.get('/livez', include_in_schema=False)
async def liveness():
    return {'status': 'alive'}


@app.get('/readyz', include_in_schema=False)
async def readiness():
    # Read persisted state only. Never call providers, tools or a runtime probe.
    from app import db
    ready = app.state.startup_complete and magi_chat_readiness()['status'] == 'configured'
    try:
        with observation_connection(db.DB_PATH) as conn:
            version = conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
            ready = ready and version == db.SCHEMA_VERSION
    except Exception:
        # Driver connection errors may contain authority; never expose them.
        ready = False
    return JSONResponse({'status': 'ready' if ready else 'unavailable'}, status_code=200 if ready else 503)


@app.get('/internal/metrics', include_in_schema=False)
async def metrics(authorization: Optional[str] = Header(None)):
    # Product read scope must not expose other principals' traffic/spend.
    token = os.getenv('MAGISTRATE_METRICS_TOKEN', '')
    if not token:
        raise HTTPException(404, 'Not found.')
    if not hmac.compare_digest((authorization or '').encode(), f'Bearer {token}'.encode()):
        raise HTTPException(401, 'Metrics authentication required.')
    return Response(prometheus_metrics(), media_type='text/plain; version=0.0.4')


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

@app.get('/api/v1/account/onboarding')
async def get_account_onboarding(principal: Principal = Depends(require_scope('account'))):
    return onboarding_state(principal.user_id)


@app.post('/api/v1/account/onboarding/welcome')
async def complete_account_welcome(principal: Principal = Depends(require_scope('account'))):
    acknowledge_welcome(principal.user_id)
    return onboarding_state(principal.user_id)


@app.get('/api/v1/account/login-methods')
async def get_account_login_methods(principal: Principal = Depends(require_scope('account'))):
    return list_login_methods(principal)


@app.delete('/api/v1/account/login-methods/{provider}')
async def delete_account_login_method(
    provider: Literal['apple', 'google'],
    principal: Principal = Depends(require_scope('account')),
):
    return unlink_login_method(principal, provider)


@app.post('/api/v1/account/avatar')
async def upload_account_avatar(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_scope('account'))
):
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='Avatar images must be 5 MB or smaller.')
    try:
        media_type = validate_content(file.content_type, file.filename or 'avatar', content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    extensions = {
        'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
        'image/webp': '.webp', 'image/bmp': '.bmp',
    }
    if media_type not in extensions:
        raise HTTPException(status_code=422, detail='An image avatar is required.')
    filename = f'avatar_{secrets.token_urlsafe(24)}{extensions[media_type]}'
    filepath = UPLOADS_DIR / filename
    previous = get_profile(principal.user_id).get('avatar_url')
    try:
        fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
        public_url = f'/uploads/avatars/{filename}'
        updated = update_profile(user_id=principal.user_id, avatar_url=public_url)
    except Exception:
        filepath.unlink(missing_ok=True)
        raise
    if isinstance(previous, str) and previous.startswith('/uploads/avatars/avatar_'):
        old_path = (UPLOADS_DIR / previous.removeprefix('/uploads/avatars/')).resolve()
        if old_path.parent == UPLOADS_DIR.resolve() and old_path != filepath:
            old_path.unlink(missing_ok=True)
    return {'status': 'stored', 'avatar_url': public_url, 'profile': updated}


@app.delete('/api/v1/account')
async def delete_current_account(
    contract: AccountDeletionRequest,
    response: Response,
    principal: Principal = Depends(require_scope('account')),
):
    """Permanently erase the authenticated principal and revoke every session."""
    response.headers['Cache-Control'] = 'no-store'
    if contract.confirmation != f"DELETE {principal.user_id}":
        raise HTTPException(
            status_code=409,
            detail="Account deletion confirmation does not match the authenticated account.",
        )
    try:
        hosted_controller = get_hosted_controller()
        if hosted_controller is not None:
            await hosted_controller.retire_owner(principal.user_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Hosted workloads could not be retired; account data was preserved.",
        ) from exc
    try:
        result = await asyncio.to_thread(
            delete_account, principal.user_id, confirmation=contract.confirmation,
        )
    except AccountDeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _clear_provider_cookie(response)
    return result


# PROJECTS

def _project_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ProjectError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get('/api/v1/projects')
async def get_projects(
    include_archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=200),
    principal: Principal = Depends(require_scope('read')),
):
    return await asyncio.to_thread(
        list_projects, principal.user_id, include_archived=include_archived, limit=limit,
    )


@app.post('/api/v1/projects', status_code=201)
async def post_project(
    contract: ProjectCreateRequest,
    principal: Principal = Depends(require_scope('account')),
):
    return await asyncio.to_thread(
        _project_call, create_project, principal.user_id,
        name=contract.name, slug=contract.slug, description=contract.description,
    )


@app.get('/api/v1/projects/{project_id}')
async def get_project_detail(
    project_id: str,
    principal: Principal = Depends(require_scope('read')),
):
    return await asyncio.to_thread(_project_call, get_project, principal.user_id, project_id)


@app.patch('/api/v1/projects/{project_id}')
async def patch_project(
    project_id: str,
    contract: ProjectUpdateRequest,
    principal: Principal = Depends(require_scope('account')),
):
    return await asyncio.to_thread(
        _project_call, update_project, principal.user_id, project_id,
        name=contract.name, description=contract.description, status=contract.status,
    )


@app.delete('/api/v1/projects/{project_id}', status_code=204)
async def remove_project(
    project_id: str,
    principal: Principal = Depends(require_scope('account')),
):
    await asyncio.to_thread(_project_call, delete_project, principal.user_id, project_id)
    return Response(status_code=204)


@app.post('/api/v1/projects/{project_id}/repositories', status_code=201)
async def post_project_repository(
    project_id: str,
    contract: RepositoryBindingRequest,
    principal: Principal = Depends(require_scope('providers')),
):
    return await asyncio.to_thread(
        _project_call, bind_github_repository, principal.user_id, project_id,
        full_name=contract.full_name, html_url=contract.html_url,
        provider_repository_id=contract.provider_repository_id,
        default_branch=contract.default_branch,
    )


@app.delete('/api/v1/projects/{project_id}/repositories/{repository_id}', status_code=204)
async def remove_project_repository(
    project_id: str,
    repository_id: str,
    principal: Principal = Depends(require_scope('providers')),
):
    await asyncio.to_thread(
        _project_call, unbind_repository, principal.user_id, project_id, repository_id,
    )
    return Response(status_code=204)


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
    _require_owner(principal)
    return await jira_adapter.get_assigned_issues()

@app.get('/api/v1/teams/mentions')
async def get_teams_mentions(principal: Principal = Depends(require_scope('providers'))):
    _require_owner(principal)
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
    _require_owner(principal)
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
    except Exception as exc:
        discard_uploads(principal.user_id, [item['upload_id'] for item in uploaded])
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise
    # The client must not infer processing success from a 200 alone. Report the
    # state the server actually reached: bytes stored and validated, and whether
    # the upload is already associated with a chat message.
    attached = bool(message_id)
    return {'uploads': [{**item, 'attached': attached} for item in uploaded]}


@app.post('/api/v1/uploads/{upload_id}/access')
async def create_chat_file_access(upload_id: str, principal: Principal = Depends(require_scope('command'))):
    """Mint a short-lived, owner-bound URL without exposing a storage path."""
    upload = get_upload(principal.user_id, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail='Upload not found.')
    expires_at = int(time.time()) + SIGNED_ACCESS_TTL_SECONDS
    signature = signed_access_token(principal.user_id, upload_id, expires_at)
    return {
        'schema_version': 'magistrate.artifact-access.v1',
        'artifact_id': upload_id,
        'expires_at': expires_at,
        'url': f'/api/v1/uploads/{upload_id}?expires={expires_at}&signature={signature}',
    }


@app.get('/api/v1/uploads/{upload_id}')
async def download_chat_file(
    upload_id: str,
    expires: Optional[int] = Query(None),
    signature: Optional[str] = Query(None),
    principal: Principal = Depends(require_scope('command')),
):
    if not re.fullmatch(r'^[A-Za-z0-9_-]{16,64}$', upload_id):
        raise HTTPException(status_code=404, detail='Upload not found.')
    # Ordinary authenticated URLs remain compatible with persisted chat rows.
    # If a signed URL is supplied, both its owner-bound signature and the
    # current authenticated principal must validate; signatures never replace auth.
    if (expires is None) != (signature is None) or (
        expires is not None and signature is not None
        and not verify_signed_access(principal.user_id, upload_id, expires, signature)
    ):
        raise HTTPException(status_code=403, detail='Artifact access has expired.')
    upload = get_upload(principal.user_id, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail='Upload not found.')
    return FileResponse(
        upload['path'], media_type=upload['media_type'], filename=upload['filename'],
        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'},
    )


@app.delete('/api/v1/uploads/{upload_id}', status_code=204)
async def remove_chat_file(upload_id: str, principal: Principal = Depends(require_scope('command'))):
    try:
        removed = delete_upload(principal.user_id, upload_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail='Upload not found.')
    return Response(status_code=204)


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


@app.post('/api/v1/execution/route-recommendation')
async def recommend_execution_route(
    contract: ExecutionRouteRequirementsContract,
    principal: Principal = Depends(require_scope('command')),
):
    """Select a truthful launch profile without driving any harness lifecycle."""
    try:
        capabilities = get_execution_capabilities(principal.user_id)
        route = select_execution_profile(
            capabilities['profiles'],
            ExecutionRequirements(**contract.model_dump()),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='Execution capability inventory is unavailable.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        'schema_version': 'execution.route-recommendation.v1',
        'selection': route.__dict__,
        'execution_started': False,
        'consumer': 'firstmate',
    }


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


@app.post('/api/v1/fleet/objectives/{objective_id}/cancellation-requests', status_code=202)
async def request_objective_cancellation(
    objective_id: str,
    contract: ObjectiveCancellationRequest,
    principal: Principal = Depends(require_scope('command')),
):
    try:
        return await objective_cancellation_service.request(
            owner_user_id=principal.user_id,
            actor_session_id=principal.session_id,
            objective_id=objective_id,
            idempotency_key=contract.idempotency_key,
        )
    except ObjectiveCancellationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
