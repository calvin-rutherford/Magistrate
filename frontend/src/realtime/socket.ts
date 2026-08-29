const HTTP_GATEWAY_URL = 'http://100.84.181.23:8000/api/v1';
const DEVICE_TOKEN = 'magistrate-device-token-12345';

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

  connect() {
    if (this.stopped || typeof WebSocket === 'undefined' || this.socket?.readyState === WebSocket.OPEN) return;
    try {
      const wsUrl = HTTP_GATEWAY_URL.replace(/^http/, 'ws') + `/events?token=${encodeURIComponent(DEVICE_TOKEN)}`;
      this.socket = new WebSocket(wsUrl);
      this.socket.onopen = () => {
        this.socket?.send(JSON.stringify({ type: 'subscribe', target: this.target }));
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
