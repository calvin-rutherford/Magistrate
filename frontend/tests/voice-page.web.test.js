const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8094;
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
    args: ['--no-sandbox', '--use-fake-ui-for-media-stream'] });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
});

test('voice page starts with microphone off and never navigates to chat on permission failure', async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    window.__voiceApiCalls = [];
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response('[]', { status: 200 }));
      if (url.includes('/api/v1/')) {
        window.__voiceApiCalls.push({ url, method: options?.method });
        return Promise.resolve(new Response('{}', { status: 500 }));
      }
      return nativeFetch(resource, options);
    };
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: () => Promise.reject(new DOMException('denied', 'NotAllowedError'))
    }});
  });
  await page.goto(VOICE_URL, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="voice-control"]');
  assert.match(await page.$eval('body', element => element.innerText), /READY · MICROPHONE OFF/);
  assert.deepEqual(await page.evaluate(() => window.__voiceApiCalls), []);
  await page.click('[data-testid="voice-control"]');
  await page.waitForSelector('[data-testid="voice-error"]');
  assert.equal(new URL(page.url()).pathname, '/voice');
  assert.equal((await page.evaluate(() => window.__voiceApiCalls)).some(call => call.url.includes('/voice/transcribe') || call.url.includes('/voice/moves')), false);
  await page.close();
});
