import { useSyncExternalStore } from 'react';
import { Platform } from 'react-native';
import {
  clearGatewaySessionPayload,
  getGatewaySessionPayload,
  removeLegacyGatewaySessionPayload,
  setGatewaySessionPayload,
} from '../services/GatewaySessionStorage';
import { parseAgentHistory } from '../services/ChatHistory';
import { VoiceInputCapabilities, VoiceInputMode } from '../services/VoiceInputModes';
import { OperatingPermissionMode } from '../services/OperatingPermissionModes';

// Production builds must provide an HTTPS gateway (usually same-origin on web).
// HTTP localhost is intentionally limited to local development.
const configuredGatewayUrl = process.env.EXPO_PUBLIC_GATEWAY_URL?.trim();

// A native release must be pointed at the public TLS gateway at build time.
// The localhost default is deliberately limited to local development and web;
// private runner addresses and credentials are never valid app configuration.
export const GATEWAY_URL = configuredGatewayUrl || (
  typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}/api/v1` : 'http://localhost:8000/api/v1'
);

function assertGatewayTransport(): void {
  if (process.env.NODE_ENV !== 'production') return;
  if (Platform.OS !== 'web' && !configuredGatewayUrl) {
    throw new Error('Native production builds must configure EXPO_PUBLIC_GATEWAY_URL.');
  }
  const parsed = new URL(GATEWAY_URL);
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
  if (parsed.protocol !== 'https:' && !local) throw new Error('Production gateway configuration must use HTTPS.');
  if (Platform.OS !== 'web' && local) throw new Error('Native production builds cannot use a localhost gateway.');
}
assertGatewayTransport();

export type GatewaySessionStatus = 'checking' | 'authentication-required' | 'authenticated';
export interface GatewaySession {
  token: string;
  expiresAt: number;
  scopes: string[];
  userId?: string;
}
export interface GatewaySessionSnapshot {
  status: GatewaySessionStatus;
  session: GatewaySession | null;
  error: string | null;
}

export class GatewayAuthError extends Error {
  constructor(message = 'Authentication required') { super(message); this.name = 'GatewayAuthError'; }
}
export class GatewayNetworkError extends Error {
  constructor(message = 'Gateway is unavailable. Check the connection and try again.') { super(message); this.name = 'GatewayNetworkError'; }
}

const rawFetch = (...args: Parameters<typeof fetch>) => fetch(...args);
let sessionToken: string | null = null;
let sessionInfo: GatewaySession | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | null = null;
let restorePromise: Promise<GatewaySession | null> | null = null;
let invalidationPromise: Promise<void> | null = null;
let sessionSnapshot: GatewaySessionSnapshot = { status: 'checking', session: null, error: null };
const sessionListeners = new Set<() => void>();

function publish(snapshot: GatewaySessionSnapshot): void {
  sessionSnapshot = snapshot;
  sessionListeners.forEach(listener => listener());
}

export function subscribeGatewaySession(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

export function useGatewaySession(): GatewaySessionSnapshot {
  return useSyncExternalStore(subscribeGatewaySession, () => sessionSnapshot, () => sessionSnapshot);
}

function sessionFromPayload(payload: unknown): GatewaySession | null {
  if (!payload || typeof payload !== 'object') return null;
  const value = payload as Record<string, unknown>;
  const token = typeof value.session_token === 'string' ? value.session_token : typeof value.token === 'string' ? value.token : null;
  const expiresAt = typeof value.expires_at === 'number' ? value.expires_at : null;
  if (!token || !token.trim() || expiresAt === null || !Number.isSafeInteger(expiresAt) || expiresAt <= Math.floor(Date.now() / 1000)) return null;
  const scopes = Array.isArray(value.scopes) ? value.scopes.filter((scope): scope is string => typeof scope === 'string') : [];
  return { token, expiresAt, scopes, userId: typeof value.user_id === 'string' ? value.user_id : undefined };
}

function storedSessionPayload(session: GatewaySession): Record<string, unknown> {
  return { token: session.token, expires_at: session.expiresAt, scopes: session.scopes, user_id: session.userId };
}

function clearExpiryTimer(): void {
  if (expiryTimer) clearTimeout(expiryTimer);
  expiryTimer = null;
}

function scheduleExpiry(session: GatewaySession): void {
  clearExpiryTimer();
  const delay = session.expiresAt * 1000 - Date.now();
  if (delay <= 0) { void invalidateGatewaySession('Your session has expired.'); return; }
  // Browsers clamp delays above the signed 32-bit timer limit. Re-arm for
  // distant test/development expiries instead of allowing an immediate wrap.
  expiryTimer = setTimeout(() => {
    if (sessionInfo?.token === session.token && sessionInfo.expiresAt === session.expiresAt) scheduleExpiry(session);
  }, Math.min(delay, 2_147_000_000));
}

async function persistSession(session: GatewaySession): Promise<void> {
  sessionToken = session.token;
  sessionInfo = session;
  await setGatewaySessionPayload(JSON.stringify(storedSessionPayload(session)));
  scheduleExpiry(session);
}

function setSessionCandidate(session: GatewaySession): void {
  sessionToken = session.token;
  sessionInfo = session;
  clearExpiryTimer();
  publish({ status: 'checking', session, error: null });
}

function responseDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const value = payload as Record<string, unknown>;
  for (const key of ['detail', 'error', 'message']) {
    if (typeof value[key] === 'string' && value[key].trim()) return value[key].trim();
  }
  return null;
}

async function readResponsePayload(response: Response): Promise<unknown> {
  const text = await response.text().catch(() => '');
  if (!text.trim()) return null;
  try { return JSON.parse(text); } catch { return text; }
}

function responseError(response: Response, payload: unknown): Error {
  return new Error(responseDetail(payload) || `Request failed (${response.status})`);
}

async function fetchRaw(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  try { return await rawFetch(input, init); }
  catch { throw new GatewayNetworkError(); }
}

export async function createGatewaySession(bootstrapSecret?: string): Promise<GatewaySession> {
  const response = await fetchRaw(`${GATEWAY_URL}/auth/session`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bootstrapSecret ? { bootstrap_secret: bootstrapSecret } : {})
  });
  const payload = await readResponsePayload(response);
  if (!response.ok) throw responseError(response, payload);
  const session = sessionFromPayload(payload);
  if (!session) throw new Error('Gateway returned an invalid session.');
  await persistSession(session);
  setSessionCandidate(session);
  return session;
}

export async function validateGatewaySession(): Promise<GatewaySession> {
  const session = sessionInfo || (sessionToken ? { token: sessionToken, expiresAt: 0, scopes: [] } : null);
  if (!session || (session.expiresAt > 0 && session.expiresAt * 1000 <= Date.now())) {
    await invalidateGatewaySession('Your session has expired.');
    throw new GatewayAuthError('Authentication required');
  }
  const response = await fetchRaw(`${GATEWAY_URL}/auth/session`, { headers: { Authorization: `Bearer ${session.token}` } });
  const payload = await readResponsePayload(response);
  if (response.status === 401) {
    await invalidateGatewaySession('Your session is no longer valid.');
    throw new GatewayAuthError('Your session is no longer valid.');
  }
  if (!response.ok) throw responseError(response, payload);
  const serverSession = sessionFromPayload(payload);
  const value = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  if (value.authenticated !== true || typeof value.expires_at !== 'number' || !Number.isSafeInteger(value.expires_at) || value.expires_at <= Math.floor(Date.now() / 1000)) throw new Error('Gateway returned an invalid session validation response.');
  const validated = { ...session, expiresAt: value.expires_at, scopes: Array.isArray(value.scopes) ? value.scopes.filter((scope): scope is string => typeof scope === 'string') : session.scopes, userId: typeof value.user_id === 'string' ? value.user_id : session.userId };
  if (serverSession?.token) validated.token = serverSession.token;
  sessionToken = validated.token;
  sessionInfo = validated;
  await setGatewaySessionPayload(JSON.stringify(storedSessionPayload(validated))).catch(() => {});
  scheduleExpiry(validated);
  publish({ status: 'authenticated', session: validated, error: null });
  return validated;
}

export async function restoreGatewaySession(): Promise<GatewaySession | null> {
  if (sessionSnapshot.status === 'authenticated' && sessionInfo) return sessionInfo;
  if (restorePromise) return restorePromise;
  restorePromise = (async () => {
    publish({ status: 'checking', session: sessionInfo, error: null });
    let stored: string | null = null;
    try {
      // Never migrate the old AsyncStorage bearer into a trusted session. The
      // native store owns all future session restores.
      await removeLegacyGatewaySessionPayload();
      stored = await getGatewaySessionPayload();
    }
    catch { publish({ status: 'authentication-required', session: null, error: 'Saved secure session storage is unavailable.' }); return null; }
    let candidate: GatewaySession | null = null;
    if (stored) {
      try {
        const parsed = JSON.parse(stored) as Record<string, unknown>;
        candidate = sessionFromPayload({ session_token: parsed.token, expires_at: parsed.expires_at, scopes: parsed.scopes, user_id: parsed.user_id });
      } catch { candidate = null; }
    }
    if (candidate && candidate.expiresAt * 1000 <= Date.now()) {
      await invalidateGatewaySession('Your saved session has expired.');
      candidate = null;
    }
    if (!candidate && process.env.NODE_ENV !== 'production') {
      // Development may explicitly opt into server-side auto-session. A
      // production bundle never probes issuance without the operator secret.
      try { await createGatewaySession(); candidate = sessionInfo; }
      catch { /* The normal production path is the explicit bootstrap form. */ }
    }
    // A user can submit the bootstrap form while the optional development
    // auto-session probe is still settling. Prefer the newer manual candidate
    // rather than letting that stale restore attempt reopen the gate.
    if (!candidate && sessionInfo) candidate = sessionInfo;
    if (!candidate) {
      publish({ status: 'authentication-required', session: null, error: null });
      return null;
    }
    if (stored) setSessionCandidate(candidate);
    try { return await validateGatewaySession(); }
    catch (error) {
      if (!(error instanceof GatewayAuthError)) publish({ status: 'authentication-required', session: candidate, error: error instanceof Error ? error.message : 'Gateway session could not be validated.' });
      return null;
    }
  })().finally(() => { restorePromise = null; });
  return restorePromise;
}

export async function invalidateGatewaySession(message = 'Authentication required'): Promise<void> {
  if (invalidationPromise) return invalidationPromise;
  invalidationPromise = (async () => {
    sessionToken = null;
    sessionInfo = null;
    clearExpiryTimer();
    await clearGatewaySessionPayload().catch(() => {});
    publish({ status: 'authentication-required', session: null, error: message });
  })().finally(() => { invalidationPromise = null; });
  return invalidationPromise;
}

export async function logoutGatewaySession(): Promise<void> {
  const token = sessionToken;
  if (token) {
    try {
      // Revoke the device's server-side delivery registration before ending
      // the session; a signed-out device must not keep receiving attention.
      await fetchRaw(`${GATEWAY_URL}/notifications/register`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
    } catch { /* Best effort while offline. The next authenticated session can re-register. */ }
    try {
      await fetchRaw(`${GATEWAY_URL}/auth/session/revoke`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
    } catch { /* Local logout must complete even if the gateway is unavailable. */ }
  }
  await invalidateGatewaySession('You have been signed out.');
}

export async function clearGatewaySession(): Promise<void> {
  await invalidateGatewaySession('Authentication required');
}

export async function getGatewaySessionToken(): Promise<string | null> {
  if (!sessionToken || sessionSnapshot.status !== 'authenticated') return null;
  if (sessionInfo && sessionInfo.expiresAt * 1000 <= Date.now()) {
    await invalidateGatewaySession('Your session has expired.');
    return null;
  }
  return sessionToken;
}

export async function authorizedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const token = await getGatewaySessionToken();
  if (!token) throw new GatewayAuthError();
  const headers = new Headers(init.headers || {});
  headers.set('Authorization', `Bearer ${token}`);
  const response = await fetchRaw(input, { ...init, headers });
  if (response.status === 401) {
    await invalidateGatewaySession('Your session is no longer valid.');
    throw new GatewayAuthError('Your session is no longer valid.');
  }
  return response;
}

export interface AgentInfo {
  id: string;
  name: string;
  harness?: string | null;
  status?: 'idle' | 'working' | 'blocked' | 'done' | 'unknown' | string | null;
  pane_id?: string;
  tab_id?: string;
  workspace_id?: string;
}

export interface AgentControlResult {
  status: string;
  target?: string;
  key?: string;
  response?: string;
  error?: string | null;
}

export interface AgentHistoryMessage {
  id?: string;
  role: 'user' | 'assistant';
  kind: 'conversation' | 'tool';
  text: string;
}

export interface AgentHistoryResult {
  target: string;
  messages: AgentHistoryMessage[];
  next_before?: string | null;
  next_after?: string | null;
  has_more_before?: boolean;
  has_more_after?: boolean;
}

export interface UsageWindow {
  id?: string;
  label?: string;
  kind?: string;
  resetsAt?: string;
  percentRemaining?: number;
  spentUsd?: number;
  limitUsd?: number;
}

export interface UsageProvider {
  provider: string;
  plan: string | null;
  status: string;
  stale: boolean | null;
  windows: UsageWindow[];
  error?: string;
}

export interface UsageSummary {
  generated_at: string | null;
  schema_version: number | null;
  providers: UsageProvider[];
  source: 'quota-axi' | string;
}

export interface HealthInfo {
  status: string;
  service: string;
  herdr_socket_connected: boolean;
  herdr_version?: string;
  firstmate_tasks_count?: number;
}

export interface ExecutionModel {
  id: string;
  label: string;
  provider?: string;
  variant?: string;
  profile_id?: string;
  available?: boolean;
  availability?: 'available' | 'unavailable' | string;
  auth?: { required: boolean; credential_key: string; status: string };
}

export interface ExecutionHarness {
  id: string;
  label: string;
  verified: boolean;
  models: ExecutionModel[];
}

export interface ExecutionProfile {
  id: string;
  variant: string;
  label: string;
  harness: { id: string; label: string };
  provider: { id: string; label: string };
  model: { id: string; label: string };
  verified: boolean;
  available: boolean;
  availability: 'available' | 'unavailable' | string;
  availability_reason?: string | null;
  auth: { required: boolean; credential_key: string; status: string };
}

export interface ExecutionCapabilities {
  harnesses: ExecutionHarness[];
  profiles?: ExecutionProfile[];
  source: string;
  configured: boolean;
  routing?: { selection_supported: boolean; migration_supported: boolean; mode: string };
}

export interface ExecutionSettings {
  profile_id: string | null;
  switching_behavior: 'migrate' | 'new-session';
  unavailable_behavior: 'error' | 'fallback';
  migration_supported: boolean;
  credential_storage?: string;
  credentials?: Array<{ credential_key: string; configured: boolean }>;
}

export interface TaskInfo {
  id: string;
  name?: string;
  title?: string;
  project?: string;
  status?: string;
  worktree?: string;
  branch?: string;
  pr?: string;
  agent?: string;
  summary?: string;
}

export interface AttentionItem {
  id: string;
  title: string;
  subtitle: string;
  type: string;
  status: 'blocked' | 'ready';
  target_id?: string;
  project: string;
}

export interface NotificationEvent extends AttentionItem {
  notification_kind: 'captain_question' | 'pr_ready' | 'blocker' | 'stall' | 'failure' | 'milestone' | 'completion' | 'consequential_decision';
  url: string;
  deep_link?: string | null;
  revision?: string;
}

export interface NotificationPreferences {
  enabled: boolean;
  quiet_start: number | null;
  quiet_end: number | null;
  mode: OperatingPermissionMode;
}

export async function fetchNotificationEvents(foreground: boolean): Promise<{ events: NotificationEvent[]; delivery?: string }> {
  const hour = new Date().getHours();
  const res = await authorizedFetch(`${GATEWAY_URL}/notifications/events?foreground=${foreground}&local_hour=${hour}`, {
  });
  return checkedJson<{ events: NotificationEvent[] }>(res);
}

export async function acknowledgeNotificationEvents(itemIds: string[]): Promise<void> {
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/events/ack', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: itemIds })
  });
  await checkedJson(res);
}

export async function fetchNotificationPreferences(): Promise<NotificationPreferences> {
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/preferences');
  return checkedJson<NotificationPreferences>(res);
}

export async function updateNotificationPreferences(enabled: boolean, quietHours: boolean, mode: OperatingPermissionMode = 'moderate'): Promise<NotificationPreferences> {
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/preferences', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, mode, quiet_start: quietHours ? 22 : null, quiet_end: quietHours ? 7 : null })
  });
  return checkedJson<NotificationPreferences>(res);
}

export async function registerNativePushToken(pushToken: string, platform: 'ios' | 'android' | 'native'): Promise<{ status: string; platform: string }> {
  const formData = new FormData();
  formData.append('push_token', pushToken);
  formData.append('platform', platform);
  formData.append('timezone_offset_minutes', String(new Date().getTimezoneOffset()));
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/register', { method: 'POST', body: formData });
  return checkedJson<{ status: string; platform: string }>(res);
}

export async function revokeNativePushToken(): Promise<void> {
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/register', { method: 'DELETE' });
  await checkedJson(res);
}

export interface UserProfile {
  user_id: string;
  name: string;
  email: string;
  avatar_url: string;
  bio: string;
  active_theme?: string;
}

export interface AuthProviderInfo {
  provider: string;
  status: 'connected' | 'disconnected' | 'expired';
  username: string;
  capabilities: string[];
  auth_url: string | null;
  available?: boolean;
  configuration?: 'available' | 'unavailable' | string;
}

export interface GitHubPR {
  id: number;
  number: number;
  title: string;
  repository: string;
  author: string;
  branch: string | null;
  state: string;
  is_draft: boolean;
  review_status: string;
  checks: { status: string; passed: number; failed: number; pending: number; summary: string };
  mergeable: string;
  summary: string;
  body: string;
  reviews: Array<{ author: string; state: string; submitted_at?: string }>;
  created_at: string | null;
  updated_at: string | null;
  merged_at: string | null;
  requires_attention: boolean;
  url: string;
}

export interface GitHubPRPage {
  items: GitHubPR[];
  page: number;
  per_page: number;
  has_more: boolean;
  cached: boolean;
}

export interface RecentActivityItem {
  id: string;
  type: 'pull_request_merged' | 'task_completed' | 'task_requested';
  title: string;
  description: string;
  occurred_at: string;
  source: 'firstmate' | 'github';
  project: string;
  url: string | null;
  pull_request_number: number | null;
}

export interface RecentActivityFeed {
  items: RecentActivityItem[];
  sources: { firstmate: 'available' | 'unavailable'; github: 'available' | 'unavailable' };
}

export async function fetchHealth() {
  const res = await authorizedFetch(GATEWAY_URL + '/health', {
  });
  return checkedJson<HealthInfo>(res);
}

export async function fetchRuntime() {
  const res = await authorizedFetch(GATEWAY_URL + '/runtime', {
  });
  return checkedJson(res);
}

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/agents', {
  });
  return checkedJson<AgentInfo[]>(res);
}

export async function fetchFleet() {
  const res = await authorizedFetch(GATEWAY_URL + '/fleet', {
  });
  return checkedJson(res);
}

export async function fetchAttention(): Promise<AttentionItem[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/attention', {
  });
  return checkedJson<AttentionItem[]>(res);
}

export interface UnifiedAttentionRecord {
  id: string;
  provider: string;
  title: string;
  subtitle: string;
  priority?: string;
  status?: string;
  url: string;
  deep_link?: string | null;
  target_id?: string;
  requires_action?: boolean;
  external_url?: string;
}

export async function fetchUnifiedAttention(): Promise<UnifiedAttentionRecord[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/attention/unified', {
  });
  const data = await checkedJson<unknown>(res);
  if (!Array.isArray(data)) throw new Error('Gateway returned invalid attention data.');
  return data as UnifiedAttentionRecord[];
}

export async function fetchRecentActivity(limit = 20): Promise<RecentActivityFeed> {
  const res = await authorizedFetch(`${GATEWAY_URL}/recent-activity?limit=${limit}`, {
  });
  const data = await checkedJson<RecentActivityFeed>(res);
  if (!data || !Array.isArray(data.items)) throw new Error('Gateway returned invalid recent activity data.');
  return data;
}

export async function fetchUserProfile(): Promise<UserProfile> {
  const res = await authorizedFetch(GATEWAY_URL + '/account/profile', {
  });
  return checkedJson<UserProfile>(res);
}

export async function updateUserProfile(profile: Partial<UserProfile>): Promise<UserProfile> {
  const formData = new FormData();
  Object.entries(profile).forEach(([key, value]) => {
    if (value != null) formData.append(key, String(value));
  });
  const res = await authorizedFetch(GATEWAY_URL + '/account/profile', {
    method: 'POST', body: formData
  });
  return checkedJson<UserProfile>(res);
}

export interface ChatUpload {
  upload_id: string;
  filename: string;
  media_type: string;
  size: number;
}

export async function uploadChatFile(uri: string, filename: string, mimeType: string = 'application/octet-stream'): Promise<ChatUpload> {
  const formData = new FormData();
  if (typeof window !== 'undefined') {
    const response = await rawFetch(uri);
    formData.append('files', await response.blob(), filename);
  } else {
    formData.append('files', { uri, name: filename, type: mimeType } as any);
  }
  const res = await authorizedFetch(GATEWAY_URL + '/uploads', { method: 'POST', body: formData });
  const data = await checkedJson<{ uploads?: ChatUpload[] }>(res);
  if (!data.uploads?.length) throw new Error('Gateway returned no upload record.');
  return data.uploads[0];
}

export async function uploadUserAvatar(imageUri: string, mimeType: string = 'image/jpeg'): Promise<any> {
  const formData = new FormData();
  if (typeof window !== 'undefined' && imageUri.startsWith('data:')) {
    const res = await rawFetch(imageUri);
    const blob = await res.blob();
    formData.append('file', blob, 'avatar.jpg');
  } else {
    formData.append('file', {
      uri: imageUri,
      name: 'avatar.jpg',
      type: mimeType
    } as any);
  }

  const res = await authorizedFetch(GATEWAY_URL + '/account/avatar', {
    method: 'POST',
    headers: {
    },
    body: formData
  });
  return checkedJson(res);
}

export async function fetchAuthProviders(): Promise<AuthProviderInfo[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/auth/providers', {
  });
  return checkedJson<AuthProviderInfo[]>(res);
}

export async function fetchUsage(): Promise<UsageSummary> {
  const res = await authorizedFetch(GATEWAY_URL + '/usage', {
  });
  const data = await checkedJson<UsageSummary>(res);
  if (!data || !Array.isArray(data.providers)) throw new Error('Gateway returned invalid usage data.');
  return data;
}

export async function fetchExecutionCapabilities(): Promise<ExecutionCapabilities> {
  const res = await authorizedFetch(GATEWAY_URL + '/execution/capabilities', {
  });
  const data = await checkedJson<unknown>(res);
  if (!data || typeof data !== 'object' || !Array.isArray((data as any).harnesses) || ('profiles' in data && !Array.isArray((data as any).profiles))) throw new Error('Gateway returned invalid execution capabilities.');
  return data as ExecutionCapabilities;
}

export async function fetchExecutionSettings(): Promise<ExecutionSettings> {
  const res = await authorizedFetch(GATEWAY_URL + '/execution/settings', {
  });
  const data = await checkedJson<unknown>(res);
  if (!data || typeof data !== 'object' || !['migrate', 'new-session'].includes((data as any).switching_behavior) || !['error', 'fallback'].includes((data as any).unavailable_behavior) || !('profile_id' in data)) throw new Error('Gateway returned invalid execution settings.');
  return data as ExecutionSettings;
}

export async function updateExecutionSettings(update: Partial<Pick<ExecutionSettings, 'profile_id' | 'switching_behavior' | 'unavailable_behavior'>>): Promise<ExecutionSettings> {
  const res = await authorizedFetch(GATEWAY_URL + '/execution/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update)
  });
  return checkedJson<ExecutionSettings>(res);
}

export async function saveExecutionCredential(credentialKey: string, credential: string): Promise<{ credential_key: string; configured: boolean }> {
  const res = await authorizedFetch(GATEWAY_URL + '/execution/credentials/' + encodeURIComponent(credentialKey), {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ credential })
  });
  return checkedJson<{ credential_key: string; configured: boolean }>(res);
}

export async function connectAuthProvider(provider: string, redirectUri: string): Promise<{ auth_url: string; provider: string; expires_in: number }> {
  const params = new URLSearchParams({ redirect_uri: redirectUri });
  const res = await authorizedFetch(GATEWAY_URL + '/auth/' + encodeURIComponent(provider) + '/connect?' + params.toString());
  return checkedJson(res);
}

export async function disconnectAuthProvider(provider: string): Promise<any> {
  const res = await authorizedFetch(GATEWAY_URL + '/auth/' + encodeURIComponent(provider) + '/disconnect', {
    method: 'POST',
  });
  return checkedJson(res);
}

async function checkedJson<T>(res: Response): Promise<T> {
  const data = await readResponsePayload(res);
  if (!res.ok) throw responseError(res, data);
  if (data === null) throw new Error('Gateway returned an invalid response.');
  return data as T;
}

export async function fetchGitHubPRs(page = 1, refresh = false): Promise<GitHubPRPage> {
  const res = await authorizedFetch(GATEWAY_URL + `/github/pulls?page=${page}&per_page=20&refresh=${refresh}`, {
  });
  const data = await checkedJson<Partial<GitHubPRPage>>(res);
  if (!Array.isArray(data.items)) throw new Error('Gateway returned invalid pull request data.');
  return data as GitHubPRPage;
}

export async function fetchGitHubPR(number: number, refresh = false): Promise<GitHubPR> {
  const res = await authorizedFetch(GATEWAY_URL + `/github/pulls/${number}?refresh=${refresh}`, {
  });
  return checkedJson<GitHubPR>(res);
}

async function requireOk<T>(res: Response): Promise<T> {
  return checkedJson<T>(res);
}

export async function fetchVoiceInputCapabilities(): Promise<VoiceInputCapabilities> {
  const res = await authorizedFetch(GATEWAY_URL + '/voice/capabilities');
  const data = await checkedJson<{ modes?: Array<{ id: VoiceInputMode; label: string; available: boolean; reason?: string }>; provider?: string; configured?: boolean }>(res);
  if (!Array.isArray(data.modes)) throw new Error('Gateway returned invalid voice capabilities.');
  return {
    modes: data.modes.map(mode => ({ id: mode.id, label: mode.label, available: mode.available ? 'available' : 'unavailable', reason: mode.reason })),
    serverProvider: data.provider,
    serverConfigured: data.configured,
  };
}

export async function transcribeVoiceAudio(audioUri: string, mimeType: string, filename: string): Promise<{ text: string; is_final: boolean }> {
  const formData = new FormData();
  if (typeof window !== 'undefined') {
    const audioResponse = await rawFetch(audioUri);
    formData.append('file', await audioResponse.blob(), filename);
  } else {
    formData.append('file', { uri: audioUri, name: filename, type: mimeType } as any);
  }
  const res = await authorizedFetch(GATEWAY_URL + '/voice/transcribe', {
    method: 'POST',
    headers: {
    },
    body: formData
  });
  return requireOk<{ text: string; is_final: boolean }>(res);
}

export interface VoiceMoveResult {
  move_id: string;
  status: 'ready' | 'confirmation_required' | 'confirmation_expired' | 'prohibited' | 'completed' | 'error';
  target: string; intent: string; impact: string; requires_confirmation: boolean;
  confirmation_token?: string; confirmation_message?: string; response?: string; error?: string;
}

export async function submitVoiceMove(utterance: string, target: string, idempotencyKey: string,
  execute = false, confirmationToken?: string): Promise<VoiceMoveResult> {
  const res = await authorizedFetch(GATEWAY_URL + '/voice/moves', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ schema_version: 'voice-move.v1', utterance, target, source: 'voice-page',
      idempotency_key: idempotencyKey, execute, confirmation_token: confirmationToken })
  });
  return requireOk<VoiceMoveResult>(res);
}

// Herdr exposes its read count as uint32 and bounds retained history separately
// through advanced.scrollback_limit_bytes; this is the theoretical max the gateway allows.
export const HERDR_MAX_READ_LINES = 0xFFFFFFFF;

// Chat only needs enough recent scrollback to seed live-poll deduplication
// (see ChatCanvas), not the full retained history - keep this small.
export const CHAT_HISTORY_LINES = 400;

export async function fetchCaptainOutput(lines: number = CHAT_HISTORY_LINES) {
  const res = await authorizedFetch(GATEWAY_URL + '/captain/output?lines=' + lines, {
  });
  return checkedJson<{ output?: string }>(res);
}

export async function fetchAgentHistory(agentId: string, lines: number = CHAT_HISTORY_LINES, cursor?: { before?: string; after?: string }): Promise<AgentHistoryResult> {
  const params = new URLSearchParams({ lines: String(lines) });
  if (cursor?.before) params.set('before', cursor.before);
  if (cursor?.after) params.set('after', cursor.after);
  const res = await authorizedFetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/history?' + params.toString(), {
  });
  // The live gateway may be one deploy behind the app. Captain output has the
  // same terminal snapshot and keeps new message rendering clean during that
  // rolling upgrade instead of exposing the prompt acknowledgement JSON.
  if (res.status === 404 && agentId === 'captain') {
    const fallback = await fetchCaptainOutput(lines);
    return { target: agentId, messages: parseAgentHistory(fallback.output || '') };
  }
  const data = await checkedJson<AgentHistoryResult>(res);
  if (!Array.isArray(data.messages)) throw new Error('Gateway returned invalid agent history.');
  return data;
}

export async function sendCaptainPrompt(text: string, source: string = 'iphone', target: string = 'captain', harness?: string, model?: string, profileId?: string | null, attachments?: ChatUpload[]) {
  const res = await authorizedFetch(GATEWAY_URL + '/captain/prompt', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      source,
      modality: 'text',
      type: 'prompt',
      text,
      target,
      ...(profileId !== undefined ? { profile_id: profileId, ...(harness && model ? { harness, model } : {}) } : harness && model ? { harness, model } : {}),
      ...(attachments?.length ? { attachments } : {})
    })
  });
  return checkedJson<{ status: string; target?: string; response?: string; error?: string }>(res);
}

export async function interruptAgent(agentId: string): Promise<AgentControlResult> {
  const res = await authorizedFetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/interrupt', {
    method: 'POST',
  });
  return checkedJson<AgentControlResult>(res);
}

export async function renameAgent(agentId: string, name: string): Promise<AgentControlResult & { name?: string }> {
  const res = await authorizedFetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/rename', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  return checkedJson<AgentControlResult & { name?: string }>(res);
}

export async function sendAgentKey(agentId: string = 'captain', key: string = 'Enter'): Promise<AgentControlResult> {
  const res = await authorizedFetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/send-key?key=' + encodeURIComponent(key), {
    method: 'POST',
  });
  return checkedJson<AgentControlResult>(res);
}
