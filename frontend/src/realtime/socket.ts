import {
  GATEWAY_URL, getGatewaySessionRevision, getGatewaySessionToken,
} from '../api/client';
import { normalizeMagiMessageRecords } from '../services/MagiConversation';
import { normalizeCanonicalActivityRecord } from '../services/CanonicalActivity';

const MAGI_EVENT_KEYS = new Set([
  'type', 'schema_version', 'payload_schema_version', 'conversation_id', 'messages',
]);
const ACTIVITY_EVENT_KEYS = new Set([
  'type', 'schema_version', 'payload_schema_version', 'records', 'next_cursor',
  'latest_cursor', 'has_more', 'summary',
]);
const hasExactKeys = (value: Record<string, unknown>, keys: Set<string>): boolean =>
  Object.keys(value).length === keys.size && Object.keys(value).every(key => keys.has(key));
const validActivitySummary = (raw: unknown): boolean => {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return false;
  const value = raw as Record<string, unknown>;
  return hasExactKeys(value, new Set(['active_objectives', 'operation_count', 'pending_decisions']))
    && [value.active_objectives, value.operation_count, value.pending_decisions]
      .every(count => typeof count === 'number' && Number.isSafeInteger(count) && count >= 0);
};

export interface RealtimeHandlers {
  onMagiMessages?: (payload: unknown) => boolean | void;
  onActivity?: (payload: unknown) => boolean | void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: unknown) => void;
}

/** Authenticated observation channel for Native Magi messages and structured Activity. */
export class RealtimeClient {
  private socket: WebSocket | null = null;
  private stopped = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectMs = 1000;
  private connectedOnce = false;
  private activityAfter: number;
  private lifecycle = 0;
  private openingEpoch: number | null = null;
  private protectedSessionRevision: number | null = null;

  constructor(private handlers: RealtimeHandlers, activityAfter = 0) {
    this.activityAfter = Number.isSafeInteger(activityAfter) && activityAfter >= 0
      ? activityAfter : 0;
  }

  connect(): void {
    const sessionRevision = getGatewaySessionRevision();
    if (!this.stopped && this.protectedSessionRevision !== sessionRevision) {
      this.disconnect();
    }
    if (this.stopped) {
      this.stopped = false;
      this.lifecycle += 1;
      this.protectedSessionRevision = sessionRevision;
    }
    void this.open(this.lifecycle);
  }

  private scheduleReconnect(epoch: number): void {
    if (this.stopped || epoch !== this.lifecycle || this.reconnectTimer) return;
    const delay = this.reconnectMs;
    this.reconnectMs = Math.min(12_000, this.reconnectMs * 2);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.stopped && epoch === this.lifecycle) void this.open(epoch);
    }, delay);
  }

  private async open(epoch: number): Promise<void> {
    if (this.stopped || epoch !== this.lifecycle || this.socket
      || this.openingEpoch === epoch || typeof WebSocket === 'undefined') return;
    this.openingEpoch = epoch;
    const sessionRevision = this.protectedSessionRevision;
    try {
      const token = await getGatewaySessionToken();
      if (!token || this.stopped || epoch !== this.lifecycle) return;
      if (sessionRevision === null || sessionRevision !== getGatewaySessionRevision()) {
        // This client belongs to the protected session that called connect();
        // a replacement session must mount its own observation lifecycle.
        this.stopped = true;
        return;
      }
      const ws = new WebSocket(GATEWAY_URL.replace(/^http/, 'ws') + '/events');
      this.socket = ws;
      let authenticated = false;
      const rejectProtocol = (message: string) => {
        // A schema violation is fatal for this client instance. Reconnecting to
        // the same incompatible stream would create an unbounded failure loop.
        this.stopped = true;
        this.handlers.onError?.(new Error(message));
        ws.close(1008, 'Invalid event protocol');
      };
      ws.onopen = () => {
        if (this.stopped || epoch !== this.lifecycle || this.socket !== ws) {
          ws.close();
          return;
        }
        // Bearer auth is always the first and only pre-acknowledgement frame.
        ws.send(JSON.stringify({ type: 'auth', token, activity_after: this.activityAfter }));
      };
      ws.onmessage = event => {
        if (this.stopped || epoch !== this.lifecycle || this.socket !== ws) return;
        if (this.protectedSessionRevision !== getGatewaySessionRevision()) {
          // Even before React unmounts the old protected tree, a frame from its
          // socket has no authority to mutate the replacement principal.
          this.stopped = true;
          this.socket = null;
          ws.close(1008, 'Protected session changed');
          return;
        }
        try {
          const rawPayload = String(event.data);
          if (rawPayload.length > 8 * 1024 * 1024) {
            rejectProtocol('Gateway event exceeded the bounded client contract.');
            return;
          }
          const decoded = JSON.parse(rawPayload) as unknown;
          if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded)) {
            rejectProtocol('Gateway returned an invalid event payload.');
            return;
          }
          const payload = decoded as Record<string, unknown>;
          if (!authenticated) {
            if (payload.type !== 'connected' || payload.schema_version !== 'magistrate.events.v2'
              || Object.keys(payload).some(key => key !== 'type' && key !== 'schema_version')) {
              rejectProtocol('Gateway returned an invalid event acknowledgement.');
              return;
            }
            authenticated = true;
            const reconnecting = this.connectedOnce;
            this.connectedOnce = true;
            this.reconnectMs = 1000;
            this.handlers.onOpen?.();
            if (reconnecting) this.handlers.onMagiMessages?.({ type: 'reconnect' });
            return;
          }
          if (payload.schema_version !== 'magistrate.events.v2') {
            rejectProtocol('Gateway returned an unsupported event schema.');
            return;
          }
          if (payload.type === 'magi_messages') {
            const conversationId = payload.conversation_id;
            const rawMessages = payload.messages;
            const messages = normalizeMagiMessageRecords(rawMessages);
            if (!hasExactKeys(payload, MAGI_EVENT_KEYS)
              || payload.payload_schema_version !== 'magi.native-chat.v1'
              || typeof conversationId !== 'string' || !/^mgc_[A-Za-z0-9_-]{4,124}$/.test(conversationId)
              || !Array.isArray(rawMessages) || messages.length !== rawMessages.length
              || messages.some(message => message.conversation_id !== conversationId)) {
              rejectProtocol('Gateway returned an invalid Magi event.');
              return;
            }
            if (this.handlers.onMagiMessages?.(payload) === false) {
              rejectProtocol('Gateway returned conflicting Magi identity.');
            }
          } else if (payload.type === 'activity_records') {
            const cursor = payload.next_cursor;
            const latest = payload.latest_cursor;
            const rawRecords = payload.records;
            if (!hasExactKeys(payload, ACTIVITY_EVENT_KEYS)
              || payload.payload_schema_version !== 'activity.v1'
              || typeof cursor !== 'number' || !Number.isSafeInteger(cursor)
              || typeof latest !== 'number' || !Number.isSafeInteger(latest) || latest < cursor
              || cursor < this.activityAfter || !Array.isArray(rawRecords)
              || rawRecords.length > 200
              || rawRecords.some(record => normalizeCanonicalActivityRecord(record) === null)
              || typeof payload.has_more !== 'boolean'
              || !validActivitySummary(payload.summary)) {
              rejectProtocol('Gateway returned an invalid Activity event.');
              return;
            }
            const accepted = this.handlers.onActivity?.({
              schema_version: payload.payload_schema_version,
              records: payload.records,
              next_cursor: payload.next_cursor,
              latest_cursor: payload.latest_cursor,
              has_more: payload.has_more,
              summary: payload.summary,
            });
            if (accepted === false) {
              rejectProtocol('Gateway returned conflicting Activity identity.');
              return;
            }
            this.activityAfter = cursor;
          } else {
            rejectProtocol('Gateway returned an unknown event type.');
          }
        } catch (error) {
          this.stopped = true;
          this.handlers.onError?.(error);
          ws.close(1008, 'Invalid event payload');
        }
      };
      ws.onerror = error => {
        if (!this.stopped && epoch === this.lifecycle && this.socket === ws) {
          this.handlers.onError?.(error);
        }
      };
      ws.onclose = event => {
        if (this.socket === ws) this.socket = null;
        if (this.stopped || epoch !== this.lifecycle) return;
        this.handlers.onClose?.();
        if (event.code === 1008 || event.code === 1009) {
          // Authentication, authorization, and protocol-policy failures need a
          // new protected session/client lifecycle; retrying this bearer would
          // create an unbounded rejected-connection loop.
          this.stopped = true;
          return;
        }
        this.scheduleReconnect(epoch);
      };
    } catch (error) {
      if (epoch !== this.lifecycle) return;
      this.socket = null;
      this.handlers.onError?.(error);
      this.scheduleReconnect(epoch);
    } finally {
      if (this.openingEpoch === epoch) this.openingEpoch = null;
    }
  }

  disconnect(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.lifecycle += 1;
    this.openingEpoch = null;
    this.protectedSessionRevision = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }
}
