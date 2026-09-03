import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MAGI_MAX_BLOCKS,
  MAGI_MAX_FALLBACK_TEXT_CHARS,
  MAGI_MAX_RESPONSE_BYTES,
  MAGI_MAX_TOTAL_INLINE_NODES,
  normalizeMagiResponse,
  validatedMagiUrl,
} from '../src/services/MagiResponse';
import {
  normalizeCanonicalMessage,
  reconcileCanonicalMessages,
  sameRenderedTranscript,
} from '../src/services/CanonicalConversation';

const richResponse = () => ({
  schema_version: 'magi.response.v1',
  blocks: [
    {
      type: 'heading', block_id: 'summary', level: 1,
      content: [
        { type: 'text', text: 'A ' },
        { type: 'strong', text: 'native' },
        { type: 'text', text: ' response with ' },
        { type: 'emphasis', text: 'safe' },
        { type: 'text', text: ' ' },
        { type: 'inline_code', text: 'blocks' },
        { type: 'text', text: ' and a ' },
        { type: 'link', text: 'reference', url: 'https://example.com/guide?q=1#read' },
      ],
    },
    { type: 'paragraph', block_id: 'detail', content: [{ type: 'text', text: 'Plain text remains the explicit fallback.' }] },
    {
      type: 'list', block_id: 'steps', style: 'unordered',
      items: [[{ type: 'text', text: 'Validate.' }], [{ type: 'strong', text: 'Render.' }]],
    },
    { type: 'code', block_id: 'code', language: 'typescript', code: 'const safe = true;' },
    { type: 'quote', block_id: 'quote', content: [{ type: 'text', text: 'Unknown nodes fail closed.' }] },
    { type: 'divider', block_id: 'divider' },
  ],
  actions: [{ type: 'open_url', action_id: 'docs', label: 'Read docs', url: 'https://example.com/docs' }],
});

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));

test('magi.response.v1 accepts the complete native allowlist as detached data', () => {
  const input = richResponse();
  const response = normalizeMagiResponse(input);
  assert.ok(response);
  assert.deepEqual(response.blocks.map(block => block.type), ['heading', 'paragraph', 'list', 'code', 'quote', 'divider']);
  assert.notEqual(response, input);
  (input.blocks[0] as any).content[0].text = 'mutated';
  assert.equal((response.blocks[0] as any).content[0].text, 'A ');
});

test('unknown instructions, malformed fields, duplicate ids, and unsafe links fail closed', () => {
  const candidates: any[] = [];
  const missingVersion: any = clone(richResponse()); delete missingVersion.schema_version; candidates.push(missingVersion);
  const unknownBlock: any = clone(richResponse()); unknownBlock.blocks[0] = { type: 'html', block_id: 'x', html: '<script />' }; candidates.push(unknownBlock);
  const unknownInline: any = clone(richResponse()); unknownInline.blocks[0].content[0] = { type: 'image', src: 'https://example.com/x' }; candidates.push(unknownInline);
  const component: any = clone(richResponse()); component.blocks[0].component = 'AdminPanel'; candidates.push(component);
  const duplicate: any = clone(richResponse()); duplicate.blocks[1].block_id = 'summary'; candidates.push(duplicate);
  const badLevel: any = clone(richResponse()); badLevel.blocks[0].level = 2.5; candidates.push(badLevel);
  const badRevisionShape: any = clone(richResponse()); badRevisionShape.blocks[2].items = [[]]; candidates.push(badRevisionShape);
  const invalidUnicode: any = clone(richResponse()); invalidUnicode.blocks[1].content[0].text = '\ud800'; candidates.push(invalidUnicode);
  const javascript: any = clone(richResponse()); javascript.blocks[0].content.at(-1).url = 'javascript:alert(1)'; candidates.push(javascript);
  const credentials: any = clone(richResponse()); credentials.actions[0].url = 'https://user:secret@example.com/'; candidates.push(credentials);
  const whitespace: any = clone(richResponse()); whitespace.actions[0].url = 'https://example.com/a b'; candidates.push(whitespace);
  const tooMany: any = clone(richResponse()); tooMany.blocks = Array.from({ length: MAGI_MAX_BLOCKS + 1 }, (_, index) => ({ type: 'paragraph', block_id: `p${index}`, content: [{ type: 'text', text: 'x' }] })); candidates.push(tooMany);
  const tooManyInline: any = clone(richResponse()); tooManyInline.blocks = [{
    type: 'list', block_id: 'wide-list', style: 'unordered',
    items: Array.from({ length: 9 }, () => Array.from({ length: 128 }, () => ({ type: 'text', text: 'x' }))),
  }]; assert.ok(tooManyInline.blocks[0].items.flat().length > MAGI_MAX_TOTAL_INLINE_NODES); candidates.push(tooManyInline);
  const tooLarge: any = clone(richResponse()); tooLarge.blocks[3].code = 'x'.repeat(MAGI_MAX_RESPONSE_BYTES); candidates.push(tooLarge);

  for (const candidate of candidates) assert.equal(normalizeMagiResponse(candidate), null);
});

test('structured links permit only absolute credential-free HTTP(S)', () => {
  assert.equal(validatedMagiUrl('https://example.com/a#b'), 'https://example.com/a#b');
  assert.equal(validatedMagiUrl('http://localhost:8080/read'), 'http://localhost:8080/read');
  for (const value of [
    '', '/relative', 'mailto:test@example.com', 'file:///etc/passwd',
    'https://user@example.com/', 'https://example.com\\@evil.test/',
    'https://example.com/a\nb', ' https://example.com/', 'https://example.com/\ud800',
  ]) assert.equal(validatedMagiUrl(value), null);
});

test('canonical reconciliation carries validated structure and updates one stable row', () => {
  const first = normalizeCanonicalMessage({
    id: 'cm_structured', turn_id: 'ct_structured', role: 'assistant', type: 'conversation',
    text: 'Draft summary', visible_in_chat: true, sequence_index: 999, revision: 1,
    source: 'magi-event', created_at: 1756000000000, turn_status: 'streaming',
    content_source: 'structured', structured_revision: 2,
    structured_content: {
      schema_version: 'magi.response.v1', actions: [],
      blocks: [{ type: 'heading', block_id: 'summary', level: 2, content: [{ type: 'text', text: 'Draft summary' }] }],
    },
  });
  assert.ok(first?.structured_content);
  let rows = reconcileCanonicalMessages([], [first]);
  assert.equal(rows[0].structuredContent?.blocks[0].block_id, 'summary');
  assert.equal(rows[0].progress, 'streaming');

  const final = normalizeCanonicalMessage({
    id: 'cm_structured', turn_id: 'ct_structured', role: 'assistant', type: 'conversation',
    text: 'A native response', visible_in_chat: true, sequence_index: 999, revision: 2,
    source: 'magi-event', created_at: 1756000000000, turn_status: 'answered',
    content_source: 'structured', structured_revision: 3, structured_content: richResponse(),
  });
  assert.ok(final);
  const before = rows;
  rows = reconcileCanonicalMessages(rows, [final]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, 'cm_structured');
  assert.equal(rows[0].structuredRevision, 3);
  assert.equal(rows[0].structuredContent?.blocks.length, 6);
  assert.equal(sameRenderedTranscript(before, rows), false);
  assert.equal(sameRenderedTranscript(rows, reconcileCanonicalMessages(rows, [final])), true);
});

test('malformed or unsupported structured content uses bounded canonical text fallback', () => {
  const malformed = normalizeCanonicalMessage({
    id: 'cm_fallback', turn_id: 'ct_fallback', role: 'assistant', type: 'conversation',
    text: 'Safe plain fallback', sequence_index: 1, revision: 4, created_at: 1756000000000,
    content_source: 'structured', structured_revision: 7,
    structured_content: { schema_version: 'magi.response.v2', blocks: [{ type: 'widget', block_id: 'x' }] },
  });
  assert.ok(malformed);
  assert.equal(malformed.structured_content, undefined);
  assert.equal(malformed.content_source, 'terminal-fallback');
  const [rendered] = reconcileCanonicalMessages([], [malformed]);
  assert.equal(rendered.text, 'Safe plain fallback');
  assert.equal(rendered.structuredContent, undefined);

  const missingRevision = normalizeCanonicalMessage({
    id: 'cm_no_revision', turn_id: 'ct_no_revision', role: 'assistant', type: 'conversation',
    text: 'Still safe', sequence_index: 2, revision: 1, created_at: 1756000000000,
    content_source: 'structured', structured_content: richResponse(),
  });
  assert.equal(missingRevision?.structured_content, undefined);
});

test('canonical fallback bounds preserve the existing prompt contract', () => {
  const longPrompt = normalizeCanonicalMessage({
    id: 'cm_long_prompt', turn_id: 'ct_long_prompt', client_message_id: 'u-long-prompt',
    role: 'user', type: 'conversation', text: 'p'.repeat(100_000),
    sequence_index: 0, revision: 1, created_at: 1756000000000,
  });
  assert.ok(longPrompt, 'the client must not drop a prompt accepted by the 100,000-character Gateway contract');

  const boundedFallback = normalizeCanonicalMessage({
    id: 'cm_large_fallback', turn_id: 'ct_large_fallback', role: 'assistant', type: 'conversation',
    text: 'r'.repeat(MAGI_MAX_FALLBACK_TEXT_CHARS), sequence_index: 999,
    revision: 1, created_at: 1756000000000, content_source: 'terminal-fallback',
  });
  assert.ok(boundedFallback);
  assert.equal(normalizeCanonicalMessage({
    ...boundedFallback,
    text: `${boundedFallback.text}x`,
  }), null);
});
