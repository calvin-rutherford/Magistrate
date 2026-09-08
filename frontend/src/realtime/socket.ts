import { GATEWAY_URL, getGatewaySessionToken } from '../api/client';

export type EventCallback = (data: any) => void;

const activeClientsByTarget = new Map<string, number>();

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private listeners = new Set<EventCallback>();
  private registeredActive = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private connecting = false;
  private target: string;
  private activityAfter: number | null;

  constructor(target = 'captain', activityAfter: number | null = null) {
    this.target = target;
    this.activityAfter = typeof activityAfter === 'number' && Number.isSafeInteger(activityAfter) && activityAfter >= 0 && activityAfter <= 9_007_199_254_740_991
      ? activityAfter : null;
  }

  private scheduleReconnect() {
    if (this.stopped || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, 3000);
  }

  async connect() {
    if (this.stopped || this.connecting || typeof WebSocket === 'undefined' || this.socket?.readyState === WebSocket.OPEN || this.socket?.readyState === WebSocket.CONNECTING) return;
    this.connecting = true;
    try {
      const token = await getGatewaySessionToken();
      // A missing token means the React auth gate is closing this client. Do not
      // manufacture a reconnect loop while logout/expiry invalidates the session.
      if (!token || this.stopped) return;
      const wsUrl = GATEWAY_URL.replace(/^http/, 'ws') + '/events';
      if (!this.registeredActive) {
        const active = (activeClientsByTarget.get(this.target) || 0) + 1;
        activeClientsByTarget.set(this.target, active);
        this.registeredActive = true;
        if (typeof __DEV__ !== 'undefined' && __DEV__ && active > 1) {
          console.warn(`[Magistrate chat] ${active} realtime clients are mounted for ${this.target}; stable message ids will deduplicate events, but check for a duplicate ChatCanvas mount.`);
        }
      }
      this.socket = new WebSocket(wsUrl);
      this.socket.onopen = () => {
        // Browser WebSocket APIs cannot set Authorization headers. Authenticate
        // before subscribing; unlike the old implementation this is not a URL
        // query parameter and cannot leak through proxy access logs.
        this.socket?.send(JSON.stringify({
          type: 'auth', token, target: this.target,
          ...(this.activityAfter !== null ? { activity_after: this.activityAfter } : {}),
        }));
      };
      this.socket.onmessage = event => {
        try {
          const payload = JSON.parse(event.data);
          this.listeners.forEach(listener => listener(payload));
        } catch { /* malformed event: polling remains authoritative fallback */ }
      };
      this.socket.onclose = () => {
        this.socket = null;
        this.listeners.forEach(listener => listener({
          type: 'connection_state', state: 'disconnected', target: this.target,
        }));
        void getGatewaySessionToken().then(currentToken => { if (currentToken) this.scheduleReconnect(); });
      };
      this.socket.onerror = () => { /* onclose schedules the fallback reconnect */ };
    } catch {
      this.socket = null;
      this.scheduleReconnect();
    } finally {
      this.connecting = false;
    }
  }

  setActivityCursor(after: number) {
    if (Number.isSafeInteger(after) && after >= (this.activityAfter || 0) && after <= 9_007_199_254_740_991) {
      this.activityAfter = after;
    }
  }

  disconnect() {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close();
    this.socket = null;
    if (this.registeredActive) {
      const active = (activeClientsByTarget.get(this.target) || 1) - 1;
      if (active > 0) activeClientsByTarget.set(this.target, active);
      else activeClientsByTarget.delete(this.target);
      this.registeredActive = false;
    }
  }

  subscribe(callback: EventCallback) {
    this.listeners.add(callback);
    return () => { this.listeners.delete(callback); };
  }
}
