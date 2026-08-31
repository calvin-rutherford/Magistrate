export type AgentHistoryMessage = {
  role: 'user' | 'assistant';
  // 'control' is an internally addressed record - a Firstmate-to-worker prompt,
  // lifecycle/transport event, or harness metadata row. It is never a message
  // of either role, but it still marks the audience boundary it occupied, so it
  // is retained as a separator rather than silently deleted.
  kind: 'conversation' | 'tool' | 'control';
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
const chromePattern = /\(ctrl\+(?:End|Home)\)|Jump to bottom|Update installed · Restart|⏵⏵|⏸\s|auto mode on ·|esc to interrupt|ctrl\+\w+ to |of your (?:usage|limit)\b/i;
const harnessArtifactPattern = /^(?:FIRSTMATE_OP\b|WAKE_(?:ACK|DRAIN|REQUIRED)\b|Planning\b|Clarifying\b|Initiating\b|Inspecting\b|Identifying\b|Verifying\b|Queuing\b|Error:\s|Took \d|Command exited with code\b|\(no output\)|\.\.\. \(\d+ earlier lines|help\[\d+\]:|\/calm\b|calm(?:ing)?(?: animation| status)?\b|edit\s*$|(?:pane|tab|workspace)(?:_id)?\s*[:=]|(?:(?:model|provider|harness)|(?:thread|session|trace|run|request|conversation|message)[ _-]?id)\s*[:=]|your usage\b|window=[^\s]+|worktree=\/|~\/[^\s]+ \(|[⠀-⣿]|[↑↓]\S.*\bCH\d|─{3,})/i;
// A Pi/Codex tool envelope is a header naming a real path plus the numbered
// excerpt or diff it produced. Both halves are required: a captain may
// legitimately write "read the release notes" or even "read src/main.py", and
// those must stay captain turns.
const toolEnvelopeHeaderPattern = /^(?:edit|read|write|create|delete|remove|move|copy|rename|apply[_ ]?patch|patch|cat|head|tail|sed|awk|grep|rg|find|ls|glob|open|view|diff|touch|mkdir)\b[ \t]+(?:[\w.@~-]*\/[\w.@/-]*|[\w.@-]+\.[A-Za-z0-9]{1,8})(:\d+(?:[-:]\d+)?)?[ \t]*$/i;
const numberedExcerptPattern = /(?:^|\n)[ \t]*(?:\.{3}[ \t]*)?[+\-]?[ \t]*\d{1,6}[ \t]{1,12}\S/;
const diffExcerptPattern = /(?:^|\n)[ \t]*(?:@@[ \t]|[+\-]{3}[ \t]|[+\-][ \t]*\d+[ \t])/;
// Firstmate drives worker panes by typing control plumbing into them: wake
// notices, launch briefs, inbox instructions, status-file directions. Those are
// lifecycle records, not the conversational prompt a worker thread shows.
// A harness overlay - settings, usage, permissions - takes over the screen and
// renders its panel chrome inside the same background boxes Pi uses for user
// turns: tab bars, block-drawing meters, and gauge rows. None is conversation.
const panelChromePattern = /^(?:[\u2500-\u259F\u2588\s]{3,}|(?:[A-Z][A-Za-z]{1,14}[ \t]{2,}){2,}[A-Z][A-Za-z]{1,14}|\d{1,3}%[ \t](?:used|remaining|left)\b.*)$/;
const systemNoticePattern = /^(?:⛵\s+[^:]+:|Run bin\/fm-wake-drain\.sh\b|Watcher continuity is extension-owned\b|Firstmate (?:instruction|steers|inbox|launch)\b|FIRSTMATE_(?:OP|WAKE)\b|Report status by appending\b|v\d+ launch-brief:)/i;
const rawTerminalPattern = /^(?:\$\s|⎿\s|(?:bash|read|edit|write|glob|grep|task|websearch|webfetch)\s*\(|[^\s@]+@[^\s:]+:[^\n]*[$#]\s)/i;

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

export function isRawTerminalArtifact(text: string): boolean {
  return rawTerminalPattern.test(stripAnsi(text).trim());
}

/**
 * True for a harness tool envelope: a path-bearing header plus a numbered file
 * excerpt or diff. Pi surfaces these inside the same ANSI background box it
 * uses for user turns, so without this they were promoted to user messages.
 */
export function isToolEnvelope(text: string): boolean {
  const value = stripAnsi(text).replace(/^[\s]+/, '');
  const breakAt = value.indexOf('\n');
  const header = (breakAt < 0 ? value : value.slice(0, breakAt)).trim();
  const match = header.match(toolEnvelopeHeaderPattern);
  if (!match) return false;
  const body = breakAt < 0 ? '' : value.slice(breakAt + 1);
  return Boolean(match[1]) || numberedExcerptPattern.test(body) || diffExcerptPattern.test(body);
}

export function isHarnessArtifact(text: string): boolean {
  const value = stripAnsi(text).trim();
  if (!value) return true;
  if (chromePattern.test(value) || transientPattern.test(value) || harnessArtifactPattern.test(value) || systemNoticePattern.test(value) || panelChromePattern.test(value) || routingPrefix.test(value)) return true;
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
    if (!text || text === 'Ask Codex to do anything' || text === 'Ask Claude anything' || isHarnessArtifact(text)) { current = null; return; }
    // Firstmate injects worker prompts through the same '\u203a' marker the captain
    // uses, and a harness tool envelope can occupy a marked row. Neither is a
    // conversation turn: retype the envelope and drop raw terminal rows.
    if (message.kind === 'conversation' && isToolEnvelope(text)) messages.push({ role: 'assistant', kind: 'tool', text });
    else if (message.kind === 'tool' || !isRawTerminalArtifact(text)) messages.push({ ...message, text });
    current = null;
  };
  let previousBlank = true;
  // Whether the last non-blank row was a '───' rule. Claude frames its composer
  // with those rules, so a marked row right after one is unsubmitted input, not
  // a turn - and a snapshot catches it mid-keystroke, which would otherwise mint
  // a fresh 'user message' for every character typed.
  let previousRule = false;
  let splitAt: number | null = null;
  for (const rawLine of output.replace(/\r/g, '').split('\n')) {
    const wasBlank = previousBlank;
    const afterRule = previousRule;
    previousBlank = !rawLine.trim();
    if (rawLine.trim()) previousRule = /^─{3,}/.test(rawLine.trim());
    const marker = rawLine.match(markerPattern);
    if (marker) {
      finish(); splitAt = null;
      const [, glyph, rawText] = marker;
      const text = rawText.replace(routingPrefix, '').trim();
      if (glyph === '›' || glyph === '❯') current = afterRule ? null : { role: 'user', kind: 'conversation', text };
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
      if (markerlessToolPattern.test(text) || isToolEnvelope(text) || /^(?:(?:edit|read|write|bash|grep|find|ls)\s*\n|[+\- ]\s*\d+\s|@@\s)/i.test(text)) messages.push({ role: 'assistant', kind: 'tool', text });
      // A background box is the only role signal Pi gives, and it wraps tool
      // envelopes and harness chrome as well as user turns. An unrecognised box
      // fails closed instead of being promoted to a user message.
      else if (!isRawTerminalArtifact(text)) messages.push({ role: 'user', kind: 'conversation', text: text.replace(routingPrefix, '').trim() });
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
    /^(?:Running|Calling|Reading|Writing|Editing|Exploring|Fetching|Searching)\b/i.test(text) || markerlessToolPattern.test(text) || isToolEnvelope(text)
  );
}

const TOOL_LABELS: Record<string, string> = { bash: 'Bash', read: 'Read', edit: 'Edit', write: 'Write', glob: 'Glob', grep: 'Grep', task: 'Task', websearch: 'WebSearch', webfetch: 'WebFetch' };
export function toolCallPreview(text: string): string {
  const value = text.trim().replace(/\s+/g, ' ');
  if (value.startsWith('$')) return 'Bash';
  const namedTool = value.match(/^(Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\b/i);
  // Harnesses spell the same tool 'Edit' and 'edit'; the chip is one label.
  if (namedTool) return TOOL_LABELS[namedTool[1].toLowerCase()] || namedTool[1];
  const activity = value.match(/^(Running|Calling|Reading|Writing|Editing|Exploring|Fetching|Searching|Ran|Called|Searched|Viewed|Created|Updated|Deleted|Downloaded|Committed|Pushed)\b/i);
  // A tool envelope's argument is a real path; show only the bounded verb.
  if (!activity && isToolEnvelope(text)) {
    const verb = value.match(/^([A-Za-z_]+)/);
    return verb ? verb[1][0].toUpperCase() + verb[1].slice(1).toLowerCase() : 'Tool';
  }
  if (!activity) return 'Tool';
  return /^(Running|Calling|Reading|Writing|Editing|Exploring|Fetching|Searching)$/i.test(activity[1]) ? `${activity[1]}…` : activity[1];
}

/**
 * Render-time filter for the normalized local store. Captain-authored text is
 * never reclassified by content: a captain may legitimately discuss JSON-RPC,
 * /calm, pane ids, or a file path. Terminal-derived rows must instead go
 * through `sanitizeTerminalHistory` before they ever reach that store.
 */
export function sanitizeAgentHistory<T extends AgentHistoryMessage>(messages: T[]): T[] {
  return messages.filter(message => {
    if (message.kind === 'control') return false;
    if (message.role === 'user') return message.kind === 'conversation';
    return message.kind === 'tool' ? isRenderableToolCall(message) : !isHarnessArtifact(message.text) && !isRawTerminalArtifact(message.text);
  });
}

/**
 * Filter for rows parsed out of a Herdr terminal snapshot. A snapshot carries
 * no audience field, so a record that is not recognisable conversation is
 * excluded rather than promoted to a user turn merely because it lacks a
 * recognised agent role: harness chrome, lifecycle/control records, tool
 * envelopes, raw terminal input/output, transport envelopes, and provider or
 * pane metadata are all internally addressed, never messages.
 */
export function sanitizeTerminalHistory<T extends AgentHistoryMessage>(messages: T[]): T[] {
  return messages.map(message => {
    if (message.kind === 'control') return message;
    if (message.kind === 'tool') {
      return message.role === 'assistant' && isRenderableToolCall(message) ? message : { ...message, kind: 'control' as const };
    }
    const conversational = !isHarnessArtifact(message.text) && !isRawTerminalArtifact(message.text) && !isToolEnvelope(message.text);
    return conversational ? message : { ...message, kind: 'control' as const };
  });
}

export function filterAgentHistory<T extends AgentHistoryMessage>(messages: T[], showToolCalls: boolean): T[] {
  return sanitizeAgentHistory(messages).filter(message => message.kind === 'conversation' || showToolCalls);
}
