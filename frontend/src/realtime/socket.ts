import { GATEWAY_URL, getGatewaySessionToken } from '../api/client';

export type EventCallback = (data: any) => void;

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private listeners: EventCallback[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private connecting = false;
  private target: string;

  constructor(target = 'captain') {
    this.target = target;
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
      this.socket = new WebSocket(wsUrl);
      this.socket.onopen = () => {
        // Browser WebSocket APIs cannot set Authorization headers. Authenticate
        // before subscribing; unlike the old implementation this is not a URL
        // query parameter and cannot leak through proxy access logs.
        this.socket?.send(JSON.stringify({ type: 'auth', token, target: this.target }));
      };
      this.socket.onmessage = event => {
        try {
          this.listeners.forEach(listener => listener(JSON.parse(event.data)));
        } catch { /* malformed event: polling remains authoritative fallback */ }
      };
      this.socket.onclose = () => {
        this.socket = null;
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

  disconnect() {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close();
    this.socket = null;
  }

  subscribe(callback: EventCallback) {
    this.listeners.push(callback);
    return () => { this.listeners = this.listeners.filter(listener => listener !== callback); };
  }
}
