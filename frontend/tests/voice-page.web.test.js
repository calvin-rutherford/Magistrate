const assert = require('node:assert/strict');
const test = require('node:test');
const { launchBrowser, startWebServer } = require('./helpers/web-server');

let server;
let browser;
// Assigned once the dev server has picked a port; the suites read it at call
// time, never at module load.
let VOICE_URL;

test.before(async () => {
  server = await startWebServer({ readyPath: '/voice' });
  VOICE_URL = `${server.base}/voice`;
  browser = await launchBrowser({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
});

test.after(async () => {
  await browser?.close();
  await server?.stop();
});

function stubSpeechSynthesis() {
  window.__spokenTexts = [];
  Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
    getVoices: () => [],
    onvoiceschanged: null,
    speaking: false,
    speak: message => { window.__spokenTexts.push(message.text); setTimeout(() => message.onend?.({}), 120); },
    cancel: () => {},
    pause: () => {},
    resume: () => {},
  }});
}

test('voice control keeps the compact stage and enlarged branded mark proportions', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return nativeFetch(resource, options);
    };
    if (typeof navigator.mediaDevices === 'undefined') Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: () => Promise.reject(new DOMException('denied', 'NotAllowedError')) } });
  });
  await page.goto(VOICE_URL, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="voice-control"]');
  const sizes = await page.$eval('[data-testid="voice-control"]', element => Array.from(element.querySelectorAll('svg')).map(svg => Math.round(svg.getBoundingClientRect().width)));
  const viewportWidth = await page.evaluate(() => innerWidth);
  const expectedStage = (viewportWidth < 680 ? Math.min(Math.max(viewportWidth - 34, 200), 360) : Math.min(viewportWidth * 0.46, 520)) * 0.95;
  const expectedMark = (viewportWidth < 680 ? Math.min(Math.max(viewportWidth * 0.54, 140), 220) : Math.min(viewportWidth * 0.28, 270)) * 1.2;
  assert.equal(sizes.length >= 2, true);
  assert.ok(Math.abs(sizes[0] - expectedStage) <= 12, JSON.stringify({ sizes, expectedStage, expectedMark, viewportWidth }));
  assert.ok(Math.abs(sizes[1] - expectedMark) <= 12, JSON.stringify({ sizes, expectedStage, expectedMark, viewportWidth }));
  await page.close();
});

test('voice mode surfaces a recoverable error and never leaves /voice when the microphone is denied', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    window.__voiceApiCalls = [];
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/voice/')) {
        window.__voiceApiCalls.push({ url, method: options?.method });
        return Promise.resolve(new Response('{}', { status: 500 }));
      }
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200 }));
      return nativeFetch(resource, options);
    };
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: () => Promise.reject(new DOMException('denied', 'NotAllowedError'))
    }});
  });
  await page.evaluateOnNewDocument(stubSpeechSynthesis);
  await page.goto(VOICE_URL, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="voice-error"]', { timeout: 20_000 });
  assert.match(await page.$eval('body', element => element.innerText), /Voice paused/);
  assert.equal(new URL(page.url()).pathname, '/voice');
  assert.deepEqual(await page.evaluate(() => window.__voiceApiCalls), []);
  await page.waitForSelector('[data-testid="end-voice-conversation"]');
  await page.close();
});

test('voice mode listens continuously: transcribes a turn, answers in the thread, speaks, and listens again', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/voice/transcribe')) {
        return Promise.resolve(new Response(JSON.stringify({ text: 'What is the fleet doing right now?', is_final: true }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/voice/moves')) {
        const body = JSON.parse(options.body);
        const move = body.execute
          ? { schema_version: 'voice-move.v1', move_id: 'vm_test', status: 'completed', impact: 'read', target: 'captain', response: 'Two agents are live and both are idle.' }
          : { schema_version: 'voice-move.v1', move_id: 'vm_test', status: 'ready', impact: 'read', target: 'captain', requires_confirmation: false };
        return Promise.resolve(new Response(JSON.stringify(move), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200 }));
      return nativeFetch(resource, options);
    };
  });
  await page.evaluateOnNewDocument(stubSpeechSynthesis);
  await page.goto(VOICE_URL, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.querySelector('#error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForFunction(() => document.body.innerText.includes('Listening'), { timeout: 20_000 });
  await new Promise(resolve => setTimeout(resolve, 1_000));
  await page.click('[data-testid="voice-control"]');
  await page.waitForSelector('[data-testid="voice-conversation"]', { timeout: 20_000 });
  const thread = await page.$eval('[data-testid="voice-conversation"]', element => element.innerText);
  assert.match(thread, /What is the fleet doing right now\?/);
  assert.match(thread, /Two agents are live and both are idle\./);
  assert.deepEqual(await page.evaluate(() => window.__spokenTexts), ['Two agents are live and both are idle.']);
  await page.waitForFunction(() => document.body.innerText.includes('Listening'), { timeout: 20_000 });
  assert.equal(new URL(page.url()).pathname, '/voice');
  await page.close();
});

test('ending a deep-linked voice session still lands back in chat', async () => {
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 860 });
  await page.evaluateOnNewDocument(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return nativeFetch(resource, options);
    };
  });
  await page.evaluateOnNewDocument(stubSpeechSynthesis);
  // No chat entry in this tab's history, so router.back() alone cannot exit.
  await page.goto(VOICE_URL, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.querySelector('#error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  await page.waitForSelector('[data-testid="end-voice-conversation"]');
  await page.click('[data-testid="end-voice-conversation"]');
  await page.waitForFunction(() => window.location.pathname === '/chat', { timeout: 20_000 });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.close();
});
