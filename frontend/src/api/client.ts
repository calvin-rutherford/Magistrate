import { parseAgentHistory } from '../services/ChatHistory';

const GATEWAY_URL = 'http://100.84.181.23:8000/api/v1';
const DEVICE_TOKEN = 'magistrate-device-token-12345';

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
  role: 'user' | 'assistant';
  kind: 'conversation' | 'tool';
  text: string;
}

export interface AgentHistoryResult {
  target: string;
  messages: AgentHistoryMessage[];
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
}

export interface ExecutionHarness {
  id: string;
  label: string;
  verified: boolean;
  models: ExecutionModel[];
}

export interface ExecutionCapabilities {
  harnesses: ExecutionHarness[];
  source: string;
  configured: boolean;
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
  const res = await fetch(`${GATEWAY_URL}/notifications/events?foreground=${foreground}&local_hour=${hour}`, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  if (!res.ok) throw new Error(`Notification events failed: ${res.status}`);
  return res.json();
}

export async function acknowledgeNotificationEvents(itemIds: string[]): Promise<void> {
  const res = await fetch(GATEWAY_URL + '/notifications/events/ack', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Magistrate-Token': DEVICE_TOKEN },
    body: JSON.stringify({ item_ids: itemIds })
  });
  if (!res.ok) throw new Error(`Notification acknowledgement failed: ${res.status}`);
}

export async function updateNotificationPreferences(enabled: boolean, quietHours: boolean): Promise<void> {
  const res = await fetch(GATEWAY_URL + '/notifications/preferences', {
    method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Magistrate-Token': DEVICE_TOKEN },
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
  auth_url: string;
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
  const res = await fetch(GATEWAY_URL + '/health', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return checkedJson<HealthInfo>(res);
}

export async function fetchRuntime() {
  const res = await fetch(GATEWAY_URL + '/runtime', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await fetch(GATEWAY_URL + '/agents', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return checkedJson<AgentInfo[]>(res);
}

export async function fetchFleet() {
  const res = await fetch(GATEWAY_URL + '/fleet', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function fetchAttention(): Promise<AttentionItem[]> {
  const res = await fetch(GATEWAY_URL + '/attention', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
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
  const res = await fetch(GATEWAY_URL + '/attention/unified', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  const data = await checkedJson<unknown>(res);
  if (!Array.isArray(data)) throw new Error('Gateway returned invalid attention data.');
  return data as UnifiedAttentionRecord[];
}

export async function fetchRecentActivity(limit = 20): Promise<RecentActivityFeed> {
  const res = await fetch(`${GATEWAY_URL}/recent-activity?limit=${limit}`, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  const data = await checkedJson<RecentActivityFeed>(res);
  if (!data || !Array.isArray(data.items)) throw new Error('Gateway returned invalid recent activity data.');
  return data;
}

export async function fetchUserProfile(): Promise<UserProfile> {
  const res = await fetch(GATEWAY_URL + '/account/profile', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function updateUserProfile(profile: Partial<UserProfile>): Promise<UserProfile> {
  const formData = new FormData();
  Object.entries(profile).forEach(([key, value]) => {
    if (value != null) formData.append(key, String(value));
  });
  const res = await fetch(GATEWAY_URL + '/account/profile', {
    method: 'POST', headers: { 'X-Magistrate-Token': DEVICE_TOKEN }, body: formData
  });
  if (!res.ok) throw new Error(`Profile update failed: ${res.status}`);
  return res.json();
}

export async function uploadUserAvatar(imageUri: string, mimeType: string = 'image/jpeg'): Promise<any> {
  const formData = new FormData();
  if (typeof window !== 'undefined' && imageUri.startsWith('data:')) {
    const res = await fetch(imageUri);
    const blob = await res.blob();
    formData.append('file', blob, 'avatar.jpg');
  } else {
    formData.append('file', {
      uri: imageUri,
      name: 'avatar.jpg',
      type: mimeType
    } as any);
  }

  const res = await fetch(GATEWAY_URL + '/account/avatar', {
    method: 'POST',
    headers: {
      'X-Magistrate-Token': DEVICE_TOKEN
    },
    body: formData
  });
  return res.json();
}

export async function fetchAuthProviders(): Promise<AuthProviderInfo[]> {
  const res = await fetch(GATEWAY_URL + '/auth/providers', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function fetchExecutionCapabilities(): Promise<ExecutionCapabilities> {
  const res = await fetch(GATEWAY_URL + '/execution/capabilities', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return checkedJson<ExecutionCapabilities>(res);
}

export async function connectAuthProvider(provider: string): Promise<any> {
  const res = await fetch(GATEWAY_URL + '/auth/' + provider + '/connect', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function disconnectAuthProvider(provider: string): Promise<any> {
  const res = await fetch(GATEWAY_URL + '/auth/' + provider + '/disconnect', {
    method: 'POST',
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
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
  const res = await fetch(GATEWAY_URL + `/github/pulls?page=${page}&per_page=20&refresh=${refresh}`, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  const data = await checkedJson<Partial<GitHubPRPage>>(res);
  if (!Array.isArray(data.items)) throw new Error('Gateway returned invalid pull request data.');
  return data as GitHubPRPage;
}

export async function fetchGitHubPR(number: number, refresh = false): Promise<GitHubPR> {
  const res = await fetch(GATEWAY_URL + `/github/pulls/${number}?refresh=${refresh}`, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
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
    const audioResponse = await fetch(audioUri);
    formData.append('file', await audioResponse.blob(), filename);
  } else {
    formData.append('file', { uri: audioUri, name: filename, type: mimeType } as any);
  }
  const res = await fetch(GATEWAY_URL + '/voice/transcribe', {
    method: 'POST',
    headers: {
      'X-Magistrate-Token': DEVICE_TOKEN
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
  const res = await fetch(GATEWAY_URL + '/voice/moves', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Magistrate-Token': DEVICE_TOKEN },
    body: JSON.stringify({ schema_version: 'voice-move.v1', utterance, target, source: 'voice-page',
      idempotency_key: idempotencyKey, execute, confirmation_token: confirmationToken })
  });
  return requireOk(res);
}

// Herdr exposes its read count as uint32 and bounds retained history separately
// through advanced.scrollback_limit_bytes. This asks for all retained rows.
export const HERDR_MAX_READ_LINES = 0xFFFFFFFF;

export async function fetchCaptainOutput(lines: number = HERDR_MAX_READ_LINES) {
  const res = await fetch(GATEWAY_URL + '/captain/output?lines=' + lines, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return checkedJson<{ output?: string }>(res);
}

export async function fetchAgentHistory(agentId: string, lines: number = HERDR_MAX_READ_LINES): Promise<AgentHistoryResult> {
  const res = await fetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/history?lines=' + lines, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
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

export async function sendCaptainPrompt(text: string, source: string = 'iphone', target: string = 'captain', harness?: string, model?: string) {
  const res = await fetch(GATEWAY_URL + '/captain/prompt', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Magistrate-Token': DEVICE_TOKEN
    },
    body: JSON.stringify({
      source,
      modality: 'text',
      type: 'prompt',
      text,
      target,
      ...(harness && model ? { harness, model } : {})
    })
  });
  return checkedJson<{ status: string; target?: string; response?: string; error?: string }>(res);
}

export async function interruptAgent(agentId: string): Promise<AgentControlResult> {
  const res = await fetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/interrupt', {
    method: 'POST',
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return checkedJson<AgentControlResult>(res);
}

export async function renameAgent(agentId: string, name: string): Promise<AgentControlResult & { name?: string }> {
  const res = await fetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Magistrate-Token': DEVICE_TOKEN },
    body: JSON.stringify({ name })
  });
  return checkedJson<AgentControlResult & { name?: string }>(res);
}

export async function sendAgentKey(agentId: string = 'captain', key: string = 'Enter'): Promise<AgentControlResult> {
  const res = await fetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/send-key?key=' + encodeURIComponent(key), {
    method: 'POST',
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return checkedJson<AgentControlResult>(res);
}
