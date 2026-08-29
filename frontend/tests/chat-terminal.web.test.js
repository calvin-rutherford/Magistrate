const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.MAGISTRATE_WEB_TEST_PORT) || 8091;
const URL = `http://127.0.0.1:${PORT}/chat`;
let server;
let browser;

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try { if ((await fetch(URL)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'), ['start', '--web', '--port', String(PORT)], { cwd: process.cwd(), env: { ...process.env, CI: '1' }, stdio: 'ignore' });
  await waitForServer();
  // Fake device/UI flags let getUserMedia() resolve with a synthetic audio
  // track instead of prompting for real microphone hardware/permission.
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
});

test.after(async () => { await browser?.close(); server?.kill('SIGTERM'); });

async function openChat(viewport, emptyInventory = false, promptResponseText = '', route = URL) {
  const page = await browser.newPage();
  await page.setViewport(viewport);
  await page.evaluateOnNewDocument((noOverrides, responseText) => {
    // Each test expects a genuinely fresh chat thread, but pages in this suite
    // share one browser profile/origin, so persisted chat history (AsyncStorage
    // on web backs onto localStorage) would otherwise leak across tests.
    try { localStorage.clear(); } catch {}
    const nativeFetch = window.fetch.bind(window);
    window.__magistrateApiCalls = [];
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/captain/prompt')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        return Promise.resolve(new Response(JSON.stringify({ status: 'submitted', target: 'captain', response: responseText }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/execution/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          harnesses: noOverrides ? [] : [
            { id: 'codex', label: 'Codex', verified: true, models: [{ id: 'gpt-5', label: 'GPT-5' }] },
            { id: 'reviewer', label: 'Reviewer', verified: true, models: [{ id: 'review-model', label: 'Review Model' }] },
          ], source: 'test', configured: !noOverrides,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'healthy', service: 'gateway', herdr_socket_connected: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/agents/w1%3Ap7/history')) return Promise.resolve(new Response(JSON.stringify({ target: 'w1:p7', messages: [
        { role: 'user', kind: 'conversation', text: 'Please check the deployment.' },
        { role: 'assistant', kind: 'tool', text: 'Ran 3 commands' },
        { role: 'assistant', kind: 'conversation', text: 'The deployment is healthy.' },
      ] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response(JSON.stringify([{ id: 'w1:p7', name: 'Deploy agent', status: 'working', harness: 'codex' }]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/attention/unified')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/github/pulls')) return Promise.resolve(new Response(JSON.stringify({ items: [], page: 1, per_page: 20, has_more: false, cached: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/auth/providers')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/voice/transcribe')) return Promise.resolve(new Response(JSON.stringify({ text: 'test transcript from mic', is_final: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  }, emptyInventory, promptResponseText);
  await page.goto(route, { waitUntil: 'networkidle0' });
  // Expo's development-only #error-toast has a zero-sized box but can still
  // win hit-testing near the viewport bottom in headless Chrome.
  await page.evaluate(() => { const toast = document.getElementById('error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.waitForSelector('[data-testid="model-menu-button"]');
  return page;
}

async function submit(page, text) {
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type(text);
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(expected => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length >= expected, {}, (await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length)) || 1);
}

test('chat starts genuinely empty with one branded logo and a minimal composer', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  assert.equal((await page.$$('[data-testid="chat-history"] img')).length, 0);
  assert.equal((await page.$eval('[data-testid="chat-history"]', element => element.innerText)).trim(), '');
  assert.equal((await page.$$('[data-testid="brand-drawer-toggle"] img')).length, 1);
  const body = await page.evaluate(() => document.body.innerText);
  assert.doesNotMatch(body, /Firstmate|melkezic/i);
  assert.equal((await page.$$('[data-testid="model-menu-button"]')).length, 1);
  await page.close();
});

test('attachment menu picks a file, previews it, and explains the gateway upload gap', async () => {
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
  assert.match(await page.$eval('[data-testid="captain-send-error"]', element => element.innerText), /gateway cannot accept uploads yet/i);
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length), 0);

  await page.click('[data-testid^="remove-file-"]');
  await page.waitForFunction(() => !document.querySelector('[data-testid="attachment-preview"]'));
  await page.close();
});

test('drawer starts collapsed, expands downward, and preserves conversation history', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await submit(page, 'keep this message');
  assert.equal(await page.$eval('[data-testid="magistrate-drawer"]', element => getComputedStyle(element).opacity), '0');
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="drawer-section-attention"]');
  await page.waitForSelector('[data-testid="drawer-panel-attention"]');
  assert.match(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /keep this message/);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) < 0.05);
  assert.match(await page.$eval('[data-testid="chat-history"]', element => element.innerText), /keep this message/);
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

test('right swipe starting in the composer does not open the drawer', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  const composer = await page.$eval('[data-testid="captain-prompt"]', element => { const rect = element.getBoundingClientRect(); return { left: rect.left, top: rect.top, height: rect.height }; });
  const client = await page.createCDPSession();
  const start = { x: composer.left + 20, y: composer.top + composer.height / 2 };
  await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] });
  for (let step = 1; step <= 8; step += 1) {
    await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x + step * 28, y: start.y }] });
  }
  await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  assert.equal(await page.$eval('[data-testid="magistrate-drawer"]', element => getComputedStyle(element).opacity), '0');
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
  await overridePage.close();
});

test('account gear opens the lower settings drawer with live network status', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  const target = await page.$eval('[data-testid="settings-open"]', element => { const rect = element.getBoundingClientRect(); return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, visible: rect.top >= 0 && rect.bottom <= innerHeight }; });
  assert.equal(target.visible, true);
  await page.mouse.click(target.x, target.y);
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="settings-sheet"]')).opacity) > 0.95);
  assert.equal(await page.$eval('[data-testid="settings-network-status"]', element => element.textContent), 'Connected');
  const ratio = await page.$eval('[data-testid="settings-sheet"]', element => element.getBoundingClientRect().height / window.innerHeight);
  assert.ok(ratio >= 0.42 && ratio <= 0.52);
  await page.click('[data-testid="settings-close"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="settings-sheet"]')).opacity) < 0.05);
  await page.close();
});

test('fleet agent opens its conversation, hides tools by default, and settings can reveal them', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="drawer-section-fleet"]');
  await page.waitForSelector('[data-testid="fleet-agent-w1:p7"]');
  await page.click('[data-testid="fleet-agent-w1:p7"]');
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
  assert.match(history, /Ran 3 commands/);
  await page.close();
});

test('fleet ellipsis shows real status and quick commands', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="drawer-section-fleet"]');
  await page.click('[data-testid="fleet-agent-w1:p7-menu"]');
  const popover = await page.$eval('[data-testid="fleet-agent-w1:p7-popover"]', element => element.innerText);
  assert.match(popover, /STATUS\s+WORKING/);
  assert.match(popover, /ACTIVE STATUS\s+ACTIVE/);
  assert.match(popover, /INTERRUPT/);
  assert.match(popover, /RENAME/);
  await page.close();
});

test('two-second hold exposes edit, copy, selection, and a sent timestamp', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await submit(page, 'editable message');
  const selector = '[data-testid^="user-message-u-"]';
  await page.waitForSelector(selector);
  assert.match(await page.$eval(selector, element => element.innerText), /Sent/);
  const rect = await page.$eval(selector, element => { const box = element.getBoundingClientRect(); return { x: box.x + box.width / 2, y: box.y + box.height / 2 }; });
  await page.mouse.move(rect.x, rect.y); await page.mouse.down(); await new Promise(resolve => setTimeout(resolve, 2100)); await page.mouse.up();
  await page.waitForSelector('[data-testid="message-actions"]');
  const actions = await page.$eval('[data-testid="message-actions"]', element => element.innerText);
  assert.match(actions, /Edit/); assert.match(actions, /Copy/); assert.match(actions, /Select text/);
  await page.locator('::-p-text(Edit)').click();
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'editable message');
  await page.close();
});

test('mic button records real audio, shows a live waveform, and fills the composer with the transcript', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await page.click('[data-testid="inline-mic-button"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="inline-mic-button"]').getAttribute('aria-label') === 'Stop microphone');
  await new Promise(resolve => setTimeout(resolve, 500));
  await page.click('[data-testid="inline-mic-button"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="captain-prompt"]').value.includes('test transcript from mic'));
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'test transcript from mic');
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
  // Gateways one deploy behind still return herdr's raw acknowledgement in
  // `response`; it is transport metadata, not the agent's message.
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

test('the agent response is appended to the conversation once the gateway replies', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Understood, working on it now.');
  await submit(page, 'status please');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Understood, working on it now.'));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /status please/);
  assert.match(history, /Understood, working on it now\./);
  await page.close();
});

function messageBubbleSelector() {
  return '[data-testid^="user-message-"], [data-testid="agent-message"], [data-testid="tool-history-message"]';
}

// Regression for the captain-reported bug: with substantial history, an unbounded
// ScrollView made jump-to-latest visibly travel across the entire thread (thousands
// of pixels, over a second of animation) - indistinguishable from a full reload and
// rescroll from message 1. Only a bounded window of recent messages should mount by
// default, older history should load on scroll-up, and jump-to-latest must land on
// the true newest message quickly.
test('long history stays windowed, loads older messages on scroll-up, and jump-to-latest reaches the true newest message', async () => {
  const total = 100;
  const messages = [];
  for (let i = 0; i < total; i += 1) {
    messages.push({ role: 'user', kind: 'conversation', text: `user turn ${i}` });
    messages.push({ role: 'assistant', kind: 'conversation', text: i === total - 1 ? 'the true latest reply' : `assistant turn ${i}` });
  }
  const page = await browser.newPage();
  await page.setViewport({ width: 900, height: 700 });
  await page.evaluateOnNewDocument((historyMessages) => {
    try { localStorage.clear(); } catch {}
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/history')) return Promise.resolve(new Response(JSON.stringify({ target: 'w1:p7', messages: historyMessages }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/execution/capabilities')) return Promise.resolve(new Response(JSON.stringify({ harnesses: [], source: 'test', configured: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response(JSON.stringify([{ id: 'w1:p7', name: 'Deploy agent', status: 'working', harness: 'codex' }]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  }, messages);
  await page.goto(`${URL}?agentId=w1%3Ap7`, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.getElementById('error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]')?.innerText.includes('the true latest reply'));

  const initialCount = (await page.$$(messageBubbleSelector())).length;
  assert.ok(initialCount < total, `expected only a windowed subset of ${total * 2} messages to be mounted, got ${initialCount}`);

  // Scroll to the top of the buffered window; this should load more history
  // (pagination), growing the mounted count, while roughly preserving the
  // reader's visual position instead of yanking it to the very top or bottom.
  await page.evaluate(() => { document.querySelector('[data-testid="chat-history"]').scrollTop = 0; });
  await page.waitForFunction(count => document.querySelectorAll(
    '[data-testid^="user-message-"], [data-testid="agent-message"], [data-testid="tool-history-message"]'
  ).length > count, {}, initialCount);
  // The scroll re-anchor happens in onContentSizeChange, a paint later than the
  // DOM node count above, so poll for it rather than reading scrollTop immediately.
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').scrollTop > 0, { timeout: 2000 });
  const scrollTopAfterLoadMore = await page.evaluate(() => document.querySelector('[data-testid="chat-history"]').scrollTop);
  assert.ok(scrollTopAfterLoadMore > 0, 'loading older history should re-anchor scroll position, not leave the reader at the very top');

  // Jump-to-latest must reach the true bottom of the full thread quickly, not
  // slowly animate across the entire rendered history.
  await page.click('[data-testid="jump-to-latest"]');
  const start = Date.now();
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="chat-history"]');
    return el.scrollTop >= el.scrollHeight - el.clientHeight - 2;
  }, { timeout: 2000 });
  assert.ok(Date.now() - start < 2000, 'jump-to-latest took too long, suggesting it is still animating across the full history');
  const finalHistory = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(finalHistory, /the true latest reply/);
  await page.close();
});

// Regression for the captain-reported bug: user message timestamps must reflect
// when the message was actually sent, not whatever time the next poll happens to run.
test('a sent user message keeps its original timestamp across the live-refresh poll', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await submit(page, 'timestamp check');
  const selector = '[data-testid^="user-message-u-"]';
  await page.waitForSelector(selector);
  const before = await page.$eval(selector, element => element.innerText);
  assert.match(before, /Sent/);
  // The background auto-refresh poll runs every 3s (see ChatCanvas's syncFromHistory).
  await new Promise(resolve => setTimeout(resolve, 3400));
  const after = await page.$eval(selector, element => element.innerText);
  assert.equal(after, before);
  assert.equal((await page.$$(selector)).length, 1);
  await page.close();
});
