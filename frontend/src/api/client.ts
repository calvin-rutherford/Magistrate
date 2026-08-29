import AsyncStorage from '@react-native-async-storage/async-storage';
import { parseAgentHistory } from '../services/ChatHistory';

// Production builds must provide an HTTPS gateway (usually same-origin on web).
// HTTP localhost is intentionally limited to local development.
export const GATEWAY_URL = process.env.EXPO_PUBLIC_GATEWAY_URL || (
  typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}/api/v1` : 'http://localhost:8000/api/v1'
);

function assertGatewayTransport(): void {
  if (process.env.NODE_ENV === 'production') {
    const parsed = new URL(GATEWAY_URL);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !local) throw new Error('Production gateway configuration must use HTTPS.');
  }
}
assertGatewayTransport();
const SESSION_STORAGE_KEY = 'magistrate.gateway.session';
const rawFetch = (...args: Parameters<typeof fetch>) => fetch(...args);
let sessionToken: string | null = null;
let sessionPromise: Promise<string | null> | null = null;

export async function createGatewaySession(bootstrapSecret?: string): Promise<string> {
  const response = await rawFetch(`${GATEWAY_URL}/auth/session`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bootstrapSecret ? { bootstrap_secret: bootstrapSecret } : {})
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || typeof payload.session_token !== 'string') {
    throw new Error(payload.detail || `Session creation failed (${response.status})`);
  }
  sessionToken = payload.session_token as string;
  await AsyncStorage.setItem(SESSION_STORAGE_KEY, sessionToken);
  return sessionToken;
}

export async function clearGatewaySession(): Promise<void> {
  sessionToken = null;
  sessionPromise = null;
  await AsyncStorage.removeItem(SESSION_STORAGE_KEY).catch(() => {});
}

export async function getGatewaySessionToken(): Promise<string | null> {
  if (sessionToken) return sessionToken;
  if (!sessionPromise) {
    sessionPromise = AsyncStorage.getItem(SESSION_STORAGE_KEY).then(async stored => {
      if (stored) { sessionToken = stored; return stored; }
      // In development the server may explicitly opt into an auto-session. No
      // credential is embedded here; production requires an operator bootstrap.
      try { return await createGatewaySession(); } catch { return null; }
    }).finally(() => { sessionPromise = null; });
  }
  return sessionPromise;
}

export async function authorizedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const token = await getGatewaySessionToken();
  const headers = new Headers(init.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return rawFetch(input, { ...init, headers });
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
  notification_kind: 'captain_question' | 'pr_ready';
  url: string;
  revision?: string;
}

export async function fetchNotificationEvents(foreground: boolean): Promise<{ events: NotificationEvent[] }> {
  const hour = new Date().getHours();
  const res = await authorizedFetch(`${GATEWAY_URL}/notifications/events?foreground=${foreground}&local_hour=${hour}`, {
  });
  if (!res.ok) throw new Error(`Notification events failed: ${res.status}`);
  return res.json();
}

export async function acknowledgeNotificationEvents(itemIds: string[]): Promise<void> {
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/events/ack', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: itemIds })
  });
  if (!res.ok) throw new Error(`Notification acknowledgement failed: ${res.status}`);
}

export async function updateNotificationPreferences(enabled: boolean, quietHours: boolean): Promise<void> {
  const res = await authorizedFetch(GATEWAY_URL + '/notifications/preferences', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, quiet_start: quietHours ? 22 : null, quiet_end: quietHours ? 7 : null })
  });
  if (!res.ok) throw new Error(`Notification preferences failed: ${res.status}`);
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
  return res.json();
}

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/agents', {
  });
  return checkedJson<AgentInfo[]>(res);
}

export async function fetchFleet() {
  const res = await authorizedFetch(GATEWAY_URL + '/fleet', {
  });
  return res.json();
}

export async function fetchAttention(): Promise<AttentionItem[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/attention', {
  });
  return res.json();
}

export interface UnifiedAttentionRecord {
  id: string;
  provider: string;
  title: string;
  subtitle: string;
  priority?: string;
  status?: string;
  url: string;
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
  return res.json();
}

export async function updateUserProfile(profile: Partial<UserProfile>): Promise<UserProfile> {
  const formData = new FormData();
  Object.entries(profile).forEach(([key, value]) => {
    if (value != null) formData.append(key, String(value));
  });
  const res = await authorizedFetch(GATEWAY_URL + '/account/profile', {
    method: 'POST', body: formData
  });
  if (!res.ok) throw new Error(`Profile update failed: ${res.status}`);
  return res.json();
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
  return res.json();
}

export async function fetchAuthProviders(): Promise<AuthProviderInfo[]> {
  const res = await authorizedFetch(GATEWAY_URL + '/auth/providers', {
  });
  return res.json();
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
  const res = await authorizedFetch(GATEWAY_URL + '/auth/' + provider + '/disconnect', {
    method: 'POST',
  });
  return res.json();
}

async function checkedJson<T>(res: Response): Promise<T> {
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    throw new Error(res.ok ? 'Gateway returned an invalid response.' : `Request failed (${res.status})`);
  }
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.error === 'string' ? data.error : null;
    throw new Error(detail || `Request failed (${res.status})`);
  }
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

async function requireOk(res: Response): Promise<any> {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.detail || payload.error || `Gateway request failed (${res.status})`);
  return payload;
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
  return requireOk(res);
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
  return requireOk(res);
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

export async function sendCaptainPrompt(text: string, source: string = 'iphone', target: string = 'captain', harness?: string, model?: string, profileId?: string | null) {
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
      ...(profileId !== undefined ? { profile_id: profileId, ...(harness && model ? { harness, model } : {}) } : harness && model ? { harness, model } : {})
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
