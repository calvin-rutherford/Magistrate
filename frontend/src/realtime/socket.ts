import { GATEWAY_URL, getGatewaySessionToken } from '../api/client';

export type EventCallback = (data: any) => void;

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private listeners: EventCallback[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private target: string;

  constructor(target = 'captain') {
    this.target = target;
  }

  async connect() {
    if (this.stopped || typeof WebSocket === 'undefined' || this.socket?.readyState === WebSocket.OPEN) return;
    const token = await getGatewaySessionToken();
    if (!token || this.stopped) {
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
      return;
    }
    try {
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
        if (!this.stopped) this.reconnectTimer = setTimeout(() => this.connect(), 3000);
      };
      this.socket.onerror = () => { /* onclose schedules the fallback reconnect */ };
    } catch {
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
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
