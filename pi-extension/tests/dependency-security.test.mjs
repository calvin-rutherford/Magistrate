import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import test from 'node:test';

const root = createRequire(import.meta.url);
const agent = createRequire(new URL('../node_modules/@earendil-works/pi-coding-agent/package.json', import.meta.url));

for (const [consumer, require] of [['adapter external Undici', root], ['agent external Undici', agent]]) {
  test(`${consumer} rejects incorrect integrity with uppercase algorithm tokens`, { timeout: 5000 }, async () => {
    const { fetch } = require('undici');
    const body = 'synthetic integrity control';
    const digest = createHash('sha256').update(body).digest('base64');
    const url = `data:text/plain,${encodeURIComponent(body)}`;
    assert.equal(await (await fetch(url, { integrity: `sha256-${digest}` })).text(), body);
    const wrong = Buffer.alloc(32).toString('base64');
    for (const algorithm of ['sha256', 'SHA256', 'Sha256']) {
      await assert.rejects(fetch(url, { integrity: `${algorithm}-${wrong}` }), error =>
        error instanceof TypeError && error.cause?.message === 'integrity mismatch');
    }
  });
}
