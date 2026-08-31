const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const { launchBrowser, startWebServer } = require('./helpers/web-server');

let server;
let browser;
// Assigned once the dev server has picked a port; the suites read it at call
// time, never at module load.
let URL;
const HISTORY_READY_TIMEOUT = 45_000;

test.before(async () => {
  server = await startWebServer({ readyPath: '/chat' });
  URL = `${server.base}/chat`;
  // Fake device/UI flags let getUserMedia() resolve with a synthetic audio
  // track instead of prompting for real microphone hardware/permission.
  browser = await launchBrowser({ args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
});

test.after(async () => {
  await browser?.close();
  await server?.stop();
});

async function openChat(viewport, emptyInventory = false, promptResponseText = '', route = URL, visualViewportShortfall = 0, historyRace = false, preserveStorage = false, colorScheme = 'light', seedMessages = [], liveUpdates = false, chatHistoryScenario = null, promptFailure = false) {
  const page = await browser.newPage();
  await page.setViewport(viewport);
  if (visualViewportShortfall) {
    // Simulate a mobile browser handing the page a visualViewport a few px
    // shorter than window.innerHeight right after a navigation (address bar
    // still animating), with no on-screen keyboard involved.
    await page.evaluateOnNewDocument(shortfall => {
      Object.defineProperty(window.visualViewport, 'height', { get: () => window.innerHeight - shortfall });
    }, visualViewportShortfall);
  }
  await page.evaluateOnNewDocument((noOverrides, responseText, simulateHistoryRace, clearStorage, initialMessages, simulateLiveUpdates, historyScenario, promptFailure) => {
    if (clearStorage) { localStorage.clear(); sessionStorage.clear(); }
    if (initialMessages.length) {
      const canonical = Object.fromEntries(initialMessages.filter(message => message.canonicalId).map(message => [message.canonicalId, message]));
      const pending = Object.fromEntries(initialMessages.filter(message => !message.canonicalId && message.role === 'user' && ['sending', 'failed'].includes(message.delivery)).map(message => [message.id, message]));
      localStorage.setItem('magistrate.chat.canonical.v1.captain', JSON.stringify({ schema_version: 'conversation-cache.v1', messages: canonical }));
      localStorage.setItem('magistrate.chat.pending.v1.captain', JSON.stringify({ schema_version: 'conversation-pending.v1', messages: pending }));
    }
    const nativeFetch = window.fetch.bind(window);
    let promptSent = false;
    let postPromptHistoryRequests = 0;

    // A stand-in for the gateway's canonical conversation record (see
    // gateway/app/conversation_store.py). It lives in its own storage key so it
    // survives a reload exactly as the server-side record does, separately from
    // the app's own cache of it.
    const RECORD_KEY = '__mockGatewayConversation';
    if (clearStorage) localStorage.removeItem(RECORD_KEY);
    const loadRecord = () => {
      try {
        const parsed = JSON.parse(localStorage.getItem(RECORD_KEY));
        if (parsed && Array.isArray(parsed.turns)) return parsed;
      } catch { /* a fresh record */ }
      return { turns: (historyScenario?.seedTurns || []).map((turn, index) => ({ status: 'answered', tools: [], index, createdAt: 1756000000000, ...turn })) };
    };
    const saveRecord = record => localStorage.setItem(RECORD_KEY, JSON.stringify(record));
    const canonicalMessages = () => {
      const messages = [];
      loadRecord().turns.forEach(turn => {
        const turnId = `ct_${turn.index}`;
        const base = { turn_id: turnId, turn_status: turn.status, revision: 1, created_at: turn.createdAt };
        messages.push({ ...base, id: `cm_${turn.index}_u`, client_message_id: turn.clientMessageId, role: 'user', type: 'conversation', text: turn.text, visible_in_chat: true, sequence_index: turn.index * 1000, source: turn.source || 'text', attachments: turn.attachments || [] });
        (turn.tools || []).forEach((tool, position) => messages.push({ ...base, id: `cm_${turn.index}_t${position}`, role: 'assistant', type: 'tool', text: tool, visible_in_chat: false, sequence_index: turn.index * 1000 + 1 + position }));
        if (turn.reply) messages.push({ ...base, id: `cm_${turn.index}_a`, role: 'assistant', type: 'conversation', text: turn.reply, visible_in_chat: true, sequence_index: turn.index * 1000 + 999, revision: turn.replyRevision || 1 });
      });
      return [...messages, ...(historyScenario?.extras || [])];
    };
    // The gateway records a reply the harness returned synchronously and refuses
    // a transport acknowledgement; see _prompt_response in herdr_client.py.
    const syntheticReply = () => {
      if (!responseText || !responseText.trim()) return null;
      try {
        const envelope = JSON.parse(responseText);
        const source = envelope && typeof envelope === 'object' && envelope.result && typeof envelope.result === 'object' ? envelope.result : envelope;
        const value = source && typeof source === 'object' ? source.response || source.text : null;
        return typeof value === 'string' && value.trim() ? value.trim() : null;
      } catch { return responseText.trim(); }
    };
    // Later phases patch the newest turn, reproducing a reply the gateway keeps
    // revising as the terminal snapshot it ingests grows.
    const applyTurnPhase = () => {
      const phases = historyScenario?.turnPhases;
      if (!phases) return;
      const record = loadRecord();
      const turn = record.turns[record.turns.length - 1];
      if (!turn || turn.frozen) return;
      Object.assign(turn, phases[Math.min(postPromptHistoryRequests, phases.length - 1)]);
      if (turn.reply) turn.status = 'answered';
      saveRecord(record);
    };
    if (historyScenario) {
      class FakeWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = FakeWebSocket.CONNECTING; window.__historySocket = this; setTimeout(() => { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }, 10); }
        send() {
          if (historyScenario.manual) return;
          const emit = () => {
            if (!promptSent || this.readyState !== FakeWebSocket.OPEN) { setTimeout(emit, 20); return; }
            // The same event is delivered twice by the socket and again by the
            // poll: canonical identity must converge on one row.
            const payload = JSON.stringify({ type: 'conversation_messages', target: 'captain', messages: canonicalMessages() });
            setTimeout(() => { this.onmessage?.({ data: payload }); this.onmessage?.({ data: payload }); }, historyScenario.delay || 0);
            setTimeout(emit, 700);
          };
          emit();
        }
        close() { this.readyState = FakeWebSocket.CLOSED; this.onclose?.(); }
      }
      window.WebSocket = FakeWebSocket;
    }
    let uploadCount = 0;
    let historyRequests = 0;
    let agentHistoryRequests = 0;
    let attentionRequests = 0;
    window.__magistrateApiCalls = [];
    window.__attentionRequests = () => attentionRequests;
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST'
          ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' }
          : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/uploads')) {
        uploadCount += 1;
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        return Promise.resolve(new Response(JSON.stringify({ uploads: [{ upload_id: `upload-${String(uploadCount).padStart(16, '0')}`, filename: 'package.json', media_type: 'application/json', size: 123, status: 'stored', attached: true }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/captain/prompt')) {
        promptSent = true;
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        if (promptFailure) return Promise.resolve(new Response(JSON.stringify({ detail: 'Gateway lost the active run.' }), { status: 502, headers: { 'Content-Type': 'application/json' } }));
        // One turn per message_id, exactly as the gateway records it.
        const body = JSON.parse(options?.body || '{}');
        const record = loadRecord();
        let turn = record.turns.find(item => item.clientMessageId === body.message_id);
        const canonicalAttachments = (body.attachments || []).map(attachment => ({
          id: attachment.upload_id, upload_id: attachment.upload_id, name: attachment.filename,
          media_type: attachment.media_type, size: attachment.size,
          url: `/api/v1/uploads/${attachment.upload_id}`,
        }));
        if (!turn) {
          turn = { clientMessageId: body.message_id, text: body.text, status: 'awaiting_reply', tools: [], index: record.turns.length, createdAt: Date.now(), attachments: canonicalAttachments };
          record.turns.push(turn);
        } else {
          turn.text = body.text;
          turn.attachments = canonicalAttachments;
        }
        const reply = syntheticReply();
        if (reply) { turn.reply = reply; turn.status = 'answered'; }
        saveRecord(record);
        const turnId = `ct_${turn.index}`;
        const response = new Response(JSON.stringify({
          status: 'submitted', target: 'captain', response: responseText, message_id: body.message_id,
          conversation: { schema_version: 'conversation.v1', target: 'captain', turn_id: turnId, messages: canonicalMessages().filter(message => message.turn_id === turnId) },
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        return historyScenario?.promptDelay ? new Promise(resolve => setTimeout(() => resolve(response), historyScenario.promptDelay)) : Promise.resolve(response);
      }
      if (url.includes('/api/v1/conversations/captain/messages')) {
        window.__canonicalReads = (window.__canonicalReads || 0) + 1;
        window.__historyRequests = [...(window.__historyRequests || []), url];
        if (promptSent && !historyScenario?.manual) { applyTurnPhase(); postPromptHistoryRequests += 1; }
        const payload = JSON.stringify({ schema_version: 'conversation.v1', target: 'captain', messages: canonicalMessages() });
        const response = new Response(payload, { status: 200, headers: { 'Content-Type': 'application/json' } });
        return promptSent && postPromptHistoryRequests === 1 && historyScenario?.delay
          ? new Promise(resolve => setTimeout(() => resolve(response), historyScenario.delay))
          : Promise.resolve(response);
      }
      if (url.includes('/cancel')) {
        window.__magistrateApiCalls.push({ url, method: options?.method });
        const record = loadRecord();
        const clientMessageId = decodeURIComponent(url.split('/turns/')[1].split('/')[0]);
        const turn = record.turns.find(item => item.clientMessageId === clientMessageId);
        // A cancelled turn is frozen: later harness output is never its reply.
        if (turn) { turn.status = 'cancelled'; turn.frozen = true; }
        saveRecord(record);
        return Promise.resolve(new Response(JSON.stringify({ status: 'cancelled' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/interrupt')) {
        window.__magistrateApiCalls.push({ url, method: options?.method });
        return Promise.resolve(new Response(JSON.stringify({ status: 'interrupted', target: 'captain' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/execution/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          harnesses: noOverrides ? [] : [
            { id: 'codex', label: 'Codex', verified: true, models: [{ id: 'gpt-5', label: 'GPT-5' }] },
            { id: 'reviewer', label: 'Reviewer', verified: true, models: [{ id: 'review-model', label: 'Review Model' }] },
          ], profiles: noOverrides ? [] : [
            { id: 'codex:gpt-5', variant: 'gpt-5', label: 'GPT-5', harness: { id: 'codex', label: 'Codex' }, provider: { id: 'openai-codex', label: 'OpenAI Codex' }, model: { id: 'gpt-5', label: 'GPT-5' }, verified: true, available: true, availability: 'available', auth: { required: false, credential_key: 'openai-codex', status: 'not-required' } },
            { id: 'reviewer:review-model', variant: 'review-model', label: 'Review Model', harness: { id: 'reviewer', label: 'Reviewer' }, provider: { id: 'anthropic', label: 'Anthropic' }, model: { id: 'review-model', label: 'Review Model' }, verified: true, available: true, availability: 'available', auth: { required: false, credential_key: 'anthropic', status: 'not-required' } },
            { id: 'pi:default', variant: 'default', label: 'GPT-5.6 Luna', harness: { id: 'pi', label: 'Pi' }, provider: { id: 'openai-codex', label: 'OpenAI Codex' }, model: { id: 'gpt-5.6-luna', label: 'GPT-5.6 Luna' }, verified: true, available: true, availability: 'available', auth: { required: false, credential_key: 'openai-codex', status: 'not-required' } },
          ], source: 'test', configured: !noOverrides,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/execution/settings')) return Promise.resolve(new Response(JSON.stringify({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/usage')) return Promise.resolve(new Response(JSON.stringify({ generated_at: '2026-08-29T18:00:00Z', schema_version: 5, source: 'quota-axi', providers: [{ provider: 'codex', plan: 'plus', status: 'fresh', stale: false, windows: [{ label: 'week', percentRemaining: 20 }] }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'healthy', service: 'gateway', herdr_socket_connected: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      // The captain thread never reads terminal history any more. Answering it
      // with a leaky payload proves the canvas ignores it entirely.
      if (url.includes('/api/v1/agents/captain/history')) {
        window.__captainTerminalReads = (window.__captainTerminalReads || 0) + 1;
        return Promise.resolve(new Response(JSON.stringify({ target: 'captain', messages: [
          { id: 'terminal-leak', role: 'assistant', kind: 'conversation', text: 'Terminal-derived row that must never render.' },
        ] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (historyScenario?.workerPhases && url.includes('/api/v1/agents/w1%3Ap7/history')) {
        const phases = historyScenario.workerPhases;
        const messages = phases[Math.min(agentHistoryRequests, phases.length - 1)];
        agentHistoryRequests += 1;
        return Promise.resolve(new Response(JSON.stringify({ target: 'w1:p7', messages }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/agents/w1%3Ap7/history')) {
        agentHistoryRequests += 1;
        if (simulateHistoryRace) {
          historyRequests += 1;
          const messages = promptSent ? [{ role: 'assistant', kind: 'conversation', text: responseText }] : [];
          const payload = JSON.stringify({ target: 'w1:p7', messages });
          return historyRequests === 1
            ? new Promise(resolve => setTimeout(() => resolve(new Response(payload, { status: 200, headers: { 'Content-Type': 'application/json' } })), 400))
            : Promise.resolve(new Response(payload, { status: 200, headers: { 'Content-Type': 'application/json' } }));
        }
        const baseline = { id: 'fleet-baseline', role: 'assistant', kind: 'conversation', text: 'Existing baseline.' };
        const messages = agentHistoryRequests === 1 ? [baseline] : [
          baseline,
          { id: 'fleet-user-live', role: 'user', kind: 'conversation', text: 'Please check the deployment.' },
          { id: 'fleet-tool-live', role: 'assistant', kind: 'tool', text: 'Ran 3 commands' },
          { id: 'fleet-agent-live', role: 'assistant', kind: 'conversation', text: 'The deployment is healthy.' },
        ];
        return Promise.resolve(new Response(JSON.stringify({ target: 'w1:p7', messages }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response(JSON.stringify([{ id: 'w1:p7', name: 'Deploy agent', status: 'working', harness: 'codex' }]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/attention/unified')) {
        attentionRequests += 1;
        const items = simulateLiveUpdates && attentionRequests > 1 ? [{ id: 'attention-live', provider: 'firstmate', title: 'New decision', subtitle: 'Choose next step', status: 'blocked', url: '', requires_action: true }] : [];
        return Promise.resolve(new Response(JSON.stringify(items), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/github/pulls')) return Promise.resolve(new Response(JSON.stringify({ items: [], page: 1, per_page: 20, has_more: false, cached: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/auth/providers')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/voice/capabilities')) return Promise.resolve(new Response(JSON.stringify({ schema_version: 'voice-capabilities.v1', provider: 'openai', configured: true, modes: [{ id: 'openai', label: 'Gateway OpenAI', available: true }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/voice/transcribe')) return Promise.resolve(new Response(JSON.stringify({ text: 'test transcript from mic', is_final: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  }, emptyInventory, promptResponseText, historyRace, !preserveStorage, seedMessages, liveUpdates, chatHistoryScenario, promptFailure);
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: colorScheme }]);
  await page.goto(route, { waitUntil: 'networkidle0' });
  // Expo's development-only #error-toast has a zero-sized box but can still
  // win hit-testing near the viewport bottom in headless Chrome.
  await page.evaluate(() => { const toast = document.getElementById('error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.waitForSelector('[data-testid="model-menu-button"]');
  // The product publishes this only after the authoritative conversation read
  // has been applied and committed. Do not race canonical hydration with a
  // fixed sleep or infer readiness from whichever row happened to paint first.
  await page.waitForSelector('[data-testid="chat-history"][aria-busy="false"]', { timeout: HISTORY_READY_TIMEOUT });
  return page;
}

async function submit(page, text) {
  const expected = (await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length)) + 1;
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type(text);
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(expectedCount => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length >= expectedCount, {}, expected);
}

test('chat starts genuinely empty with one branded logo and a minimal composer', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  assert.equal((await page.$$('[data-testid="chat-history"] img')).length, 0);
  assert.equal((await page.$eval('[data-testid="chat-history"]', element => element.innerText)).trim(), '');
  assert.equal((await page.$$('[data-testid="brand-drawer-toggle"] img')).length, 1);
  const body = await page.evaluate(() => document.body.innerText);
  assert.doesNotMatch(body, /Firstmate|melkezic/i);
  // Opening Chat and its recurring history refresh must never submit a prompt;
  // prompts are sent only after an explicit composer action.
  await new Promise(resolve => setTimeout(resolve, 3200));
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length), 0);
  assert.equal((await page.$$('[data-testid="model-menu-button"]')).length, 1);
  await page.close();
});

test('chat starts at the measured latest content and preserves older-message reading position', async () => {
  // Six recorded turns, straight from the canonical record.
  const seedTurns = Array.from({ length: 6 }, (_, index) => ({
    clientMessageId: `seed-${index}`, text: `turn ${index * 2}`, reply: `turn ${index * 2 + 1}`,
  }));
  const page = await openChat({ width: 900, height: 700 }, false, 'Reply', URL, 0, false, false, 'light', [], false, { manual: true, seedTurns });
  await page.waitForFunction(() => document.querySelectorAll('[data-testid^="user-message-seed-"]').length === 6);
  const fixedControls = () => page.evaluate(() => {
    const logo = document.querySelector('[data-testid="brand-drawer-toggle"]').getBoundingClientRect();
    const composer = document.querySelector('[data-testid="captain-prompt"]').closest('[class]')?.getBoundingClientRect();
    return { logo: { x: logo.x, y: logo.y }, composer: composer ? { x: composer.x, y: composer.y } : null };
  });
  const fixedBefore = await fixedControls();

  const metrics = () => page.$eval('[data-testid="chat-history"]', element => ({
    scrollTop: element.scrollTop,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  }));
  let position = await metrics();
  assert.ok(position.scrollTop + position.clientHeight >= position.scrollHeight - 2, 'initial messages should end at the latest content');
  const typography = await page.$eval('[data-testid^="user-message-seed-"]', element => {
    const text = element.querySelector('[data-testid^="user-message-text-"]');
    return text ? { fontSize: getComputedStyle(text).fontSize, lineHeight: getComputedStyle(text).lineHeight } : null;
  });
  assert.ok(typography && Number.parseFloat(typography.fontSize) >= 17);
  assert.ok(typography && Number.parseFloat(typography.lineHeight) >= 26);

  await page.evaluate(() => {
    const element = document.querySelector('[data-testid="chat-history"]');
    element.scrollTop = 0;
    element.dispatchEvent(new Event('scroll', { bubbles: true }));
  });
  await page.waitForSelector('[data-testid="jump-to-latest"]');
  position = await metrics();
  assert.ok(position.scrollTop + position.clientHeight < position.scrollHeight - 2, 'user scroll should leave the viewport above the end');
  assert.deepEqual(await fixedControls(), fixedBefore, 'logo and composer must remain detached from history scrolling');
  assert.equal((await page.$eval('[data-testid="jump-to-latest"]', element => element.innerText)).trim(), '↓');
  const jumpStyle = await page.$eval('[data-testid="jump-to-latest"]', element => ({ background: getComputedStyle(element).backgroundColor, width: getComputedStyle(element).width }));
  assert.match(jumpStyle.background, /rgba\(17, 23, 34, 0\.62\)/);
  assert.equal(jumpStyle.width, '38px');

  await submit(page, 'while reading older messages');
  await page.waitForSelector('[data-testid="jump-to-latest"]');
  position = await metrics();
  assert.ok(position.scrollTop + position.clientHeight < position.scrollHeight - 2, 'new content must not steal an older-message reading position');

  await page.click('[data-testid="jump-to-latest"]');
  await page.waitForFunction(() => {
    const element = document.querySelector('[data-testid="chat-history"]');
    return element.scrollTop + element.clientHeight >= element.scrollHeight - 2 && !document.querySelector('[data-testid="jump-to-latest"]');
  });
  await page.evaluate(() => {
    const element = document.querySelector('[data-testid="chat-history"]');
    element.scrollTop = 0; element.dispatchEvent(new Event('scroll', { bubbles: true }));
  });
  await page.waitForSelector('[data-testid="jump-to-latest"]');
  await page.focus('[data-testid="jump-to-latest"]');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => !document.querySelector('[data-testid="jump-to-latest"]'));
  await submit(page, 'at the latest');
  await page.waitForFunction(() => {
    const element = document.querySelector('[data-testid="chat-history"]');
    return element.scrollTop + element.clientHeight >= element.scrollHeight - 2;
  });
  position = await metrics();
  assert.ok(position.scrollTop + position.clientHeight >= position.scrollHeight - 2, 'new content at the end should stay at the end');
  await page.close();
});

test('floating chat controls stay pressable and leave the final message clear', async () => {
  // Seed the gateway record, not the local mirror: an authoritative captain
  // sync intentionally discards local-only rows from the terminal era.
  const seedTurns = Array.from({ length: 10 }, (_, index) => ({
    clientMessageId: `floating-${index * 2}`,
    text: `floating turn ${index * 2}`,
    reply: `floating turn ${index * 2 + 1}`,
  }));
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, { manual: true, seedTurns });
  await page.waitForFunction(() => document.querySelectorAll('[data-testid^="user-message-floating-"]').length === 10);
  await page.$eval('[data-testid="chat-history"]', element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  await page.waitForSelector('[data-testid="jump-to-latest"]');

  await page.$eval('[data-testid="chat-history"]', element => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  const controls = await page.evaluate(() => {
    const history = document.querySelector('[data-testid="chat-history"]');
    const logo = document.querySelector('[data-testid="brand-drawer-toggle"]');
    const composer = document.querySelector('[data-testid="captain-prompt"]');
    const last = document.querySelector('[data-testid="user-message-floating-18"]');
    const composerRect = composer.getBoundingClientRect();
    const lastRect = last.getBoundingClientRect();
    return {
      logoVisible: (() => { const rect = logo.getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= innerHeight; })(),
      composerVisible: composerRect.top >= 0 && composerRect.bottom <= innerHeight,
      lastClear: lastRect.bottom <= composerRect.top,
      bottomInset: history.scrollHeight - history.clientHeight,
    };
  });
  assert.equal(controls.logoVisible, true);
  assert.equal(controls.composerVisible, true);
  assert.equal(controls.lastClear, true, 'the final message must scroll clear of the floating composer');
  assert.ok(controls.bottomInset > 0, 'history needs an inset for the detached composer');

  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForSelector('[data-testid="magistrate-drawer"]');
  await page.focus('[data-testid="captain-prompt"]');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-testid')), 'captain-prompt');
  await page.close();
});

test('drawer Settings and Account remain pinned above a long scrolling list', async () => {
  const page = await openChat({ width: 390, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForSelector('[data-testid="magistrate-drawer"]');
  await page.$eval('[data-testid="drawer-scroll"]', element => {
    const content = element.firstElementChild || element;
    for (let index = 0; index < 30; index += 1) {
      const row = document.createElement('div'); row.textContent = `long drawer item ${index}`; row.style.height = '42px'; row.style.padding = '10px 4px'; content.appendChild(row);
    }
  });
  const before = await page.evaluate(() => ['drawer-settings-control', 'settings-open'].map(id => {
    const rect = document.querySelector(`[data-testid="${id}"]`).getBoundingClientRect();
    return { id, top: rect.top, bottom: rect.bottom, height: rect.height };
  }));
  await page.$eval('[data-testid="drawer-scroll"]', element => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  const after = await page.evaluate(() => ['drawer-settings-control', 'settings-open'].map(id => {
    const rect = document.querySelector(`[data-testid="${id}"]`).getBoundingClientRect();
    return { id, top: rect.top, bottom: rect.bottom, height: rect.height, visible: rect.top >= 0 && rect.bottom <= innerHeight };
  }));
  assert.deepEqual(after.map(item => ({ id: item.id, height: item.height })), before.map(item => ({ id: item.id, height: item.height })));
  assert.ok(after.every(item => item.visible), 'drawer footer controls must remain visible');
  await page.click('[data-testid="drawer-settings-control"]');
  await page.waitForSelector('[data-testid="settings-sheet"]');
  await page.close();
});

test('attachment menu picks a file, previews it, and requires a descriptive message', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await page.click('[data-testid="attachment-menu-button"]');
  await page.waitForSelector('[data-testid="attachment-menu"]');
  assert.match(await page.$eval('[data-testid="attachment-menu"]', element => element.innerText), /Photos[\s\S]*Files/);

  const chooserPromise = page.waitForFileChooser();
  await page.click('[data-testid="attachment-option-files"]');
  const chooser = await chooserPromise;
  await chooser.accept([path.join(process.cwd(), 'package.json')]);
  await page.waitForFunction(() => document.querySelector('[data-testid="attachment-preview"]')?.innerText.includes('package.json'));

  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="captain-send-error"]');
  assert.equal(await page.$eval('[data-testid="captain-send-error"]', element => element.innerText), 'Add a message describing the attached file before sending.');
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length), 0);

  await page.click('[data-testid^="remove-file-"]');
  await page.waitForFunction(() => !document.querySelector('[data-testid="attachment-preview"]'));
  await page.close();
});

test('attachment upload is associated with the message and survives the visible history', async () => {
  const page = await openChat({ width: 1100, height: 760 }, false, 'Attachment received.', URL, 0, false, true);
  const chooserPromise = page.waitForFileChooser();
  await page.click('[data-testid="attachment-menu-button"]');
  await page.click('[data-testid="attachment-option-files"]');
  const chooser = await chooserPromise;
  await chooser.accept([path.join(process.cwd(), 'package.json')]);
  await page.waitForFunction(() => document.querySelector('[data-testid="attachment-preview"]')?.innerText.includes('package.json'));
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('Review the attached manifest');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/captain/prompt')));
  const promptBody = JSON.parse(await page.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')).body));
  assert.equal(promptBody.text, 'Review the attached manifest');
  assert.equal(promptBody.attachments.length, 1);
  assert.equal(promptBody.attachments[0].filename, 'package.json');
  assert.match(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /package\.json.*application\/json/);
  assert.equal(await page.$('[data-testid^="message-sending-"]'), null);
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]')?.innerText.includes('package.json'));
  await page.close();
});

test('open chat refreshes a newly arriving attention item without a page reload', async () => {
  const page = await openChat({ width: 1100, height: 760 }, false, '', URL, 0, false, false, 'light', [], true);
  await new Promise(resolve => setTimeout(resolve, 16000));
  assert.ok(await page.evaluate(() => window.__attentionRequests() > 1), 'the live refresh should poll attention while chat is open');
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.$eval('[data-testid="drawer-section-attention"]', element => element.click());
  await page.waitForFunction(() => document.querySelector('[data-testid="magistrate-drawer"]')?.innerText.includes('New decision'));
  assert.match(await page.$eval('[data-testid="drawer-section-attention"]', element => element.innerText), /1/);
  await page.close();
});

test('drawer starts collapsed, expands downward, and preserves conversation history', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await submit(page, 'keep this message');
  assert.equal(await page.$eval('[data-testid="magistrate-drawer"]', element => getComputedStyle(element).opacity), '0');
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.evaluate(() => document.querySelector('[data-testid="drawer-section-attention"]').click());
  await page.waitForSelector('[data-testid="drawer-panel-attention"]');
  assert.match(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /keep this message/);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) < 0.05);
  assert.match(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /keep this message/);
  await page.close();
});

test('drawer scales every icon by 20% without showing dropdown arrows or changing its controls', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  const sizes = await page.evaluate(() => ({
    row: getComputedStyle(document.querySelector('[data-testid="drawer-section-attention-icon"]')).fontSize,
    account: getComputedStyle(document.querySelector('[data-testid="drawer-account-icon"]')).fontSize,
    gear: document.querySelector('[data-testid="settings-gear-icon"]').getBoundingClientRect().width,
    rowHitTarget: document.querySelector('[data-testid="drawer-section-attention"]').getBoundingClientRect().height,
  }));
  assert.equal(sizes.row, '16.8px');
  assert.equal(sizes.account, '22.8px');
  assert.ok(Math.abs(sizes.gear - 21.6) < 0.02);
  assert.equal(sizes.rowHitTarget, 42);
  assert.doesNotMatch(await page.$eval('[data-testid="magistrate-drawer"]', element => element.innerText), /⌃|⌄/);

  await page.close();
});

test('mobile drawer slides chat aside, swipes closed, and composer focus is stable', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  const before = await page.$eval('[data-testid="branded-chat-shell"]', element => ({ left: element.getBoundingClientRect().left, width: element.getBoundingClientRect().width }));
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(left => document.querySelector('[data-testid="branded-chat-shell"]').getBoundingClientRect().left > left + 100, {}, before.left);
  const drawer = await page.$eval('[data-testid="magistrate-drawer"]', element => { const rect = element.getBoundingClientRect(); return { left: rect.left, right: rect.right, top: rect.top, height: rect.height }; });
  const client = await page.createCDPSession();
  const start = { x: drawer.right - 20, y: drawer.top + drawer.height / 2 };
  await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] });
  for (let step = 1; step <= 8; step += 1) {
    await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x - step * 28, y: start.y }] });
  }
  await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) < 0.05);
  // The drawer fade and the chat shell slide-back are separate springs, so wait
  // for the shell to actually settle before measuring it.
  await page.waitForFunction(left => Math.abs(document.querySelector('[data-testid="branded-chat-shell"]').getBoundingClientRect().left - left) < 2, {}, before.left);
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('status please');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'status please');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => getComputedStyle(element).fontSize), '16px');
  const after = await page.$eval('[data-testid="branded-chat-shell"]', element => ({ left: element.getBoundingClientRect().left, width: element.getBoundingClientRect().width }));
  assert.ok(Math.abs(after.left - before.left) < 2);
  assert.ok(Math.abs(after.width - before.width) < 2);
  await page.close();
});

// Regression for a viewport jump right after navigating into Chat: mobile
// browsers hand the page a visualViewport that can be a few px shorter than
// window.innerHeight immediately after a navigation (the address bar is
// still animating), with no keyboard open. The chat canvas used to snapshot
// that mismatch unconditionally on mount and pin an explicit height, which
// then snapped back once a later resize/paint corrected it - a visible jump
// before the screen ever settled. It must only pin a height once the visual
// viewport is genuinely smaller than the window (a real on-screen keyboard).
test('entering chat with a momentarily short visualViewport (no keyboard) does not pin a stale canvas height', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true }, false, '', URL, 4);
  const height = await page.$eval('[data-testid="branded-chat-shell"]', element => element.style.height);
  assert.equal(height, '', 'the canvas must not pin an explicit height when no keyboard is open');
  const rect = await page.$eval('[data-testid="branded-chat-shell"]', element => element.getBoundingClientRect().height);
  assert.ok(rect > 647, `the canvas should retain the settled layout height, got ${rect}`);
  await page.close();
});

// Regression for browser zoom-on-focus: mobile Safari/Chrome zoom the whole
// page in when a focused text input's computed font-size is under 16px.
// Cover every text input reachable from the chat screen, not just the
// composer, so a future input doesn't silently reintroduce the zoom.
test('every focusable text input on the chat screen stays at 16px or larger to avoid mobile zoom-on-focus', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  await page.focus('[data-testid="captain-prompt"]');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => getComputedStyle(element).fontSize), '16px');
  const scaleAfterComposerFocus = await page.evaluate(() => window.visualViewport.scale);
  assert.equal(scaleAfterComposerFocus, 1, 'focusing the composer must not change the page scale');

  // Check every text input rendered by ChatCanvas. The drawer's rename field
  // uses the same mobile-safe minimum in the stylesheet, while this assertion
  // stays independent of asynchronous fleet data and tests the actual focus
  // path that can trigger browser zoom.
  const inputSizes = await page.$$eval('input, textarea', elements => elements.map(element => ({ type: element.type, size: Number.parseFloat(getComputedStyle(element).fontSize) })));
  assert.ok(inputSizes.length > 0);
  assert.ok(inputSizes.every(input => input.size >= 16), `all chat text inputs must be at least 16px: ${JSON.stringify(inputSizes)}`);
  await page.close();
});

// Regression: navigating into Chat from another route (a real SPA
// transition, not a fresh page load) must not change the page scale or
// resize the layout viewport - it's a client-side route change, so anything
// that looks like a "zoom" here is the app's own doing, not the browser's.
test('navigating into chat from another screen preserves the viewport scale and size', async () => {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 667, isMobile: true, hasTouch: true });
  await page.evaluateOnNewDocument(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  });
  await page.goto(`${server.base}/`, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.getElementById('error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForSelector('a[href="/chat"]');
  const before = await page.evaluate(() => ({ scale: window.visualViewport.scale, innerWidth: window.innerWidth, innerHeight: window.innerHeight }));

  // Use the real anchor's click handler, while avoiding Puppeteer's hit-test
  // sensitivity when the mobile home layout is still settling.
  await page.$eval('a[href="/chat"]', element => element.click());
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await new Promise(resolve => setTimeout(resolve, 300));
  const after = await page.evaluate(() => ({ scale: window.visualViewport.scale, innerWidth: window.innerWidth, innerHeight: window.innerHeight }));

  assert.deepEqual(after, before, 'navigating into chat must not change the viewport scale or size');
  await page.close();
});

test('right swipe on the chat screen opens the mobile drawer', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  assert.equal(await page.$eval('[data-testid="magistrate-drawer"]', element => getComputedStyle(element).opacity), '0');
  const shell = await page.$eval('[data-testid="branded-chat-shell"]', element => { const rect = element.getBoundingClientRect(); return { left: rect.left, top: rect.top, height: rect.height }; });
  const client = await page.createCDPSession();
  const start = { x: shell.left + 40, y: shell.top + shell.height / 2 };
  await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] });
  for (let step = 1; step <= 8; step += 1) {
    await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x + step * 28, y: start.y }] });
  }
  await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.close();
});

test('right swipe from the focused composer opens the drawer without losing input state', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('preserve focused draft');
  const composer = await page.$eval('[data-testid="captain-prompt"]', element => { const rect = element.getBoundingClientRect(); return { left: rect.left, top: rect.top, height: rect.height }; });
  const client = await page.createCDPSession();
  const start = { x: composer.left + 20, y: composer.top + composer.height / 2 };
  await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] });
  for (let step = 1; step <= 8; step += 1) {
    await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x + step * 28, y: start.y }] });
  }
  await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'preserve focused draft');
  assert.equal(await page.evaluate(() => location.pathname), '/chat', 'swipe must not trigger browser back navigation');
  await page.close();
});

test('current backend session works without setup and explicit model overrides stay paired', async () => {
  const currentPage = await openChat({ width: 900, height: 700 }, true);
  await submit(currentPage, 'use current');
  const currentBody = JSON.parse(await currentPage.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')).body));
  assert.equal(currentBody.text, 'use current');
  assert.equal(currentBody.harness, undefined);
  assert.equal(currentBody.model, undefined);
  await currentPage.close();

  const overridePage = await openChat({ width: 900, height: 700 });
  await overridePage.click('[data-testid="model-menu-button"]');
  await overridePage.click('[data-testid="model-option-reviewer-review-model"]');
  await submit(overridePage, 'review latest');
  const overrideBody = JSON.parse(await overridePage.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')).body));
  assert.equal(overrideBody.harness, 'reviewer');
  assert.equal(overrideBody.model, 'review-model');
  assert.equal(overrideBody.profile_id, 'reviewer:review-model');
  await overridePage.close();
});

test('Pi remains an atomic harness/provider/model selection', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="model-menu-button"]');
  const option = await page.$('[data-testid="model-option-pi-gpt-5-6-luna"]');
  assert.ok(option);
  assert.match(await option.evaluate(element => element.getAttribute('aria-label')), /Pi, OpenAI Codex, GPT-5.6 Luna/);
  await option.click();
  await submit(page, 'use pi');
  const body = JSON.parse(await page.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')).body));
  assert.equal(body.profile_id, 'pi:default');
  assert.equal(body.harness, 'pi');
  assert.equal(body.model, 'gpt-5.6-luna');
  await page.close();
});

test('usage lives in Settings and reports quota evidence without inventing amounts', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  assert.equal(await page.$('[data-testid="drawer-section-usage"]'), null);
  await page.click('[data-testid="settings-open"]');
  await page.waitForSelector('[data-testid="settings-usage-section"]');
  assert.equal(await page.$('[data-testid="settings-usage-content"]'), null);
  await page.click('[data-testid="settings-section-usage"]');
  await page.waitForSelector('[data-testid="settings-usage-content"]');
  const usage = await page.$eval('[data-testid="settings-usage-section"]', element => element.innerText);
  assert.match(usage, /codex.*plus/i);
  assert.match(usage, /20% left/i);
  await page.close();
});

test('Settings sections are collapsed, keyboard accessible, and retain persisted controls', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.click('[data-testid="settings-open"]');
  await page.waitForSelector('[data-testid="settings-sheet"]');
  const sheetRatio = await page.$eval('[data-testid="settings-sheet"]', element => element.getBoundingClientRect().height / window.innerHeight);
  assert.ok(sheetRatio >= 0.86 && sheetRatio <= 0.92, `settings panel should be about 15% larger: ${sheetRatio}`);
  assert.equal(await page.$('[data-testid="settings-execution-content"]'), null);
  assert.equal(await page.$('[data-testid="settings-voice-mode-options"]'), null);
  const execution = '[data-testid="settings-section-execution"]';
  assert.equal(await page.$eval(execution, element => element.getAttribute('aria-expanded')), 'false');
  await page.focus(execution);
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-testid="settings-execution-content"]');
  assert.equal(await page.$eval(execution, element => element.getAttribute('aria-expanded')), 'true');
  assert.ok(await page.$('[data-testid="switching-option-migrate"]'));
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('[data-testid="settings-execution-content"]') === null);
  assert.equal(await page.$('[data-testid="switching-option-migrate"]'), null);

  await page.click('[data-testid="settings-theme"]');
  await page.waitForSelector('[data-testid="settings-tool-calls-toggle"]');
  await page.click('[data-testid="settings-tool-calls-toggle"]');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.chat.show-tool-calls')), 'true');
  await page.close();
});

test('pending response moves thinking into history and Stop/Escape share interruption without late insertion', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, {
    turnPhases: [{ reply: 'late response must stay hidden' }], promptDelay: 5000,
  });
  await submit(page, 'first request');
  const pendingState = await page.evaluate(() => ({ history: document.querySelector('[data-testid="chat-history"]')?.innerText, stop: Boolean(document.querySelector('[data-testid="stop-captain-response"]')), thinking: Boolean(document.querySelector('[data-testid="agent-thinking-message"]')) }));
  assert.equal(pendingState.stop, true, `expected pending control; state=${JSON.stringify(pendingState)}`);
  assert.equal(pendingState.thinking, true, `expected in-conversation thinking; state=${JSON.stringify(pendingState)}`);
  assert.equal(await page.$('[data-testid="composer-status"] [data-testid="thinking-dots"]'), null, 'thinking must not sit under the composer');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('[data-testid="agent-thinking-message"]'));
  assert.ok(await page.$('[data-testid="send-captain-prompt"]'));
  assert.ok(await page.$('[data-testid^="message-cancelled-"]'));
  assert.equal((await page.$$('[data-testid^="user-message-u-"]')).length, 1, 'the captain message remains available');
  // The cancelled turn is frozen server-side, so the harness output that arrives
  // afterwards is never recorded as its reply and never rendered.
  await new Promise(resolve => setTimeout(resolve, 3600));
  assert.doesNotMatch(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /late response/);
  assert.equal((await page.$$('[data-testid="agent-message"]')).length, 0);
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/interrupt')).length), 1);
  assert.ok(await page.evaluate(() => window.__magistrateApiCalls.some(call => call.url.includes('/cancel'))), 'stopping must cancel the canonical turn');
  await page.close();
});

test('account gear opens the lower settings drawer with live network status', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  const target = await page.$eval('[data-testid="settings-open"]', element => { const rect = element.getBoundingClientRect(); return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, visible: rect.top >= 0 && rect.bottom <= innerHeight }; });
  assert.equal(target.visible, true);
  await page.mouse.click(target.x, target.y);
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="settings-sheet"]')).opacity) > 0.95);
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) < 0.05);
  await page.click('[data-testid="settings-theme"]');
  await page.waitForSelector('[data-testid="settings-appearance-window"]');
  assert.ok(await page.$('[data-testid="settings-tool-calls-toggle"]'));
  await page.click('[data-testid="settings-appearance-close"]');
  assert.equal(await page.$eval('[data-testid="settings-network-status"]', element => element.textContent), 'Connected');
  const ratio = await page.$eval('[data-testid="settings-sheet"]', element => element.getBoundingClientRect().height / window.innerHeight);
  assert.ok(ratio >= 0.86 && ratio <= 0.92);
  await page.$eval('[data-testid="settings-close"]', element => element.click());
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="settings-sheet"]')).opacity) < 0.05);
  await page.close();
});

test('settings default to system theme and automatic background, then persist explicit dark mode across refresh', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light');
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="settings-open"]');
  await page.waitForSelector('[data-testid="settings-usage-section"]');
  await page.click('[data-testid="settings-theme"]');
  assert.equal(await page.$eval('[data-testid="theme-option-system"]', element => getComputedStyle(element).backgroundColor), 'rgb(36, 216, 255)');
  assert.equal(await page.$eval('[data-testid="background-option-auto"]', element => getComputedStyle(element).backgroundColor), 'rgb(36, 216, 255)');
  await page.click('[data-testid="theme-option-dark"]');
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="branded-chat-shell"]')).backgroundColor.includes('10, 14, 20'));
  await page.close();

  const refreshed = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true, 'dark');
  await refreshed.click('[data-testid="brand-drawer-toggle"]');
  await refreshed.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await refreshed.click('[data-testid="settings-open"]');
  await refreshed.click('[data-testid="settings-theme"]');
  assert.equal(await refreshed.$eval('[data-testid="theme-option-dark"]', element => getComputedStyle(element).backgroundColor), 'rgb(36, 216, 255)');
  assert.match(await refreshed.$eval('[data-testid="branded-chat-shell"]', element => getComputedStyle(element).backgroundColor), /rgba\(10, 14, 20, 0\.78\)/);
  await refreshed.close();
});

test('system theme follows the OS palette without retaining a stale light surface', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'dark');
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="settings-open"]');
  await page.click('[data-testid="settings-theme"]');
  await page.click('[data-testid="theme-option-system"]');
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="branded-chat-shell"]')).backgroundColor.includes('10, 14, 20'));
  assert.equal(await page.$eval('[data-testid="theme-option-system"]', element => getComputedStyle(element).backgroundColor), 'rgb(36, 216, 255)');
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'light' }]);
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="branded-chat-shell"]')).backgroundColor.includes('255, 255, 255'));
  await page.close();
});

test('fleet agent opens its conversation, hides tools by default, and settings can reveal them', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.evaluate(() => document.querySelector('[data-testid="drawer-section-fleet"]').click());
  await page.waitForSelector('[data-testid="fleet-agent-w1:p7"]');
  const fleetName = await page.$eval('[data-testid="fleet-agent-w1:p7"]', element => element.innerText);
  assert.match(fleetName, /Deploy agent/);
  assert.doesNotMatch(fleetName, /w1:p7/, 'Fleet Summary should present the assigned name rather than raw pane diagnostics');
  await page.evaluate(() => document.querySelector('[data-testid="fleet-agent-w1:p7"]').click());
  await page.waitForFunction(() => new URL(location.href).searchParams.get('agentId') === 'w1:p7');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('The deployment is healthy.'));
  let history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /Please check the deployment/);
  assert.match(history, /The deployment is healthy/);
  assert.doesNotMatch(history, /Ran 3 commands/);
  assert.equal(await page.$('[data-testid="tool-history-message"]'), null);

  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) < 0.05);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="settings-open"]');
  await page.waitForSelector('[data-testid="settings-theme"]');
  await page.click('[data-testid="settings-theme"]');
  await page.waitForSelector('[data-testid="settings-tool-calls-toggle"]');
  await page.click('[data-testid="settings-tool-calls-toggle"]');
  await page.waitForSelector('[data-testid="tool-history-message"]');
  history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /(?:^|\n)Ran(?:\n|$)/);
  assert.doesNotMatch(history, /Ran 3 commands/);
  await page.close();
});

test('fleet ellipsis shows real status and quick commands', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.evaluate(() => document.querySelector('[data-testid="drawer-section-fleet"]').click());
  await page.evaluate(() => document.querySelector('[data-testid="fleet-agent-w1:p7-menu"]').click());
  const popover = await page.$eval('[data-testid="fleet-agent-w1:p7-popover"]', element => element.innerText);
  assert.match(popover, /STATUS\s+WORKING/);
  assert.match(popover, /ACTIVE STATUS\s+ACTIVE/);
  assert.match(popover, /INTERRUPT/);
  assert.match(popover, /RENAME/);
  await page.close();
});

test('two-second hold exposes edit, copy, and selection for plain messages', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await submit(page, 'editable message');
  const selector = '[data-testid^="user-message-u-"]';
  await page.waitForSelector(selector);
  assert.equal(await page.$eval(`${selector} [data-testid^="user-message-text-"]`, element => element.innerText), 'editable message');
  const rect = await page.$eval(selector, element => { const box = element.getBoundingClientRect(); return { x: box.x + box.width / 2, y: box.y + box.height / 2 }; });
  await page.mouse.move(rect.x, rect.y); await page.mouse.down(); await new Promise(resolve => setTimeout(resolve, 2100)); await page.mouse.up();
  await page.waitForSelector('[data-testid="message-actions"]');
  const actions = await page.$eval('[data-testid="message-actions"]', element => element.innerText);
  assert.match(actions, /Edit/); assert.match(actions, /Copy/); assert.match(actions, /Select text/);
  await page.locator('::-p-text(Edit)').click();
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'editable message');
  await page.close();
});

test('voice input mode selection is explicit and persists without sending a prompt', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.click('[data-testid="settings-open"]');
  await page.click('[data-testid="settings-section-voice-input"]');
  await page.waitForSelector('[data-testid="settings-voice-mode-options"]');
  await page.click('[data-testid="voice-mode-option-openai"]');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.voice.input-mode')), 'openai');
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length), 0);
  await page.close();
  const refreshed = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true);
  await refreshed.click('[data-testid="brand-drawer-toggle"]');
  await refreshed.click('[data-testid="settings-open"]');
  await refreshed.click('[data-testid="settings-section-voice-input"]');
  await refreshed.waitForSelector('[data-testid="voice-mode-option-openai"]');
  assert.equal(await refreshed.evaluate(() => localStorage.getItem('magistrate.voice.input-mode')), 'openai');
  assert.match(await refreshed.$eval('[data-testid="settings-voice-input-section"]', element => element.innerText), /Gateway OpenAI/);
  await refreshed.close();
});

test('mic button records real audio, shows the active mark, and fills the composer for review', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="inline-mic-button"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="inline-mic-button"]').getAttribute('aria-label') === 'Stop microphone');
  assert.ok(await page.$('[data-testid="active-voice-surface"]'));
  assert.match(await page.$eval('[data-testid="mic-status"]', element => element.innerText), /Listening/);
  await new Promise(resolve => setTimeout(resolve, 500));
  await page.click('[data-testid="inline-mic-button"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="captain-prompt"]').value.includes('test transcript from mic'));
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'test transcript from mic');
  assert.match(await page.$eval('[data-testid="mic-status"]', element => element.innerText), /review/i);
  await page.close();
});

test('voice settings expose and persist capture and transcript behavior', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]'); await page.click('[data-testid="settings-open"]');
  await page.click('[data-testid="settings-section-voice-input"]');
  await page.click('[data-testid="voice-capture-option-hold-to-talk"]');
  await page.click('[data-testid="voice-transcript-option-auto-send"]');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.voice.capture-behavior')), 'hold-to-talk');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.voice.transcript-behavior')), 'auto-send');
  await page.close();
  const refreshed = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true);
  await refreshed.click('[data-testid="brand-drawer-toggle"]'); await refreshed.click('[data-testid="settings-open"]'); await refreshed.click('[data-testid="settings-section-voice-input"]');
  assert.ok(await refreshed.$('[data-testid="voice-capture-option-hold-to-talk"]'));
  assert.ok(await refreshed.$('[data-testid="voice-transcript-option-auto-send"]'));
  assert.equal(await refreshed.evaluate(() => localStorage.getItem('magistrate.voice.capture-behavior')), 'hold-to-talk');
  assert.equal(await refreshed.evaluate(() => localStorage.getItem('magistrate.voice.transcript-behavior')), 'auto-send');
  await refreshed.close();
});

test('auto-send and insert choices are visible and persisted as explicit behavior', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]'); await page.click('[data-testid="settings-open"]'); await page.click('[data-testid="settings-section-voice-input"]');
  assert.ok(await page.$('[data-testid="voice-transcript-option-insert"]'));
  assert.ok(await page.$('[data-testid="voice-transcript-option-auto-send"]'));
  await page.click('[data-testid="voice-transcript-option-auto-send"]');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.voice.transcript-behavior')), 'auto-send');
  await page.close();
});

test('the composer mic and wrench icons render at the same size as the voice-mode icon', async () => {
  // Pinned because a rebase that takes the composer wholesale silently reverts
  // these to the 18px default, which reads visually smaller than the
  // send-button's soundwave glyph.
  const page = await openChat({ width: 900, height: 700 });
  const box = async selector => page.$eval(selector, element => {
    const rect = element.querySelector('svg').getBoundingClientRect();
    return { width: Number(rect.width.toFixed(2)), height: Number(rect.height.toFixed(2)) };
  });
  assert.deepEqual(await box('[data-testid="inline-mic-button"]'), { width: 24, height: 24 });
  assert.deepEqual(await box('[data-testid="model-menu-button"]'), { width: 24, height: 24 });
  await page.close();
});

test('a herdr RPC acknowledgement envelope never reaches the conversation', async () => {
  // The prompt endpoint still echoes herdr's raw acknowledgement in `response`;
  // it is transport metadata, so it is never recorded as a canonical reply and
  // the captain thread never reads that field.
  const envelope = JSON.stringify({ jsonrpc: '2.0', id: 7, result: { ok: true, target: 'captain' } });
  const page = await openChat({ width: 900, height: 700 }, false, envelope);
  await submit(page, 'status please');
  await new Promise(resolve => setTimeout(resolve, 1200));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /status please/);
  assert.doesNotMatch(history, /jsonrpc/);
  assert.doesNotMatch(history, /result/);
  assert.doesNotMatch(history, /[{}]/);
  await page.close();
});

test('a response appearing while the initial conversation read is in flight is not lost', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Reply arrived via history.', URL, 0, true, false, 'light', [], false, { delay: 900 });
  await submit(page, 'race response');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Reply arrived via history.'), { timeout: 10_000 });
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /race response/);
  assert.match(history, /Reply arrived via history\./);
  await page.close();
});

test('normalized chat messages persist across a page reload without replaying terminal history', async () => {
  const first = await openChat({ width: 900, height: 700 });
  await submit(first, 'persist this normalized turn');
  await first.close();
  const reloaded = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true);
  await pageWaitForText(reloaded, 'persist this normalized turn');
  assert.match(await reloaded.$eval('[data-testid="chat-history"]', element => element.innerText), /persist this normalized turn/);
  await reloaded.close();
});

async function pageWaitForText(page, text) {
  await page.waitForFunction(expected => document.querySelector('[data-testid="chat-history"]')?.innerText.includes(expected), {}, text);
}
async function cachedCanonicalMessages(page) {
  return page.evaluate(() => {
    const payload = JSON.parse(localStorage.getItem('magistrate.chat.canonical.v1.captain'));
    return Object.values(payload?.messages || {}).sort((left, right) => left.sequenceIndex - right.sequenceIndex);
  });
}
test('the agent response is appended to the conversation once the gateway replies', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Understood, working on it now.');
  await submit(page, 'status please');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Understood, working on it now.'));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /status please/);
  assert.match(history, /Understood, working on it now\./);
  await page.close();
});

test('canonical-first hydration replaces a stale cache, keeps only unacknowledged sends, and uses gateway time', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [
    { id: 'u-stale', role: 'user', kind: 'conversation', text: 'stale local text', source: 'text', audience: 'captain', delivery: 'sent', sentAt: 123, canonicalId: 'cm_0_u', canonicalRevision: 1, sequenceIndex: 0 },
    { id: 'cm-poison', role: 'assistant', kind: 'conversation', text: 'cache-only reply must disappear', source: 'text', audience: 'primary', sentAt: 124, canonicalId: 'cm_poison', canonicalRevision: 1, sequenceIndex: 999 },
    { id: 'u-unacknowledged', role: 'user', kind: 'conversation', text: 'still sending locally', source: 'text', audience: 'captain', delivery: 'sending', sentAt: 1760000000123 },
  ], false, {
    manual: true,
    seedTurns: [{ clientMessageId: 'u-stale', text: 'reconcile this persisted turn', reply: 'The persisted primary response.' }],
  });
  await pageWaitForText(page, 'The persisted primary response.');
  await pageWaitForText(page, 'still sending locally');
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /reconcile this persisted turn/);
  assert.match(history, /The persisted primary response/);
  assert.match(history, /still sending locally/);
  assert.doesNotMatch(history, /stale local text|cache-only reply/);
  assert.ok(history.indexOf('reconcile this persisted turn') < history.indexOf('The persisted primary response'));
  await page.waitForFunction(() => {
    const payload = JSON.parse(localStorage.getItem('magistrate.chat.canonical.v1.captain'));
    return Object.keys(payload?.messages || {}).length === 2;
  });
  const persisted = await cachedCanonicalMessages(page);
  assert.deepEqual(persisted.map(message => message.text), ['reconcile this persisted turn', 'The persisted primary response.']);
  assert.equal(persisted[0].id, 'u-stale', 'client_message_id still joins the optimistic bubble to the canonical row');
  assert.equal(persisted[0].sentAt, 1756000000000, 'gateway millisecond time replaces the optimistic/cache timestamp');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.chat.messages.v2.captain')), null, 'the terminal-era array is invalidated, not merged');
  await page.close();
});

test('internal canonical records are never rendered as conversation', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, {
    manual: true,
    seedTurns: [{ clientMessageId: 'u-captain-only', text: 'captain-only turn', reply: 'The captain reply.' }],
    extras: [
      { id: 'cm_internal_u', turn_id: 'ct_9', role: 'user', type: 'internal', text: 'internal worker turn', visible_in_chat: false, sequence_index: 9000, revision: 1 },
      { id: 'cm_internal_a', turn_id: 'ct_9', role: 'assistant', type: 'internal', text: 'Internal worker reply must stay hidden.', visible_in_chat: false, sequence_index: 9001, revision: 1 },
      { id: 'cm_status', turn_id: 'ct_9', role: 'assistant', type: 'status', text: 'Working (2s)', visible_in_chat: false, sequence_index: 9002, revision: 1 },
    ],
  });
  await pageWaitForText(page, 'The captain reply.');
  await new Promise(resolve => setTimeout(resolve, 1200));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /captain-only turn/);
  assert.doesNotMatch(history, /Internal worker reply|internal worker turn|Working \(2s\)/);
  await page.close();
});

test('a delayed primary response remains paired with its captain message', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, {
    turnPhases: [{ reply: 'The delayed primary response.' }], delay: 1500, promptDelay: 2500,
  });
  await submit(page, 'delayed persistence turn');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('The delayed primary response.'), { timeout: 10_000 });
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /delayed persistence turn/);
  assert.match(history, /The delayed primary response/);
  await page.close();
});

test('an explicitly wrapped response is unwrapped without displaying transport JSON', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, JSON.stringify({ jsonrpc: '2.0', id: 8, result: { response: 'Wrapped reply from the agent.' } }));
  await submit(page, 'wrapped response');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Wrapped reply from the agent.'));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.doesNotMatch(history, /\{"response"/);
  await page.close();
});

// Regression for the captain-reported bug: replaying/reconstructing a large
// Herdr backlog into the chat thread produced infinite-scroll/refresh bugs and
// a visibly jittering terminal pane. Chat is now live-only: it must not render
// existing backlog on open, and its history read must stay bounded rather than
// asking Herdr for its near-unbounded max line count.
test('chat does not replay existing backlog on open and requests a bounded history read', async () => {
  const backlog = [];
  for (let i = 0; i < 100; i += 1) {
    backlog.push({ role: 'user', kind: 'conversation', text: `backlog user turn ${i}` });
    backlog.push({ role: 'assistant', kind: 'conversation', text: `backlog assistant turn ${i}` });
  }
  const page = await browser.newPage();
  await page.setViewport({ width: 900, height: 700 });
  await page.evaluateOnNewDocument((historyMessages) => {
    const nativeFetch = window.fetch.bind(window);
    window.__historyRequests = [];
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/history')) {
        window.__historyRequests.push(url);
        return Promise.resolve(new Response(JSON.stringify({ target: 'w1:p7', messages: historyMessages }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/execution/capabilities')) return Promise.resolve(new Response(JSON.stringify({ harnesses: [], source: 'test', configured: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response(JSON.stringify([{ id: 'w1:p7', name: 'Deploy agent', status: 'working', harness: 'codex' }]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  }, backlog);
  await page.goto(`${URL}?agentId=w1%3Ap7`, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.getElementById('error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForFunction(() => (window.__historyRequests || []).length > 0);

  // Give the poll a couple of ticks to prove the backlog never gets rendered.
  await new Promise(resolve => setTimeout(resolve, 3400));
  assert.equal((await page.$eval('[data-testid="chat-history"]', element => element.innerText)).trim(), '');

  const requestedLines = await page.evaluate(() => {
    const url = new URL(window.__historyRequests[0], window.location.origin);
    return Number(url.searchParams.get('lines'));
  });
  assert.ok(requestedLines > 0 && requestedLines <= 2000, `expected a small bounded lines= request, got ${requestedLines}`);
  await page.close();
});

// Plain chat intentionally omits transport metadata; a live-refresh poll must
// not duplicate or rewrite the visible user message.
test('user bubbles are opaque and repeated legitimate messages remain distinct', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Reply');
  await submit(page, 'same wording');
  await page.waitForFunction(() => !document.querySelector('[data-testid="send-captain-prompt"]').getAttribute('aria-label').startsWith('Queue'));
  await submit(page, 'same wording');
  assert.equal((await page.$$('[data-testid^="user-message-u-"]')).length, 2);
  const bubble = await page.$eval('[data-testid^="user-message-u-"]', element => getComputedStyle(element).backgroundColor);
  assert.match(bubble, /rgb\(36, 216, 255\)/);
  assert.doesNotMatch(bubble, /rgba/);
  assert.doesNotMatch(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /\/calm/);
  await page.close();
});

test('repeated canonical delivery keeps one turn, and tool events stay bounded labels', async () => {
  // The gateway classified these before delivery: the leak classes arrive as
  // internal records and the tool event arrives as a bounded label, never as a
  // shell command. The socket and the poll then redeliver the same records.
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, {
    delay: 500,
    turnPhases: [{ reply: 'Actual response only.', tools: ['Running…'] }],
    extras: [
      { id: 'cm_leak_rpc', turn_id: 'ct_9', role: 'assistant', type: 'internal', text: '{"jsonrpc":"2.0","result":{"ok":true}}', visible_in_chat: false, sequence_index: 9000, revision: 1 },
      { id: 'cm_leak_worker', turn_id: 'ct_9', role: 'user', type: 'internal', text: 'FIRSTMATE_OP: inspect this for Firstmate', visible_in_chat: false, sequence_index: 9001, revision: 1 },
      { id: 'cm_leak_scout', turn_id: 'ct_9', role: 'assistant', type: 'internal', text: 'Scout report for Firstmate only.', visible_in_chat: false, sequence_index: 9002, revision: 1 },
      { id: 'cm_leak_pane', turn_id: 'ct_9', role: 'assistant', type: 'internal', text: 'pane_id=w1:p9 tab_id=secret', visible_in_chat: false, sequence_index: 9003, revision: 1 },
    ],
  });
  await submit(page, 'live source question');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Actual response only.'), { timeout: 10_000 });
  await new Promise(resolve => setTimeout(resolve, 3400));
  assert.equal((await page.$$('[data-testid^="user-message-u-"]')).length, 1);
  assert.equal((await page.$$('[data-testid="agent-message"]')).length, 1);
  let historyText = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.doesNotMatch(historyText, /calm|jsonrpc|hidden-secret|Running npm test|FIRSTMATE_OP|Scout report|pane_id|tab_id|raw-pane|raw-terminal/);
  assert.doesNotMatch(historyText, /Terminal-derived row/, 'the captain thread must ignore terminal history entirely');

  await page.$eval('[data-testid="brand-drawer-toggle"]', element => element.click());
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.$eval('[data-testid="settings-open"]', element => element.click());
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="settings-sheet"]')).opacity) > 0.95);
  await page.$eval('[data-testid="settings-theme"]', element => element.click());
  await page.focus('[data-testid="settings-tool-calls-toggle"] input');
  await page.keyboard.press('Space');
  await page.waitForFunction(() => localStorage.getItem('magistrate.chat.show-tool-calls') === 'true');
  await page.waitForSelector('[data-testid="tool-history-message"]');
  historyText = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(historyText, /Running…/);
  assert.doesNotMatch(historyText, /hidden-secret|npm test|cat|pane/);
  assert.equal((await page.$$('[data-testid="tool-history-message"]')).length, 1);
  await page.close();

  const reloaded = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true, 'light', [], false, { manual: true });
  await reloaded.waitForSelector('[data-testid="tool-history-message"]');
  assert.equal((await reloaded.$$('[data-testid="agent-message"]')).length, 1);
  assert.equal((await reloaded.$$('[data-testid="tool-history-message"]')).length, 1);
  await reloaded.click('[data-testid="brand-drawer-toggle"]');
  await reloaded.click('[data-testid="settings-open"]');
  await reloaded.click('[data-testid="settings-theme"]');
  await reloaded.click('[data-testid="settings-tool-calls-toggle"]');
  await reloaded.waitForFunction(() => !document.querySelector('[data-testid="tool-history-message"]'));
  await reloaded.close();
});

test('gateway millisecond time replaces the optimistic timestamp and survives polling and reload', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await submit(page, 'timestamp check');
  const selector = '[data-testid^="user-message-u-"]';
  await page.waitForSelector(selector);
  assert.equal(await page.$eval(`${selector} [data-testid^="user-message-text-"]`, element => element.innerText), 'timestamp check');
  await page.waitForFunction(() => Object.keys(JSON.parse(localStorage.getItem('magistrate.chat.canonical.v1.captain'))?.messages || {}).length === 1);
  const before = await page.$eval(`${selector} [data-testid^="message-timestamp-"]`, element => element.innerText);
  assert.match(before, /\d/);
  const persisted = (await cachedCanonicalMessages(page))[0];
  const gatewayTime = await page.evaluate(() => JSON.parse(localStorage.getItem('__mockGatewayConversation')).turns[0].createdAt);
  assert.equal(persisted.sentAt, gatewayTime);
  assert.ok(gatewayTime > 1_000_000_000_000, 'the canonical record retains millisecond precision');
  // The background auto-refresh poll runs every 3s (see ChatCanvas's syncFromHistory).
  await new Promise(resolve => setTimeout(resolve, 3400));
  assert.equal(await page.$eval(`${selector} [data-testid^="message-timestamp-"]`, element => element.innerText), before);
  assert.equal((await page.$$(selector)).length, 1);
  await page.close();

  const reloaded = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true);
  await reloaded.waitForSelector(selector);
  assert.equal(await reloaded.$eval(`${selector} [data-testid^="message-timestamp-"]`, element => element.innerText), before);
  const afterReload = (await cachedCanonicalMessages(reloaded))[0].sentAt;
  assert.equal(afterReload, gatewayTime);
  await reloaded.close();
});

// Regression for the deployed-demo report after PRs #61/#63: agent/internal
// metadata rendered as highlighted user messages, and one captain turn plus one
// primary reply rendered twice. The gateway now classifies these at ingestion
// (see gateway/tests/test_conversation_store.py), so the browser cases assert
// the second half of the contract: a client that is handed these records - by a
// stale cache, or by a mislabelled delivery - still renders none of them.
const LEAKED_METADATA = [
  // Pi boxes a tool envelope exactly like a user turn.
  { role: 'user', text: 'edit gateway/app/notifications.py\n\n... 340     elif parsed.path == "/pr-detail":\n341         target_type = "pull-request"' },
  { role: 'user', text: 'FIRSTMATE_OP: v1 launch-brief: you are a crewmate' },
  { role: 'assistant', text: 'Scout report for Firstmate only.' },
  { role: 'assistant', text: '⠦ Working...' },
  { role: 'assistant', text: '~/.treehouse/Magistrate-7ab3fc/1/Magistrate (fm/magistra...' },
  { role: 'assistant', text: 'model: claude-opus-5' },
  { role: 'assistant', text: 'session_id: 5f2c-trace' },
  { role: 'assistant', text: 'pane_id=w1:p9 tab_id=secret' },
  { role: 'assistant', text: '{"jsonrpc":"2.0","result":{"ok":true}}' },
  { role: 'assistant', text: '$ cat /tmp/raw-pane-output' },
].map((row, index) => ({ ...row, id: `cm_leak_${index}`, turn_id: 'ct_9', type: 'internal', visible_in_chat: false, sequence_index: 9000 + index, revision: 1 }));
const FORBIDDEN = /notifications\.py|pr-detail|pull-request|FIRSTMATE_OP|crewmate|Scout report|Working\.\.\.|treehouse|claude-opus-5|session_id|pane_id|tab_id|jsonrpc|raw-pane/;

const conversationRows = page => page.evaluate(() => ({
  user: [...document.querySelectorAll('[data-testid^="user-message-"]')]
    .filter(element => !element.getAttribute('data-testid').startsWith('user-message-text-'))
    .map(element => ({ id: element.getAttribute('data-testid'), background: getComputedStyle(element).backgroundColor, text: element.innerText })),
  agent: [...document.querySelectorAll('[data-testid="agent-message"]')].map(element => element.innerText),
  tool: [...document.querySelectorAll('[data-testid="tool-history-message"]')].length,
  text: document.querySelector('[data-testid="chat-history"]').innerText,
}));

test('one captain turn with leaked metadata renders exactly one captain row and one primary row', async () => {
  const scenario = {
    delay: 300,
    turnPhases: [{ reply: 'The deploy is healthy and finished at 09:12.', tools: ['Running…'] }],
    extras: LEAKED_METADATA,
  };
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, scenario);
  await submit(page, 'summarize the deploy');
  await pageWaitForText(page, 'The deploy is healthy and finished at 09:12.');
  // Let the socket replay and several polls redeliver the identical records.
  await new Promise(resolve => setTimeout(resolve, 6800));
  const rows = await conversationRows(page);
  assert.equal(rows.user.length, 1, `expected one captain row, got ${JSON.stringify(rows.user)}`);
  assert.equal(rows.agent.length, 1, `expected one primary row, got ${JSON.stringify(rows.agent)}`);
  assert.match(rows.user[0].id, /^user-message-u-/, 'the only user-styled row must be the locally submitted captain message');
  assert.match(rows.user[0].background, /rgb\(36, 216, 255\)/, 'captain rows keep the opaque captain bubble');
  assert.match(rows.agent[0], /The deploy is healthy and finished at 09:12\./);
  assert.doesNotMatch(rows.text, FORBIDDEN);
  assert.doesNotMatch(rows.text, /hidden-secret/);
  const persisted = await cachedCanonicalMessages(page);
  assert.deepEqual(persisted.map(message => [message.role, message.kind, message.audience]), [
    ['user', 'conversation', 'captain'], ['assistant', 'tool', 'primary'], ['assistant', 'conversation', 'primary'],
  ]);
  assert.ok(persisted.every(message => typeof message.canonicalId === 'string'), 'every persisted captain row carries canonical identity');
  await page.close();

  // Reload reads the same canonical record: still exactly two visible rows.
  const reloaded = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true, 'light', [], false, { ...scenario, manual: true });
  await pageWaitForText(reloaded, 'The deploy is healthy and finished at 09:12.');
  await new Promise(resolve => setTimeout(resolve, 3600));
  const afterReload = await conversationRows(reloaded);
  assert.equal(afterReload.user.length, 1);
  assert.equal(afterReload.agent.length, 1);
  assert.doesNotMatch(afterReload.text, FORBIDDEN);
  await reloaded.close();
});

test('a reply the gateway keeps revising stays one primary row and settles on the final text', async () => {
  const partial = 'The tests are running';
  const middle = 'The tests are running and 30 of them have';
  const settled = 'The tests are running and all 42 of them pass.';
  // One canonical message, three revisions - the shape a Herdr snapshot growing
  // between reads now produces in the canonical record.
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, {
    turnPhases: [{ reply: partial, replyRevision: 1 }, { reply: middle, replyRevision: 2 }, { reply: settled, replyRevision: 3 }],
  });
  await submit(page, 'run the tests');
  await pageWaitForText(page, settled);
  // Polling and WebSocket delivery can cross in flight. Re-deliver revision 2
  // after revision 3 is visible: canonical identity must reject the stale row,
  // not briefly or permanently roll the assistant bubble backwards.
  await page.evaluate(staleText => window.__historySocket?.onmessage?.({ data: JSON.stringify({
    type: 'conversation_messages', target: 'captain', messages: [{
      id: 'cm_0_a', turn_id: 'ct_0', role: 'assistant', type: 'conversation',
      text: staleText, visible_in_chat: true, sequence_index: 999, revision: 2,
      turn_status: 'answered', source: 'text',
    }],
  }) }), middle);
  await new Promise(resolve => setTimeout(resolve, 250));
  let rows = await conversationRows(page);
  assert.equal(rows.user.length, 1);
  assert.equal(rows.agent.length, 1, `expected one primary row, got ${JSON.stringify(rows.agent)}`);
  assert.match(rows.agent[0], /The tests are running and all 42 of them pass\./);
  await new Promise(resolve => setTimeout(resolve, 3600));
  rows = await conversationRows(page);
  assert.equal(rows.agent.length, 1);
  assert.match(rows.agent[0], /The tests are running and all 42 of them pass\./);
  await page.close();

  const reloaded = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true, 'light', [], false, { manual: true });
  await pageWaitForText(reloaded, settled);
  await new Promise(resolve => setTimeout(resolve, 3600));
  rows = await conversationRows(reloaded);
  assert.equal(rows.user.length, 1);
  assert.equal(rows.agent.length, 1);
  await reloaded.close();
});

test('two legitimate identical captain messages keep one row and one reply each', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'First reply arrived.');
  await submit(page, 'same wording');
  await pageWaitForText(page, 'First reply arrived.');
  await page.close();

  const second = await openChat({ width: 900, height: 700 }, false, 'Second reply arrived.', URL, 0, false, true);
  await pageWaitForText(second, 'First reply arrived.');
  await submit(second, 'same wording');
  await pageWaitForText(second, 'Second reply arrived.');
  await new Promise(resolve => setTimeout(resolve, 3600));
  const rows = await conversationRows(second);
  assert.equal(rows.user.length, 2, `repeated text sent at different times stays two rows: ${JSON.stringify(rows.user)}`);
  assert.equal(rows.agent.length, 2);
  assert.ok(rows.text.indexOf('First reply arrived.') < rows.text.indexOf('Second reply arrived.'));
  await second.close();
});

test('a worker thread excludes firstmate prompts and worker metadata and never duplicates a growing reply', async () => {
  const leak = [
    { id: 'w-envelope', role: 'user', kind: 'conversation', text: 'edit gateway/app/notifications.py\n\n... 340     elif parsed.path == "/pr-detail":' },
    { id: 'w-firstmate', role: 'user', kind: 'conversation', text: 'FIRSTMATE_OP: v1 launch-brief: you are a crewmate' },
  ];
  const reply = text => ({ id: `w-reply-${text.length}`, role: 'assistant', kind: 'conversation', text });
  const settled = 'The worker finished the migration and reported no errors.';
  const page = await openChat({ width: 900, height: 700 }, false, '', `${URL}?agentId=w1%3Ap7`, 0, false, false, 'light', [], false, {
    workerPhases: [
      [{ id: 'w-baseline', role: 'assistant', kind: 'conversation', text: 'Baseline row.' }],
      [...leak, reply('The worker finished the migration')],
      [...leak, reply(settled)],
    ],
  });
  await pageWaitForText(page, settled);
  await new Promise(resolve => setTimeout(resolve, 3600));
  const rows = await conversationRows(page);
  assert.equal(rows.user.length, 0, `no worker-thread row may be styled as a captain message: ${JSON.stringify(rows.user)}`);
  assert.equal(rows.agent.length, 1, `expected one agent row, got ${JSON.stringify(rows.agent)}`);
  assert.match(rows.agent[0], /reported no errors\./);
  assert.doesNotMatch(rows.text, FORBIDDEN);
  await page.close();
});

test('gateway error and cancellation fixtures render truthful states', async () => {
  // The turn's own status is what drives these labels now; the canonical record
  // never claims a completed response for a failed or cancelled turn. The
  // 'streaming' label is pinned in tests/chat-evidence.test.ts, which the
  // canonical record deliberately does not produce (see CHAT_ARCHITECTURE_FIX.md).
  const page = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, {
    manual: true,
    seedTurns: [
      { clientMessageId: 'u-fixture-error', text: 'gateway failure fixture', reply: 'Partial gateway answer', status: 'failed' },
      { clientMessageId: 'u-fixture-cancel', text: 'cancellation fixture', reply: 'Stopped answer', status: 'cancelled' },
    ],
  });
  await pageWaitForText(page, 'Partial gateway answer');
  assert.match(await page.$eval('[data-testid^="assistant-failed-"]', element => element.innerText), /Response stopped before completion/);
  assert.equal(await page.$eval('[data-testid^="assistant-cancelled-"]', element => element.innerText), 'Response stopped');
  assert.match(await page.$eval('[data-testid^="message-cancelled-"]', element => element.innerText), /Response stopped/);
  await page.close();

  const failed = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, false, 'light', [], false, { manual: true }, true);
  await submit(failed, 'gateway interruption fixture');
  await failed.waitForSelector('[data-testid^="message-failed-"]');
  assert.match(await failed.$eval('[data-testid^="message-failed-"]', element => element.innerText), /Not sent/);
  await failed.close();
});

test('chat surface exposes accessible roles and labels for the composer and drawer', async () => {
  const page = await openChat({ width: 900, height: 700 });
  const accessibility = await page.evaluate(() => ({
    composer: document.querySelector('[data-testid="captain-prompt"]')?.getAttribute('aria-label'),
    sendRole: document.querySelector('[data-testid="send-captain-prompt"]')?.getAttribute('aria-label'),
    drawerRole: document.querySelector('[data-testid="brand-drawer-toggle"]')?.getAttribute('aria-label'),
    roleCount: document.querySelectorAll('[role="button"]').length,
  }));
  assert.equal(accessibility.composer, 'Message Magistrate');
  assert.match(accessibility.sendRole || '', /Send|Queue|voice mode/);
  assert.match(accessibility.drawerRole || '', /drawer/i);
  assert.ok(accessibility.roleCount >= 3);
  await page.close();
});
