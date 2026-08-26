import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict

from app.contracts import VoiceImpact, VoiceMoveRequest
from app.db import record_voice_audit

PROHIBITED_PATTERN = re.compile(
    r'\b(delete|destroy|deploy|merge|push|reset|checkout|shell|terminal|sudo|chmod)\b|\brm\s+-|\|\s*(?:sh|bash)\b', re.I,
)
CONTROL_PATTERN = re.compile(r'\b(interrupt|stop|cancel)\b', re.I)
STATUS_PATTERN = re.compile(r'\b(status|fleet|list (?:the )?agents|what(?:\'s| is) (?:running|working))\b', re.I)
UNSUPPORTED_NAVIGATION_PATTERN = re.compile(
    r'\b(open|go to|navigate|show me)\b.*\b(chat|home|github|pull requests?|jira|teams|attention|terminal)\b', re.I,
)
VAGUE_PATTERN = re.compile(r'^(?:do it|go ahead|yes|okay|ok|that|this|continue)$', re.I)
SESSION_TTL_SECONDS = 15 * 60
CONFIRMATION_TTL_SECONDS = 60
RESULT_TTL_SECONDS = 30 * 60


class IdempotencyConflict(ValueError):
    pass


@dataclass
class PendingConfirmation:
    actor_id: str
    session_id: str
    idempotency_key: str
    digest: str
    fleet_digest: str
    expires_at: float


@dataclass
class StoredMove:
    actor_id: str
    session_id: str
    idempotency_key: str
    fingerprint: str
    result: Dict[str, Any]
    expires_at: float


class VoiceMoveService:
    """Server-owned Voice action resolver and policy boundary."""

    def __init__(self, herdr_client: Any):
        self.herdr = herdr_client
        self._confirmations: Dict[str, PendingConfirmation] = {}
        self._pending: Dict[tuple[str, str, str], tuple[str, Dict[str, Any], float]] = {}
        self._results: Dict[tuple[str, str, str], StoredMove] = {}
        self._moves_by_id: Dict[str, StoredMove] = {}
        self._sessions: Dict[tuple[str, str], float] = {}

    def bind_session(self, actor_id: str, session_id: str) -> None:
        now = time.time()
        self._purge(now)
        key = (actor_id, session_id)
        if key in self._sessions and self._sessions[key] + SESSION_TTL_SECONDS < now:
            self._sessions.pop(key, None)
        self._sessions[key] = now

    async def handle(self, request: VoiceMoveRequest, actor_id: str) -> Dict[str, Any]:
        now = time.time()
        self.bind_session(actor_id, request.session_id)
        key = (actor_id, request.session_id, request.idempotency_key)
        fingerprint = self._fingerprint(request)
        pending_request = self._pending.get(key)
        if pending_request:
            pending_fingerprint, pending_result, pending_expires_at = pending_request
            if pending_expires_at < now:
                self._pending.pop(key, None)
            elif pending_fingerprint != fingerprint:
                raise IdempotencyConflict('This idempotency key was already used for a different Voice move.')
            elif not request.execute:
                return pending_result
        existing = self._results.get(key)
        if existing:
            if existing.expires_at < now:
                self._results.pop(key, None)
            elif existing.fingerprint != fingerprint:
                raise IdempotencyConflict('This idempotency key was already used for a different Voice move.')
            elif not (request.execute and existing.result.get('status') == 'ready'):
                return existing.result

        agents = await self.herdr.list_agents()
        target = self._resolve_target(request.target, request.utterance, agents)
        utterance = request.utterance.strip()
        intent, impact = self._classify(utterance)
        move_id = self._move_id(actor_id, request.session_id, request.idempotency_key)
        base = {
            'schema_version': 'voice-move.v1', 'move_id': move_id, 'session_id': request.session_id,
            'intent': intent, 'action': intent, 'impact': impact.value, 'target': target,
            'arguments': {'target': target, 'utterance': utterance},
            'requires_confirmation': impact in {VoiceImpact.PROMPT, VoiceImpact.CONTROL},
            'result_url': f'/api/v1/voice/moves/{move_id}?session_id={request.session_id}',
        }

        if impact == VoiceImpact.PROHIBITED:
            return self._store(actor_id, request, fingerprint, {**base, 'status': 'prohibited',
                'error': 'Voice Mode cannot execute terminal or destructive actions.'})
        if intent == 'unsupported':
            return self._store(actor_id, request, fingerprint, {**base, 'impact': VoiceImpact.NAVIGATE.value, 'status': 'unsupported',
                'requires_confirmation': False,
                'error': 'That navigation or command is not available in standalone Voice Mode.'})

        fleet_digest = self._fleet_digest(agents)
        if not request.execute:
            if base['requires_confirmation']:
                token = secrets.token_urlsafe(24)
                confirmation = {**base, 'status': 'confirmation_required', 'confirmation_token': token,
                                'confirmation_expires_at': now + CONFIRMATION_TTL_SECONDS,
                                'confirmation_message': self._confirmation_message(intent, target, utterance)}
                self._confirmations[token] = PendingConfirmation(
                    actor_id=actor_id, session_id=request.session_id, idempotency_key=request.idempotency_key,
                    digest=self._digest(actor_id, request.session_id, request.idempotency_key, utterance, target, intent),
                    fleet_digest=fleet_digest, expires_at=now + CONFIRMATION_TTL_SECONDS,
                )
                self._pending[key] = (fingerprint, confirmation, now + CONFIRMATION_TTL_SECONDS)
                return confirmation
            return self._store(actor_id, request, fingerprint, {**base, 'status': 'ready'})

        if base['requires_confirmation']:
            pending = self._confirmations.pop(request.confirmation_token or '', None)
            expected = self._digest(actor_id, request.session_id, request.idempotency_key, utterance, target, intent)
            if (not pending or pending.actor_id != actor_id or pending.session_id != request.session_id
                    or pending.idempotency_key != request.idempotency_key or pending.expires_at < now
                    or pending.fleet_digest != fleet_digest or pending.digest != expected):
                return {**base, 'status': 'confirmation_expired',
                        'error': 'Confirmation is missing, expired, or no longer matches this live fleet.'}
            self._pending.pop(key, None)

        if intent == 'agent_status':
            result = {**base, 'status': 'completed', 'response': self._agent_summary(agents), 'result_status': 'complete'}
        elif intent == 'interrupt_agent':
            action = await self.herdr.interrupt_agent(target)
            ok = action.get('status') == 'interrupted'
            result = {**base, 'status': 'completed' if ok else 'error',
                      'response': f'Interrupted {target}.' if ok else '', 'error': action.get('error'),
                      'result_status': 'complete' if ok else 'error'}
        else:
            action = await self.herdr.prompt_agent(target, utterance)
            ok = action.get('status') == 'submitted'
            result = {**base, 'status': 'acknowledged' if ok else 'error',
                      'acknowledgement': f'Request accepted by {target}.' if ok else '',
                      'response': f'Request accepted by {target}.' if ok else '',
                      'error': action.get('error'), 'result_status': 'pending' if ok else 'error',
                      'poll_after_ms': 1500}
        return self._store(actor_id, request, fingerprint, result)

    async def get_result(self, move_id: str, actor_id: str, session_id: str) -> Dict[str, Any]:
        self.bind_session(actor_id, session_id)
        stored = self._moves_by_id.get(move_id)
        if not stored:
            for pending_key, (_, pending_result, expires_at) in self._pending.items():
                if pending_result.get('move_id') == move_id and pending_key[:2] == (actor_id, session_id) and expires_at >= time.time():
                    return pending_result
        if not stored or stored.expires_at < time.time():
            raise ValueError('Voice result was not found or has expired.')
        if stored.actor_id != actor_id or stored.session_id != session_id:
            raise ValueError('Voice result does not belong to this authenticated session.')
        # Herdr currently exposes submission acknowledgement but no authenticated
        # run/result event. Pending is deliberately not presented as completion.
        return stored.result

    async def cancel(self, move_id: str, actor_id: str, session_id: str) -> Dict[str, Any]:
        self.bind_session(actor_id, session_id)
        stored = self._moves_by_id.get(move_id)
        if not stored:
            for pending_key, (pending_fingerprint, pending_result, expires_at) in list(self._pending.items()):
                if pending_result.get('move_id') == move_id and pending_key[:2] == (actor_id, session_id) and expires_at >= time.time():
                    self._pending.pop(pending_key, None)
                    cancelled = {**pending_result, 'status': 'cancelled', 'result_status': 'cancelled',
                                 'response': 'Cancelled before the request was sent.'}
                    stored_cancelled = StoredMove(actor_id, session_id, pending_key[2], pending_fingerprint, cancelled, time.time() + RESULT_TTL_SECONDS)
                    self._results[pending_key] = stored_cancelled
                    self._moves_by_id[move_id] = stored_cancelled
                    self._audit(actor_id, session_id, pending_key[2], cancelled, pending_result.get('intent', 'unknown'))
                    return cancelled
        if not stored or stored.actor_id != actor_id or stored.session_id != session_id:
            raise ValueError('Voice move was not found for this authenticated session.')
        current = stored.result
        if current.get('status') in {'completed', 'prohibited', 'unsupported', 'cancelled', 'error'}:
            return current
        if current.get('status') == 'acknowledged':
            action = await self.herdr.interrupt_agent(str(current['target']))
            if action.get('status') != 'interrupted':
                raise ValueError(action.get('error') or 'The accepted Voice move could not be cancelled.')
        cancelled = {**current, 'status': 'cancelled', 'result_status': 'cancelled',
                     'response': f'Cancelled the request for {current["target"]}.'}
        stored.result = cancelled
        self._audit(actor_id, session_id, stored.idempotency_key, cancelled, current.get('intent', 'unknown'))
        return cancelled

    @staticmethod
    def _resolve_target(requested: str, utterance: str, agents: list[dict]) -> str:
        def matches(value: str) -> list[dict]:
            folded = value.casefold()
            return [agent for agent in agents if folded in {
                str(agent.get('id', '')).casefold(), str(agent.get('pane_id', '')).casefold(),
                str(agent.get('name', '')).casefold()
            }]

        requested_matches = matches(requested)
        if requested.casefold() in {'captain', 'firstmate', 'codex'}:
            named = [agent for agent in agents if str(agent.get('name', '')).casefold() in {'captain', 'firstmate', 'codex'}]
            requested_matches = named or [agent for agent in agents if str(agent.get('harness', '')).casefold() == 'codex']
        if len(requested_matches) > 1:
            raise ValueError(f'Target "{requested}" is ambiguous in the live fleet.')
        if not requested_matches:
            raise ValueError(f'Target "{requested}" is not present in the live fleet.')

        lowered = utterance.casefold().strip()
        spoken_candidates = []
        for agent in agents:
            name = str(agent.get('name') or agent.get('id') or '').strip()
            if not name:
                continue
            name_pattern = re.escape(name.casefold())
            if re.search(rf'(?:^|\b)(?:ask|tell|send to|for|to|interrupt|stop|check|status of)\s+{name_pattern}(?:\b|$)', lowered):
                spoken_candidates.append(agent)
            elif re.match(rf'^{name_pattern}[,:]\s*', lowered):
                spoken_candidates.append(agent)
        unique = {str(agent.get('id')): agent for agent in spoken_candidates}
        if len(unique) > 1:
            raise ValueError('The spoken target is ambiguous. Choose one live target chip.')
        if unique:
            return str(next(iter(unique.values())).get('id'))
        return str(requested_matches[0].get('id'))

    @staticmethod
    def _classify(text: str) -> tuple[str, VoiceImpact]:
        if PROHIBITED_PATTERN.search(text):
            return 'prohibited_action', VoiceImpact.PROHIBITED
        if UNSUPPORTED_NAVIGATION_PATTERN.search(text):
            return 'unsupported', VoiceImpact.READ
        if VAGUE_PATTERN.fullmatch(text):
            return 'unsupported', VoiceImpact.READ
        if CONTROL_PATTERN.search(text):
            return 'interrupt_agent', VoiceImpact.CONTROL
        if STATUS_PATTERN.search(text):
            return 'agent_status', VoiceImpact.READ
        return 'prompt_agent', VoiceImpact.PROMPT

    @staticmethod
    def _fingerprint(request: VoiceMoveRequest) -> str:
        return hashlib.sha256(f'{request.utterance}\0{request.target}\0{request.source}'.encode()).hexdigest()

    @staticmethod
    def _move_id(actor_id: str, session_id: str, key: str) -> str:
        return f'vm_{hashlib.sha256(f"{actor_id}\0{session_id}\0{key}".encode()).hexdigest()[:20]}'

    @staticmethod
    def _digest(actor_id: str, session_id: str, key: str, text: str, target: str, intent: str) -> str:
        return hashlib.sha256(f'{actor_id}\0{session_id}\0{key}\0{text}\0{target}\0{intent}'.encode()).hexdigest()

    @staticmethod
    def _fleet_digest(agents: list[dict]) -> str:
        stable = '|'.join(sorted(f'{agent.get("id")}:{agent.get("name")}:{agent.get("status")}' for agent in agents))
        return hashlib.sha256(stable.encode()).hexdigest()

    def _store(self, actor_id: str, request: VoiceMoveRequest, fingerprint: str, result: Dict[str, Any]) -> Dict[str, Any]:
        stored = StoredMove(actor_id, request.session_id, request.idempotency_key, fingerprint, result, time.time() + RESULT_TTL_SECONDS)
        self._results[(actor_id, request.session_id, request.idempotency_key)] = stored
        self._moves_by_id[result['move_id']] = stored
        self._pending.pop((actor_id, request.session_id, request.idempotency_key), None)
        self._audit(actor_id, request.session_id, request.idempotency_key, result, result.get('intent', 'unknown'))
        return result

    def _audit(self, actor_id: str, session_id: str, key: str, result: Dict[str, Any], action: str) -> None:
        record_voice_audit(
            event_id=f'{result["move_id"]}:{result.get("status")}', actor_id=actor_id,
            session_id=session_id, move_id=result['move_id'], action=action,
            target=str(result.get('target', '')), status=str(result.get('status', 'unknown')),
            utterance_digest=hashlib.sha256(key.encode()).hexdigest(),
        )

    def _purge(self, now: float) -> None:
        self._sessions = {key: value for key, value in self._sessions.items() if value + SESSION_TTL_SECONDS >= now}
        self._results = {key: value for key, value in self._results.items() if value.expires_at >= now}
        self._moves_by_id = {key: value for key, value in self._moves_by_id.items() if value.expires_at >= now}
        self._confirmations = {key: value for key, value in self._confirmations.items() if value.expires_at >= now}
        self._pending = {key: value for key, value in self._pending.items() if value[2] >= now}

    @staticmethod
    def _confirmation_message(intent: str, target: str, text: str) -> str:
        return f'Interrupt {target}?' if intent == 'interrupt_agent' else f'Send this request to {target}: “{text}”'

    @staticmethod
    def _agent_summary(agents: list[dict]) -> str:
        if not agents:
            return 'No live agents are currently available.'
        states: Dict[str, int] = {}
        for agent in agents:
            state = str(agent.get('status', 'unknown'))
            states[state] = states.get(state, 0) + 1
        detail = ', '.join(f'{count} {state}' for state, count in sorted(states.items()))
        return f'{len(agents)} live agents: {detail}.'
