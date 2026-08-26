const GATEWAY_URL = 'http://100.84.181.23:8000/api/v1';
const DEVICE_TOKEN = 'magistrate-device-token-12345';

export interface AgentInfo {
  id: string;
  name: string;
  harness: string;
  status: 'idle' | 'working' | 'blocked' | 'done' | 'unknown';
  pane_id?: string;
  tab_id?: string;
  workspace_id?: string;
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

export interface UserProfile {
  user_id: string;
  name: string;
  email: string;
  avatar_url: string;
  bio: string;
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
  pr_number: number;
  title: string;
  repository: string;
  author: string;
  agent: string;
  branch: string;
  state: string;
  review_status: string;
  checks: string;
  mergeable: string;
  summary: string;
  requires_attention: boolean;
  url: string;
}

export async function fetchHealth() {
  const res = await fetch(GATEWAY_URL + '/health', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
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
  return res.json();
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

export async function fetchUserProfile(): Promise<UserProfile> {
  const res = await fetch(GATEWAY_URL + '/account/profile', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
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

export async function fetchGitHubPRs(): Promise<GitHubPR[]> {
  const res = await fetch(GATEWAY_URL + '/github/pulls', {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function transcribeVoiceAudio(audioUri?: string): Promise<{ text: string }> {
  const formData = new FormData();
  if (audioUri) {
    formData.append('file', {
      uri: audioUri,
      name: 'speech.wav',
      type: 'audio/wav'
    } as any);
  }
  const res = await fetch(GATEWAY_URL + '/voice/transcribe', {
    method: 'POST',
    headers: {
      'X-Magistrate-Token': DEVICE_TOKEN
    },
    body: formData
  });
  return res.json();
}

// Herdr exposes its read count as uint32 and bounds retained history separately
// through advanced.scrollback_limit_bytes. This asks for all retained rows.
export const HERDR_MAX_READ_LINES = 0xFFFFFFFF;

export async function fetchCaptainOutput(lines: number = HERDR_MAX_READ_LINES) {
  const res = await fetch(GATEWAY_URL + '/captain/output?lines=' + lines, {
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function sendCaptainPrompt(text: string, source: string = 'iphone', target: string = 'captain') {
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
      target
    })
  });
  return res.json();
}

export async function interruptAgent(agentId: string) {
  const res = await fetch(GATEWAY_URL + '/agents/' + agentId + '/interrupt', {
    method: 'POST',
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}

export async function sendAgentKey(agentId: string = 'captain', key: string = 'Enter') {
  const res = await fetch(GATEWAY_URL + '/agents/' + encodeURIComponent(agentId) + '/send-key?key=' + encodeURIComponent(key), {
    method: 'POST',
    headers: { 'X-Magistrate-Token': DEVICE_TOKEN }
  });
  return res.json();
}
