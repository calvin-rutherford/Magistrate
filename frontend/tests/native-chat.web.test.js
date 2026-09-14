const assert = require('node:assert/strict');
const test = require('node:test');
const { launchBrowser, startWebServer } = require('./helpers/web-server');

let server;
let browser;

function installNativeGatewayMock() {
  const longReply = [
    '# Native Magi', '', '## Complete numbered result', '',
    '1. alpha', '2. beta', '3. gamma',
    ...Array.from({ length: 27 }, (_, index) => `${index + 4}. Native item ${index + 4} is complete.`),
    '', '## Repeated markers', '', '1. alpha', '1. beta', '1. gamma',
    '', '## Details', '',
    'This paragraph proves Markdown and long-form prose stay in one canonical assistant message.',
    '', '```ts', 'const transcript = "provider-native";', '```',
  ].join('\n');
  const nativeFetch = window.fetch.bind(window);
  const json = (body, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }));
  const increment = key => {
    const value = Number(localStorage.getItem(key) || '0') + 1;
    localStorage.setItem(key, String(value));
  };
  const readRecord = () => {
    try { return JSON.parse(localStorage.getItem('native-chat-browser-record') || 'null'); }
    catch { return null; }
  };
  const emptyRecord = () => ({
    schema_version: 'magi.native-chat.v1',
    conversation: { id: 'mgc_browser_native_0001', created_at: 1789000000000, updated_at: 1789000000000 },
    conversation_id: 'mgc_browser_native_0001', messages: [], has_more: false, next_before: null, latest_change: 0,
  });
  window.fetch = (resource, options = {}) => {
    const url = typeof resource === 'string' ? resource : resource.url;
    if (url.includes('/api/v1/auth/session')) {
      const payload = options.method === 'POST'
        ? { session_token: 'native-browser-session-token-000001', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' }
        : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user', auth_method: 'operator-bootstrap', onboarding_required: false };
      return json(payload);
    }
    if (url.includes('/api/v1/magi/conversations/current')) {
      increment('native-chat-get-count');
      return json(readRecord() || emptyRecord());
    }
    if (url.includes('/api/v1/magi/messages') && options.method === 'POST') {
      increment('native-chat-post-count');
      const body = JSON.parse(options.body || '{}');
      const previous = readRecord();
      if (previous?.messages?.some(message => message.client_message_id === body.client_message_id)) {
        const user = previous.messages.find(message => message.client_message_id === body.client_message_id);
        const assistant = previous.messages.find(message => message.reply_to_message_id === user.id);
        return json({ schema_version: previous.schema_version, conversation: previous.conversation,
          conversation_id: previous.conversation_id, status: assistant.status, user_message: user,
          assistant_message: assistant, messages: [user, assistant], duplicate: true, retry: false, attempt: 1 });
      }
      const createdAt = 1789000001000 + Number(localStorage.getItem('native-chat-post-count'));
      const turnId = `mgt_browser_${body.client_message_id.replace(/[^A-Za-z0-9_-]/g, '')}`;
      const conversation = previous?.conversation || emptyRecord().conversation;
      const start = previous?.messages?.length || 0;
      const messages = [...(previous?.messages || []), {
        id: `mgm_user_${start}`, conversation_id: conversation.id, turn_id: turnId,
        client_message_id: body.client_message_id, reply_to_message_id: null,
        role: 'user', content: body.content, status: 'completed', source: body.source || 'text',
        sequence_index: start, revision: 1, attachments: [], created_at: createdAt, updated_at: createdAt,
      }, {
        id: `mgm_assistant_${start + 1}`, conversation_id: conversation.id, turn_id: turnId,
        client_message_id: null, reply_to_message_id: `mgm_user_${start}`,
        role: 'assistant', content: longReply, status: 'completed', source: 'magi-native',
        sequence_index: start + 1, revision: 2, attachments: [], created_at: createdAt, updated_at: createdAt + 1,
      }];
      const record = {
        schema_version: 'magi.native-chat.v1',
        conversation: { ...conversation, updated_at: createdAt + 1 }, conversation_id: conversation.id,
        messages, has_more: false, next_before: null, latest_change: messages.length + 1,
      };
      localStorage.setItem('native-chat-browser-record', JSON.stringify(record));
      const response = { schema_version: record.schema_version, conversation: record.conversation,
        conversation_id: record.conversation_id, status: 'completed', user_message: messages.at(-2),
        assistant_message: messages.at(-1), messages: messages.slice(-2), duplicate: false, retry: false, attempt: 1 };
      if (localStorage.getItem('native-chat-delay-post') === '1') {
        return new Promise(resolve => setTimeout(() => { void json(response).then(resolve); }, 1_200));
      }
      return json(response);
    }
    if (url.includes('/api/v1/voice/transcribe')) return json({ schema_version: 'voice-transcription.v1', text: 'Native voice message', is_final: true });
    if (url.includes('/api/v1/captain/prompt') || url.includes('/api/v1/conversations/captain')
      || url.includes('/api/v1/agents/captain/history') || url.includes('/api/v1/voice/moves')) {
      increment('native-chat-forbidden-count');
      return json({ detail: 'legacy chat must not be called' }, 500);
    }
    if (url.includes('/api/v1/activity/snapshot')) {
      if (localStorage.getItem('native-chat-activity-unavailable') === '1') return Promise.reject(new TypeError('Activity service unavailable.'));
      return json({
        schema_version: 'activity.v1', records: [], focus_records: [], focus_truncated: false,
        snapshot_cursor: 0, latest_sequence: 0, next_before: null, has_more: false,
        summary: { active_objectives: 0, operation_count: 0, pending_decisions: 0 },
        reconciliation: 'persisted-only', sources: [],
      });
    }
    if (url.includes('/api/v1/activity')) {
      if (localStorage.getItem('native-chat-activity-unavailable') === '1') return Promise.reject(new TypeError('Activity service unavailable.'));
      return json({
      schema_version: 'activity.v1', records: [], next_cursor: 0, latest_cursor: 0,
      has_more: false, summary: { active_objectives: 0, operation_count: 0, pending_decisions: 0 },
      reconciliation: 'persisted-only', sources: [],
      });
    }
    if (url.includes('/api/v1/execution/capabilities')) return json({ harnesses: [], profiles: [], source: 'native', configured: false });
    if (url.includes('/api/v1/execution/settings')) return json({ profile_id: null, routing_profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
    if (url.includes('/api/v1/voice/capabilities')) return json({ schema_version: 'voice-capabilities.v1', provider: 'openai', configured: true, modes: [{ id: 'openai', label: 'Gateway OpenAI', available: true }] });
    if (url.includes('/api/v1/notifications/')) return json({ events: [], unread: [], enabled: true, mode: 'moderate', quiet: false, suppressed_foreground: false, delivery: 'none' });
    if (url.includes('/api/v1/agents')) return json([]);
    if (url.includes('/api/v1/attention')) return json([]);
    if (url.includes('/api/v1/recent-activity')) return json({ items: [], sources: { firstmate: 'unavailable', github: 'unavailable' } });
    if (url.includes('/api/v1/auth/providers')) return json([]);
    if (url.includes('/api/v1/usage')) return json({ source: 'test', providers: [] });
    if (url.includes('/api/v1/health')) return json({ status: 'healthy', service: 'gateway' });
    if (url.includes('/api/v1/')) return json({});
    return nativeFetch(resource, options);
  };
}

function stubSpeechSynthesis() {
  window.__nativeSpoken = [];
  Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
    getVoices: () => [], onvoiceschanged: null, speaking: false,
    speak: message => { window.__nativeSpoken.push(message.text); setTimeout(() => message.onend?.({}), 120); },
    cancel: () => {}, pause: () => {}, resume: () => {},
  }});
}

test.before(async () => {
  server = await startWebServer({ readyPath: '/chat' });
  browser = await launchBrowser({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
});

test.after(async () => {
  await browser?.close();
  await server?.stop();
});

test('production-default chat composer persists and restores only through native Magi APIs', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(installNativeGatewayMock);
  await page.goto(`${server.base}/chat`, { waitUntil: 'networkidle0' });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.querySelector('#error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForSelector('[data-testid="chat-history"][aria-busy="false"]', { timeout: 20_000 });
  await page.focus('[data-testid="magi-prompt"]');
  await page.keyboard.type('Hello native Magi');
  await page.click('[data-testid="send-magi-prompt"]');
  await page.waitForFunction(() => document.body.innerText.includes('Native item 30 is complete.'), { timeout: 20_000 });
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-post-count'))), 1);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('native-chat-browser-record')).messages[0].source), 'text');
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-forbidden-count') || '0')), 0);
  assert.equal((await page.$$('[data-testid="agent-message"]')).length, 1);
  const orderedMarkers = await page.$$eval('[data-testid^="assistant-markdown-"][data-testid*="-ordered-marker-"]', elements => elements.map(element => element.textContent));
  assert.deepEqual(orderedMarkers.slice(0, 3), ['1.', '2.', '3.'], 'sequential source markers render as 1, 2, 3');
  assert.deepEqual(orderedMarkers.slice(-3), ['1.', '2.', '3.'], 'CommonMark repeated markers render as 1, 2, 3');

  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="chat-history"][aria-busy="false"]', { timeout: 20_000 });
  await page.waitForFunction(() => document.body.innerText.includes('Native item 30 is complete.'));
  assert.equal((await page.$$('[data-testid="agent-message"]')).length, 1, 'reload must not duplicate the canonical reply');
  assert.ok(await page.evaluate(() => Number(localStorage.getItem('native-chat-get-count'))) >= 2);
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-forbidden-count') || '0')), 0);
  await page.close();
});

test('an activity outage does not interrupt an active native LLM turn', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    localStorage.removeItem('native-chat-browser-record');
    localStorage.removeItem('native-chat-post-count');
    localStorage.removeItem('native-chat-forbidden-count');
    localStorage.setItem('native-chat-activity-unavailable', '1');
    localStorage.setItem('native-chat-delay-post', '1');
  });
  await page.evaluateOnNewDocument(installNativeGatewayMock);
  await page.goto(`${server.base}/chat`, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.querySelector('#error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForSelector('[data-testid="chat-history"][aria-busy="false"]', { timeout: 20_000 });
  await page.focus('[data-testid="magi-prompt"]');
  await page.keyboard.type('Continue through the optional activity outage');
  await page.click('[data-testid="send-magi-prompt"]');
  await page.waitForSelector('[data-testid="stop-magi-response"]', { timeout: 5_000 });
  await page.waitForFunction(() => document.body.innerText.includes('Native item 30 is complete.'), { timeout: 20_000 });
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-post-count'))), 1);
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-forbidden-count') || '0')), 0);
  await page.close();
});

test('voice-to-text uses the same native message endpoint and never invokes voice moves', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(installNativeGatewayMock);
  await page.evaluateOnNewDocument(stubSpeechSynthesis);
  await page.goto(`${server.base}/voice`, { waitUntil: 'networkidle0' });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.querySelector('#error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForFunction(() => document.body.innerText.includes('Listening'), { timeout: 20_000 });
  await new Promise(resolve => setTimeout(resolve, 900));
  await page.click('[data-testid="voice-control"]');
  await page.waitForSelector('[data-testid="voice-conversation"]', { timeout: 20_000 });
  const transcript = await page.$eval('[data-testid="voice-conversation"]', element => element.innerText);
  assert.match(transcript, /Native voice message/);
  assert.match(transcript, /Native Magi/);
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-post-count'))), 1);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('native-chat-browser-record')).messages[0].source), 'voice');
  assert.equal(await page.evaluate(() => Number(localStorage.getItem('native-chat-forbidden-count') || '0')), 0);
  assert.equal((await page.$eval('body', element => element.innerText)).includes('FIRSTMATE / VOICE'), false);
  await page.close();
});
