/** The small, deliberately bounded formatting contract used by chat prose. */
export type SafeInline =
  | { type: 'text'; value: string }
  | { type: 'strong' | 'emphasis' | 'code'; value: string }
  | { type: 'link'; value: string; url: string };

export type SafeMarkdownBlock =
  | { type: 'paragraph' | 'heading'; level?: 1 | 2 | 3; inline: SafeInline[] }
  | { type: 'unordered-list' | 'ordered-list'; items: SafeInline[][] }
  | { type: 'code'; language?: string; value: string };

/** Chat links intentionally use the same conservative policy as externalLinks. */
export function isSafeChatUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
}

/** Remove HTML rather than interpreting it as a second rendering language. */
export function sanitizeChatMarkdown(value: string): string {
  return value
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<script\b[\s\S]*?<\/script\s*>/gi, '')
    .replace(/<style\b[\s\S]*?<\/style\s*>/gi, '')
    .replace(/<[^>]*>/g, '')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '');
}

function inline(value: string): SafeInline[] {
  const result: SafeInline[] = [];
  let rest = value;
  const pushText = (text: string) => { if (text) result.push({ type: 'text', value: text }); };
  while (rest) {
    const image = rest.match(/^!\[([^\]]*)\]\([^)]*\)/);
    if (image) { pushText(image[1]); rest = rest.slice(image[0].length); continue; }
    const link = rest.match(/^\[([^\]]+)\]\(([^\s)]+)(?:\s+"[^"]*")?\)/);
    if (link) {
      if (isSafeChatUrl(link[2])) result.push({ type: 'link', value: link[1], url: link[2] });
      else pushText(link[1]);
      rest = rest.slice(link[0].length); continue;
    }
    const code = rest.match(/^`([^`\n]+)`/);
    if (code) { result.push({ type: 'code', value: code[1] }); rest = rest.slice(code[0].length); continue; }
    const strong = rest.match(/^(?:\*\*|__)([^\n]+?)(?:\*\*|__)/);
    if (strong) { result.push({ type: 'strong', value: strong[1] }); rest = rest.slice(strong[0].length); continue; }
    const emphasis = rest.match(/^(?:\*|_)([^\n]+?)(?:\*|_)/);
    if (emphasis) { result.push({ type: 'emphasis', value: emphasis[1] }); rest = rest.slice(emphasis[0].length); continue; }
    const next = rest.slice(1).search(/[`*_\[]/);
    pushText(rest.slice(0, next < 0 ? rest.length : next + 1));
    rest = next < 0 ? '' : rest.slice(next + 1);
  }
  return result.length ? result : [{ type: 'text', value: '' }];
}

/**
 * Parse only common conversational Markdown. This is not an HTML/Markdown
 * browser: raw tags are discarded and unsafe links become ordinary text.
 */
export function parseSafeMarkdown(markdown: string): SafeMarkdownBlock[] {
  const source = sanitizeChatMarkdown(String(markdown || '')).replace(/\r/g, '');
  const lines = source.split('\n');
  const blocks: SafeMarkdownBlock[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const fence = line.match(/^\s*```\s*([A-Za-z0-9+#._-]{0,24})\s*$/);
    if (fence) {
      const code: string[] = []; index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) { code.push(lines[index]); index += 1; }
      if (index < lines.length) index += 1;
      blocks.push({ type: 'code', language: fence[1] || undefined, value: code.join('\n') });
      continue;
    }
    const heading = line.match(/^\s*(#{1,3})\s+(.+?)\s*#*\s*$/);
    if (heading) { blocks.push({ type: 'heading', level: heading[1].length as 1 | 2 | 3, inline: inline(heading[2]) }); index += 1; continue; }
    const list = line.match(/^\s*([-*+] |\d+[.)] )(.+)$/);
    if (list) {
      const ordered = /^\d/.test(list[1]); const items: SafeInline[][] = [];
      while (index < lines.length) {
        const item = lines[index].match(ordered ? /^\s*\d+[.)] (.+)$/ : /^\s*[-*+] (.+)$/);
        if (!item) break;
        items.push(inline(item[1])); index += 1;
      }
      blocks.push({ type: ordered ? 'ordered-list' : 'unordered-list', items }); continue;
    }
    const paragraph: string[] = [line.trim()]; index += 1;
    while (index < lines.length && lines[index].trim() && !/^\s*(?:```|#{1,3}\s|[-*+] |\d+[.)] )/.test(lines[index])) { paragraph.push(lines[index].trim()); index += 1; }
    blocks.push({ type: 'paragraph', inline: inline(paragraph.join('\n')) });
  }
  return blocks;
}

export function formatConversationTimestamp(sentAt?: number, locale?: string): string | null {
  if (typeof sentAt !== 'number' || !Number.isFinite(sentAt) || sentAt < 0) return null;
  const date = new Date(sentAt);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
}

export function formatAccessibleTimestamp(sentAt?: number, locale?: string): string | null {
  if (typeof sentAt !== 'number' || !Number.isFinite(sentAt) || sentAt < 0) return null;
  const date = new Date(sentAt);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString(locale, { dateStyle: 'full', timeStyle: 'short' });
}

export type SafeThinkingSummary = { provider: string; text: string };
export function safeThinkingSummary(summary?: SafeThinkingSummary | null): SafeThinkingSummary | null {
  if (!summary || typeof summary.provider !== 'string' || typeof summary.text !== 'string') return null;
  const provider = summary.provider.trim().slice(0, 48);
  const text = sanitizeChatMarkdown(summary.text).trim().slice(0, 280);
  return provider && text ? { provider, text } : null;
}
