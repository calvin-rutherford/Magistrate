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

async function openChat(viewport, emptyInventory = false, promptResponseText = '', route = URL, visualViewportShortfall = 0, historyRace = false, preserveStorage = false, colorScheme = 'light') {
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
  await page.evaluateOnNewDocument((noOverrides, responseText, simulateHistoryRace, clearStorage) => {
    if (clearStorage) { localStorage.clear(); sessionStorage.clear(); }
    const nativeFetch = window.fetch.bind(window);
    let promptSent = false;
    let historyRequests = 0;
    let agentHistoryRequests = 0;
    window.__magistrateApiCalls = [];
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST'
          ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' }
          : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/captain/prompt')) {
        promptSent = true;
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        return Promise.resolve(new Response(JSON.stringify({ status: 'submitted', target: 'captain', response: responseText }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
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
        const messages = agentHistoryRequests < 3 ? [] : [
          { role: 'user', kind: 'conversation', text: 'Please check the deployment.' },
          { role: 'assistant', kind: 'tool', text: 'Ran 3 commands' },
          { role: 'assistant', kind: 'conversation', text: 'The deployment is healthy.' },
        ];
        return Promise.resolve(new Response(JSON.stringify({ target: 'w1:p7', messages }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response(JSON.stringify([{ id: 'w1:p7', name: 'Deploy agent', status: 'working', harness: 'codex' }]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/attention/unified')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/github/pulls')) return Promise.resolve(new Response(JSON.stringify({ items: [], page: 1, per_page: 20, has_more: false, cached: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/auth/providers')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/voice/capabilities')) return Promise.resolve(new Response(JSON.stringify({ schema_version: 'voice-capabilities.v1', provider: 'openai', configured: true, modes: [{ id: 'openai', label: 'Gateway OpenAI', available: true }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/voice/transcribe')) return Promise.resolve(new Response(JSON.stringify({ text: 'test transcript from mic', is_final: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  }, emptyInventory, promptResponseText, historyRace, !preserveStorage);
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: colorScheme }]);
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
  // Opening Chat and its recurring history refresh must never submit a prompt;
  // prompts are sent only after an explicit composer action.
  await new Promise(resolve => setTimeout(resolve, 3200));
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length), 0);
  assert.equal((await page.$$('[data-testid="model-menu-button"]')).length, 1);
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
  await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'networkidle0' });
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
  assert.ok(Number(await page.$eval('[data-testid="magistrate-drawer"]', element => getComputedStyle(element).opacity)) < 0.05);
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
  const usage = await page.$eval('[data-testid="settings-usage-section"]', element => element.innerText);
  assert.match(usage, /codex.*plus/i);
  assert.match(usage, /20% left/i);
  await page.close();
});

test('additional messages queue while the first response is pending', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, '');
  await submit(page, 'first request');
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('second request');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="queued-message-count"]');
  assert.match(await page.$eval('[data-testid="queued-message-count"]', element => element.innerText), /1 queued/);
  assert.ok(await page.$('[data-testid="thinking-dots"]'));
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
  assert.equal(await page.$eval('[data-testid="settings-network-status"]', element => element.textContent), 'Connected');
  const ratio = await page.$eval('[data-testid="settings-sheet"]', element => element.getBoundingClientRect().height / window.innerHeight);
  assert.ok(ratio >= 0.74 && ratio <= 0.82);
  await page.click('[data-testid="settings-close"]');
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
  assert.equal(await page.$eval(selector, element => element.innerText), 'editable message');
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
  await page.waitForSelector('[data-testid="settings-voice-mode-options"]');
  await page.click('[data-testid="voice-mode-option-openai"]');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.voice.input-mode')), 'openai');
  assert.equal(await page.evaluate(() => window.__magistrateApiCalls.filter(call => call.url.includes('/captain/prompt')).length), 0);
  await page.close();
  const refreshed = await openChat({ width: 900, height: 700 }, false, '', URL, 0, false, true);
  await refreshed.click('[data-testid="brand-drawer-toggle"]');
  await refreshed.click('[data-testid="settings-open"]');
  await refreshed.waitForSelector('[data-testid="voice-mode-option-openai"]');
  assert.equal(await refreshed.evaluate(() => localStorage.getItem('magistrate.voice.input-mode')), 'openai');
  assert.match(await refreshed.$eval('[data-testid="settings-voice-input-section"]', element => element.innerText), /Gateway OpenAI/);
  await refreshed.close();
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

test('a response appearing while the initial history read is in flight is not lost', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Reply arrived via history.', URL, 0, true);
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
test('the agent response is appended to the conversation once the gateway replies', async () => {
  const page = await openChat({ width: 900, height: 700 }, false, 'Understood, working on it now.');
  await submit(page, 'status please');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]').innerText.includes('Understood, working on it now.'));
  const history = await page.$eval('[data-testid="chat-history"]', element => element.innerText);
  assert.match(history, /status please/);
  assert.match(history, /Understood, working on it now\./);
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
test('a sent user message remains plain and stable across the live-refresh poll', async () => {
  const page = await openChat({ width: 900, height: 700 });
  await submit(page, 'timestamp check');
  const selector = '[data-testid^="user-message-u-"]';
  await page.waitForSelector(selector);
  const before = await page.$eval(selector, element => element.innerText);
  assert.equal(before, 'timestamp check');
  // The background auto-refresh poll runs every 3s (see ChatCanvas's syncFromHistory).
  await new Promise(resolve => setTimeout(resolve, 3400));
  const after = await page.$eval(selector, element => element.innerText);
  assert.equal(after, before);
  assert.equal((await page.$$(selector)).length, 1);
  await page.close();
});
