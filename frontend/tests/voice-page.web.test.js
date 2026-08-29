const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.MAGISTRATE_WEB_TEST_PORT) || 8094;
const VOICE_URL = `http://127.0.0.1:${PORT}/voice`;
let server;
let browser;

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try { if ((await fetch(VOICE_URL)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'),
    ['start', '--web', '--port', String(PORT)],
    { cwd: process.cwd(), env: { ...process.env, CI: '1' }, stdio: 'ignore' });
  await waitForServer();
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true,
    args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
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

test('voice mode surfaces a recoverable error and never leaves /voice when the microphone is denied', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    window.__voiceApiCalls = [];
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
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
