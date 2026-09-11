import type {
  BeforeAgentStartEvent,
  ExtensionAPI,
  ExtensionContext,
  InputEvent,
  InputEventResult,
  TurnEndEvent,
} from '@earendil-works/pi-coding-agent';
import {
  chmodSync,
  closeSync,
  constants as fsConstants,
  existsSync,
  fsyncSync,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createHmac,
  randomBytes,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';
import { createServer, createConnection, type Server, type Socket } from 'node:net';
import { dirname, isAbsolute, join, resolve as resolvePath } from 'node:path';
import { tmpdir } from 'node:os';
import { isDeepStrictEqual } from 'node:util';

const IPC_SCHEMA = 'magistrate.pi.ipc.v1';
const OWNERSHIP_SCHEMA = 'magistrate.pi.ownership.v1';
const JOURNAL_SCHEMA = 'magistrate.pi.adapter-journal.v1';
const PREPARE_TYPE = 'magistrate.pi.dispatch.prepare.v1';
const BIND_TYPE = 'magistrate.pi.dispatch.bind.v1';
const FINALIZE_TYPE = 'magistrate.pi.dispatch.finalize.v1';
const MAX_FRAME_BYTES = 1_250_000;
const MAX_SOURCE_ENTRIES = 4096;
const MAX_VISIBLE_BLOCKS = 4096;
const MAX_VISIBLE_CHARS = 200_000;
const MAX_VISIBLE_BYTES = 1_000_000;
const MAX_RECORDS = 16;
const MAX_JOURNAL_PLAINTEXT_BYTES = MAX_RECORDS * MAX_FRAME_BYTES + 65_536;
const MAX_JOURNAL_FILE_BYTES = Math.ceil(MAX_JOURNAL_PLAINTEXT_BYTES * 4 / 3) + 4096;
const MAX_RECENT_NONCES = 4096;
const CLOCK_SKEW_MS = 30_000;
const BIND_WAIT_MS = 15_000;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const DISPATCH_ID = /^pdi_[A-Za-z0-9_-]{16,96}$/;
const CAPABILITY = /^pic_[A-Za-z0-9_-]{40,96}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const NONCE = /^[A-Za-z0-9_-]{16,128}$/;

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Stage = 'prepared' | 'dispatching' | 'bound' | 'finalized' | 'failed';
type SessionEntry = Record<string, unknown>;

interface DispatchIdentity {
  dispatch_incarnation: string;
  capability_sha256: string;
  tenant_id: string;
  principal_id: string;
  conversation_id: string;
  turn_id: string;
  assistant_message_id: string;
  objective_id: string;
  run_id: string;
  prompt_sha256: string;
  expires_at: number;
}

interface DispatchRequest extends DispatchIdentity {
  schema_version: typeof IPC_SCHEMA;
  message_type: 'dispatch' | 'status' | 'ack';
  request_nonce: string;
  issued_at: number;
  capability: string;
  prompt?: string;
  accepted_envelope_sha256?: string;
}

interface AdapterRecord extends DispatchIdentity {
  stage: Stage;
  pi_session_id: string;
  prepare_entry_id: string;
  user_entry_id?: string;
  user_entry_order?: number;
  user_content_sha256?: string;
  bind_entry_id?: string;
  assistant_entry_id?: string;
  assistant_entry_order?: number;
  finalize_entry_id?: string;
  bound_at?: number;
  finalized_at?: number;
  visible_content_sha256?: string;
  final_source_sequence?: Array<Record<string, Json>>;
  final_assistant_content?: Array<{ index: number; type: 'text'; text: string }>;
  error_code?: string;
  updated_at: number;
}

interface JournalDocument {
  schema_version: typeof JOURNAL_SCHEMA;
  records: AdapterRecord[];
}

class ProtocolError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function canonicalValue(value: unknown): Json {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isSafeInteger(value)) return value;
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value === 'object') {
    const output: { [key: string]: Json } = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const item = (value as Record<string, unknown>)[key];
      if (item !== undefined) output[key] = canonicalValue(item);
    }
    return output;
  }
  throw new ProtocolError('invalid-json-value');
}

function canonicalJson(value: unknown): Buffer {
  return Buffer.from(JSON.stringify(canonicalValue(value)), 'utf8');
}

function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex');
}

function scalarLength(value: string): number {
  return [...value].length;
}

function validUnicode(value: string): boolean {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (code >= 0xd800 && code <= 0xdfff) return false;
  }
  return true;
}

function currentUid(): number {
  if (typeof process.geteuid !== 'function') throw new ProtocolError('peer-identity-unavailable');
  return process.geteuid();
}

function requirePrivateDirectory(path: string): void {
  try {
    mkdirSync(path, { recursive: true, mode: 0o700 });
    const info = lstatSync(path);
    if (realpathSync(path) !== resolvePath(path)
      || !info.isDirectory() || info.isSymbolicLink() || info.uid !== currentUid()
      || (info.mode & 0o7777) !== 0o700) throw new ProtocolError('untrusted-local-runtime');
  } catch (error) {
    if (error instanceof ProtocolError) throw error;
    throw new ProtocolError('local-runtime-unavailable');
  }
}

function configuredPath(variable: string, fallback: string): string {
  const runtime = process.env.MAGISTRATE_PI_RUNTIME_DIR?.trim()
    || (process.env.XDG_RUNTIME_DIR?.trim()
      ? join(process.env.XDG_RUNTIME_DIR.trim(), 'magistrate')
      : join(tmpdir(), `magistrate-${currentUid()}`));
  const path = process.env[variable]?.trim() || join(runtime, fallback);
  if (!isAbsolute(path)) throw new ProtocolError('invalid-local-path');
  return path;
}

function readKey(path: string): Buffer {
  requirePrivateDirectory(dirname(path));
  const noFollow = fsConstants.O_NOFOLLOW ?? 0;
  let descriptor: number;
  try {
    descriptor = openSync(path, fsConstants.O_RDONLY | noFollow);
  } catch {
    throw new ProtocolError('ipc-key-unavailable');
  }
  try {
    const opened = fstatSync(descriptor);
    const linked = lstatSync(path);
    if (!opened.isFile() || linked.isSymbolicLink() || opened.uid !== currentUid()
      || opened.nlink !== 1 || opened.size > 1025 || (opened.mode & 0o7777) !== 0o600
      || opened.dev !== linked.dev || opened.ino !== linked.ino) {
      throw new ProtocolError('untrusted-ipc-key');
    }
    const file = readFileSync(descriptor);
    if (file.length > 1024 || file.length < 33 || file[file.length - 1] !== 10) {
      throw new ProtocolError('invalid-ipc-key');
    }
    const key = Buffer.from(file.subarray(0, -1));
    if (key.length < 32 || key.some(byte => byte < 33 || byte > 126)) {
      key.fill(0);
      throw new ProtocolError('invalid-ipc-key');
    }
    file.fill(0);
    return key;
  } finally {
    closeSync(descriptor);
  }
}

function fsyncDirectory(path: string): void {
  const descriptor = openSync(path, fsConstants.O_RDONLY);
  try { fsyncSync(descriptor); } finally { closeSync(descriptor); }
}

class EncryptedJournal {
  private readonly encryptionKey: Buffer;
  private records = new Map<string, AdapterRecord>();

  constructor(private readonly path: string, sharedKey: Buffer) {
    requirePrivateDirectory(dirname(path));
    this.encryptionKey = createHash('sha256')
      .update(sharedKey)
      .update('\0magistrate-pi-adapter-journal-v1')
      .digest();
    this.load();
  }

  all(): AdapterRecord[] {
    return [...this.records.values()].map(value => structuredClone(value));
  }

  get(id: string): AdapterRecord | undefined {
    const value = this.records.get(id);
    return value ? structuredClone(value) : undefined;
  }

  save(record: AdapterRecord): void {
    if (canonicalJson(record).length > MAX_FRAME_BYTES) {
      throw new ProtocolError('adapter-record-too-large');
    }
    const next = new Map(this.records);
    if (!next.has(record.dispatch_incarnation) && next.size >= MAX_RECORDS) {
      throw new ProtocolError('adapter-journal-full');
    }
    next.set(record.dispatch_incarnation, structuredClone(record));
    this.persist(next);
    this.records = next;
  }

  remove(dispatchIncarnation: string): void {
    if (!this.records.has(dispatchIncarnation)) return;
    const next = new Map(this.records);
    next.delete(dispatchIncarnation);
    if (next.size) {
      this.persist(next);
    } else {
      try {
        const info = lstatSync(this.path);
        if (!info.isFile() || info.isSymbolicLink() || info.uid !== currentUid()
          || (info.mode & 0o7777) !== 0o600) {
          throw new Error('untrusted');
        }
        unlinkSync(this.path);
        fsyncDirectory(dirname(this.path));
      } catch {
        throw new ProtocolError('adapter-journal-unavailable');
      }
    }
    this.records = next;
  }

  close(): void {
    this.encryptionKey.fill(0);
  }

  private load(): void {
    if (!existsSync(this.path)) return;
    const noFollow = fsConstants.O_NOFOLLOW ?? 0;
    let descriptor: number;
    try {
      descriptor = openSync(this.path, fsConstants.O_RDONLY | noFollow);
    } catch {
      throw new ProtocolError('untrusted-adapter-journal');
    }
    let wrapper: Record<string, unknown>;
    try {
      const opened = fstatSync(descriptor);
      const linked = lstatSync(this.path);
      if (!opened.isFile() || linked.isSymbolicLink() || opened.uid !== currentUid()
        || opened.nlink !== 1 || opened.size > MAX_JOURNAL_FILE_BYTES
        || (opened.mode & 0o7777) !== 0o600
        || opened.dev !== linked.dev || opened.ino !== linked.ino) {
        throw new ProtocolError('untrusted-adapter-journal');
      }
      wrapper = JSON.parse(readFileSync(descriptor, 'utf8')) as Record<string, unknown>;
    } catch (error) {
      if (error instanceof ProtocolError) throw error;
      throw new ProtocolError('invalid-adapter-journal');
    } finally {
      closeSync(descriptor);
    }
    if (!exactKeys(wrapper, ['version', 'nonce', 'tag', 'ciphertext'])
      || wrapper.version !== 1 || typeof wrapper.nonce !== 'string'
      || typeof wrapper.tag !== 'string' || typeof wrapper.ciphertext !== 'string') {
      throw new ProtocolError('invalid-adapter-journal');
    }
    try {
      const nonce = Buffer.from(wrapper.nonce, 'base64url');
      const tag = Buffer.from(wrapper.tag, 'base64url');
      const ciphertext = Buffer.from(wrapper.ciphertext, 'base64url');
      if (nonce.length !== 12 || tag.length !== 16
        || ciphertext.length > MAX_JOURNAL_PLAINTEXT_BYTES) {
        throw new Error('bounds');
      }
      const decipher = createDecipheriv('aes-256-gcm', this.encryptionKey, nonce);
      decipher.setAAD(Buffer.from(JOURNAL_SCHEMA));
      decipher.setAuthTag(tag);
      const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
      const document = JSON.parse(plaintext.toString('utf8')) as JournalDocument;
      plaintext.fill(0);
      if (!document || typeof document !== 'object'
        || !exactKeys(document as unknown as Record<string, unknown>, ['schema_version', 'records'])
        || document.schema_version !== JOURNAL_SCHEMA || !Array.isArray(document.records)
        || document.records.length > MAX_RECORDS) throw new Error('shape');
      for (const record of document.records) {
        if (!record || !DISPATCH_ID.test(record.dispatch_incarnation)
          || !['prepared', 'dispatching', 'bound', 'finalized', 'failed'].includes(record.stage)
          || canonicalJson(record).length > MAX_FRAME_BYTES
          || this.records.has(record.dispatch_incarnation)) {
          throw new Error('record');
        }
        this.records.set(record.dispatch_incarnation, structuredClone(record));
      }
    } catch {
      throw new ProtocolError('unauthenticated-adapter-journal');
    }
  }

  private persist(records: Map<string, AdapterRecord>): void {
    const document: JournalDocument = {
      schema_version: JOURNAL_SCHEMA,
      records: [...records.values()].sort((left, right) =>
        left.dispatch_incarnation.localeCompare(right.dispatch_incarnation)),
    };
    const plaintext = canonicalJson(document);
    if (plaintext.length > MAX_JOURNAL_PLAINTEXT_BYTES) {
      plaintext.fill(0);
      throw new ProtocolError('adapter-journal-full');
    }
    const nonce = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', this.encryptionKey, nonce);
    cipher.setAAD(Buffer.from(JOURNAL_SCHEMA));
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
    plaintext.fill(0);
    const wrapper = canonicalJson({
      version: 1,
      nonce: nonce.toString('base64url'),
      tag: cipher.getAuthTag().toString('base64url'),
      ciphertext: ciphertext.toString('base64url'),
    });
    if (wrapper.length > MAX_JOURNAL_FILE_BYTES) {
      throw new ProtocolError('adapter-journal-full');
    }
    const temporary = `${this.path}.tmp-${process.pid}-${randomUUID()}`;
    const noFollow = fsConstants.O_NOFOLLOW ?? 0;
    let descriptor: number | undefined;
    try {
      descriptor = openSync(
        temporary,
        fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | noFollow,
        0o600,
      );
      writeFileSync(descriptor, wrapper);
      fsyncSync(descriptor);
      closeSync(descriptor);
      descriptor = undefined;
      renameSync(temporary, this.path);
      chmodSync(this.path, 0o600);
      fsyncDirectory(dirname(this.path));
    } catch {
      if (descriptor !== undefined) {
        try { closeSync(descriptor); } catch { /* already closed */ }
      }
      try { unlinkSync(temporary); } catch { /* absent or already renamed */ }
      throw new ProtocolError('adapter-journal-unavailable');
    }
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ProtocolError('invalid-request');
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index]);
}

function boundedString(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && scalarLength(value) <= maximum && validUnicode(value);
}

function safeIdentity(value: string): boolean {
  return ![...value].some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || (code >= 127 && code <= 159)
      || code === 0x2028 || code === 0x2029;
  });
}

function safeVisibleText(value: string): boolean {
  return ![...value].some(character => {
    const code = character.codePointAt(0) ?? 0;
    return (code < 32 && ![9, 10, 13].includes(code))
      || (code >= 127 && code <= 159);
  });
}

function parseRequest(raw: Buffer, sharedKey: Buffer, seenNonces: Map<string, number>): DispatchRequest {
  if (!raw.length || raw.length > MAX_FRAME_BYTES || raw[raw.length - 1] !== 10) {
    throw new ProtocolError('invalid-request-frame');
  }
  let frame: Record<string, unknown>;
  try { frame = object(JSON.parse(raw.subarray(0, -1).toString('utf8'))); }
  catch { throw new ProtocolError('invalid-request-frame'); }
  if (!canonicalJson(frame).equals(raw.subarray(0, -1))) {
    throw new ProtocolError('noncanonical-request-frame');
  }
  if (!exactKeys(frame, ['body', 'mac']) || typeof frame.mac !== 'string' || !SHA256.test(frame.mac)) {
    throw new ProtocolError('invalid-request-frame');
  }
  const body = object(frame.body);
  const expected = createHmac('sha256', sharedKey).update(canonicalJson(body)).digest('hex');
  const supplied = Buffer.from(frame.mac, 'ascii');
  const expectedBuffer = Buffer.from(expected, 'ascii');
  if (supplied.length !== expectedBuffer.length || !timingSafeEqual(supplied, expectedBuffer)) {
    throw new ProtocolError('unauthenticated-request');
  }
  if (body.schema_version !== IPC_SCHEMA
    || !['dispatch', 'status', 'ack'].includes(String(body.message_type))) {
    throw new ProtocolError('unsupported-request');
  }
  const required = [
    'schema_version', 'message_type', 'request_nonce', 'issued_at',
    'dispatch_incarnation', 'capability', 'capability_sha256', 'tenant_id', 'principal_id',
    'conversation_id', 'turn_id', 'assistant_message_id', 'objective_id',
    'run_id', 'prompt_sha256', 'expires_at',
    ...(body.message_type === 'dispatch' ? ['prompt']
      : body.message_type === 'ack' ? ['accepted_envelope_sha256'] : []),
  ];
  if (!exactKeys(body, required)) throw new ProtocolError('invalid-request-shape');
  if (typeof body.request_nonce !== 'string' || !NONCE.test(body.request_nonce)
    || typeof body.issued_at !== 'number' || !Number.isSafeInteger(body.issued_at)
    || Math.abs(Date.now() - body.issued_at) > CLOCK_SKEW_MS) throw new ProtocolError('stale-request');
  if (seenNonces.has(body.request_nonce)) throw new ProtocolError('replayed-request');
  const observedAt = Date.now();
  for (const [nonce, observed] of seenNonces) {
    if (observed < observedAt - 2 * CLOCK_SKEW_MS) seenNonces.delete(nonce);
  }
  if (seenNonces.size >= MAX_RECENT_NONCES) throw new ProtocolError('nonce-window-full');
  seenNonces.set(body.request_nonce, observedAt);
  if (typeof body.dispatch_incarnation !== 'string' || !DISPATCH_ID.test(body.dispatch_incarnation)
    || typeof body.capability !== 'string' || !CAPABILITY.test(body.capability)
    || typeof body.prompt_sha256 !== 'string' || !SHA256.test(body.prompt_sha256)
    || sha256(body.capability) !== body.capability_sha256
    || typeof body.capability_sha256 !== 'string' || !SHA256.test(body.capability_sha256)) {
    throw new ProtocolError('invalid-capability');
  }
  for (const key of [
    'tenant_id', 'principal_id', 'conversation_id', 'turn_id',
    'assistant_message_id', 'objective_id', 'run_id',
  ]) {
    const candidate = body[key];
    const maximum = key === 'tenant_id' || key === 'principal_id' ? 256 : 128;
    if (!boundedString(candidate, maximum) || !safeIdentity(candidate)) {
      throw new ProtocolError('invalid-identity');
    }
  }
  if (typeof body.expires_at !== 'number' || !Number.isSafeInteger(body.expires_at)
    || body.expires_at < 1_000_000_000_000
    || body.expires_at > body.issued_at + 10 * 60_000 + CLOCK_SKEW_MS) {
    throw new ProtocolError('invalid-expiry');
  }
  if (body.message_type === 'dispatch') {
    if (!boundedString(body.prompt, 100_000)
      || Buffer.byteLength(body.prompt, 'utf8') > MAX_VISIBLE_BYTES
      || sha256(body.prompt) !== body.prompt_sha256) throw new ProtocolError('invalid-prompt');
  } else if (body.message_type === 'ack'
    && (typeof body.accepted_envelope_sha256 !== 'string'
      || !SHA256.test(body.accepted_envelope_sha256))) {
    throw new ProtocolError('invalid-acknowledgement');
  }
  return body as unknown as DispatchRequest;
}

function requestIdentity(request: DispatchRequest): DispatchIdentity {
  return {
    dispatch_incarnation: request.dispatch_incarnation,
    capability_sha256: request.capability_sha256,
    tenant_id: request.tenant_id,
    principal_id: request.principal_id,
    conversation_id: request.conversation_id,
    turn_id: request.turn_id,
    assistant_message_id: request.assistant_message_id,
    objective_id: request.objective_id,
    run_id: request.run_id,
    prompt_sha256: request.prompt_sha256,
    expires_at: request.expires_at,
  };
}

function sameIdentity(left: DispatchIdentity, right: DispatchIdentity): boolean {
  const keys: Array<keyof DispatchIdentity> = [
    'dispatch_incarnation', 'capability_sha256', 'tenant_id', 'principal_id',
    'conversation_id', 'turn_id', 'assistant_message_id', 'objective_id',
    'run_id', 'prompt_sha256', 'expires_at',
  ];
  return keys.every(key => left[key] === right[key]);
}

function customData(entry: SessionEntry, expectedType: string): Record<string, unknown> | null {
  return entry.type === 'custom' && entry.customType === expectedType
    && entry.data && typeof entry.data === 'object' && !Array.isArray(entry.data)
    ? entry.data as Record<string, unknown> : null;
}

function message(entry: SessionEntry): Record<string, unknown> | null {
  return entry.type === 'message' && entry.message && typeof entry.message === 'object'
    ? entry.message as Record<string, unknown> : null;
}

function messageText(entry: SessionEntry): string | null {
  const value = message(entry);
  if (!value) return null;
  if (typeof value.content === 'string') return value.content;
  if (!Array.isArray(value.content)) return null;
  const pieces: string[] = [];
  for (const block of value.content) {
    if (!block || typeof block !== 'object' || (block as Record<string, unknown>).type !== 'text'
      || typeof (block as Record<string, unknown>).text !== 'string') return null;
    pieces.push((block as Record<string, unknown>).text as string);
  }
  return pieces.join('');
}

function markerIdentity(record: AdapterRecord): Record<string, Json> {
  return {
    schema_version: OWNERSHIP_SCHEMA,
    dispatch_incarnation: record.dispatch_incarnation,
    capability_sha256: record.capability_sha256,
    tenant_id: record.tenant_id,
    principal_id: record.principal_id,
    conversation_id: record.conversation_id,
    turn_id: record.turn_id,
    assistant_message_id: record.assistant_message_id,
    objective_id: record.objective_id,
    run_id: record.run_id,
    prompt_sha256: record.prompt_sha256,
  };
}

function markerMatches(entry: SessionEntry, type: string, record: AdapterRecord): boolean {
  const data = customData(entry, type);
  if (!data) return false;
  const base = markerIdentity(record);
  if (!Object.entries(base).every(([key, value]) => data[key] === value)) return false;
  if (type === PREPARE_TYPE) return exactKeys(data, Object.keys(base));
  if (type === BIND_TYPE) {
    return exactKeys(data, [
      ...Object.keys(base), 'pi_prepare_entry_id', 'pi_user_entry_id',
      'pi_user_content_sha256', 'bound_at',
    ])
      && data.pi_prepare_entry_id === record.prepare_entry_id
      && typeof data.pi_user_entry_id === 'string' && SAFE_ID.test(data.pi_user_entry_id)
      && data.pi_user_content_sha256 === record.prompt_sha256
      && typeof data.bound_at === 'number' && Number.isSafeInteger(data.bound_at);
  }
  if (type === FINALIZE_TYPE) {
    return exactKeys(data, [
      ...Object.keys(base), 'pi_assistant_entry_id',
      'visible_content_sha256', 'finalized_at',
    ])
      && typeof data.pi_assistant_entry_id === 'string' && SAFE_ID.test(data.pi_assistant_entry_id)
      && typeof data.visible_content_sha256 === 'string' && SHA256.test(data.visible_content_sha256)
      && typeof data.finalized_at === 'number' && Number.isSafeInteger(data.finalized_at);
  }
  return false;
}

function entryId(entry: SessionEntry): string {
  if (typeof entry.id !== 'string' || !SAFE_ID.test(entry.id)) throw new ProtocolError('unsafe-pi-entry-identity');
  return entry.id;
}

function appendMarker(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  customType: string,
  data: Record<string, Json>,
): SessionEntry {
  const before = ctx.sessionManager.getLeafId();
  pi.appendEntry(customType, data);
  const leaf = ctx.sessionManager.getLeafEntry() as unknown as SessionEntry | undefined;
  const persisted = leaf ? customData(leaf, customType) : null;
  if (!leaf || leaf.parentId !== before || !persisted || !isDeepStrictEqual(persisted, data)) {
    throw new ProtocolError('pi-entry-persistence-failed');
  }
  return leaf;
}

function describeSequence(
  branch: SessionEntry[],
  record: AdapterRecord,
  endId: string,
): Array<Record<string, Json>> {
  const start = branch.findIndex(entry => entry.id === record.prepare_entry_id);
  const end = branch.findIndex(entry => entry.id === endId);
  if (start < 0 || end < start || end - start + 1 > MAX_SOURCE_ENTRIES) {
    throw new ProtocolError('pi-source-out-of-range');
  }
  return branch.slice(start, end + 1).map((entry, relative) => {
    const id = entryId(entry);
    const parent = entry.parentId;
    if (parent !== null && parent !== undefined && (typeof parent !== 'string' || !SAFE_ID.test(parent))) {
      throw new ProtocolError('unsafe-pi-entry-identity');
    }
    let entryType: 'message' | 'custom' | 'redacted' = 'redacted';
    let role: 'user' | 'assistant' | null = null;
    let customType: string | null = null;
    let stopReason: 'stop' | 'length' | 'toolUse' | 'error' | 'aborted' | null = null;
    if (id === record.prepare_entry_id) {
      entryType = 'custom'; customType = PREPARE_TYPE;
    } else if (id === record.bind_entry_id) {
      entryType = 'custom'; customType = BIND_TYPE;
    } else if (id === record.finalize_entry_id) {
      entryType = 'custom'; customType = FINALIZE_TYPE;
    } else if (id === record.user_entry_id) {
      entryType = 'message'; role = 'user';
    } else if (id === record.assistant_entry_id) {
      const value = message(entry);
      if (!value || value.role !== 'assistant'
        || !['stop', 'length', 'toolUse', 'error', 'aborted'].includes(String(value.stopReason))) {
        throw new ProtocolError('invalid-final-assistant-entry');
      }
      entryType = 'message'; role = 'assistant';
      stopReason = value.stopReason as typeof stopReason;
    }
    return {
      order: start + relative,
      entry_id: id,
      parent_id: parent == null ? null : parent,
      entry_type: entryType,
      role,
      custom_type: customType,
      stop_reason: stopReason,
    };
  });
}

function visibleContent(entry: SessionEntry): Array<{ index: number; type: 'text'; text: string }> {
  const value = message(entry);
  if (!value || value.role !== 'assistant' || !Array.isArray(value.content)) {
    throw new ProtocolError('invalid-final-assistant-entry');
  }
  const visible: Array<{ index: number; type: 'text'; text: string }> = [];
  for (const block of value.content) {
    if (block && typeof block === 'object' && (block as Record<string, unknown>).type === 'text') {
      const text = (block as Record<string, unknown>).text;
      if (typeof text !== 'string' || !validUnicode(text)) throw new ProtocolError('invalid-visible-content');
      visible.push({ index: visible.length, type: 'text', text });
    }
    // Thinking, tool calls, and unknown/internal blocks are intentionally not
    // copied, counted, hashed, logged, or represented in the envelope.
  }
  const joined = visible.map(block => block.text).join('');
  if (!joined.trim() || !safeVisibleText(joined)
    || visible.length > MAX_VISIBLE_BLOCKS
    || scalarLength(joined) > MAX_VISIBLE_CHARS
    || Buffer.byteLength(joined, 'utf8') > MAX_VISIBLE_BYTES) {
    throw new ProtocolError('invalid-visible-content');
  }
  return visible;
}

function evidence(record: AdapterRecord, branch: SessionEntry[], requestNonce: string): Record<string, Json> {
  let eventType: 'dispatch.prepared' | 'dispatch.bound' | 'dispatch.finalized' | 'dispatch.failed';
  if (record.stage === 'finalized') eventType = 'dispatch.finalized';
  else if (record.stage === 'bound') eventType = 'dispatch.bound';
  else if (record.stage === 'failed') eventType = 'dispatch.failed';
  else eventType = 'dispatch.prepared';
  const hasBinding = eventType === 'dispatch.bound' || eventType === 'dispatch.finalized';
  const endId = eventType === 'dispatch.finalized' ? record.finalize_entry_id
    : eventType === 'dispatch.bound' ? record.bind_entry_id : undefined;
  const sequence = eventType === 'dispatch.finalized' && record.final_source_sequence
    ? structuredClone(record.final_source_sequence)
    : endId ? describeSequence(branch, record, endId) : [];
  const content = eventType === 'dispatch.finalized' && record.final_assistant_content
    ? structuredClone(record.final_assistant_content)
    : eventType === 'dispatch.finalized'
      ? visibleContent(branch.find(entry => entry.id === record.assistant_entry_id)!) : [];
  const visible = content.map(block => ({ index: block.index, text: block.text }));
  return {
    schema_version: OWNERSHIP_SCHEMA,
    event_type: eventType,
    request_nonce: requestNonce,
    dispatch_incarnation: record.dispatch_incarnation,
    capability_sha256: record.capability_sha256,
    tenant_id: record.tenant_id,
    principal_id: record.principal_id,
    conversation_id: record.conversation_id,
    turn_id: record.turn_id,
    assistant_message_id: record.assistant_message_id,
    objective_id: record.objective_id,
    run_id: record.run_id,
    source_revision: eventType === 'dispatch.finalized' ? 2 : eventType === 'dispatch.bound' ? 1 : 0,
    pi_session_id: hasBinding ? record.pi_session_id : null,
    pi_prepare_entry_id: hasBinding ? record.prepare_entry_id : null,
    pi_user_entry_id: hasBinding ? record.user_entry_id ?? null : null,
    pi_user_entry_order: hasBinding ? record.user_entry_order ?? null : null,
    pi_user_content_sha256: hasBinding ? record.user_content_sha256 ?? null : null,
    pi_bind_entry_id: hasBinding ? record.bind_entry_id ?? null : null,
    pi_assistant_entry_id: eventType === 'dispatch.finalized' ? record.assistant_entry_id ?? null : null,
    pi_assistant_entry_order: eventType === 'dispatch.finalized' ? record.assistant_entry_order ?? null : null,
    pi_finalize_entry_id: eventType === 'dispatch.finalized' ? record.finalize_entry_id ?? null : null,
    finality: eventType === 'dispatch.finalized' ? 'final' : eventType === 'dispatch.failed' ? 'failed' : 'pending',
    stop_reason: eventType === 'dispatch.finalized' ? 'stop' : null,
    bound_at: hasBinding ? record.bound_at ?? null : null,
    finalized_at: eventType === 'dispatch.finalized' ? record.finalized_at ?? null : null,
    source_start_order: sequence.length ? sequence[0].order : null,
    source_end_order: sequence.length ? sequence[sequence.length - 1].order : null,
    source_cursor: sequence.length ? sequence[sequence.length - 1].entry_id : null,
    source_sequence: sequence,
    source_sequence_sha256: sequence.length ? sha256(canonicalJson(sequence)) : null,
    visible_content_sha256: eventType === 'dispatch.finalized' ? sha256(canonicalJson(visible)) : null,
    assistant_content: content,
    error_code: eventType === 'dispatch.failed' ? record.error_code ?? 'adapter-failed' : null,
  };
}

function clearFinalEvidence(record: AdapterRecord): void {
  delete record.assistant_entry_id;
  delete record.assistant_entry_order;
  delete record.finalize_entry_id;
  delete record.finalized_at;
  delete record.visible_content_sha256;
  delete record.final_source_sequence;
  delete record.final_assistant_content;
}

function ensureResponseFits(body: Record<string, Json>): void {
  const frameBytes = canonicalJson({ body, mac: '0'.repeat(64) }).length + 1;
  if (frameBytes > MAX_FRAME_BYTES) throw new ProtocolError('response-too-large');
}

function ensureEvidenceFits(record: AdapterRecord): void {
  ensureResponseFits(evidence(record, [], 'N'.repeat(128)));
}

function semanticEvidenceHash(record: AdapterRecord, branch: SessionEntry[]): string {
  const value = evidence(record, branch, 'semantic_hash_nonce');
  delete value.request_nonce;
  return sha256(canonicalJson(value));
}

class OwnershipCoordinator {
  private context?: ExtensionContext;
  private waiters = new Map<string, Array<() => void>>();
  private activeInProcess = new Set<string>();
  private inputAuthorized = new Set<string>();
  private turnAuthorized = new Set<string>();
  private pendingNativeDispatch?: string;
  private requestQueue: Promise<void> = Promise.resolve();

  constructor(private readonly pi: ExtensionAPI, private readonly journal: EncryptedJournal) {}

  setContext(context: ExtensionContext): void {
    this.context = context;
  }

  hasOpenDispatch(): boolean {
    return this.journal.all().some(record =>
      record.stage !== 'finalized' && record.stage !== 'failed');
  }

  async dispatch(request: DispatchRequest): Promise<Record<string, Json>> {
    const previous = this.requestQueue;
    let release!: () => void;
    this.requestQueue = new Promise<void>(resolve => { release = resolve; });
    await previous;
    try {
      return await this.dispatchLocked(request);
    } finally {
      if (this.pendingNativeDispatch === request.dispatch_incarnation) {
        this.pendingNativeDispatch = undefined;
      }
      this.inputAuthorized.delete(request.dispatch_incarnation);
      this.turnAuthorized.delete(request.dispatch_incarnation);
      release();
    }
  }

  private async dispatchLocked(request: DispatchRequest): Promise<Record<string, Json>> {
    const ctx = this.context;
    if (!ctx) throw new ProtocolError('pi-session-unavailable');
    const identity = requestIdentity(request);
    let record = this.journal.get(request.dispatch_incarnation);
    if (record && !sameIdentity(record, identity)) throw new ProtocolError('dispatch-identity-conflict');
    if (record && record.pi_session_id !== ctx.sessionManager.getSessionId()
      && record.stage !== 'finalized' && record.stage !== 'failed') {
      throw new ProtocolError('dispatch-session-conflict');
    }
    if (request.message_type === 'ack') {
      if (!record) throw new ProtocolError('unknown-dispatch');
      if (record.stage !== 'finalized' && record.stage !== 'failed') {
        throw new ProtocolError('premature-acknowledgement');
      }
      const accepted = request.accepted_envelope_sha256!;
      const expected = semanticEvidenceHash(
        record,
        (record.final_source_sequence || record.stage === 'failed') ? [] : this.branch(),
      );
      if (accepted !== expected) throw new ProtocolError('acknowledgement-mismatch');
      // Canonical acceptance is the deletion boundary. If the signed response
      // is lost after this fsync, Gateway treats authenticated unknown-dispatch
      // for its already-final state as a converged receipt.
      this.journal.remove(record.dispatch_incarnation);
      return {
        schema_version: IPC_SCHEMA,
        event_type: 'acknowledged',
        request_nonce: request.request_nonce,
        dispatch_incarnation: record.dispatch_incarnation,
        accepted_envelope_sha256: accepted,
      };
    }
    if (!record) {
      if (this.hasOpenDispatch()) throw new ProtocolError('pi-session-busy');
      if (request.message_type !== 'dispatch' || request.prompt === undefined) {
        throw new ProtocolError('unknown-dispatch');
      }
      const sessionId = ctx.sessionManager.getSessionId();
      if (typeof sessionId !== 'string' || !SAFE_ID.test(sessionId)) {
        throw new ProtocolError('unsafe-pi-session-identity');
      }
      const provisional: AdapterRecord = {
        ...identity,
        stage: 'prepared',
        pi_session_id: sessionId,
        prepare_entry_id: 'pending',
        updated_at: Date.now(),
      };
      if (Date.now() > request.expires_at) {
        provisional.stage = 'failed';
        provisional.error_code = 'capability-expired';
        provisional.prepare_entry_id = 'not-created';
        this.journal.save(provisional);
        return evidence(provisional, [], request.request_nonce);
      }
      if (!ctx.isIdle() || ctx.hasPendingMessages()) throw new ProtocolError('pi-session-busy');
      // If the process died after native appendEntry but before the journal
      // rename, reconstruct that exact marker instead of minting another.
      const recovered = this.branch().filter(entry => markerMatches(entry, PREPARE_TYPE, provisional));
      if (recovered.length > 1) {
        provisional.stage = 'failed';
        provisional.error_code = 'ambiguous-prepare-marker';
        provisional.prepare_entry_id = 'not-created';
        provisional.updated_at = Date.now();
        this.journal.save(provisional);
        return evidence(provisional, [], request.request_nonce);
      }
      const marker = recovered[0]
        ?? appendMarker(this.pi, ctx, PREPARE_TYPE, markerIdentity(provisional));
      provisional.prepare_entry_id = entryId(marker);
      this.journal.save(provisional);
      record = provisional;
    }

    if (record.stage === 'finalized' && record.final_source_sequence
      && record.final_assistant_content) {
      return evidence(record, [], request.request_nonce);
    }
    if (record.stage === 'failed') {
      return evidence(record, [], request.request_nonce);
    }
    if (record.stage === 'dispatching') {
      try {
        // A persisted bind marker can recover here. A bare matching user entry
        // cannot: only this extension's native input lifecycle may mint bind.
        record = this.reconcile(record);
      } catch {
        record.stage = 'failed'; record.error_code = 'indeterminate-before-bind'; record.updated_at = Date.now();
        this.journal.save(record);
      }
    } else {
      try {
        record = this.reconcile(record);
      } catch (error) {
        record.stage = 'failed';
        record.error_code = error instanceof ProtocolError
          && /^[a-z0-9._-]{1,64}$/.test(error.code)
          ? error.code : 'reconciliation-failed';
        record.updated_at = Date.now();
        this.journal.save(record);
      }
    }
    if (record.stage === 'finalized' || record.stage === 'failed') {
      this.activeInProcess.delete(record.dispatch_incarnation);
    }
    if (record.stage === 'bound' && request.message_type === 'status'
      && !this.activeInProcess.has(record.dispatch_incarnation)
      && ctx.isIdle() && !ctx.hasPendingMessages()) {
      // `agent_settled` is normally authoritative. This equivalent status-time
      // check closes an abnormal stop if the process missed that event.
      this.onAgentSettled();
      record = this.journal.get(record.dispatch_incarnation) ?? record;
    }
    if (record.stage === 'prepared') {
      if (request.message_type !== 'dispatch' || request.prompt === undefined) {
        return evidence(record, this.branch(), request.request_nonce);
      }
      if (Date.now() > record.expires_at) {
        record.stage = 'failed'; record.error_code = 'capability-expired'; record.updated_at = Date.now();
        this.journal.save(record);
      } else if (!ctx.isIdle() || ctx.hasPendingMessages()) {
        throw new ProtocolError('pi-session-busy');
      } else {
        // The fsynced dispatching state is the at-most-once boundary. Recovery
        // never resubmits an indeterminate native API call unless an exact user
        // entry proves what happened.
        record.stage = 'dispatching'; record.updated_at = Date.now();
        this.journal.save(record);
        const transition = this.waitForTransition(record.dispatch_incarnation, BIND_WAIT_MS);
        this.pendingNativeDispatch = record.dispatch_incarnation;
        this.pi.sendUserMessage(request.prompt, { expandPromptTemplates: false });
        const afterSend = this.journal.get(record.dispatch_incarnation) ?? record;
        if (afterSend.stage === 'dispatching' || afterSend.stage === 'prepared') {
          await transition;
        }
        if (this.pendingNativeDispatch === record.dispatch_incarnation) {
          this.pendingNativeDispatch = undefined;
        }
        this.inputAuthorized.delete(record.dispatch_incarnation);
        record = this.reconcile(this.journal.get(record.dispatch_incarnation) ?? afterSend);
      }
    }
    let response = evidence(record, this.branch(), request.request_nonce);
    try {
      ensureResponseFits(response);
    } catch (error) {
      if (record.stage === 'finalized' || record.stage === 'failed') throw error;
      record.stage = 'failed';
      record.error_code = 'response-too-large';
      clearFinalEvidence(record);
      record.updated_at = Date.now();
      this.journal.save(record);
      response = evidence(record, [], request.request_nonce);
    }
    return response;
  }

  onInput(event: InputEvent): InputEventResult {
    const dispatchId = this.pendingNativeDispatch;
    if (!dispatchId) return { action: 'continue' };
    const current = this.journal.get(dispatchId);
    const noImages = event.images === undefined || event.images.length === 0;
    if (current?.stage === 'dispatching' && event.source === 'extension'
      && noImages && sha256(event.text) === current.prompt_sha256) {
      this.inputAuthorized.add(dispatchId);
    }
    return { action: 'continue' };
  }

  onBeforeAgentStart(event: BeforeAgentStartEvent): void {
    const dispatchId = this.pendingNativeDispatch;
    if (!dispatchId || !this.inputAuthorized.has(dispatchId)) return;
    const current = this.journal.get(dispatchId);
    const noImages = event.images === undefined || event.images.length === 0;
    if (current?.stage === 'dispatching' && noImages
      && sha256(event.prompt) === current.prompt_sha256) {
      this.turnAuthorized.add(dispatchId);
    }
  }

  onTurnStart(): void {
    for (const current of this.journal.all()) {
      if (current.stage !== 'dispatching') continue;
      try {
        if (!this.turnAuthorized.has(current.dispatch_incarnation)) {
          throw new ProtocolError('unattributed-user-entry');
        }
        const updated = this.reconcile(current, true);
        if (updated.stage === 'bound') this.activeInProcess.add(updated.dispatch_incarnation);
        if (updated.stage === 'failed') this.activeInProcess.delete(updated.dispatch_incarnation);
        if (updated.stage === 'bound' || updated.stage === 'failed') this.resolve(updated.dispatch_incarnation);
      } catch (error) {
        current.stage = 'failed';
        current.error_code = error instanceof ProtocolError
          && error.code === 'unattributed-user-entry'
          ? error.code : 'ambiguous-user-binding';
        current.updated_at = Date.now();
        this.activeInProcess.delete(current.dispatch_incarnation);
        this.journal.save(current);
        this.resolve(current.dispatch_incarnation);
      } finally {
        this.turnAuthorized.delete(current.dispatch_incarnation);
        this.inputAuthorized.delete(current.dispatch_incarnation);
        if (this.pendingNativeDispatch === current.dispatch_incarnation) {
          this.pendingNativeDispatch = undefined;
        }
      }
    }
  }

  onTurnEnd(event: TurnEndEvent): void {
    for (const current of this.journal.all()) {
      if (current.stage !== 'bound') continue;
      const finalMessage = event.message as unknown as Record<string, unknown>;
      if (finalMessage.role !== 'assistant' || finalMessage.stopReason !== 'stop') continue;
      try {
        const branch = this.branch();
        const bindIndex = branch.findIndex(entry => entry.id === current.bind_entry_id);
        const matches = branch.slice(bindIndex + 1).filter(entry => {
          const candidate = message(entry);
          return candidate?.role === 'assistant'
            && isDeepStrictEqual(candidate, finalMessage);
        });
        if (bindIndex < 0 || matches.length !== 1) throw new ProtocolError('ambiguous-final-assistant');
        const assistant = matches[0];
        const assistantIndex = branch.findIndex(entry => entry.id === assistant.id);
        const laterMessages = branch.slice(assistantIndex + 1).filter(entry => message(entry));
        if (laterMessages.some(entry => message(entry)?.role === 'user')) {
          throw new ProtocolError('crossed-user-boundary');
        }
        if (laterMessages.some(entry => message(entry)?.role === 'assistant')) {
          throw new ProtocolError('ambiguous-final-assistant');
        }
        // Compute/validate visible content before persisting any final marker.
        const content = visibleContent(assistant);
        const visible = content.map(block => ({ index: block.index, text: block.text }));
        current.assistant_entry_id = entryId(assistant);
        current.assistant_entry_order = assistantIndex;
        current.visible_content_sha256 = sha256(canonicalJson(visible));
        current.finalized_at = Math.max(Date.now(), current.bound_at ?? 0);
        const marker = appendMarker(this.pi, this.context!, FINALIZE_TYPE, {
          ...markerIdentity(current),
          pi_assistant_entry_id: current.assistant_entry_id,
          visible_content_sha256: current.visible_content_sha256,
          finalized_at: current.finalized_at,
        });
        current.finalize_entry_id = entryId(marker);
        current.final_source_sequence = describeSequence(
          this.branch(), current, current.finalize_entry_id,
        );
        current.final_assistant_content = structuredClone(content);
        current.stage = 'finalized'; current.updated_at = Date.now();
        this.activeInProcess.delete(current.dispatch_incarnation);
        ensureEvidenceFits(current);
        this.journal.save(current);
      } catch (error) {
        current.stage = 'failed';
        current.error_code = error instanceof ProtocolError
          && ['response-too-large', 'adapter-record-too-large'].includes(error.code)
          ? 'response-too-large' : 'ambiguous-final-assistant';
        clearFinalEvidence(current);
        this.activeInProcess.delete(current.dispatch_incarnation);
        current.updated_at = Date.now();
        this.journal.save(current);
      }
    }
  }

  onAgentSettled(): void {
    for (const current of this.journal.all()) {
      if (current.stage !== 'bound') continue;
      this.activeInProcess.delete(current.dispatch_incarnation);
      try {
        const updated = this.reconcile(current);
        if (updated.stage !== 'bound') continue;
        const branch = this.branch();
        const bindIndex = branch.findIndex(entry => entry.id === updated.bind_entry_id);
        const assistants = branch.slice(bindIndex + 1)
          .map(entry => message(entry))
          .filter((entry): entry is Record<string, unknown> => entry?.role === 'assistant');
        const reason = String(assistants.at(-1)?.stopReason ?? 'missing');
        const errorCode: Record<string, string> = {
          length: 'assistant-length', error: 'assistant-error',
          aborted: 'assistant-aborted', toolUse: 'assistant-tool-use',
          missing: 'assistant-missing',
        };
        updated.stage = 'failed';
        updated.error_code = errorCode[reason] ?? 'assistant-invalid';
        updated.updated_at = Date.now();
        this.journal.save(updated);
      } catch (error) {
        current.stage = 'failed';
        current.error_code = error instanceof ProtocolError
          && ['response-too-large', 'adapter-record-too-large'].includes(error.code)
          ? 'response-too-large' : 'assistant-settled-ambiguous';
        current.updated_at = Date.now();
        this.journal.save(current);
      }
    }
  }

  private reconcile(input: AdapterRecord, allowBind = false): AdapterRecord {
    const record = structuredClone(input);
    const branch = this.branch();
    const prepareIndex = branch.findIndex(entry =>
      entry.id === record.prepare_entry_id && markerMatches(entry, PREPARE_TYPE, record));
    if (prepareIndex < 0) {
      // A journal fsync can precede the custom-entry write only for a process
      // failure. No prompt was sent at that point, so this is a closed failure.
      record.stage = 'failed'; record.error_code = 'prepare-entry-missing'; record.updated_at = Date.now();
      this.journal.save(record); return record;
    }
    const bindMarkers = branch.slice(prepareIndex + 1).filter(entry => markerMatches(entry, BIND_TYPE, record));
    if (bindMarkers.length > 1) throw new ProtocolError('ambiguous-bind-marker');
    if (bindMarkers.length === 1) {
      const bind = bindMarkers[0];
      const bindIndex = branch.findIndex(entry => entry.id === bind.id);
      const users = branch.slice(prepareIndex + 1, bindIndex).filter(entry => message(entry)?.role === 'user');
      if (users.length !== 1 || sha256(messageText(users[0]) ?? Buffer.alloc(0)) !== record.prompt_sha256) {
        throw new ProtocolError('ambiguous-user-binding');
      }
      record.user_entry_id = entryId(users[0]);
      record.user_entry_order = branch.findIndex(entry => entry.id === users[0].id);
      record.user_content_sha256 = record.prompt_sha256;
      record.bind_entry_id = entryId(bind);
      const bindData = customData(bind, BIND_TYPE)!;
      if (
        bindData.pi_prepare_entry_id !== record.prepare_entry_id
        || bindData.pi_user_entry_id !== record.user_entry_id
        || bindData.pi_user_content_sha256 !== record.prompt_sha256
      ) throw new ProtocolError('bind-marker-mismatch');
      record.bound_at = typeof bindData.bound_at === 'number' ? bindData.bound_at : record.bound_at;
      if (!record.bound_at || record.bound_at > record.expires_at) throw new ProtocolError('capability-expired');
      if (record.stage !== 'finalized') record.stage = 'bound';
    } else if ((record.stage === 'dispatching' || record.stage === 'prepared') && allowBind) {
      const users = branch.slice(prepareIndex + 1).filter(entry => message(entry)?.role === 'user');
      if (users.length === 0) {
        if (record.stage === 'dispatching' && Date.now() > record.expires_at
          && this.context!.isIdle() && !this.context!.hasPendingMessages()) {
          record.stage = 'failed';
          record.error_code = 'indeterminate-before-bind';
        }
        record.updated_at = Date.now();
        this.journal.save(record);
        return record;
      }
      if (users.length !== 1 || sha256(messageText(users[0]) ?? Buffer.alloc(0)) !== record.prompt_sha256) {
        throw new ProtocolError('ambiguous-user-binding');
      }
      const userIndex = branch.findIndex(entry => entry.id === users[0].id);
      if (Date.now() > record.expires_at) throw new ProtocolError('capability-expired');
      record.user_entry_id = entryId(users[0]);
      record.user_entry_order = userIndex;
      record.user_content_sha256 = record.prompt_sha256;
      record.bound_at = Date.now();
      const bind = appendMarker(this.pi, this.context!, BIND_TYPE, {
        ...markerIdentity(record),
        pi_prepare_entry_id: record.prepare_entry_id,
        pi_user_entry_id: record.user_entry_id,
        pi_user_content_sha256: record.user_content_sha256,
        bound_at: record.bound_at,
      });
      record.bind_entry_id = entryId(bind);
      record.stage = 'bound';
    } else if (record.stage === 'dispatching' && Date.now() > record.expires_at
      && this.context!.isIdle() && !this.context!.hasPendingMessages()) {
      record.stage = 'failed';
      record.error_code = 'indeterminate-before-bind';
      record.updated_at = Date.now();
      this.journal.save(record);
      return record;
    }

    if (record.bind_entry_id) {
      let finalizeMarkers = branch.filter(entry => markerMatches(entry, FINALIZE_TYPE, record));
      if (finalizeMarkers.length > 1) throw new ProtocolError('ambiguous-finalize-marker');
      if (finalizeMarkers.length === 0 && record.stage === 'bound') {
        // message_end precedes Pi persistence, but the message is durable by
        // turn_end. If the process died in that narrow interval, recover the
        // sole normally-stopped assistant directly from the native branch.
        const bindIndex = branch.findIndex(entry => entry.id === record.bind_entry_id);
        const tail = branch.slice(bindIndex + 1);
        if (bindIndex < 0 || tail.some(entry => message(entry)?.role === 'user')) {
          throw new ProtocolError('crossed-user-boundary');
        }
        const assistants = tail.filter(entry => message(entry)?.role === 'assistant');
        const completed = assistants.filter(entry => message(entry)?.stopReason === 'stop');
        if (completed.length > 1
          || (completed.length === 1 && assistants.at(-1)?.id !== completed[0].id)) {
          throw new ProtocolError('ambiguous-final-assistant');
        }
        if (completed.length === 1) {
          const assistant = completed[0];
          const content = visibleContent(assistant);
          const visible = content.map(block => ({ index: block.index, text: block.text }));
          record.assistant_entry_id = entryId(assistant);
          record.assistant_entry_order = branch.findIndex(entry => entry.id === assistant.id);
          record.visible_content_sha256 = sha256(canonicalJson(visible));
          record.finalized_at = Math.max(Date.now(), record.bound_at ?? 0);
          const marker = appendMarker(this.pi, this.context!, FINALIZE_TYPE, {
            ...markerIdentity(record),
            pi_assistant_entry_id: record.assistant_entry_id,
            visible_content_sha256: record.visible_content_sha256,
            finalized_at: record.finalized_at,
          });
          record.finalize_entry_id = entryId(marker);
          finalizeMarkers = [marker];
        }
      }
      if (finalizeMarkers.length === 1) {
        const marker = finalizeMarkers[0];
        const data = customData(marker, FINALIZE_TYPE)!;
        const assistantId = data.pi_assistant_entry_id;
        const assistant = branch.find(entry => entry.id === assistantId);
        const bindIndex = branch.findIndex(entry => entry.id === record.bind_entry_id);
        const assistantIndex = branch.findIndex(entry => entry.id === assistant?.id);
        if (!assistant || bindIndex < 0 || assistantIndex <= bindIndex
          || message(assistant)?.role !== 'assistant' || message(assistant)?.stopReason !== 'stop') {
          throw new ProtocolError('invalid-final-assistant-entry');
        }
        const content = visibleContent(assistant);
        const visibleHash = sha256(canonicalJson(content.map(block => ({ index: block.index, text: block.text }))));
        if (data.visible_content_sha256 !== visibleHash) throw new ProtocolError('visible-content-mismatch');
        record.assistant_entry_id = entryId(assistant);
        record.assistant_entry_order = assistantIndex;
        record.finalize_entry_id = entryId(marker);
        record.visible_content_sha256 = visibleHash;
        record.finalized_at = typeof data.finalized_at === 'number' ? data.finalized_at : record.finalized_at;
        if (!record.finalized_at) record.finalized_at = record.updated_at;
        record.final_source_sequence = describeSequence(
          this.branch(), record, record.finalize_entry_id,
        );
        record.final_assistant_content = structuredClone(content);
        record.stage = 'finalized';
        ensureEvidenceFits(record);
      }
    }
    record.updated_at = Date.now();
    this.journal.save(record);
    return record;
  }

  private branch(): SessionEntry[] {
    if (!this.context) throw new ProtocolError('pi-session-unavailable');
    return this.context.sessionManager.getBranch() as unknown as SessionEntry[];
  }

  private waitForTransition(id: string, timeout: number): Promise<void> {
    return new Promise(resolve => {
      const list = this.waiters.get(id) ?? [];
      list.push(resolve); this.waiters.set(id, list);
      setTimeout(() => {
        const pending = this.waiters.get(id) ?? [];
        this.waiters.set(id, pending.filter(item => item !== resolve));
        resolve();
      }, timeout).unref();
    });
  }

  private resolve(id: string): void {
    for (const resolve of this.waiters.get(id) ?? []) resolve();
    this.waiters.delete(id);
  }
}

function signed(body: Record<string, Json>, key: Buffer): Buffer {
  const mac = createHmac('sha256', key).update(canonicalJson(body)).digest('hex');
  const frame = Buffer.concat([canonicalJson({ body, mac }), Buffer.from('\n')]);
  if (frame.length > MAX_FRAME_BYTES) throw new ProtocolError('response-too-large');
  return frame;
}

function probeSocket(path: string): Promise<boolean> {
  return new Promise(resolve => {
    const client = createConnection(path);
    const timer = setTimeout(() => { client.destroy(); resolve(false); }, 250);
    client.once('connect', () => { clearTimeout(timer); client.destroy(); resolve(true); });
    client.once('error', () => { clearTimeout(timer); resolve(false); });
  });
}

class LocalServer {
  private server?: Server;
  private readonly nonces = new Map<string, number>();

  constructor(
    private readonly path: string,
    private readonly key: Buffer,
    private readonly coordinator: OwnershipCoordinator,
  ) {}

  async start(): Promise<void> {
    if (this.server?.listening) return;
    requirePrivateDirectory(dirname(this.path));
    if (existsSync(this.path)) {
      const info = lstatSync(this.path);
      if (!info.isSocket() || info.isSymbolicLink() || info.uid !== currentUid()) {
        throw new ProtocolError('untrusted-adapter-socket');
      }
      if (await probeSocket(this.path)) throw new ProtocolError('adapter-already-running');
      unlinkSync(this.path);
    }
    this.server = createServer(socket => this.handle(socket));
    try {
      await new Promise<void>((resolve, reject) => {
        this.server!.once('error', reject);
        this.server!.listen(this.path, () => resolve());
      });
      chmodSync(this.path, 0o600);
    } catch {
      try { this.server.close(); } catch { /* not listening */ }
      throw new ProtocolError('adapter-socket-unavailable');
    }
  }

  async close(): Promise<void> {
    if (this.server) await new Promise<void>(resolve => this.server!.close(() => resolve()));
    try {
      const info = lstatSync(this.path);
      if (info.isSocket() && info.uid === currentUid()) unlinkSync(this.path);
    } catch { /* already removed */ }
  }

  private handle(socket: Socket): void {
    socket.setTimeout(30_000, () => socket.destroy());
    let chunks: Buffer[] = [];
    let size = 0;
    let handled = false;
    socket.on('data', chunk => {
      if (handled) { socket.destroy(); return; }
      size += chunk.length;
      if (size > MAX_FRAME_BYTES) { handled = true; socket.destroy(); return; }
      chunks.push(Buffer.from(chunk));
      const aggregate = Buffer.concat(chunks, size);
      const newline = aggregate.indexOf(10);
      if (newline < 0) return;
      if (newline !== aggregate.length - 1) { handled = true; socket.destroy(); return; }
      handled = true; chunks = [];
      void this.respond(socket, aggregate);
    });
    socket.on('error', () => {});
  }

  private async respond(socket: Socket, raw: Buffer): Promise<void> {
    let nonce: string | undefined;
    try {
      const request = parseRequest(raw, this.key, this.nonces);
      nonce = request.request_nonce;
      const response = await this.coordinator.dispatch(request);
      socket.end(signed(response, this.key));
    } catch (error) {
      // Only an authenticated frame yields a signed, correlated error. Invalid
      // MAC/frame input gets a silent close and no local protocol oracle.
      if (!nonce) { socket.destroy(); return; }
      const code = error instanceof ProtocolError ? error.code : 'adapter-internal-error';
      socket.end(signed({
        schema_version: IPC_SCHEMA,
        event_type: 'error',
        request_nonce: nonce,
        error_code: /^[a-z0-9._-]{1,64}$/.test(code) ? code : 'adapter-internal-error',
      }, this.key));
    }
  }
}

export default function magistratePiOwnership(pi: ExtensionAPI): void {
  const enabled = (process.env.MAGISTRATE_PI_OWNERSHIP_ENABLED ?? '').trim().toLowerCase();
  if (['', '0', 'false', 'no', 'off'].includes(enabled)) return;
  if (!['1', 'true', 'yes', 'on'].includes(enabled)) {
    throw new ProtocolError('invalid-feature-flag');
  }

  const keyPath = configuredPath('MAGISTRATE_PI_IPC_KEY_PATH', 'pi-ownership.key');
  const socketPath = configuredPath('MAGISTRATE_PI_ADAPTER_SOCKET', 'pi-ownership.sock');
  const journalPath = configuredPath('MAGISTRATE_PI_ADAPTER_JOURNAL', 'pi-ownership.journal');
  const key = readKey(keyPath);
  const journal = new EncryptedJournal(journalPath, key);
  const coordinator = new OwnershipCoordinator(pi, journal);
  const server = new LocalServer(socketPath, key, coordinator);
  let adapterStarted = false;

  pi.on('session_start', async (_event, ctx) => {
    coordinator.setContext(ctx);
    await server.start();
    adapterStarted = true;
  });
  pi.on('session_before_switch', () => adapterStarted ? ({
    cancel: coordinator.hasOpenDispatch(),
  }) : undefined);
  pi.on('session_before_fork', () => adapterStarted ? ({
    cancel: coordinator.hasOpenDispatch(),
  }) : undefined);
  pi.on('session_before_tree', () => adapterStarted ? ({
    cancel: coordinator.hasOpenDispatch(),
  }) : undefined);
  pi.on('input', (event, ctx) => {
    if (!adapterStarted) return { action: 'continue' };
    coordinator.setContext(ctx);
    return coordinator.onInput(event);
  });
  pi.on('before_agent_start', (event, ctx) => {
    if (!adapterStarted) return;
    coordinator.setContext(ctx);
    coordinator.onBeforeAgentStart(event);
  });
  pi.on('turn_start', (_event, ctx) => {
    if (!adapterStarted) return;
    coordinator.setContext(ctx);
    coordinator.onTurnStart();
  });
  pi.on('turn_end', (event, ctx) => {
    if (!adapterStarted) return;
    coordinator.setContext(ctx);
    coordinator.onTurnEnd(event);
  });
  pi.on('agent_settled', (_event, ctx) => {
    if (!adapterStarted) return;
    coordinator.setContext(ctx);
    coordinator.onAgentSettled();
  });
  pi.on('session_shutdown', async () => {
    if (adapterStarted) await server.close();
    adapterStarted = false;
    journal.close();
    key.fill(0);
  });
}
