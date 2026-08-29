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
// Footer/status overlays herdr captures mid-frame; they can land on an indented
// row directly under a message, so they are dropped wherever they appear.
const chromePattern = /\(ctrl\+(?:End|Home)\)|Jump to bottom|Update installed · Restart|⏵⏵|⏸\s|auto mode on ·|esc to interrupt|ctrl\+\w+ to /;

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
  let previousBlank = true;
  // Offset in current.text where the last blank-line-separated unmarked row
  // begins: a later '⎿' detail row proves that row was tool activity, not prose.
  let splitAt: number | null = null;
  for (const rawLine of output.replace(/\r/g, '').split('\n')) {
    const wasBlank = previousBlank;
    previousBlank = !rawLine.trim();
    const marker = rawLine.match(markerPattern);
    if (marker) {
      finish();
      splitAt = null;
      const [, glyph, rawText] = marker;
      const text = rawText.replace(routingPrefix, '').trim();
      if (glyph === '›' || glyph === '❯') current = { role: 'user', kind: 'conversation', text };
      else if (!transientPattern.test(text.trim()) && !chromePattern.test(text)) current = { role: 'assistant', kind: toolPattern.test(text.trim()) ? 'tool' : 'conversation', text };
      continue;
    }
    const stripped = rawLine.trim();
    // Claude's harness prints tool activity as unmarked rows set off by a blank
    // line rather than a response marker. Without this they would be folded
    // into the conversational message above them and leak into chat.
    if (wasBlank && stripped && toolPattern.test(stripped)) {
      finish();
      current = { role: 'assistant', kind: 'tool', text: stripped };
      splitAt = null;
      continue;
    }
    if (!current) continue;
    if (stripped.startsWith('───') || chromePattern.test(stripped) || /^[^\s]+\s+(?:low|medium|high|xhigh|max|ultra)\s+·\s+/.test(stripped)) {
      finish();
      continue;
    }
    if (wasBlank && transientPattern.test(stripped)) {
      finish();
      continue;
    }
    // A '⎿' detail row is the harness's unambiguous tool-output marker, so it
    // retypes the row above it even when that row's verb is not recognised.
    if (stripped.startsWith('⎿')) {
      const entry = current;
      if (entry.kind === 'conversation' && splitAt !== null) {
        const tail = entry.text.slice(splitAt).trim();
        entry.text = entry.text.slice(0, splitAt);
        finish();
        current = { role: 'assistant', kind: 'tool', text: tail };
      } else {
        entry.kind = 'tool';
      }
      splitAt = null;
      continue;
    }
    if (!rawLine || /^\s/.test(rawLine)) {
      if (wasBlank && stripped && current.kind === 'conversation') splitAt = current.text.length + 1;
      current.text += `\n${stripped}`;
    } else finish();
  }
  finish();
  // Pi's terminal renderer intentionally emits plain transcript rows rather
  // than the marker glyphs used by Codex/Claude. Herdr exposes snapshots, not
  // a conversation API, so preserve those rows as assistant prose instead of
  // returning an empty history. Drop recognizable command output and terminal
  // chrome; chat deduplicates locally-authored prompts by text when merging.
  if (!messages.length) {
    for (const block of output.replace(/\r/g, '').trim().split(/\n\s*\n/)) {
      const lines = block.split('\n').map(line => line.trim()).filter(line => line && !chromePattern.test(line) && !transientPattern.test(line) && !line.startsWith('───') && !markerlessToolPattern.test(line));
      const text = unwrapTerminalText(lines.join('\n')).replace(routingPrefix, '').trim();
      if (text && text !== 'Ask Codex to do anything' && text !== 'Ask Claude anything' && text !== 'Skipping dev server') messages.push({ role: 'assistant', kind: 'conversation', text });
    }
  }
  return messages;
}

export function filterAgentHistory<T extends AgentHistoryMessage>(messages: T[], showToolCalls: boolean): T[] {
  return showToolCalls ? messages : messages.filter(message => message.kind === 'conversation');
}
