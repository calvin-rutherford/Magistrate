/**
 * Closed, bounded `magi.response.v1` contract for untrusted network/cache data.
 *
 * This intentionally does not parse Markdown, HTML, or arbitrary JSON-shaped
 * component instructions. A document is renderable only after every node has
 * matched this allowlist; callers retain canonical plain text as the fallback.
 */
export const MAGI_RESPONSE_SCHEMA = 'magi.response.v1' as const;
export const MAGI_MAX_RESPONSE_BYTES = 256 * 1024;
// The plain-text projection adds only bounded list/link separators to strings
// already present in the JSON document, so its character count remains below
// the document's byte ceiling. Canonical delivery uses this as the independent
// fallback bound instead of assuming the aggregate source-text limit is also
// the rendered projection limit.
export const MAGI_MAX_FALLBACK_TEXT_CHARS = MAGI_MAX_RESPONSE_BYTES;
export const MAGI_MAX_TEXT_CHARS = 200_000;
export const MAGI_MAX_BLOCKS = 128;
export const MAGI_MAX_INLINE_NODES = 128;
export const MAGI_MAX_TOTAL_INLINE_NODES = 1024;
export const MAGI_MAX_LIST_ITEMS = 100;
export const MAGI_MAX_ACTIONS = 16;

export type MagiInlineNode =
  | { type: 'text'; text: string }
  | { type: 'strong'; text: string }
  | { type: 'emphasis'; text: string }
  | { type: 'inline_code'; text: string }
  | { type: 'link'; text: string; url: string };

export type MagiResponseBlock =
  | { type: 'paragraph'; block_id: string; content: MagiInlineNode[] }
  | { type: 'heading'; block_id: string; level: 1 | 2 | 3 | 4; content: MagiInlineNode[] }
  | { type: 'list'; block_id: string; style: 'ordered' | 'unordered'; items: MagiInlineNode[][] }
  | { type: 'code'; block_id: string; code: string; language?: string | null }
  | { type: 'quote'; block_id: string; content: MagiInlineNode[] }
  | { type: 'divider'; block_id: string };

export type MagiResponseAction = {
  type: 'open_url';
  action_id: string;
  label: string;
  url: string;
};

export interface MagiResponseV1 {
  schema_version: typeof MAGI_RESPONSE_SCHEMA;
  blocks: MagiResponseBlock[];
  actions: MagiResponseAction[];
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const LANGUAGE = /^[A-Za-z0-9_+.#-]{0,32}$/;
const INLINE_TYPES = new Set(['text', 'strong', 'emphasis', 'inline_code', 'link']);

const record = (raw: unknown): Record<string, unknown> | null =>
  raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, unknown> : null;

function isWellFormedUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function exactKeys(value: Record<string, unknown>, required: string[], optional: string[] = []): boolean {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return required.every(key => Object.prototype.hasOwnProperty.call(value, key))
    && keys.every(key => allowed.has(key));
}

const boundedString = (value: unknown, minimum: number, maximum: number): value is string =>
  typeof value === 'string' && isWellFormedUnicode(value)
  && Array.from(value).length >= minimum && Array.from(value).length <= maximum;

/** Same conservative external-link policy as the Gateway contract. */
export function validatedMagiUrl(value: unknown): string | null {
  if (!boundedString(value, 1, 2048) || value !== value.trim() || /[\\\s\u0000-\u001f\u007f]/u.test(value)) return null;
  try {
    const parsed = new URL(value);
    if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
      || !parsed.hostname || parsed.username || parsed.password) return null;
    return value;
  } catch {
    return null;
  }
}

function normalizeInline(raw: unknown): MagiInlineNode | null {
  const value = record(raw);
  if (!value || typeof value.type !== 'string' || !INLINE_TYPES.has(value.type)) return null;
  if (value.type === 'link') {
    if (!exactKeys(value, ['type', 'text', 'url'])
      || !boundedString(value.text, 1, 4096)
      || !validatedMagiUrl(value.url)) return null;
    return { type: 'link', text: value.text, url: value.url as string };
  }
  if (!exactKeys(value, ['type', 'text']) || !boundedString(value.text, 1, 16_384)) return null;
  if (value.type === 'text' || value.type === 'strong' || value.type === 'emphasis' || value.type === 'inline_code') {
    return { type: value.type, text: value.text };
  }
  return null;
}

function normalizeInlineList(raw: unknown): MagiInlineNode[] | null {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > MAGI_MAX_INLINE_NODES) return null;
  const nodes = raw.map(normalizeInline);
  return nodes.every((node): node is MagiInlineNode => node !== null) ? nodes : null;
}

function normalizeBlock(raw: unknown): MagiResponseBlock | null {
  const value = record(raw);
  if (!value || !boundedString(value.block_id, 1, 128) || !ID.test(value.block_id)) return null;
  switch (value.type) {
    case 'paragraph':
    case 'quote': {
      if (!exactKeys(value, ['type', 'block_id', 'content'])) return null;
      const content = normalizeInlineList(value.content);
      return content ? { type: value.type, block_id: value.block_id, content } : null;
    }
    case 'heading': {
      if (!exactKeys(value, ['type', 'block_id', 'level', 'content'])
        || !Number.isInteger(value.level) || ![1, 2, 3, 4].includes(value.level as number)) return null;
      const content = normalizeInlineList(value.content);
      return content ? { type: 'heading', block_id: value.block_id, level: value.level as 1 | 2 | 3 | 4, content } : null;
    }
    case 'list': {
      if (!exactKeys(value, ['type', 'block_id', 'style', 'items'])
        || (value.style !== 'ordered' && value.style !== 'unordered')
        || !Array.isArray(value.items) || value.items.length < 1 || value.items.length > MAGI_MAX_LIST_ITEMS) return null;
      const items = value.items.map(normalizeInlineList);
      return items.every((item): item is MagiInlineNode[] => item !== null)
        ? { type: 'list', block_id: value.block_id, style: value.style, items }
        : null;
    }
    case 'code': {
      if (!exactKeys(value, ['type', 'block_id', 'code'], ['language'])
        || !boundedString(value.code, 1, 65_536)
        || (value.language !== undefined && value.language !== null
          && (typeof value.language !== 'string' || !LANGUAGE.test(value.language)))) return null;
      return {
        type: 'code', block_id: value.block_id, code: value.code,
        ...(value.language !== undefined ? { language: value.language as string | null } : {}),
      };
    }
    case 'divider':
      return exactKeys(value, ['type', 'block_id']) ? { type: 'divider', block_id: value.block_id } : null;
    default:
      return null;
  }
}

function normalizeAction(raw: unknown): MagiResponseAction | null {
  const value = record(raw);
  if (!value || !exactKeys(value, ['type', 'action_id', 'label', 'url']) || value.type !== 'open_url'
    || !boundedString(value.action_id, 1, 128) || !ID.test(value.action_id)
    || !boundedString(value.label, 1, 120) || !validatedMagiUrl(value.url)) return null;
  return { type: 'open_url', action_id: value.action_id, label: value.label, url: value.url as string };
}

function utf8Bytes(value: string): number {
  let bytes = 0;
  for (const character of value) {
    const point = character.codePointAt(0) as number;
    bytes += point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4;
  }
  return bytes;
}

/** Return a detached, validated document or null. Unknown means unrenderable. */
export function normalizeMagiResponse(raw: unknown): MagiResponseV1 | null {
  const value = record(raw);
  if (!value || !exactKeys(value, ['schema_version', 'blocks'], ['actions'])
    || value.schema_version !== MAGI_RESPONSE_SCHEMA
    || !Array.isArray(value.blocks) || value.blocks.length < 1 || value.blocks.length > MAGI_MAX_BLOCKS
    || (value.actions !== undefined && (!Array.isArray(value.actions) || value.actions.length > MAGI_MAX_ACTIONS))) return null;
  // Reject pathological aggregate fan-out before walking and detaching every
  // nested node. Per-group checks still run in normalizeBlock.
  let rawInlineNodes = 0;
  for (const rawBlock of value.blocks) {
    const block = record(rawBlock);
    if (block?.type === 'list' && Array.isArray(block.items)) {
      for (const item of block.items) {
        if (Array.isArray(item)) rawInlineNodes += item.length;
        if (rawInlineNodes > MAGI_MAX_TOTAL_INLINE_NODES) return null;
      }
    } else if (block && Array.isArray(block.content)) {
      rawInlineNodes += block.content.length;
      if (rawInlineNodes > MAGI_MAX_TOTAL_INLINE_NODES) return null;
    }
  }
  const blocks = value.blocks.map(normalizeBlock);
  const actions = (value.actions || []).map(normalizeAction);
  if (!blocks.every((block): block is MagiResponseBlock => block !== null)
    || !actions.every((action): action is MagiResponseAction => action !== null)) return null;
  const blockIds = blocks.map(block => block.block_id);
  const actionIds = actions.map(action => action.action_id);
  if (new Set(blockIds).size !== blockIds.length || new Set(actionIds).size !== actionIds.length) return null;

  let textChars = 0;
  for (const block of blocks) {
    if (block.type === 'code') textChars += Array.from(block.code).length;
    else if (block.type !== 'divider') {
      const groups = block.type === 'list' ? block.items : [block.content];
      for (const group of groups) for (const node of group) {
        textChars += Array.from(node.text).length;
        if (node.type === 'link') textChars += Array.from(node.url).length;
      }
    }
  }
  for (const action of actions) textChars += Array.from(action.label).length + Array.from(action.url).length;
  if (textChars > MAGI_MAX_TEXT_CHARS) return null;

  const response: MagiResponseV1 = { schema_version: MAGI_RESPONSE_SCHEMA, blocks, actions };
  try {
    if (utf8Bytes(JSON.stringify(response)) > MAGI_MAX_RESPONSE_BYTES) return null;
  } catch {
    return null;
  }
  return response;
}
