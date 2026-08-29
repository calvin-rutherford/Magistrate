export type AgentHistoryMessage = {
  role: 'user' | 'assistant';
  kind: 'conversation' | 'tool';
  text: string;
};

const markerPattern = /^\s*([›❯•⏺●])\s+(.*)$/;
const toolPattern = /^(?:Ran\b|Called\b|Explored\b|Searched\b|Read\b|Viewed\b|Edited\b|Added\b|Updated\b|Wrote\b|Applied\b|Waited\b|Interacted\b|Deleted\b|Removed\b|Created\b|Listed\b|Fetched\b|Downloaded\b|SessionStart\b|(?:Bash|Read|Edit|Write|Glob|Grep|Task|WebSearch|WebFetch)\s*\()/i;
const transientPattern = /^(?:Working\s*\(|You have \d+ usage|Session renamed\b)/i;

const lineBreakPattern = /^(?:[-*•‣]|\d+[.)]\s|#)/;

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

export function parseAgentHistory(output: string): AgentHistoryMessage[] {
  const messages: AgentHistoryMessage[] = [];
  let current: AgentHistoryMessage | null = null;
  const finish = () => {
    if (!current) return;
    const message = current as AgentHistoryMessage;
    const text = message.kind === 'conversation' ? unwrapTerminalText(message.text) : message.text.trim();
    if (text && text !== 'Ask Codex to do anything' && text !== 'Ask Claude anything') messages.push({ ...message, text });
    current = null;
  };
  for (const rawLine of output.replace(/\r/g, '').split('\n')) {
    const marker = rawLine.match(markerPattern);
    if (marker) {
      finish();
      const [, glyph, text] = marker;
      if (glyph === '›' || glyph === '❯') current = { role: 'user', kind: 'conversation', text };
      else if (!transientPattern.test(text.trim())) current = { role: 'assistant', kind: toolPattern.test(text.trim()) ? 'tool' : 'conversation', text };
      continue;
    }
    if (!current) continue;
    const stripped = rawLine.trim();
    if (stripped.startsWith('───') || /^[^\s]+\s+(?:low|medium|high|xhigh|max|ultra)\s+·\s+/.test(stripped)) {
      finish();
      continue;
    }
    if (!rawLine || /^\s/.test(rawLine)) current.text += `\n${stripped}`;
    else finish();
  }
  finish();
  return messages;
}

export function filterAgentHistory<T extends AgentHistoryMessage>(messages: T[], showToolCalls: boolean): T[] {
  return showToolCalls ? messages : messages.filter(message => message.kind === 'conversation');
}
