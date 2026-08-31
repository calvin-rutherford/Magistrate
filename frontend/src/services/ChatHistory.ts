export type AgentHistoryMessage = {
  role: 'user' | 'assistant';
  kind: 'conversation' | 'tool';
  text: string;
};

const markerPattern = /^\s*([›❯•⏺●])\s+(.*)$/;
// Gerunds are deliberately count-qualified: bare "Running ..." also opens prose.
const toolPattern = /^(?:Ran\b|Called\b|Explored\b|Searched\b|Read\b|Viewed\b|Edited\b|Added\b|Updated\b|Wrote\b|Applied\b|Waited\b|Interacted\b|Deleted\b|Removed\b|Created\b|Listed\b|Fetched\b|Downloaded\b|Background command\b|Pushed\b|Committed\b|SessionStart\b|(?:Running|Calling|Reading|Writing|Editing|Exploring|Fetching)\s+\d+\b|Searching for \d+\b|(?:Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\s*\()/i;
const transientPattern = /^(?:Working\s*\(|You have \d+ usage|Session renamed\b|Stop hook feedback\b|Tip:|(?:low|medium|high|xhigh|max|ultra)\s+·\s+\/)/i;
const routingPrefix = /^\[Magistrate execution:[^\]]+\]\s*/i;
const markerlessToolPattern = /^(?:\$\s|⎿\s|Ran\b|Called\b|Explored\b|Searched\b|Read\b|Viewed\b|Edited\b|Added\b|Updated\b|Wrote\b|Applied\b|Waited\b|Interacted\b|Deleted\b|Removed\b|Created\b|Listed\b|Fetched\b|Downloaded\b|Background command\b|Pushed\b|Committed\b|SessionStart\b|(?:Running|Calling|Reading|Writing|Editing|Exploring|Fetching)\s+\d+\b|Searching for \d+\b|(?:Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\s*\()/i;
const lineBreakPattern = /^(?:[-*•‣]|\d+[.)]\s|#)/;
const ansiPattern = /\x1b(?:\[[0-?]*[ -\/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))/g;
// Footer/status overlays Herdr catches mid-frame; they can overwrite a message row.
const chromePattern = /\(ctrl\+(?:End|Home)\)|Jump to bottom|Update installed · Restart|⏵⏵|⏸\s|auto mode on ·|esc to interrupt|ctrl\+\w+ to /i;
const harnessArtifactPattern = /^(?:FIRSTMATE_OP\b|WAKE_(?:ACK|DRAIN|REQUIRED)\b|Planning\b|Clarifying\b|Initiating\b|Inspecting\b|Identifying\b|Verifying\b|Queuing\b|Error:\s|Took \d|Command exited with code\b|\(no output\)|\.\.\. \(\d+ earlier lines|help\[\d+\]:|\/calm\b|calm(?:ing)?(?: animation| status)?\b|edit\s*$|(?:pane|tab|workspace)(?:_id)?\s*[:=]|window=[^\s]+|worktree=\/|~\/[^\s]+ \([^)]*\)\s*$|[↑↓]\S.*\bCH\d|─{3,})/i;
const systemNoticePattern = /^(?:⛵\s+[^:]+:|Run bin\/fm-wake-drain\.sh\b|Watcher continuity is extension-owned\b)/i;

const stripAnsi = (text: string) => text.replace(ansiPattern, '');

// Herdr snapshots hard-wrap prose at the terminal width; rejoin those lines so
// messages reflow in chat bubbles, keeping list items and paragraph breaks.
export function unwrapTerminalText(text: string): string {
  const blocks: string[] = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) { blocks.push(''); continue; }
    const previous = blocks[blocks.length - 1];
    if (previous && !lineBreakPattern.test(trimmed)) blocks[blocks.length - 1] += /[-/]$/.test(previous) ? trimmed : ` ${trimmed}`;
    else blocks.push(trimmed);
  }
  return blocks.join('\n').replace(/\n{2,}/g, '\n\n').trim();
}

export function isHarnessArtifact(text: string): boolean {
  const value = stripAnsi(text).trim();
  if (!value) return true;
  if (chromePattern.test(value) || transientPattern.test(value) || harnessArtifactPattern.test(value) || systemNoticePattern.test(value) || routingPrefix.test(value)) return true;
  try {
    const envelope = JSON.parse(value) as unknown;
    if (envelope && typeof envelope === 'object' && !Array.isArray(envelope)) {
      const keys = Object.keys(envelope as Record<string, unknown>);
      if (keys.some(key => ['jsonrpc', 'result', 'status', 'target', 'event', 'pane_id', 'tab_id', 'workspace_id'].includes(key))) return true;
    }
  } catch { /* conversational prose */ }
  return false;
}

function parseMarkedHistory(output: string): AgentHistoryMessage[] {
  const messages: AgentHistoryMessage[] = [];
  let current: AgentHistoryMessage | null = null;
  const finish = () => {
    if (!current) return;
    const message = current as AgentHistoryMessage;
    const text = message.kind === 'conversation' ? unwrapTerminalText(message.text) : message.text.trim();
    if (text && text !== 'Ask Codex to do anything' && text !== 'Ask Claude anything' && !isHarnessArtifact(text)) messages.push({ ...message, text });
    current = null;
  };
  let previousBlank = true;
  let splitAt: number | null = null;
  for (const rawLine of output.replace(/\r/g, '').split('\n')) {
    const wasBlank = previousBlank;
    previousBlank = !rawLine.trim();
    const marker = rawLine.match(markerPattern);
    if (marker) {
      finish(); splitAt = null;
      const [, glyph, rawText] = marker;
      const text = rawText.replace(routingPrefix, '').trim();
      if (glyph === '›' || glyph === '❯') current = { role: 'user', kind: 'conversation', text };
      else if (!transientPattern.test(text) && !chromePattern.test(text)) current = { role: 'assistant', kind: toolPattern.test(text) ? 'tool' : 'conversation', text };
      continue;
    }
    const stripped = rawLine.trim();
    if (wasBlank && stripped && toolPattern.test(stripped)) {
      finish(); current = { role: 'assistant', kind: 'tool', text: stripped }; splitAt = null; continue;
    }
    if (!current) continue;
    if (stripped.startsWith('───') || chromePattern.test(stripped) || /^[^\s]+\s+(?:low|medium|high|xhigh|max|ultra)\s+·\s+/.test(stripped)) { finish(); continue; }
    if (wasBlank && transientPattern.test(stripped)) { finish(); continue; }
    if (stripped.startsWith('⎿')) {
      const entry = current;
      if (entry.kind === 'conversation' && splitAt !== null) {
        const tail = entry.text.slice(splitAt).trim(); entry.text = entry.text.slice(0, splitAt); finish();
        current = { role: 'assistant', kind: 'tool', text: tail };
      } else entry.kind = 'tool';
      splitAt = null; continue;
    }
    if (!rawLine || /^\s/.test(rawLine)) {
      if (wasBlank && stripped && current.kind === 'conversation') splitAt = current.text.length + 1;
      current.text += `\n${stripped}`;
    } else finish();
  }
  finish();
  return messages;
}

function parsePiAnsiHistory(output: string): AgentHistoryMessage[] {
  if (!output.includes('\x1b[')) return [];
  const messages: AgentHistoryMessage[] = [];
  const lines = output.replace(/\r/g, '').split('\n');
  let assistantLines: string[] = [];
  const finishAssistant = () => {
    const text = unwrapTerminalText(assistantLines.join('\n'));
    if (text && !isHarnessArtifact(text)) messages.push({ role: 'assistant', kind: 'conversation', text });
    assistantLines = [];
  };
  for (let index = 0; index < lines.length;) {
    const backgrounds = [...lines[index].matchAll(/\x1b\[48;5;(\d+)m/g)];
    if (backgrounds.length) {
      finishAssistant();
      const background = backgrounds[backgrounds.length - 1][1];
      const run: string[] = [];
      while (index < lines.length) {
        const matches = [...lines[index].matchAll(/\x1b\[48;5;(\d+)m/g)];
        if (!matches.length || matches[matches.length - 1][1] !== background) break;
        run.push(stripAnsi(lines[index]).trimEnd()); index += 1;
      }
      const text = unwrapTerminalText(run.join('\n'));
      if (!text || isHarnessArtifact(text)) continue;
      if (markerlessToolPattern.test(text) || /^(?:(?:edit|read|write|bash|grep|find|ls)\s*\n|[+\- ]\s*\d+\s|@@\s)/i.test(text)) messages.push({ role: 'assistant', kind: 'tool', text });
      else messages.push({ role: 'user', kind: 'conversation', text: text.replace(routingPrefix, '').trim() });
      continue;
    }
    const rawLine = lines[index++];
    const text = stripAnsi(rawLine).trimEnd();
    // Pi renders private reasoning/status in bold italic gray. It is neither an
    // assistant response nor a tool call and must terminate any prose block.
    if (/\x1b\[(?:[^m;]*;)*3(?:;[^m]*)?m/.test(rawLine)) { finishAssistant(); continue; }
    if (!text.trim()) { if (assistantLines.length && assistantLines[assistantLines.length - 1] !== '') assistantLines.push(''); continue; }
    if (isHarnessArtifact(text)) { finishAssistant(); continue; }
    assistantLines.push(text);
  }
  finishAssistant();
  return messages;
}

export function parseAgentHistory(output: string): AgentHistoryMessage[] {
  const plain = stripAnsi(output);
  if (plain.split(/\r?\n/).some(line => markerPattern.test(line))) return parseMarkedHistory(plain);
  // Pi's text-only terminal snapshot loses the only reliable role boundary: its
  // user/tool background boxes. Parse ANSI snapshots semantically and fail
  // closed for plain markerless output rather than presenting arbitrary harness
  // rows as assistant prose.
  return parsePiAnsiHistory(output);
}

export function isRenderableToolCall(message: Pick<AgentHistoryMessage, 'kind' | 'text'>): boolean {
  const text = message.text.trim();
  return message.kind === 'tool' && Boolean(text) && !isHarnessArtifact(text) && (
    /^(?:Running|Calling|Reading|Writing|Editing|Exploring|Fetching|Searching)\b/i.test(text) || markerlessToolPattern.test(text)
  );
}

export function toolCallPreview(text: string): string {
  const value = text.trim().replace(/\s+/g, ' ');
  if (value.startsWith('$')) return 'Bash';
  const namedTool = value.match(/^(Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\b/i);
  if (namedTool) return namedTool[1];
  const activity = value.match(/^(Running|Calling|Reading|Writing|Editing|Exploring|Fetching|Searching|Ran|Called|Searched|Viewed|Created|Updated|Deleted|Downloaded|Committed|Pushed)\b/i);
  if (!activity) return 'Tool';
  return /^(Running|Calling|Reading|Writing|Editing|Exploring|Fetching|Searching)$/i.test(activity[1]) ? `${activity[1]}…` : activity[1];
}

export function sanitizeAgentHistory<T extends AgentHistoryMessage>(messages: T[]): T[] {
  return messages.filter(message => {
    // User-authored text is never reclassified by content: a captain may
    // legitimately discuss JSON-RPC, /calm, pane ids, or any other artifact.
    if (message.role === 'user') return message.kind === 'conversation';
    return message.kind === 'tool' ? isRenderableToolCall(message) : !isHarnessArtifact(message.text);
  });
}

export function filterAgentHistory<T extends AgentHistoryMessage>(messages: T[], showToolCalls: boolean): T[] {
  return sanitizeAgentHistory(messages).filter(message => message.kind === 'conversation' || showToolCalls);
}
