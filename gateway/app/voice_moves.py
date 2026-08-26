import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict

from app.contracts import VoiceImpact, VoiceMoveRequest

PROHIBITED_PATTERN = re.compile(
    r'\b(delete|destroy|deploy|merge|push|reset|checkout|shell|terminal|sudo|chmod)\b|\brm\s+-|\|\s*(?:sh|bash)\b',
    re.I,
)
CONTROL_PATTERN = re.compile(r'\b(interrupt|stop|cancel)\b', re.I)
STATUS_PATTERN = re.compile(r'\b(status|fleet|list (?:the )?agents|what(?:\'s| is) (?:running|working))\b', re.I)

@dataclass
class PendingConfirmation:
    digest: str
    expires_at: float

class VoiceMoveService:
    def __init__(self, herdr_client: Any):
        self.herdr = herdr_client
        self._confirmations: Dict[str, PendingConfirmation] = {}
        self._results: Dict[str, Dict[str, Any]] = {}

    async def handle(self, request: VoiceMoveRequest) -> Dict[str, Any]:
        now = time.time()
        self._confirmations = {token: pending for token, pending in self._confirmations.items()
                               if pending.expires_at >= now}
        if request.idempotency_key in self._results:
            return self._results[request.idempotency_key]
        agents = await self.herdr.list_agents()
        target = self._resolve_target(request.target, agents)
        utterance = request.utterance.strip()
        intent, impact = self._classify(utterance)
        move_id = f'vm_{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]}'
        base = {'schema_version': 'voice-move.v1', 'move_id': move_id, 'utterance': utterance,
                'intent': intent, 'impact': impact.value, 'target': target,
                'requires_confirmation': impact in {VoiceImpact.PROMPT, VoiceImpact.CONTROL}}
        if impact == VoiceImpact.PROHIBITED:
            return {**base, 'status': 'prohibited',
                    'error': 'Voice Mode cannot execute terminal or destructive actions.'}
        if not request.execute:
            if base['requires_confirmation']:
                token = secrets.token_urlsafe(24)
                self._confirmations[token] = PendingConfirmation(
                    self._digest(utterance, target, intent), time.time() + 60)
                return {**base, 'status': 'confirmation_required', 'confirmation_token': token,
                        'confirmation_message': self._confirmation_message(intent, target, utterance)}
            return {**base, 'status': 'ready'}
        if base['requires_confirmation']:
            pending = self._confirmations.pop(request.confirmation_token or '', None)
            if not pending or pending.expires_at < time.time() or pending.digest != self._digest(utterance, target, intent):
                return {**base, 'status': 'confirmation_expired',
                        'error': 'Confirmation is missing, expired, or does not match this move.'}
        if intent == 'agent_status':
            result = {**base, 'status': 'completed', 'response': self._agent_summary(agents)}
        elif intent == 'interrupt_agent':
            action = await self.herdr.interrupt_agent(target)
            ok = action.get('status') == 'interrupted'
            result = {**base, 'status': 'completed' if ok else 'error',
                      'response': f'Interrupted {target}.' if ok else '', 'error': action.get('error')}
        else:
            action = await self.herdr.prompt_agent(target, utterance)
            ok = action.get('status') == 'submitted'
            result = {**base, 'status': 'completed' if ok else 'error',
                      'response': action.get('response') or (f'Request submitted to {target}.' if ok else ''),
                      'error': action.get('error')}
        self._results[request.idempotency_key] = result
        return result

    @staticmethod
    def _resolve_target(requested: str, agents: list[dict]) -> str:
        aliases = {'captain', 'firstmate', 'codex'}
        if requested.lower() in aliases:
            for agent in agents:
                if agent.get('name', '').lower() in aliases:
                    return str(agent['id'])
            for agent in agents:
                if agent.get('harness', '').lower() == 'codex':
                    return str(agent['id'])
            raise ValueError('Firstmate is not present in the live fleet.')
        for agent in agents:
            if requested in {str(agent.get('id')), str(agent.get('pane_id'))}:
                return str(agent.get('id'))
        raise ValueError(f'Target "{requested}" is not present in the live fleet.')

    @staticmethod
    def _classify(text: str) -> tuple[str, VoiceImpact]:
        if PROHIBITED_PATTERN.search(text): return 'prohibited_action', VoiceImpact.PROHIBITED
        if CONTROL_PATTERN.search(text): return 'interrupt_agent', VoiceImpact.CONTROL
        if STATUS_PATTERN.search(text): return 'agent_status', VoiceImpact.READ
        return 'prompt_agent', VoiceImpact.PROMPT

    @staticmethod
    def _digest(text: str, target: str, intent: str) -> str:
        return hashlib.sha256(f'{text}\0{target}\0{intent}'.encode()).hexdigest()

    @staticmethod
    def _confirmation_message(intent: str, target: str, text: str) -> str:
        return f'Interrupt {target}?' if intent == 'interrupt_agent' else f'Send this request to {target}: “{text}”'

    @staticmethod
    def _agent_summary(agents: list[dict]) -> str:
        if not agents: return 'No live agents are currently available.'
        states: Dict[str, int] = {}
        for agent in agents:
            state = str(agent.get('status', 'unknown'))
            states[state] = states.get(state, 0) + 1
        detail = ', '.join(f'{count} {state}' for state, count in sorted(states.items()))
        return f'{len(agents)} live agents: {detail}.'
