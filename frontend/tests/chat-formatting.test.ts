import assert from 'node:assert/strict';
import test from 'node:test';
import { formatAccessibleTimestamp, formatConversationTimestamp, isSafeChatUrl, parseSafeMarkdown, safeThinkingSummary, sanitizeChatMarkdown } from '../src/services/ChatFormatting';

test('safe Markdown covers prose, headings, lists, emphasis, code, and safe links', () => {
  const blocks = parseSafeMarkdown('# Title\n\nA **strong** and *quiet* `value` with [docs](https://example.com).\n\n- one\n- two\n\n```ts\nconst x = 1;\n```');
  assert.deepEqual(blocks.map(block => block.type), ['heading', 'paragraph', 'unordered-list', 'code']);
  assert.equal(blocks[1].type, 'paragraph');
  if (blocks[1].type === 'paragraph') assert.deepEqual(blocks[1].inline.map(item => item.type), ['text', 'strong', 'text', 'emphasis', 'text', 'code', 'text', 'link', 'text']);
  assert.equal(blocks[3].type, 'code');
});

test('HTML and unsafe URL schemes never become active content', () => {
  assert.equal(sanitizeChatMarkdown('<script>alert(1)</script><b>hello</b>'), 'hello');
  assert.equal(isSafeChatUrl('https://example.com/a'), true);
  assert.equal(isSafeChatUrl('javascript:alert(1)'), false);
  const blocks = parseSafeMarkdown('[run](javascript:alert(1))');
  assert.equal(blocks[0].type, 'paragraph');
  if (blocks[0].type === 'paragraph') assert.equal(blocks[0].inline.some(item => item.type === 'link'), false);
});

test('timestamps are absent for replayed messages and accessible values are full localized values', () => {
  assert.equal(formatConversationTimestamp(undefined), null);
  assert.equal(formatAccessibleTimestamp(undefined), null);
  const timestamp = Date.UTC(2026, 0, 2, 15, 4);
  assert.match(formatConversationTimestamp(timestamp, 'en-US') || '', /:04/);
  assert.match(formatAccessibleTimestamp(timestamp, 'en-US') || '', /January 2, 2026/);
});

test('thinking summaries require a provider label and are bounded', () => {
  assert.equal(safeThinkingSummary({ provider: '', text: 'private reasoning' }), null);
  const result = safeThinkingSummary({ provider: 'Anthropic', text: 'A safe summary' });
  assert.deepEqual(result, { provider: 'Anthropic', text: 'A safe summary' });
});
