import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import { AppState, AppStateStatus, Platform } from 'react-native';
import { enqueuePendingIntent, parsePendingIntent, pendingIntentPath, PendingIntentPayload, subscribeConsumedIntent } from './PendingIntentRouter';
import { getMagiConversationPrincipal } from './MagiConversationSession';
import {
  acknowledgeNotificationEvents,
  fetchNotificationEvents,
  markNotificationEventsDelivered,
  NotificationEvent,
  registerNativePushToken as registerTokenWithGateway,
  revokeNativePushToken,
  GatewayNetworkError,
  getGatewaySessionRevision,
} from '../api/client';

if (Platform.OS !== 'web') {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: false,
      shouldSetBadge: false,
    }),
  });
}

export type NativePushStatus = 'not-started' | 'registering' | 'registered' | 'permission-required' | 'permission-denied' | 'unavailable' | 'offline' | 'error';

function safeEventUrl(event?: NotificationEvent): string {
  const parsed = parsePendingIntent(event?.deep_link || event?.url);
  return parsed ? pendingIntentPath(parsed) : '/attention?overview=true';
}

function copyFor(events: NotificationEvent[]) {
  if (events.length > 1) return { title: `${events.length} updates need attention`, body: 'Open the app to review them.', url: safeEventUrl(events[0]) };
  const event = events[0];
  const copy: Partial<Record<NotificationEvent['notification_kind'], [string, string]>> = {
    captain_question: ['Your answer is needed', 'Open the app to review an item that needs your attention.'],
    consequential_decision: ['Your answer is needed', 'Open the app to review an item that needs your attention.'],
    blocker: ['Your answer is needed', 'Open the app to review an item that needs your attention.'],
    pr_ready: ['A pull request is ready', 'Open the app to review it.'],
    completion: ['Work completed', 'Open the app to review the result.'],
    failure: ['Work needs attention', 'Open the app to review what happened.'],
    stall: ['Work needs attention', 'Open the app to review what happened.'],
    budget: ['Usage needs attention', 'Open the app to review account usage.'],
    credit: ['Usage needs attention', 'Open the app to review account usage.'],
    repository_disconnected: ['A repository needs reconnecting', 'Open the app to review the connection.'],
    payment_issue: ['Billing needs attention', 'Open the app to review billing.'],
  };
  const [title, body] = copy[event.notification_kind] || ['Needs attention', 'Open the app to review the update.'];
  return { title, body, url: safeEventUrl(event) };
}

function openNotificationData(value: unknown, deliveryId?: string): void {
  // RootLayout owns navigation. Queueing here preserves a cold/terminated
  // notification tap through authentication and validates the route centrally.
  if (typeof value === 'string') enqueuePendingIntent(value);
  else if (value && typeof value === 'object') {
    const payload = value as PendingIntentPayload;
    // A cold tap is navigation, not an authenticated acknowledgement. RootLayout
    // acknowledges only after session restoration; never issue a pre-auth request.
    enqueuePendingIntent({ ...payload, ...(deliveryId ? { delivery_id: deliveryId } : {}) });
  }
}

class NotificationManagerService {
  private intervalId: ReturnType<typeof setInterval> | null = null;
  private appState: AppStateStatus = AppState.currentState;
  private appStateSubscription: { remove(): void } | null = null;
  private responseSubscription: { remove(): void } | null = null;
  private tokenSubscription: { remove(): void } | null = null;
  private generation = 0;
  private registrationAttempts = 0;
  private nextRegistrationAt = 0;
  private pendingDeviceToken: Notifications.DevicePushToken | undefined;
  private polling = false;
  private registering = false;
  private fallbackEvents: NotificationEvent[] = [];
  private fallbackListeners = new Set<(events: NotificationEvent[]) => void>();
  private unreadEvents: NotificationEvent[] = [];
  private unreadListeners = new Set<(events: NotificationEvent[]) => void>();
  private viewedInFlight = new Set<string>();
  private browserDelivered = new Set<string>();
  private status: NativePushStatus = 'not-started';
  private statusListeners = new Set<(status: NativePushStatus) => void>();

  subscribeFallback(listener: (events: NotificationEvent[]) => void) {
    this.fallbackListeners.add(listener);
    listener(this.fallbackEvents);
    return () => { this.fallbackListeners.delete(listener); };
  }

  subscribeUnread(listener: (events: NotificationEvent[]) => void) {
    this.unreadListeners.add(listener);
    listener(this.unreadEvents);
    return () => { this.unreadListeners.delete(listener); };
  }

  getUnreadEvents(): NotificationEvent[] { return this.unreadEvents; }

  private setUnread(events: NotificationEvent[]) {
    const seen = new Set<string>();
    this.unreadEvents = events.filter(event => {
      if (seen.has(event.id)) return false;
      seen.add(event.id);
      return event.requires_action !== false;
    });
    this.unreadListeners.forEach(listener => listener(this.unreadEvents));
  }

  async markViewed(itemId: string): Promise<void> {
    if (!itemId || this.viewedInFlight.has(itemId)) return;
    this.viewedInFlight.add(itemId);
    try {
      await acknowledgeNotificationEvents([itemId]);
      this.setUnread(this.unreadEvents.filter(event => event.id !== itemId));
      this.fallbackEvents = this.fallbackEvents.filter(event => event.id !== itemId);
      this.fallbackListeners.forEach(listener => listener(this.fallbackEvents));
    } catch (error) {
      // Keep the dot when acknowledgement cannot reach the gateway. A retry
      // on the next view/poll is preferable to claiming the item was read.
      console.error('Notification acknowledgement error:', error);
    } finally {
      this.viewedInFlight.delete(itemId);
    }
  }

  subscribePushStatus(listener: (status: NativePushStatus) => void) {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => { this.statusListeners.delete(listener); };
  }

  getPushStatus(): NativePushStatus { return this.status; }

  private setStatus(status: NativePushStatus) {
    this.status = status;
    this.statusListeners.forEach(listener => listener(status));
  }

  dismissFallback(itemId: string) {
    this.fallbackEvents = this.fallbackEvents.filter(event => event.id !== itemId);
    this.fallbackListeners.forEach(listener => listener(this.fallbackEvents));
  }

  private showFallback(events: NotificationEvent[]) {
    const existing = new Set(this.fallbackEvents.map(event => event.id));
    this.fallbackEvents = [...this.fallbackEvents, ...events.filter(event => !existing.has(event.id))];
    this.fallbackListeners.forEach(listener => listener(this.fallbackEvents));
  }

  async registerNativePushToken(requestPermission = false): Promise<NativePushStatus> {
    if (Platform.OS === 'web') return 'unavailable';
    if (this.registering) return this.status;
    if (!requestPermission && (this.registrationAttempts >= 3 || Date.now() < this.nextRegistrationAt)) return this.status;
    const owner = getMagiConversationPrincipal();
    if (!owner) return this.status;
    const generation = this.generation;
    const revision = getGatewaySessionRevision();
    const currentSession = () => generation === this.generation && owner === getMagiConversationPrincipal() && revision === getGatewaySessionRevision();
    if (requestPermission) this.registrationAttempts = 0;
    this.registrationAttempts += 1;
    this.nextRegistrationAt = Date.now() + 60_000;
    const devicePushToken = this.pendingDeviceToken;
    this.pendingDeviceToken = undefined;
    this.registering = true;
    this.setStatus('registering');
    try {
      if (!Device.isDevice) {
        this.setStatus('unavailable');
        return this.status;
      }
      const current = await Notifications.getPermissionsAsync();
      let granted = current.granted || current.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      if (!granted && requestPermission && current.canAskAgain) {
        const requested = await Notifications.requestPermissionsAsync({ ios: { allowAlert: true, allowBadge: false, allowSound: false } });
        granted = requested.granted || requested.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      }
      if (!currentSession()) return this.status;
      if (!granted) {
        if (!requestPermission && current.canAskAgain) {
          this.setStatus('permission-required');
          return this.status;
        }
        await revokeNativePushToken().catch(() => undefined);
        if (currentSession()) this.setStatus('permission-denied');
        return this.status;
      }
      const projectId = (Constants.easConfig as any)?.projectId || (Constants.expoConfig as any)?.extra?.eas?.projectId || process.env.EXPO_PUBLIC_EAS_PROJECT_ID;
      if (typeof projectId !== 'string' || !projectId) {
        this.setStatus('unavailable');
        return this.status;
      }
      const token = await Notifications.getExpoPushTokenAsync({ projectId, ...(devicePushToken ? { devicePushToken } : {}) });
      if (!token?.data) throw new Error('Expo did not return a push token.');
      if (!currentSession()) return this.status;
      await registerTokenWithGateway(token.data, Platform.OS === 'ios' ? 'ios' : 'android');
      if (!currentSession()) return this.status;
      this.registrationAttempts = 0;
      this.setStatus('registered');
      return this.status;
    } catch (error) {
      console.error('Native push registration error:', error);
      if (currentSession()) this.setStatus(error instanceof GatewayNetworkError || error instanceof TypeError ? 'offline' : 'error');
      return this.status;
    } finally {
      this.registering = false;
      if (!currentSession() || this.pendingDeviceToken) {
        // The authenticated foreground poll owns the next attempt. Do not
        // recursively register after a session change or token callback.
        this.setStatus('not-started');
      }
    }
  }

  /** Install before authentication so a terminated notification tap survives the auth gate. */
  installNotificationRouting() {
    if (Platform.OS === 'web' || this.responseSubscription) return;
    // This is the real remote-push response path. No local notification is
    // scheduled from the reconciliation feed.
    const routeResponse = (response: Notifications.NotificationResponse | null) => {
      if (!response || response.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) return;
      const request = response.notification.request;
      const data = request.content.data || {};
      const identity = typeof data.delivery_id === 'string' ? data.delivery_id : request.identifier;
      openNotificationData(data.intent_version ? data : { intent_version: 1, route: data.route || data.url }, identity);
    };
    subscribeConsumedIntent(intent => {
      const response = Notifications.getLastNotificationResponse();
      const request = response?.notification.request;
      const identity = request?.content.data?.delivery_id || request?.identifier;
      if (intent.deliveryId && intent.deliveryId === identity) {
        // Expo 57's synchronous API returns void; Promise.resolve also contains
        // a rejected native/compatibility implementation without losing routing.
        try { void Promise.resolve(Notifications.clearLastNotificationResponse()).catch(() => undefined); }
        catch { /* OS cleanup failure must not swallow authenticated navigation. */ }
      }
    });
    this.responseSubscription = Notifications.addNotificationResponseReceivedListener(routeResponse);
    routeResponse(Notifications.getLastNotificationResponse());
  }

  startMonitoring() {
    if (this.intervalId) return;
    this.appState = AppState.currentState || 'active';
    this.appStateSubscription = AppState.addEventListener('change', state => {
      const resumed = this.appState !== 'active' && state === 'active';
      this.appState = state;
      if (resumed) {
        this.registrationAttempts = 0;
        this.nextRegistrationAt = 0;
        if (Platform.OS !== 'web') void this.registerNativePushToken(false);
        void this.poll();
      }
    });
    if (Platform.OS !== 'web') {
      this.installNotificationRouting();
      this.tokenSubscription = Notifications.addPushTokenListener(deviceToken => {
        // This listener receives APNs/FCM tokens, not Expo tokens. Re-fetch the
        // Expo token for our project instead of registering a raw device token.
        // Supply the changed token so Expo does not recursively fetch it.
        this.pendingDeviceToken = deviceToken;
        this.registrationAttempts = 0;
        this.nextRegistrationAt = 0;
        void this.registerNativePushToken(false);
      });
      // Do not trigger an OS permission prompt during authenticated startup.
      // Account settings supplies the explicit user action; this call only
      // registers a permission already granted by the owner.
      void this.registerNativePushToken(false);
    }
    void this.poll();
    this.intervalId = setInterval(() => void this.poll(), 10_000);
  }

  stopMonitoring() {
    if (this.intervalId) clearInterval(this.intervalId);
    this.intervalId = null;
    this.appStateSubscription?.remove();
    this.appStateSubscription = null;
    this.tokenSubscription?.remove();
    this.tokenSubscription = null;
    this.generation += 1;
    this.pendingDeviceToken = undefined;
    this.registrationAttempts = 0;
    this.nextRegistrationAt = 0;
    this.setStatus('not-started');
    this.setUnread([]);
    this.fallbackEvents = [];
    this.browserDelivered.clear();
  }

  private async poll() {
    // APNs/Expo owns background delivery. Foreground reconciliation resumes
    // immediately on activation and never burns a background polling timer.
    if (this.appState !== 'active' || this.polling) return;
    this.polling = true;
    const generation = this.generation;
    const owner = getMagiConversationPrincipal();
    try {
      // The gateway performs native delivery and acknowledges only successful
      // remote sends. Web receives this feed for its open-tab fallback.
      const result = await fetchNotificationEvents(false);
      if (generation !== this.generation || owner !== getMagiConversationPrincipal()) return;
      // The gateway keeps delivery and viewing separate. This is the source
      // for the restrained chat-logo indicator, including pushes accepted while
      // the app was backgrounded or terminated.
      const events = Array.isArray(result?.events) ? result.events : [];
      const unread = Array.isArray(result?.unread) ? result.unread : events;
      this.setUnread(unread);
      if (Platform.OS !== 'web' && ['offline', 'error', 'not-started'].includes(this.status)) void this.registerNativePushToken(false);
      if (!events.length) return;
      if (Platform.OS === 'web') {
        const freshEvents = events.filter(event => !this.browserDelivered.has(`${event.id}:${event.revision || ''}`));
        if (!freshEvents.length) return;
        const delivered = await this.deliverBrowser(freshEvents);
        if (delivered) {
          freshEvents.forEach(event => this.browserDelivered.add(`${event.id}:${event.revision || ''}`));
          await markNotificationEventsDelivered(freshEvents.map(event => event.id));
        }
      } else {
        // A failed/absent remote channel is honest in-app fallback, not a
        // background-push claim. The gateway's bounded retries already ran.
        // There is intentionally no in-app popup; the unread dot and drawer
        // remain the fallback until the captain opens the item.
        this.showFallback(events);
      }
    } catch (error) {
      if (Platform.OS !== 'web' && generation === this.generation) this.setStatus('offline');
      console.error('Notification monitoring error:', error);
    } finally {
      this.polling = false;
    }
  }

  private async deliverBrowser(events: NotificationEvent[]): Promise<boolean> {
    const BrowserNotification = (globalThis as any).Notification;
    if (typeof BrowserNotification !== 'function') return false;
    if (BrowserNotification.permission !== 'granted') return false;
    const copy = copyFor(events);
    const payload = { intent_version: 1, route: copy.url };
    try {
      const notification = new BrowserNotification(copy.title, {
        body: copy.body,
        data: payload,
      });
      notification.onclick = () => {
        if (typeof window !== 'undefined') window.focus();
        events.forEach(event => { void this.markViewed(event.id); });
        openNotificationData(payload);
        notification.close?.();
      };
      return true;
    } catch { return false; }
  }
}

export const notificationManager = new NotificationManagerService();
