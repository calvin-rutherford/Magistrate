/**
 * Negative coverage: a fake connected account, an invented metric, or an
 * optimistic attachment success must be unreachable in the real web build.
 *
 * Each case drives one honest-state class through the actual screens - missing
 * configuration, wrong/expired credentials, a provider error, a spoofed
 * payload, a deferred provider, a non-2xx source, and an unconfirmed upload -
 * and asserts the UI never reads as success.
 */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.MAGISTRATE_WEB_TEST_PORT) || 8097;
const ORIGIN = `http://127.0.0.1:${PORT}`;
let server;
let browser;

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try { if ((await fetch(`${ORIGIN}/account`)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'), ['start', '--web', '--port', String(PORT)], { cwd: process.cwd(), env: { ...process.env, CI: '1' }, stdio: 'ignore' });
  await waitForServer();
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
});

test.after(async () => { await browser?.close(); server?.kill('SIGTERM'); });

const SESSION_SCOPES = ['read', 'account', 'providers', 'notifications', 'voice', 'command'];

async function openPage(route, mocks) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1100, height: 900 });
  await page.evaluateOnNewDocument(({ providers, providersStatus, uploadRecord, uploadStatus, healthPayload, healthStatus, scopes }) => {
    localStorage.clear();
    sessionStorage.clear();
    window.__calls = [];
    const json = (payload, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } }));
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      window.__calls.push({ url, method: options?.method, body: typeof options?.body === 'string' ? options.body : null });
      if (url.includes('/api/v1/auth/session')) {
        return json(options?.method === 'POST'
          ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes, user_id: 'default_user' }
          : { authenticated: true, expires_at: 4102444800, scopes, user_id: 'default_user' });
      }
      if (url.includes('/api/v1/auth/providers')) {
        return providersStatus === 200 ? json(providers) : json({ detail: 'Provider directory is unavailable on this gateway.' }, providersStatus);
      }
      if (/\/api\/v1\/auth\/[a-z]+\/connect/.test(url)) return json({ detail: 'Provider OAuth is unavailable or not configured.' }, 503);
      if (url.includes('/api/v1/account/profile')) return json({ user_id: 'default_user', name: 'Owner', email: 'owner@example.test', avatar_url: '', bio: '' });
      if (url.includes('/api/v1/health')) return healthStatus === 200 ? json(healthPayload) : json({ detail: 'Gateway telemetry is unavailable.' }, healthStatus);
      if (url.includes('/api/v1/uploads')) return uploadStatus === 200 ? json({ uploads: [uploadRecord] }) : json({ detail: 'The file content does not match its declared type.' }, uploadStatus);
      if (url.includes('/api/v1/captain/prompt')) return json({ status: 'submitted', target: 'captain', response: 'Received.' });
      if (url.includes('/api/v1/agents') && url.includes('/history')) return json({ target: 'captain', messages: [] });
      if (url.includes('/api/v1/agents')) return json([]);
      if (url.includes('/api/v1/attention/unified')) return json([]);
      if (url.includes('/api/v1/github/pulls')) return json({ items: [], page: 1, per_page: 20, has_more: false, cached: false });
      if (url.includes('/api/v1/notifications/events')) return json({ events: [] });
      if (url.includes('/api/v1/notifications/preferences')) return json({ enabled: true, quiet_start: 22, quiet_end: 7, mode: 'moderate' });
      if (url.includes('/api/v1/execution/capabilities')) return json({ harnesses: [], profiles: [] });
      if (url.includes('/api/v1/execution/settings')) return json({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error' });
      if (url.includes('/api/v1/voice/capabilities')) return json({ schema_version: 'voice-capabilities.v1', modes: [], provider: null, configured: false });
      if (url.includes('/api/v1/recent-activity')) return json({ items: [], sources: { firstmate: 'unavailable', github: 'unavailable' } });
      if (url.includes('/api/v1/usage')) return json({ providers: [] });
      return json({});
    };
  }, {
    providers: mocks.providers || [],
    providersStatus: mocks.providersStatus || 200,
    uploadRecord: mocks.uploadRecord || null,
    uploadStatus: mocks.uploadStatus || 200,
    healthStatus: mocks.healthStatus || 200,
    healthPayload: mocks.healthPayload || { status: 'healthy', service: 'magistrate-gateway', herdr_socket_connected: true, herdr_version: '0.9.0', degraded_sources: [] },
    scopes: SESSION_SCOPES,
  });
  await page.goto(`${ORIGIN}${route}`, { waitUntil: 'networkidle0' });
  return page;
}

async function openConnections(mocks) {
  const page = await openPage('/account', mocks);
  await page.waitForSelector('[data-testid="account-section-connections"]');
  await page.click('[data-testid="account-section-connections"]');
  return page;
}

const provider = (overrides) => ({
  provider: 'github',
  status: 'disconnected',
  username: '',
  capabilities: ['read_prs'],
  auth_url: null,
  available: true,
  deferred: false,
  unavailable_reason: null,
  configuration: 'available',
  ...overrides,
});

// --- Missing configuration -------------------------------------------------

test('a provider with missing configuration renders unavailable with its reason', async () => {
  const page = await openConnections({ providers: [provider({
    status: 'unavailable', available: false, configuration: 'unavailable',
    unavailable_reason: 'GitHub OAuth is not configured on this gateway (missing GITHUB_OAUTH_CLIENT_ID).',
  })] });
  await page.waitForSelector('[data-testid="account-provider-github"]');
  const row = await page.$eval('[data-testid="account-provider-github"]', node => node.innerText);
  assert.match(row, /UNAVAILABLE/);
  assert.doesNotMatch(row, /CONNECTED/);
  assert.match(await page.$eval('[data-testid="account-provider-reason-github"]', node => node.innerText), /missing GITHUB_OAUTH_CLIENT_ID/);
  assert.equal(await page.$eval('[data-testid="account-provider-action-github"]', node => node.getAttribute('aria-disabled')), 'true');
  await page.close();
});

// --- Wrong / expired credentials -------------------------------------------

test('an expired credential renders RECONNECT, never a connected account', async () => {
  const page = await openConnections({ providers: [provider({
    status: 'expired', username: 'octocat',
    unavailable_reason: 'The stored credential has expired. Reconnect to restore access.',
  })] });
  await page.waitForSelector('[data-testid="account-provider-github"]');
  const row = await page.$eval('[data-testid="account-provider-github"]', node => node.innerText);
  assert.match(row, /RECONNECT/);
  assert.doesNotMatch(row, /CONNECTED/);
  assert.match(row, /credential has expired/);
  await page.close();
});

test('a stale connected row without a credential renders disconnected with its reason', async () => {
  const page = await openConnections({ providers: [provider({
    status: 'disconnected',
    unavailable_reason: 'The stored credential for this account is missing. Reconnect to restore access.',
  })] });
  await page.waitForSelector('[data-testid="account-provider-github"]');
  const row = await page.$eval('[data-testid="account-provider-github"]', node => node.innerText);
  assert.match(row, /CONNECT \+/);
  assert.doesNotMatch(row, /CONNECTED ✓/);
  assert.match(row, /credential for this account is missing/);
  await page.close();
});

// --- Spoofed / inconsistent payloads fail closed ---------------------------

test('a connected status on an unavailable provider fails closed to unavailable', async () => {
  const page = await openConnections({ providers: [provider({
    provider: 'github', status: 'connected', username: 'octocat', available: false, configuration: 'unavailable',
  })] });
  await page.waitForSelector('[data-testid="account-provider-github"]');
  const row = await page.$eval('[data-testid="account-provider-github"]', node => node.innerText);
  assert.match(row, /UNAVAILABLE/);
  assert.doesNotMatch(row, /CONNECTED/);
  // The identity is withheld too: a username beside an unavailable provider
  // reads as a connection that does not exist.
  assert.doesNotMatch(row, /octocat/);
  await page.close();
});

test('an unknown status string is not promoted to connected', async () => {
  const page = await openConnections({ providers: [provider({ status: 'totally-fine', username: 'octocat' })] });
  await page.waitForSelector('[data-testid="account-provider-github"]');
  const row = await page.$eval('[data-testid="account-provider-github"]', node => node.innerText);
  assert.match(row, /CONNECT \+/);
  assert.doesNotMatch(row, /CONNECTED ✓|totally-fine/);
  await page.close();
});

// --- Deferred providers ----------------------------------------------------

test('Jira and Teams render as deferred and are never connectable', async () => {
  const deferredReason = provider({ deferred: true, available: false, status: 'unavailable', configuration: 'unavailable' });
  const page = await openConnections({ providers: [
    { ...deferredReason, provider: 'jira', unavailable_reason: 'Jira is deferred for this release. It becomes available only when Jira OAuth credentials are configured.' },
    // A deferred provider claiming a connection is still shown as deferred.
    { ...deferredReason, provider: 'teams', status: 'connected', username: 'owner@example.test', unavailable_reason: 'Microsoft Teams is deferred for this release. It becomes available only when Microsoft Teams OAuth credentials are configured.' },
  ] });
  await page.waitForSelector('[data-testid="account-provider-jira"]');
  for (const name of ['jira', 'teams']) {
    const row = await page.$eval(`[data-testid="account-provider-${name}"]`, node => node.innerText);
    assert.match(row, /DEFERRED/, name);
    assert.doesNotMatch(row, /CONNECTED/, name);
    assert.match(row, /deferred for this release/, name);
    assert.equal(await page.$eval(`[data-testid="account-provider-action-${name}"]`, node => node.getAttribute('aria-disabled')), 'true', name);
  }
  assert.doesNotMatch(await page.$eval('[data-testid="account-provider-teams"]', node => node.innerText), /owner@example\.test/);
  await page.close();
});

// --- Non-2xx from the provider directory -----------------------------------

test('a non-2xx provider directory surfaces the gateway detail, not an empty account', async () => {
  const page = await openConnections({ providersStatus: 503 });
  await page.waitForSelector('[data-testid="account-providers-error"]');
  const text = await page.$eval('[data-testid="account-providers-error"]', node => node.innerText);
  assert.match(text, /Provider directory is unavailable on this gateway\./);
  // "No integrations connected" would be a claim about the account we cannot make.
  const body = await page.evaluate(() => document.body.innerText);
  assert.doesNotMatch(body, /No integrations connected/);
  assert.doesNotMatch(body, /CONNECTED ✓/);
  await page.close();
});

test('a 500 with no body still surfaces a specific status-bearing error', async () => {
  const page = await openConnections({ providersStatus: 500 });
  await page.waitForSelector('[data-testid="account-providers-error"]');
  assert.match(await page.$eval('[data-testid="account-providers-error"]', node => node.innerText), /unavailable|Request failed \(500\)/);
  await page.close();
});

test('a truly empty provider directory is stated as such, not as an error', async () => {
  const page = await openConnections({ providers: [] });
  await page.waitForSelector('[data-testid="account-providers-empty"]');
  assert.match(await page.$eval('[data-testid="account-providers-empty"]', node => node.innerText), /reported no integrations/);
  assert.equal(await page.$('[data-testid="account-providers-error"]'), null);
  await page.close();
});

// --- Only a real grant can render connected --------------------------------

test('a configured provider with a live credential is the only connected render', async () => {
  const page = await openConnections({ providers: [provider({ status: 'connected', username: 'octocat' })] });
  await page.waitForSelector('[data-testid="account-provider-github"]');
  const row = await page.$eval('[data-testid="account-provider-github"]', node => node.innerText);
  assert.match(row, /CONNECTED ✓/);
  assert.match(row, /octocat/);
  assert.equal(await page.$('[data-testid="account-provider-reason-github"]'), null);
  await page.close();
});

// --- Health / telemetry honesty -------------------------------------------

test('a degraded gateway never shows an invented Herdr version or a healthy state', async () => {
  const page = await openPage('/diagnostics', {
    healthPayload: { status: 'degraded', service: 'magistrate-gateway', herdr_socket_connected: false, herdr_version: null, degraded_sources: ['herdr'] },
  });
  await page.waitForFunction(() => document.body.innerText.includes('HERDR VERSION'));
  const body = await page.evaluate(() => document.body.innerText);
  assert.match(body, /DEGRADED/);
  assert.doesNotMatch(body, /OPERATIONAL/);
  // 0.8.2 was the placeholder the snapshot fallback substituted for a version
  // nobody observed, which also made the socket read as connected.
  assert.doesNotMatch(body, /0\.8\.2/);
  assert.equal(await page.$eval('[data-testid="diagnostics-herdr-version"]', node => node.innerText), 'HERDR VERSION: NOT REPORTED');
  assert.equal(await page.$eval('[data-testid="diagnostics-degraded-sources"]', node => node.innerText), 'UNAVAILABLE SOURCES: HERDR');
  await page.close();
});

test('a healthy gateway with a real Herdr snapshot reports the observed version', async () => {
  const page = await openPage('/diagnostics', {
    healthPayload: { status: 'healthy', service: 'magistrate-gateway', herdr_socket_connected: true, herdr_version: '0.9.1', degraded_sources: [] },
  });
  await page.waitForFunction(() => document.body.innerText.includes('HERDR VERSION'));
  assert.equal(await page.$eval('[data-testid="diagnostics-herdr-version"]', node => node.innerText), 'HERDR VERSION: 0.9.1');
  assert.equal(await page.$('[data-testid="diagnostics-degraded-sources"]'), null);
  assert.match(await page.evaluate(() => document.body.innerText), /OPERATIONAL/);
  await page.close();
});

test('a non-2xx health response surfaces unavailable rather than a fabricated status', async () => {
  const page = await openPage('/diagnostics', { healthStatus: 503 });
  await page.waitForSelector('[data-testid="diagnostics-health-error"]');
  assert.match(await page.$eval('[data-testid="diagnostics-health-error"]', node => node.innerText), /Gateway telemetry is unavailable\./);
  const body = await page.evaluate(() => document.body.innerText);
  assert.match(body, /UNAVAILABLE/);
  assert.doesNotMatch(body, /OPERATIONAL/);
  // Nothing may stand in for telemetry we never received.
  assert.equal(await page.$('[data-testid="diagnostics-herdr-version"]'), null);
  await page.close();
});

// --- Attachment processing state -----------------------------------------

test('an upload the gateway did not confirm as stored fails instead of reading as sent', async () => {
  const page = await openPage('/chat', { uploadRecord: { upload_id: 'upload-000000000000abcd', filename: 'package.json', media_type: 'application/json', size: 123 } });
  await page.waitForSelector('[data-testid="attachment-menu-button"]');
  const chooserPromise = page.waitForFileChooser();
  await page.click('[data-testid="attachment-menu-button"]');
  await page.click('[data-testid="attachment-option-files"]');
  await (await chooserPromise).accept([path.join(process.cwd(), 'package.json')]);
  await page.waitForFunction(() => document.querySelector('[data-testid="attachment-preview"]')?.innerText.includes('package.json'));
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('Review the attached manifest');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="captain-send-error"]');
  assert.match(await page.$eval('[data-testid="captain-send-error"]', node => node.innerText), /did not confirm the upload was stored/);
  // The prompt is never sent, so nothing can present the file as delivered.
  assert.equal(await page.evaluate(() => window.__calls.filter(call => call.url.includes('/captain/prompt')).length), 0);
  const history = await page.$eval('[data-testid="chat-history"]', node => node.innerText);
  assert.match(history, /Upload failed/);
  assert.doesNotMatch(history, /· Attached/);
  await page.close();
});

test('a rejected upload reports the gateway reason and never claims delivery', async () => {
  const page = await openPage('/chat', { uploadStatus: 422 });
  await page.waitForSelector('[data-testid="attachment-menu-button"]');
  const chooserPromise = page.waitForFileChooser();
  await page.click('[data-testid="attachment-menu-button"]');
  await page.click('[data-testid="attachment-option-files"]');
  await (await chooserPromise).accept([path.join(process.cwd(), 'package.json')]);
  await page.waitForFunction(() => document.querySelector('[data-testid="attachment-preview"]')?.innerText.includes('package.json'));
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('Review the attached manifest');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="captain-send-error"]');
  assert.match(await page.$eval('[data-testid="captain-send-error"]', node => node.innerText), /does not match its declared type/);
  assert.equal(await page.evaluate(() => window.__calls.filter(call => call.url.includes('/captain/prompt')).length), 0);
  assert.doesNotMatch(await page.$eval('[data-testid="chat-history"]', node => node.innerText), /· Attached/);
  await page.close();
});

test('a confirmed upload reports Attached only after the prompt is accepted', async () => {
  const page = await openPage('/chat', { uploadRecord: { upload_id: 'upload-000000000000abcd', filename: 'package.json', media_type: 'application/json', size: 123, status: 'stored', attached: true } });
  await page.waitForSelector('[data-testid="attachment-menu-button"]');
  const chooserPromise = page.waitForFileChooser();
  await page.click('[data-testid="attachment-menu-button"]');
  await page.click('[data-testid="attachment-option-files"]');
  await (await chooserPromise).accept([path.join(process.cwd(), 'package.json')]);
  await page.waitForFunction(() => document.querySelector('[data-testid="attachment-preview"]')?.innerText.includes('package.json'));
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('Review the attached manifest');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="chat-history"]')?.innerText.includes('· Attached'));
  assert.equal(await page.$('[data-testid="captain-send-error"]'), null);
  // Only the four contract fields reach the prompt; the local status flags
  // never travel to the gateway as if they were part of the manifest.
  const body = JSON.parse(await page.evaluate(() => window.__calls.find(item => item.url.includes('/captain/prompt')).body));
  assert.deepEqual(Object.keys(body.attachments[0]).sort(), ['filename', 'media_type', 'size', 'upload_id']);
  assert.equal(body.attachments[0].upload_id, 'upload-000000000000abcd');
  await page.close();
});
