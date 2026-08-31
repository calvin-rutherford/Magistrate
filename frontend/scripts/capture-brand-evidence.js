// Captures the reference-comparison screenshots for the native-shell touch-up.
// Run from `frontend/`: node scripts/capture-brand-evidence.js
//
// It drives the same headless Chrome the web suites use (chrome-devtools-axi
// cannot write files on this box; see AGENTS.md) against a dev server of its
// own, with the gateway mocked exactly as the chat suite mocks it, so the
// captures show real rendering rather than an error state.
const fs = require('node:fs');
const path = require('node:path');
const { launchBrowser, startWebServer } = require('../tests/helpers/web-server');

const OUT = path.resolve(__dirname, '..', '..', 'docs', 'evidence', 'brand-touchup-c1');
const IPHONE = { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const DESKTOP = { width: 1280, height: 900, deviceScaleFactor: 2 };

const TURNS = [
  { role: 'user', text: 'Where is the auth repair up to?' },
  { role: 'assistant', text: 'The auth repair branch is green. I re-ran the lifecycle suite after the expiry fix and all seven cases pass, including the 403 case that must **not** invalidate the session.\n\nNext I will open the PR and hand it to review.' },
];

function mockGateway(page) {
  return page.evaluateOnNewDocument(turns => {
    localStorage.clear();
    // Same shape the client persists (see src/services/GatewaySessionStorage.ts):
    // seconds-based expiry and the scope list the gateway issues.
    localStorage.setItem('magistrate.gateway.session', JSON.stringify({ token: 'evidence-session', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' }));
    localStorage.setItem('magistrate.account.display-name', 'Cal Rutherford');
    const json = body => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const messages = turns.map((turn, index) => ({
      id: `cm_${index}`, revision: 1, sequence_index: index, role: turn.role,
      audience: turn.role === 'user' ? 'captain' : 'primary', kind: 'conversation',
      text: turn.text, created_at: 1756000000000 + index * 1000, status: 'answered',
    }));
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.includes('/api/v1/')) return nativeFetch(input, init);
      if (url.includes('/auth/session')) return json({ authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' });
      if (url.includes('/conversations/captain/messages')) return json({ target: 'captain', messages: window.__evidenceEmpty ? [] : messages });
      if (url.includes('/execution/capabilities')) return json({ harnesses: [
        { id: 'claude', label: 'Claude Code', verified: true, models: [{ id: 'claude-sonnet-5', label: 'Claude Sonnet', provider: 'anthropic', variant: 'sonnet', available: true }] },
        { id: 'codex', label: 'Codex', verified: true, models: [{ id: 'gpt-5-6', label: 'GPT-5.6', provider: 'openai', variant: 'gpt-5-6', available: true }] },
      ] });
      if (url.includes('/execution/settings')) return json({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
      if (url.includes('/health')) return json({ status: 'healthy', service: 'gateway', herdr_socket_connected: true });
      if (url.includes('/agents')) return json([
        { id: 'w1:p1', name: 'firstmate', status: 'working', harness: 'claude' },
        { id: 'w1:p2', name: 'auth-repair', status: 'working', harness: 'codex' },
        { id: 'w1:p3', name: 'native-beta', status: 'waiting', harness: 'claude' },
      ]);
      if (url.includes('/attention/unified')) return json([
        { id: 'q-1', title: 'Approve the Minimal Light environment', subtitle: 'Magi needs a product decision before shipping', provider: 'firstmate', requires_action: true, url: '/attention' },
        { id: 'q-2', title: 'Review PR 73', subtitle: 'Native shell touch-up is ready for review', provider: 'github', requires_action: true, url: '/prs' },
      ]);
      if (url.includes('/recent-activity')) return json({ items: [
        { id: 'a1', title: 'Canonical conversation record', description: 'Merged', project: 'Magistrate', occurred_at: '2026-08-31' },
        { id: 'a2', title: 'Truthful provider states', description: 'Merged', project: 'Magistrate', occurred_at: '2026-08-30' },
        { id: 'a3', title: 'Quota reporting', description: 'Merged', project: 'EVERSANA', occurred_at: '2026-08-29' },
      ] });
      if (url.includes('/usage')) return json({ generated_at: '2026-08-31T18:00:00Z', schema_version: 5, source: 'quota-axi', providers: [] });
      if (url.includes('/auth/providers')) return json([]);
      if (url.includes('/voice/capabilities')) return json({ schema_version: 'voice-capabilities.v1', provider: 'openai', configured: true, modes: [{ id: 'openai', label: 'Gateway OpenAI', available: true }] });
      return json({});
    };
  }, TURNS);
}

async function shot(page, name) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  console.log('captured', name);
}

const settle = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const server = await startWebServer({ readyPath: '/chat' });
  const browser = await launchBrowser();
  const url = `${server.base}/chat`;
  try {
    // 1. Resting Magi surface: mark, greeting, one composer, nothing else.
    let page = await browser.newPage();
    await page.setViewport(IPHONE);
    await page.evaluateOnNewDocument(() => { window.__evidenceEmpty = true; });
    await mockGateway(page);
    await page.goto(url, { waitUntil: 'networkidle2' });
    await page.waitForSelector('[data-testid="chat-empty-state"]');
    await settle(1200);
    await shot(page, '01-resting-magi-iphone');

    // 2. Drawer over the same screen.
    await page.click('[data-testid="brand-drawer-toggle"]');
    await settle(700);
    await shot(page, '02-drawer-iphone');
    await page.click('[data-testid="drawer-section-fleet"]');
    await settle(500);
    await shot(page, '03-drawer-fleet-iphone');
    await page.click('[data-testid="drawer-section-fleet"]');
    await page.click('[data-testid="drawer-section-attention"]');
    await settle(500);
    await shot(page, '04-drawer-attention-iphone');

    // 3. Settings sheet and Appearance.
    await page.click('[data-testid="drawer-settings-control"]');
    await settle(700);
    await shot(page, '05-settings-sheet-iphone');
    await page.click('[data-testid="settings-theme"]');
    await settle(600);
    await shot(page, '06-appearance-iphone');
    await page.close();

    // 4. Conversation with the transcript populated.
    page = await browser.newPage();
    await page.setViewport(IPHONE);
    await mockGateway(page);
    await page.goto(url, { waitUntil: 'networkidle2' });
    await page.waitForSelector('[data-testid="agent-message"]');
    await settle(1200);
    await shot(page, '07-conversation-iphone');

    // 5. Execution selector.
    await page.click('[data-testid="model-menu-button"]');
    await page.waitForSelector('[data-testid="model-menu"]');
    await settle(500);
    await shot(page, '08-execution-sheet-iphone');
    await page.click('[data-testid="model-advanced-toggle"]');
    await settle(400);
    await shot(page, '09-execution-advanced-iphone');
    await page.close();

    // 6. The same design language on the web breakpoint.
    page = await browser.newPage();
    await page.setViewport(DESKTOP);
    await mockGateway(page);
    await page.goto(url, { waitUntil: 'networkidle2' });
    await page.waitForSelector('[data-testid="agent-message"]');
    await settle(1200);
    await shot(page, '10-conversation-desktop');
    await page.click('[data-testid="brand-drawer-toggle"]');
    await settle(700);
    await shot(page, '11-drawer-desktop');
    await page.close();
  } finally {
    await browser.close();
    await server.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
