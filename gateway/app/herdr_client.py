import asyncio
import json
import os
import re
import subprocess
import hashlib
from typing import Dict, Any, List, Optional

HERDR_SOCKET_PATH = os.getenv('HERDR_SOCKET_PATH', os.path.expanduser('~/.config/herdr/herdr.sock'))
HERDR_MAX_READ_LINES = 2**32 - 1
# Chat only needs enough recent scrollback to seed live-poll deduplication, not
# the full retained history - keep the default request small.
DEFAULT_HISTORY_LINES = 400

_HISTORY_MARKER = re.compile(r'^\s*([›❯•⏺●])\s+(.*)$')
_TOOL_SUMMARY = re.compile(
    r'^(?:Ran\b|Called\b|Explored\b|Searched\b|Read\b|Viewed\b|Edited\b|Added\b|Updated\b|Wrote\b|Applied\b|Waited\b|Interacted\b|Deleted\b|Removed\b|Created\b|Listed\b|Fetched\b|Downloaded\b'
    r'|Background command\b|Pushed\b|Committed\b|SessionStart\b'
    r'|(?:Running|Calling|Reading|Writing|Editing|Exploring|Fetching)\s+\d+\b|Searching for \d+\b'
    r'|(?:Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\s*\()',
    re.IGNORECASE,
)
_TRANSIENT_SUMMARY = re.compile(
    r'^(?:Working\s*\(|You have \d+ usage|Session renamed\b|Stop hook feedback\b|Tip:'
    r'|(?:low|medium|high|xhigh|max|ultra)\s+·\s+/)',
    re.IGNORECASE,
)
_TRANSIENT_USER_TEXT = {'Ask Codex to do anything', 'Ask Claude anything', 'Skipping dev server'}
_GENERIC_AGENT_NAMES = {'magistrate', 'firstmate', 'π - magistrate', 'π - firstmate'}


def _human_agent_name(value: Any, identifiers: set[str]) -> Optional[str]:
    """Keep only a real Herdr pane name; IDs and harness titles are not names."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    normalized = candidate.lower()
    if not candidate or normalized in _GENERIC_AGENT_NAMES or normalized in identifiers:
        return None
    if re.match(r'^(?:pane|tab|workspace)(?:[_ -]?id)?\s*[:=]', candidate, re.IGNORECASE):
        return None
    return candidate
_ROUTING_PREFIX = re.compile(r'^\[Magistrate execution:[^\]]+\]\s*', re.IGNORECASE)
_MARKERLESS_TOOL = re.compile(
    r'^(?:\$\s|⎿\s|Ran\b|Called\b|Explored\b|Searched\b|Read\b|Viewed\b|Edited\b|Added\b|Updated\b|Wrote\b|Applied\b|Waited\b|Interacted\b|Deleted\b|Removed\b|Created\b|Listed\b|Fetched\b|Downloaded\b|Background command\b|Pushed\b|Committed\b|SessionStart\b|(?:Running|Calling|Reading|Writing|Editing|Exploring|Fetching)\s+\d+\b|Searching for \d+\b|(?:Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\s*\()',
    re.IGNORECASE,
)
_LINE_BREAK = re.compile(r'^(?:[-*•‣]|\d+[.)]\s|#)')
_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
_ANSI_BACKGROUND = re.compile(r'\x1b\[48;5;(\d+)m')
_ANSI_ITALIC = re.compile(r'\x1b\[(?:[^m;]*;)*3(?:;[^m]*)?m')
# Footer/status overlays herdr captures mid-frame; they can land on an indented
# row directly under a message, so they are dropped wherever they appear.
_TERMINAL_CHROME = re.compile(
    r'\(ctrl\+(?:End|Home)\)|Jump to bottom|Update installed · Restart|⏵⏵|⏸\s|auto mode on ·|esc to interrupt|ctrl\+\w+ to |of your (?:usage|limit)\b',
    re.IGNORECASE,
)
_HARNESS_ARTIFACT = re.compile(
    r'^(?:FIRSTMATE_OP\b|WAKE_(?:ACK|DRAIN|REQUIRED)\b|Planning\b|Clarifying\b|Initiating\b|Inspecting\b|Identifying\b|Verifying\b|Queuing\b|Error:\s|Took \d|Command exited with code\b|\(no output\)|\.\.\. \(\d+ earlier lines|help\[\d+\]:|/calm\b|calm(?:ing)?(?: animation| status)?\b|edit\s*$'
    r'|(?:pane|tab|workspace)(?:_id)?\s*[:=]|(?:(?:model|provider|harness)|(?:thread|session|trace|run|request|conversation|message)[ _-]?id)\s*[:=]|your usage\b'
    r'|window=\S+|worktree=/|~/\S+ \(|[⠀-⣿]|[↑↓]\S.*\bCH\d|─{3,})',
    re.IGNORECASE,
)
# Firstmate drives worker panes by typing control plumbing into them: wake
# notices, launch briefs, inbox instructions, status-file directions. Those are
# lifecycle records, not the conversational prompt a worker thread shows.
# A harness overlay - settings, usage, permissions - takes over the screen and
# renders its panel chrome inside the same background boxes Pi uses for user
# turns: tab bars, block-drawing meters, and gauge rows. None is conversation.
_PANEL_CHROME = re.compile(r'^(?:[\u2500-\u259F\u2588\s]{3,}|(?:[A-Z][A-Za-z]{1,14}[ \t]{2,}){2,}[A-Z][A-Za-z]{1,14}|\d{1,3}%[ \t](?:used|remaining|left)\b.*)$')
_SYSTEM_NOTICE = re.compile(
    r'^(?:⛵\s+[^:]+:|Run bin/fm-wake-drain\.sh\b|Watcher continuity is extension-owned\b'
    r'|Firstmate (?:instruction|steers|inbox|launch)\b|FIRSTMATE_(?:OP|WAKE)\b|Report status by appending\b|v\d+ launch-brief:)',
    re.IGNORECASE,
)
# A Pi/Codex tool envelope is a header naming a real path plus the numbered
# excerpt or diff it produced. Both halves are required: a captain may
# legitimately write "read the release notes" or even "read src/main.py", and
# those must stay captain turns. Mirrors isToolEnvelope in
# frontend/src/services/ChatHistory.ts.
_TOOL_ENVELOPE_HEADER = re.compile(
    r'^(?:edit|read|write|create|delete|remove|move|copy|rename|apply[_ ]?patch|patch|cat|head|tail|sed|awk|grep|rg|find|ls|glob|open|view|diff|touch|mkdir)'
    r'\b[ \t]+(?:[\w.@~-]*/[\w.@/-]*|[\w.@-]+\.[A-Za-z0-9]{1,8})(:\d+(?:[-:]\d+)?)?[ \t]*$',
    re.IGNORECASE,
)
_NUMBERED_EXCERPT = re.compile(r'(?:^|\n)[ \t]*(?:\.{3}[ \t]*)?[+\-]?[ \t]*\d{1,6}[ \t]{1,12}\S')
_DIFF_EXCERPT = re.compile(r'(?:^|\n)[ \t]*(?:@@[ \t]|[+\-]{3}[ \t]|[+\-][ \t]*\d+[ \t])')
_RAW_TERMINAL = re.compile(r'^(?:\$\s|⎿\s|(?:bash|read|edit|write|glob|grep|task|websearch|webfetch)\s*\(|[^\s@]+@[^\s:]+:[^\n]*[$#]\s)', re.IGNORECASE)


def unwrap_terminal_text(text: str) -> str:
    """Rejoin prose hard-wrapped at the terminal width, keeping list items and paragraph breaks."""
    blocks: List[str] = []
    for line in text.split('\n'):
        trimmed = line.strip()
        if not trimmed:
            blocks.append('')
            continue
        if blocks and blocks[-1] and not _LINE_BREAK.match(trimmed):
            blocks[-1] += trimmed if blocks[-1][-1] in '-/' else ' ' + trimmed
        else:
            blocks.append(trimmed)
    return re.sub(r'\n{2,}', '\n\n', '\n'.join(blocks)).strip()


def _is_harness_artifact(text: str) -> bool:
    value = _ANSI.sub('', text).strip()
    if not value or _TERMINAL_CHROME.search(value) or _TRANSIENT_SUMMARY.match(value) or _HARNESS_ARTIFACT.match(value) or _SYSTEM_NOTICE.match(value) or _PANEL_CHROME.match(value) or _ROUTING_PREFIX.match(value):
        return True
    try:
        envelope = json.loads(value)
    except json.JSONDecodeError:
        return False
    return isinstance(envelope, dict) and any(key in envelope for key in ('jsonrpc', 'result', 'status', 'target', 'event', 'pane_id', 'tab_id', 'workspace_id'))


def _is_tool_envelope(text: str) -> bool:
    """A harness tool envelope, which Pi boxes exactly like a user turn."""
    value = _ANSI.sub('', text).lstrip()
    header, _, body = value.partition('\n')
    match = _TOOL_ENVELOPE_HEADER.match(header.strip())
    if not match:
        return False
    return bool(match.group(1)) or bool(_NUMBERED_EXCERPT.search(body)) or bool(_DIFF_EXCERPT.search(body))


def _parse_pi_ansi_history(output: str) -> List[Dict[str, str]]:
    """Use Pi's ANSI background boxes as the role boundary stripped by text mode."""
    messages: List[Dict[str, str]] = []
    lines = output.replace('\r', '').split('\n')
    assistant_lines: List[str] = []

    def finish_assistant() -> None:
        nonlocal assistant_lines
        text = unwrap_terminal_text('\n'.join(assistant_lines))
        if text and not _is_harness_artifact(text) and not _RAW_TERMINAL.match(text):
            messages.append({'role': 'assistant', 'kind': 'conversation', 'text': text})
        assistant_lines = []

    index = 0
    while index < len(lines):
        backgrounds = _ANSI_BACKGROUND.findall(lines[index])
        if backgrounds:
            finish_assistant()
            background = backgrounds[-1]
            run: List[str] = []
            while index < len(lines):
                matches = _ANSI_BACKGROUND.findall(lines[index])
                if not matches or matches[-1] != background:
                    break
                run.append(_ANSI.sub('', lines[index]).rstrip())
                index += 1
            text = unwrap_terminal_text('\n'.join(run))
            if not text or _is_harness_artifact(text):
                continue
            if _MARKERLESS_TOOL.match(text) or _is_tool_envelope(text) or re.match(r'^(?:(?:edit|read|write|bash|grep|find|ls)\s*\n|[+\- ]\s*\d+\s|@@\s)', text, re.IGNORECASE):
                messages.append({'role': 'assistant', 'kind': 'tool', 'text': text})
            # A background box is the only role signal Pi gives, and it wraps
            # tool envelopes and harness chrome as well as user turns. An
            # unrecognised box fails closed rather than being promoted to a
            # user message just because it lacks a recognised agent role.
            elif not _RAW_TERMINAL.match(text):
                messages.append({'role': 'user', 'kind': 'conversation', 'text': _ROUTING_PREFIX.sub('', text).strip()})
            continue
        raw_line = lines[index]
        index += 1
        text = _ANSI.sub('', raw_line).rstrip()
        if _ANSI_ITALIC.search(raw_line):
            finish_assistant()
            continue
        if not text.strip():
            if assistant_lines and assistant_lines[-1] != '':
                assistant_lines.append('')
            continue
        if _is_harness_artifact(text):
            finish_assistant()
            continue
        assistant_lines.append(text)
    finish_assistant()
    return messages


def parse_agent_history(output: str) -> List[Dict[str, str]]:
    """Turn a Herdr terminal snapshot into typed conversation entries.

    Codex and Claude expose stable text markers. Pi exposes role boundaries only
    through ANSI background boxes, so markerless plain text deliberately fails
    closed instead of leaking tools, reasoning, and terminal chrome as prose.
    """
    plain_output = _ANSI.sub('', output)
    if not any(_HISTORY_MARKER.match(line) for line in plain_output.splitlines()):
        return _parse_pi_ansi_history(output) if '\x1b[' in output else []
    output = plain_output
    messages: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None

    def finish() -> None:
        nonlocal current
        if not current:
            return
        text = unwrap_terminal_text(current['text']) if current['kind'] == 'conversation' else current['text'].strip()
        if not text or text in _TRANSIENT_USER_TEXT or _is_harness_artifact(text):
            current = None
            return
        # Firstmate injects worker prompts through the same '\u203a' marker the
        # captain uses, and a harness tool envelope can occupy a marked row.
        if current['kind'] == 'conversation' and _is_tool_envelope(text):
            messages.append({'role': 'assistant', 'kind': 'tool', 'text': text})
        elif current['kind'] == 'tool' or not _RAW_TERMINAL.match(text):
            messages.append({**current, 'text': text})
        current = None

    previous_blank = True
    # Whether the last non-blank row was a '───' rule. Claude frames its composer
    # with those rules, so a marked row right after one is unsubmitted input, not
    # a turn - and a snapshot catches it mid-keystroke, which would otherwise mint
    # a fresh 'user message' for every character typed.
    previous_rule = False
    # Offset in current['text'] where the last blank-line-separated unmarked row
    # begins: a later '⎿' detail row proves that row was tool activity, not prose.
    split_at: Optional[int] = None
    for raw_line in output.replace('\r', '').splitlines():
        was_blank, previous_blank = previous_blank, not raw_line.strip()
        after_rule = previous_rule
        if raw_line.strip():
            previous_rule = bool(re.match(r'^─{3,}', raw_line.strip()))
        marker = _HISTORY_MARKER.match(raw_line)
        if marker:
            finish()
            split_at = None
            glyph, text = marker.groups()
            text = _ROUTING_PREFIX.sub('', text).strip()
            if glyph in ('›', '❯'):
                current = None if after_rule else {'role': 'user', 'kind': 'conversation', 'text': text}
            else:
                if _TRANSIENT_SUMMARY.match(text.strip()) or _TERMINAL_CHROME.search(text):
                    current = None
                    continue
                kind = 'tool' if _TOOL_SUMMARY.match(text.strip()) else 'conversation'
                current = {'role': 'assistant', 'kind': kind, 'text': text}
            continue
        stripped = raw_line.strip()
        # Claude's harness prints tool activity as unmarked rows set off by a
        # blank line rather than a response marker. Without this they would be
        # folded into the conversational message above them and leak into chat.
        if was_blank and stripped and _TOOL_SUMMARY.match(stripped):
            finish()
            current = {'role': 'assistant', 'kind': 'tool', 'text': stripped}
            split_at = None
            continue
        if current is None:
            continue
        if stripped.startswith('───') or _TERMINAL_CHROME.search(stripped) or re.match(r'^[^\s]+\s+(?:low|medium|high|xhigh|max|ultra)\s+·\s+', stripped):
            finish()
            continue
        if was_blank and _TRANSIENT_SUMMARY.match(stripped):
            finish()
            continue
        # A '⎿' detail row is the harness's unambiguous tool-output marker, so it
        # retypes the row above it even when that row's verb is not recognised.
        if stripped.startswith('⎿'):
            if current['kind'] == 'conversation' and split_at is not None:
                tail = current['text'][split_at:].strip()
                current['text'] = current['text'][:split_at]
                finish()
                current = {'role': 'assistant', 'kind': 'tool', 'text': tail}
            else:
                current['kind'] = 'tool'
            split_at = None
        # Wrapped transcript rows are indented. Empty rows preserve paragraphs;
        # unindented terminal chrome ends the current entry.
        if not raw_line or raw_line[:1].isspace():
            if was_blank and stripped and current['kind'] == 'conversation':
                split_at = len(current['text']) + 1
            current['text'] += '\n' + stripped
        else:
            finish()
    finish()
    return messages


def _prompt_response(output: str) -> Optional[str]:
    """Extract a real synchronous reply, never transport JSON or tool output."""
    response = output.strip()
    if not response:
        return None
    try:
        envelope = json.loads(response)
    except json.JSONDecodeError:
        # A harness may print a real plain-text reply, but never return a
        # command summary/status overlay as if it were conversation.
        if any(_MARKERLESS_TOOL.match(line.strip()) or _TERMINAL_CHROME.search(line.strip())
               for line in response.splitlines()):
            parsed = parse_agent_history(response)
            return next((message['text'] for message in parsed if message['kind'] == 'conversation'), None)
        return response
    if not isinstance(envelope, dict):
        return None
    # Herdr normally returns a JSON-RPC acknowledgement. It is not a reply,
    # but preserve an explicitly supplied response/text for verified harnesses.
    if 'result' in envelope or 'jsonrpc' in envelope:
        result = envelope.get('result')
        if isinstance(result, dict):
            for key in ('response', 'text'):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None
    for key in ('response', 'text'):
        value = envelope.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _run_cli(*args: str) -> tuple[bytes, bytes, int]:
    """Run a Herdr CLI command, tolerating an unavailable local runtime."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return b'', b'Herdr is unavailable.', 127
    stdout, stderr = await proc.communicate()
    return stdout or b'', stderr or b'', proc.returncode if proc.returncode is not None else 1


class HerdrClient:
    def __init__(self, socket_path: str = HERDR_SOCKET_PATH):
        self.socket_path = socket_path

    async def send_rpc_request(self, method: str, params: Optional[Dict[str, Any]] = None, req_id: str = 'magistrate:rpc') -> Dict[str, Any]:
        payload = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params or {}}
        if not os.path.exists(self.socket_path):
            return await self._cli_rpc_fallback(method, params)

        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            writer.write(json.dumps(payload).encode('utf-8') + bytes([10]))
            await writer.drain()

            line = await reader.readline()
            writer.close()
            await writer.wait_closed()

            if not line:
                return {}
            return json.loads(line.decode('utf-8'))
        except Exception:
            return await self._cli_rpc_fallback(method, params)

    async def _cli_rpc_fallback(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        stdout, stderr, returncode = await _run_cli('herdr', 'api', 'snapshot')
        if returncode == 0 and stdout:
            try:
                return json.loads(stdout.decode('utf-8'))
            except json.JSONDecodeError:
                pass
        return {'error': {'message': stderr.decode('utf-8') if stderr else 'CLI fallback failed'}}

    async def get_snapshot(self) -> Dict[str, Any]:
        res = await self.send_rpc_request('session.snapshot')
        if 'result' in res and 'snapshot' in res['result']:
            return res['result']['snapshot']
        
        stdout, _, _ = await _run_cli('herdr', 'api', 'snapshot')
        if stdout:
            try:
                data = json.loads(stdout.decode('utf-8'))
                if 'result' in data and 'snapshot' in data['result']:
                    return data['result']['snapshot']
            except json.JSONDecodeError:
                pass
        return {'agents': [], 'workspaces': [], 'tabs': [], 'panes': [], 'version': '0.8.2'}

    async def list_agents(self) -> List[Dict[str, Any]]:
        snapshot = await self.get_snapshot()
        agents = snapshot.get('agents', [])
        formatted_agents = []
        for ag in agents:
            raw_status = ag.get('agent_status')
            status = raw_status.get('state') if isinstance(raw_status, dict) else raw_status
            status = status or 'unknown'
            agent_id = ag.get('pane_id') or ag.get('id') or ag.get('name')
            if not agent_id:
                continue
            identifiers = {str(value).strip().lower() for value in (agent_id, ag.get('pane_id'), ag.get('tab_id'), ag.get('workspace_id')) if value}
            # Herdr can expose both the configured pane name and a terminal
            # title. Prefer the configured name, then other human-readable
            # labels. Never turn an ID or generic harness title into identity.
            display_name = None
            for candidate in (ag.get('name'), ag.get('label'), ag.get('terminal_title_stripped'), ag.get('terminal_title')):
                display_name = _human_agent_name(candidate, identifiers)
                if display_name:
                    break
            formatted_agents.append({
                'id': agent_id,
                'name': display_name,
                'harness': ag.get('agent') or ag.get('harness'),
                'status': status,
                'pane_id': ag.get('pane_id'),
                'tab_id': ag.get('tab_id'),
                'workspace_id': ag.get('workspace_id')
            })
        return formatted_agents

    async def resolve_target(self, target: str) -> str:
        if target in ('captain', 'codex', 'firstmate'):
            agents = await self.list_agents()
            if agents:
                # Firstmate currently names Pi panes "π - firstmate" / "π -
                # Magistrate". Prefer an explicit captain name, then a known
                # verified harness, rather than silently selecting the first pane.
                # Do not silently route a Pi captain to an unrelated first pane
                # when another harness is present.
                for ag in agents:
                    name = (ag.get('name') or '').strip().lower()
                    if name in ('captain', 'codex', 'firstmate') or name.endswith(' - firstmate'):
                        return ag.get('pane_id') or ag.get('id')
                for ag in agents:
                    if ag.get('harness') in ('codex', 'pi'):
                        return ag.get('pane_id') or ag.get('id')
                return agents[0].get('pane_id') or agents[0].get('id')
        return target

    async def prompt_agent(
        self, target: str, text: str, harness: Optional[str] = None,
        model: Optional[str] = None, profile_id: Optional[str] = None,
        provider: Optional[str] = None, variant: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        submitted_text = text
        if harness and model:
            # Herdr's prompt command has no launch-time selection flags. Carry
            # the validated selection to Firstmate as structured prompt context
            # while leaving its full-permissions launch behavior unchanged.
            context = f'harness={harness}; model={model}'
            if provider:
                context += f'; provider={provider}'
            if variant:
                context += f'; variant={variant}'
            if profile_id:
                context += f'; profile={profile_id}'
            submitted_text = f'[Magistrate execution: {context}]\n{text}'
        cmd = ['herdr', 'agent', 'prompt', resolved_target, submitted_text]
        stdout, stderr, returncode = await _run_cli(*cmd)
        output_str = stdout.decode('utf-8') if stdout else ''
        err_str = stderr.decode('utf-8') if stderr else ''

        if returncode == 0:
            # `herdr agent prompt` normally prints an RPC acknowledgement. It
            # is transport metadata, never an assistant message. Verified
            # harnesses may provide an explicit response/text field, which is
            # safe to return synchronously; all other replies arrive via the
            # history poll and are parsed by parse_agent_history().
            response = _prompt_response(output_str)
            return {
                'status': 'submitted', 'target': resolved_target, 'response': response,
                'harness': harness, 'provider': provider, 'model': model, 'variant': variant,
                'profile_id': profile_id,
                'routing': {'selection_supported': True, 'migration_supported': False, 'mode': 'prompt-context'},
            }
        else:
            return {
                'status': 'error', 'target': resolved_target, 'error': err_str.strip() or output_str.strip() or 'Herdr is unavailable.',
                'harness': harness, 'provider': provider, 'model': model, 'variant': variant,
                'profile_id': profile_id,
                'routing': {'selection_supported': True, 'migration_supported': False, 'mode': 'prompt-context'},
            }

    async def read_agent_output(self, target: str, lines: int = HERDR_MAX_READ_LINES, source: str = 'recent-unwrapped', output_format: str = 'text') -> str:
        resolved_target = await self.resolve_target(target)
        # Herdr's API models lines as an unsigned 32-bit value. Asking for its
        # maximum returns every row still retained by the configured byte-sized
        # scrollback buffer without imposing a second, arbitrary gateway cap.
        lines = min(max(lines, 0), HERDR_MAX_READ_LINES)
        cmd = [
            'herdr', 'agent', 'read', resolved_target,
            '--source', source, '--lines', str(lines), '--format', output_format
        ]
        stdout, _, returncode = await _run_cli(*cmd)
        if returncode != 0 and source != 'visible':
            # Active alternate-screen agents cannot expose scrollback while
            # working. Their visible viewport still contains the latest live
            # conversation, so return that instead of an empty history.
            return await self.read_agent_output(resolved_target, lines=lines, source='visible', output_format=output_format)
        return stdout.decode('utf-8', errors='replace') if stdout else ''

    async def get_agent_history(
        self, target: str, lines: int = DEFAULT_HISTORY_LINES,
        before: Optional[str] = None, after: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        output = await self.read_agent_output(resolved_target, lines=lines, output_format='ansi')
        messages = parse_agent_history(output)
        # Herdr exposes terminal text rather than durable message IDs. Hashing
        # normalized content gives clients stable cursors within retained
        # scrollback while keeping the public response independent of ANSI rows.
        entries = []
        occurrences: dict[str, int] = {}
        for message in messages:
            key = f"{message.get('role')}|{message.get('kind')}|{message.get('text')}"
            # Terminal snapshots do not expose durable message ids. Include
            # the occurrence number so two legitimate identical turns remain
            # distinct while WS/poll/history reads converge on the same id.
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            entries.append({**message, 'id': hashlib.sha256(f'{key}|{occurrence}'.encode()).hexdigest()[:20]})
        start = 0
        end = len(entries)
        ids = [entry['id'] for entry in entries]
        if before:
            if before not in ids:
                raise ValueError('The history cursor is no longer available.')
            end = ids.index(before)
            start = max(0, end - 200)
        elif after:
            if after not in ids:
                raise ValueError('The history cursor is no longer available.')
            start = ids.index(after) + 1
        page = entries[start:end]
        return {
            'target': resolved_target, 'messages': page,
            'next_before': page[0]['id'] if start > 0 and page else None,
            'next_after': page[-1]['id'] if end < len(entries) and page else None,
            'has_more_before': start > 0, 'has_more_after': end < len(entries),
        }

    async def rename_agent(self, target: str, name: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        stdout, stderr, returncode = await _run_cli('herdr', 'agent', 'rename', resolved_target, name)
        if returncode == 0:
            return {'status': 'renamed', 'target': resolved_target, 'name': name}
        return {
            'status': 'error',
            'target': resolved_target,
            'error': (stderr or stdout).decode('utf-8', errors='replace').strip() or 'Herdr could not rename the agent.',
        }

    async def interrupt_agent(self, target: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'C-c']
        stdout, stderr, returncode = await _run_cli(*cmd)
        if returncode == 0:
            return {'status': 'interrupted', 'target': resolved_target}
        else:
            return {'status': 'error', 'target': resolved_target, 'error': stderr.decode('utf-8').strip()}

    async def send_agent_key(self, target: str, key: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        if key in ('Enter', 'Return', 'ENTER'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Enter']
        elif key in ('Escape', 'Esc', 'ESC'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Escape']
        elif key in ('C-c', 'Ctrl+C', 'CTRL+C'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'C-c']
        elif key in ('Up', 'UP', '↑'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Up']
        elif key in ('Down', 'DOWN', '↓'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Down']
        elif key in ('Tab', 'TAB'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Tab']
        elif key in ('y', 'Y', 'YES'):
            cmd = ['herdr', 'agent', 'prompt', resolved_target, 'y']
        elif key in ('n', 'N', 'NO'):
            cmd = ['herdr', 'agent', 'prompt', resolved_target, 'n']
        else:
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, key]

        stdout, stderr, returncode = await _run_cli(*cmd)
        return {
            'status': 'submitted',
            'target': resolved_target,
            'key': key,
            'response': stdout.decode('utf-8').strip(),
            'error': stderr.decode('utf-8').strip() if returncode != 0 else None
        }
