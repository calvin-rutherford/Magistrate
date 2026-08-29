const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8091;
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
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('status please');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'status please');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => getComputedStyle(element).fontSize), '16px');
  const after = await page.$eval('[data-testid="branded-chat-shell"]', element => ({ left: element.getBoundingClientRect().left, width: element.getBoundingClientRect().width }));
  assert.ok(Math.abs(after.left - before.left) < 2);
  assert.ok(Math.abs(after.width - before.width) < 2);
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
  assert.ok(ratio >= 0.4 && ratio <= 0.48);
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

test('the agent response is appended to the conversation once the gateway replies', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Understood, working on it now.');
  await submit(page, 'status please');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Understood, working on it now.'));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /status please/);
  assert.match(history, /Understood, working on it now\./);
  await page.close();
});
