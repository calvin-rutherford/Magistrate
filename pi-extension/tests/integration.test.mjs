import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { connect } from 'node:net';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalValue(value[key])]));
  }
  return value;
}
const canonical = value => Buffer.from(JSON.stringify(canonicalValue(value)), 'utf8');
const hash = value => createHash('sha256').update(value).digest('hex');

test('canonical hashes match the language-neutral ownership vector', async () => {
  const vector = JSON.parse(await readFile(new URL('./protocol-vector.json', import.meta.url), 'utf8'));
  assert.equal(hash(canonical(vector.source_sequence)), vector.source_sequence_sha256);
  assert.equal(hash(canonical(vector.visible_blocks)), vector.visible_content_sha256);
  const semantic = structuredClone(vector.ownership_envelope);
  delete semantic.request_nonce;
  assert.equal(hash(canonical(semantic)), vector.semantic_envelope_sha256);
});

function rawExchange(socketPath, frame) {
  return new Promise((resolve, reject) => {
    const socket = connect(socketPath);
    let response = Buffer.alloc(0);
    socket.once('connect', () => socket.write(frame));
    socket.on('data', chunk => { response = Buffer.concat([response, chunk]); });
    socket.once('error', reject);
    socket.once('end', () => resolve(response));
  });
}

function exchange(socketPath, key, body) {
  return new Promise((resolve, reject) => {
    const socket = connect(socketPath);
    const mac = createHmac('sha256', key).update(canonical(body)).digest('hex');
    let response = Buffer.alloc(0);
    socket.once('connect', () => socket.write(Buffer.concat([
      canonical({ body, mac }), Buffer.from('\n'),
    ])));
    socket.on('data', chunk => { response = Buffer.concat([response, chunk]); });
    socket.once('error', reject);
    socket.once('end', () => {
      const frame = JSON.parse(response.toString('utf8'));
      const expected = createHmac('sha256', key).update(canonical(frame.body)).digest('hex');
      assert.equal(frame.mac, expected);
      resolve(frame.body);
    });
  });
}

function mockPi(
  sessionId, initialEntries = [], emitTurnEnd = true,
  finalStopReason = 'stop', inputSource = 'extension',
) {
  const entries = structuredClone(initialEntries);
  const handlers = new Map();
  let counter = entries.length;
  let completion;
  const context = {
    sessionManager: {
      getSessionId: () => sessionId,
      getLeafId: () => entries.at(-1)?.id ?? null,
      getLeafEntry: () => entries.at(-1),
      getBranch: () => entries,
    },
    isIdle: () => true,
    hasPendingMessages: () => false,
  };
  const append = entry => {
    entries.push({
      ...entry,
      id: `entry_${String(++counter).padStart(4, '0')}`,
      parentId: entries.at(-1)?.id ?? null,
      timestamp: new Date().toISOString(),
    });
    return entries.at(-1);
  };
  const pi = {
    on(name, handler) { handlers.set(name, handler); },
    appendEntry(customType, data) { append({ type: 'custom', customType, data }); },
    sendUserMessage(prompt, options) {
      assert.deepEqual(options, { expandPromptTemplates: false });
      completion = new Promise((resolve, reject) => setImmediate(async () => {
        try {
          const inputResult = await handlers.get('input')({
            type: 'input', text: prompt, source: inputSource,
          }, context);
          if (inputResult?.action === 'handled') { resolve(); return; }
          await handlers.get('before_agent_start')({
            type: 'before_agent_start', prompt, systemPrompt: 'test', systemPromptOptions: {},
          }, context);
          append({ type: 'message', message: { role: 'user', content: prompt, timestamp: Date.now() } });
          await handlers.get('turn_start')({ type: 'turn_start', turnIndex: 0, timestamp: Date.now() }, context);
          if (emitTurnEnd) {
            const retry = {
              role: 'assistant', stopReason: 'error', timestamp: Date.now(),
              content: [{ type: 'text', text: 'PRIVATE_RETRY_OUTPUT_SENTINEL' }],
            };
            append({ type: 'message', message: retry });
            await handlers.get('turn_end')({
              type: 'turn_end', turnIndex: 0, message: retry, toolResults: [],
            }, context);
          }
          append({ type: 'model_change', provider: 'PRIVATE_PROVIDER_SENTINEL', modelId: 'private-model' });
          const assistant = {
            role: 'assistant', stopReason: finalStopReason, timestamp: Date.now(),
            content: [
              { type: 'thinking', thinking: 'PRIVATE_REASONING_SENTINEL' },
              { type: 'text', text: '  exact semantic 🙂\n' },
              { type: 'toolCall', id: 'tool-secret', name: 'read', arguments: { token: 'PRIVATE_TOOL_SENTINEL' } },
              { type: 'text', text: 'tail with spaces  ' },
            ],
            provider: 'test', model: 'test', usage: { input: 1, output: 2 },
          };
          append({ type: 'message', message: assistant });
          if (emitTurnEnd) {
            await handlers.get('turn_end')({
              type: 'turn_end', turnIndex: 0, message: assistant, toolResults: [],
            }, context);
          }
          resolve();
        } catch (error) { reject(error); }
      }));
    },
  };
  return { pi, handlers, context, entries, completed: () => completion };
}

const fixedExpiry = Date.now() + 5 * 60_000;

function request(capability, prompt, nonce, messageType = 'dispatch', acceptedHash) {
  const suffix = '0123456789abcdef0123456789abcdef';
  const body = {
    schema_version: 'magistrate.pi.ipc.v1', message_type: messageType,
    request_nonce: nonce, issued_at: Date.now(),
    dispatch_incarnation: `pdi_${suffix}`,
    capability, capability_sha256: hash(capability),
    tenant_id: 'operator', principal_id: 'operator',
    conversation_id: `cv_${suffix}`, turn_id: `ct_${suffix}`,
    assistant_message_id: `cm_${suffix}`, objective_id: `obj_${suffix}`,
    run_id: `run_${suffix}`, prompt_sha256: hash(prompt),
    expires_at: fixedExpiry,
  };
  if (messageType === 'dispatch') body.prompt = prompt;
  if (messageType === 'ack') body.accepted_envelope_sha256 = acceptedHash;
  return body;
}

test('native Pi dispatch binds exact entries, redacts internals, and recovers from encrypted journal', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'magistrate-pi-extension-'));
  const runtime = join(directory, 'runtime');
  await mkdir(runtime, { mode: 0o700 });
  const keyPath = join(runtime, 'channel.key');
  const socketPath = join(runtime, 'channel.sock');
  const journalPath = join(runtime, 'channel.journal');
  const key = Buffer.from('K'.repeat(48));
  await writeFile(keyPath, Buffer.concat([key, Buffer.from('\n')]), { mode: 0o600 });
  await chmod(keyPath, 0o600);
  Object.assign(process.env, {
    MAGISTRATE_PI_OWNERSHIP_ENABLED: 'true',
    MAGISTRATE_PI_RUNTIME_DIR: runtime,
    MAGISTRATE_PI_IPC_KEY_PATH: keyPath,
    MAGISTRATE_PI_ADAPTER_SOCKET: socketPath,
    MAGISTRATE_PI_ADAPTER_JOURNAL: journalPath,
  });

  const { default: extension } = await import('../.test-dist/index.js');
  const first = mockPi('session_exact_1');
  extension(first.pi);
  await first.handlers.get('session_start')({ type: 'session_start' }, first.context);

  const capability = `pic_${'A'.repeat(43)}`;
  const prompt = 'dispatch exactly, including 🙂 and trailing space ';
  const initialRequest = request(capability, prompt, 'nonce_initial_1234567890');
  const unauthenticated = await rawExchange(socketPath, Buffer.concat([
    canonical({ body: initialRequest, mac: '0'.repeat(64) }), Buffer.from('\n'),
  ]));
  assert.equal(unauthenticated.length, 0);
  const bound = await exchange(socketPath, key, initialRequest);
  assert.equal(bound.event_type, 'dispatch.bound');
  assert.equal(bound.pi_user_content_sha256, hash(prompt));
  assert.equal(bound.source_sequence.filter(entry => entry.role === 'user').length, 1);
  const replayMac = createHmac('sha256', key).update(canonical(initialRequest)).digest('hex');
  const replayed = await rawExchange(socketPath, Buffer.concat([
    canonical({ body: initialRequest, mac: replayMac }), Buffer.from('\n'),
  ]));
  assert.equal(replayed.length, 0);
  await first.completed();

  const final = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_status_12345678901', 'status',
  ));
  assert.equal(final.event_type, 'dispatch.finalized', JSON.stringify(final));
  assert.deepEqual(final.assistant_content, [
    { index: 0, type: 'text', text: '  exact semantic 🙂\n' },
    { index: 1, type: 'text', text: 'tail with spaces  ' },
  ]);
  const serialized = JSON.stringify(final);
  assert.equal(serialized.includes('PRIVATE_REASONING_SENTINEL'), false);
  assert.equal(serialized.includes('PRIVATE_TOOL_SENTINEL'), false);
  assert.equal(serialized.includes('PRIVATE_PROVIDER_SENTINEL'), false);
  assert.equal(serialized.includes('PRIVATE_RETRY_OUTPUT_SENTINEL'), false);
  assert.equal(serialized.includes('thinking'), false);
  assert.equal(serialized.includes('toolCall'), false);
  assert.ok(final.source_sequence.some(entry => entry.entry_type === 'redacted'));
  const encrypted = await readFile(journalPath, 'utf8');
  assert.equal(encrypted.includes(prompt), false);
  assert.equal(encrypted.includes(capability), false);
  assert.equal(encrypted.includes('exact semantic'), false);

  const acceptedEvidence = structuredClone(final);
  delete acceptedEvidence.request_nonce;
  const acceptedHash = hash(canonical(acceptedEvidence));
  const acknowledgement = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_acknowledge_12345678', 'ack', acceptedHash,
  ));
  assert.equal(acknowledgement.event_type, 'acknowledged');
  assert.equal(acknowledgement.accepted_envelope_sha256, acceptedHash);

  await assert.rejects(readFile(journalPath, 'utf8'), /ENOENT/);
  await first.handlers.get('session_shutdown')({ type: 'session_shutdown' }, first.context);

  // Once Gateway acknowledges exact canonical acceptance, the adapter fsyncs
  // evidence deletion. A lost response converges through authenticated unknown.
  const second = mockPi('session_after_restart', []);
  extension(second.pi);
  await second.handlers.get('session_start')({ type: 'session_start' }, second.context);
  const recovered = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_restart_1234567890', 'status',
  ));
  assert.equal(recovered.event_type, 'error');
  assert.equal(recovered.error_code, 'unknown-dispatch');
  await second.handlers.get('session_shutdown')({ type: 'session_shutdown' }, second.context);

  const wrapper = JSON.parse(encrypted);
  wrapper.ciphertext = `${wrapper.ciphertext[0] === 'A' ? 'B' : 'A'}${wrapper.ciphertext.slice(1)}`;
  await writeFile(journalPath, JSON.stringify(wrapper), { mode: 0o600 });
  await chmod(journalPath, 0o600);
  assert.throws(
    () => extension(mockPi('session_tampered').pi),
    /unauthenticated-adapter-journal/,
  );
  await rm(directory, { recursive: true, force: true });
});

test('status recovery finalizes a persisted assistant after a lost turn_end boundary', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'magistrate-pi-boundary-'));
  const runtime = join(directory, 'runtime');
  await mkdir(runtime, { mode: 0o700 });
  const keyPath = join(runtime, 'channel.key');
  const socketPath = join(runtime, 'channel.sock');
  const journalPath = join(runtime, 'channel.journal');
  const key = Buffer.from('R'.repeat(48));
  await writeFile(keyPath, Buffer.concat([key, Buffer.from('\n')]), { mode: 0o600 });
  await chmod(keyPath, 0o600);
  Object.assign(process.env, {
    MAGISTRATE_PI_OWNERSHIP_ENABLED: 'true',
    MAGISTRATE_PI_RUNTIME_DIR: runtime,
    MAGISTRATE_PI_IPC_KEY_PATH: keyPath,
    MAGISTRATE_PI_ADAPTER_SOCKET: socketPath,
    MAGISTRATE_PI_ADAPTER_JOURNAL: journalPath,
  });
  const { default: extension } = await import('../.test-dist/index.js');
  const harness = mockPi('session_crash_boundary', [], false);
  extension(harness.pi);
  await harness.handlers.get('session_start')({ type: 'session_start' }, harness.context);
  const capability = `pic_${'B'.repeat(43)}`;
  const prompt = 'persist before turn end';
  const bound = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_boundary_123456789',
  ));
  assert.equal(bound.event_type, 'dispatch.bound');
  await harness.completed();
  assert.equal(harness.entries.some(entry => entry.customType === 'magistrate.pi.dispatch.finalize.v1'), false);
  assert.equal(harness.handlers.get('session_before_switch')().cancel, true);
  assert.equal(harness.handlers.get('session_before_fork')().cancel, true);
  assert.equal(harness.handlers.get('session_before_tree')().cancel, true);

  const otherCapability = `pic_${'C'.repeat(43)}`;
  const otherPrompt = 'must not cross an open ownership boundary';
  const otherRequest = request(
    otherCapability, otherPrompt, 'nonce_other_dispatch_12345',
  );
  const otherSuffix = 'abcdef0123456789abcdef0123456789';
  Object.assign(otherRequest, {
    dispatch_incarnation: `pdi_${otherSuffix}`,
    conversation_id: `cv_${otherSuffix}`,
    turn_id: `ct_${otherSuffix}`,
    assistant_message_id: `cm_${otherSuffix}`,
    objective_id: `obj_${otherSuffix}`,
    run_id: `run_${otherSuffix}`,
  });
  const blocked = await exchange(socketPath, key, otherRequest);
  assert.equal(blocked.event_type, 'error');
  assert.equal(blocked.error_code, 'pi-session-busy');

  const recovered = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_boundary_status_1234', 'status',
  ));
  assert.equal(recovered.event_type, 'dispatch.finalized');
  assert.equal(harness.entries.filter(entry =>
    entry.customType === 'magistrate.pi.dispatch.finalize.v1').length, 1);
  await harness.handlers.get('session_shutdown')({ type: 'session_shutdown' }, harness.context);
  await rm(directory, { recursive: true, force: true });
});

test('a matching non-native Pi input cannot bind the prepared ownership', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'magistrate-pi-attribution-'));
  const runtime = join(directory, 'runtime');
  await mkdir(runtime, { mode: 0o700 });
  const keyPath = join(runtime, 'channel.key');
  const socketPath = join(runtime, 'channel.sock');
  const journalPath = join(runtime, 'channel.journal');
  const key = Buffer.from('T'.repeat(48));
  await writeFile(keyPath, Buffer.concat([key, Buffer.from('\n')]), { mode: 0o600 });
  await chmod(keyPath, 0o600);
  Object.assign(process.env, {
    MAGISTRATE_PI_OWNERSHIP_ENABLED: 'true',
    MAGISTRATE_PI_RUNTIME_DIR: runtime,
    MAGISTRATE_PI_IPC_KEY_PATH: keyPath,
    MAGISTRATE_PI_ADAPTER_SOCKET: socketPath,
    MAGISTRATE_PI_ADAPTER_JOURNAL: journalPath,
  });
  const { default: extension } = await import('../.test-dist/index.js');
  const harness = mockPi('session_unattributed_input', [], true, 'stop', 'interactive');
  extension(harness.pi);
  await harness.handlers.get('session_start')({ type: 'session_start' }, harness.context);
  const capability = `pic_${'E'.repeat(43)}`;
  const prompt = 'same text, wrong Pi input source';
  const failed = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_unattributed_1234567',
  ));
  assert.equal(failed.event_type, 'dispatch.failed');
  assert.equal(failed.error_code, 'unattributed-user-entry');
  assert.deepEqual(failed.assistant_content, []);
  await harness.completed();
  const semantic = structuredClone(failed);
  delete semantic.request_nonce;
  await exchange(socketPath, key, request(
    capability, prompt, 'nonce_unattributed_ack_123', 'ack', hash(canonical(semantic)),
  ));
  await harness.handlers.get('session_shutdown')({ type: 'session_shutdown' }, harness.context);
  await rm(directory, { recursive: true, force: true });
});

test('unknown feature flag values fail closed before extension activation', async () => {
  const { default: extension } = await import('../.test-dist/index.js');
  process.env.MAGISTRATE_PI_OWNERSHIP_ENABLED = 'tru';
  assert.throws(() => extension(mockPi('invalid_flag_session').pi), /invalid-feature-flag/);
  process.env.MAGISTRATE_PI_OWNERSHIP_ENABLED = 'true';
});

test('restart status closes an abnormal persisted assistant without exposing its text', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'magistrate-pi-abnormal-'));
  const runtime = join(directory, 'runtime');
  await mkdir(runtime, { mode: 0o700 });
  const keyPath = join(runtime, 'channel.key');
  const socketPath = join(runtime, 'channel.sock');
  const journalPath = join(runtime, 'channel.journal');
  const key = Buffer.from('S'.repeat(48));
  await writeFile(keyPath, Buffer.concat([key, Buffer.from('\n')]), { mode: 0o600 });
  await chmod(keyPath, 0o600);
  Object.assign(process.env, {
    MAGISTRATE_PI_OWNERSHIP_ENABLED: 'true',
    MAGISTRATE_PI_RUNTIME_DIR: runtime,
    MAGISTRATE_PI_IPC_KEY_PATH: keyPath,
    MAGISTRATE_PI_ADAPTER_SOCKET: socketPath,
    MAGISTRATE_PI_ADAPTER_JOURNAL: journalPath,
  });
  const { default: extension } = await import('../.test-dist/index.js');
  const capability = `pic_${'D'.repeat(43)}`;
  const prompt = 'abnormal restart boundary';
  const first = mockPi('session_abnormal_restart', [], false, 'error');
  extension(first.pi);
  await first.handlers.get('session_start')({ type: 'session_start' }, first.context);
  const bound = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_abnormal_bound_1234',
  ));
  assert.equal(bound.event_type, 'dispatch.bound');
  await first.completed();
  await first.handlers.get('session_shutdown')({ type: 'session_shutdown' }, first.context);

  const second = mockPi('session_abnormal_restart', first.entries, false, 'error');
  extension(second.pi);
  await second.handlers.get('session_start')({ type: 'session_start' }, second.context);
  const failed = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_abnormal_status_123', 'status',
  ));
  assert.equal(failed.event_type, 'dispatch.failed');
  assert.equal(failed.error_code, 'assistant-error');
  assert.deepEqual(failed.assistant_content, []);
  assert.deepEqual(failed.source_sequence, []);
  const semantic = structuredClone(failed);
  delete semantic.request_nonce;
  const acceptedHash = hash(canonical(semantic));
  const acknowledgement = await exchange(socketPath, key, request(
    capability, prompt, 'nonce_abnormal_ack_123456', 'ack', acceptedHash,
  ));
  assert.equal(acknowledgement.event_type, 'acknowledged');
  await second.handlers.get('session_shutdown')({ type: 'session_shutdown' }, second.context);
  await rm(directory, { recursive: true, force: true });
});
