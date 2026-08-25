const WS_URL = 'ws://100.84.181.23:8000/api/v1/events?token=magistrate-device-token-12345';

export type EventCallback = (data: any) => void;

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private listeners: EventCallback[] = [];
  private isConnected = false;
  private reconnectInterval = 3000;

  connect() {
    try {
      this.socket = new WebSocket(WS_URL);

      this.socket.onopen = () => {
        this.isConnected = true;
        console.log('[Realtime] WebSocket Connected to Magistrate Gateway');
      };

      this.socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.listeners.forEach((listener) => listener(data));
        } catch (e) {
          console.error('[Realtime] Parse error:', e);
        }
      };

      this.socket.onclose = () => {
        this.isConnected = false;
        setTimeout(() => this.connect(), this.reconnectInterval);
      };

      this.socket.onerror = (err) => {
        console.error('[Realtime] Socket error:', err);
      };
    } catch (e) {
      console.error('[Realtime] Failed to connect WebSocket:', e);
    }
  }

  subscribe(callback: EventCallback) {
    this.listeners.push(callback);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== callback);
    };
  }

  status() {
    return this.isConnected;
  }
}

export const realtimeClient = new RealtimeClient();
