import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import { AppState, AppStateStatus, Platform } from 'react-native';
import { enqueuePendingIntent, PendingIntentPayload } from './PendingIntentRouter';
import {
  acknowledgeNotificationEvents,
  fetchNotificationEvents,
  markNotificationEventsDelivered,
  NotificationEvent,
  registerNativePushToken as registerTokenWithGateway,
  revokeNativePushToken,
  GatewayNetworkError,
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

function copyFor(events: NotificationEvent[]) {
  if (events.length > 1) return { title: `${events.length} items need your attention`, body: 'Questions or captain decisions are waiting.', url: events[0]?.deep_link || events[0]?.url || '/attention' };
  const event = events[0];
  return event.notification_kind === 'pr_ready'
    ? { title: 'A pull request is ready', body: event.subtitle, url: event.deep_link || event.url }
    : { title: 'Your answer is needed', body: event.subtitle, url: event.deep_link || event.url };
}

function openNotificationData(value: unknown): void {
  // RootLayout owns navigation. Queueing here preserves a cold/terminated
  // notification tap through authentication and validates the route centrally.
  if (typeof value === 'string') enqueuePendingIntent(value);
  else if (value && typeof value === 'object') {
    const payload = value as PendingIntentPayload;
    if (typeof payload.item_id === 'string') void notificationManager.markViewed(payload.item_id);
    enqueuePendingIntent(payload);
  }
}

class NotificationManagerService {
  private intervalId: ReturnType<typeof setInterval> | null = null;
  private appState: AppStateStatus = AppState.currentState;
  private appStateSubscription: { remove(): void } | null = null;
  private responseSubscription: { remove(): void } | null = null;
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
    if (this.registering || this.status === 'registered') return this.status;
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
      if (!granted) {
        if (!requestPermission && current.canAskAgain) {
          this.setStatus('permission-required');
          return this.status;
        }
        await revokeNativePushToken().catch(() => undefined);
        this.setStatus('permission-denied');
        return this.status;
      }
      const projectId = (Constants.easConfig as any)?.projectId || (Constants.expoConfig as any)?.extra?.eas?.projectId || process.env.EXPO_PUBLIC_EAS_PROJECT_ID;
      if (typeof projectId !== 'string' || !projectId) {
        this.setStatus('unavailable');
        return this.status;
      }
      const token = await Notifications.getExpoPushTokenAsync({ projectId });
      if (!token?.data) throw new Error('Expo did not return a push token.');
      await registerTokenWithGateway(token.data, Platform.OS === 'ios' ? 'ios' : 'android');
      this.setStatus('registered');
      return this.status;
    } catch (error) {
      console.error('Native push registration error:', error);
      this.setStatus(error instanceof GatewayNetworkError || error instanceof TypeError ? 'offline' : 'error');
      return this.status;
    } finally {
      this.registering = false;
    }
  }

  /** Install before authentication so a terminated notification tap survives the auth gate. */
  installNotificationRouting() {
    if (Platform.OS === 'web' || this.responseSubscription) return;
    // This is the real remote-push response path. No local notification is
    // scheduled from the reconciliation feed.
    this.responseSubscription = Notifications.addNotificationResponseReceivedListener(response => {
      const data = response.notification.request.content.data || {};
      openNotificationData(data.intent_version ? data : data.route || data.url);
    });
    const initialData = Notifications.getLastNotificationResponse()?.notification.request.content.data || {};
    openNotificationData(initialData.intent_version ? initialData : initialData.route || initialData.url);
  }

  startMonitoring() {
    if (this.intervalId) return;
    this.appStateSubscription = AppState.addEventListener('change', state => { this.appState = state; });
    if (Platform.OS !== 'web') {
      this.installNotificationRouting();
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
  }

  private async poll() {
    if (this.polling) return;
    this.polling = true;
    try {
      // The gateway performs native delivery and acknowledges only successful
      // remote sends. Web receives this feed for its open-tab fallback.
      const result = await fetchNotificationEvents(false);
      // The gateway keeps delivery and viewing separate. This is the source
      // for the restrained chat-logo indicator, including pushes accepted while
      // the app was backgrounded or terminated.
      const events = Array.isArray(result?.events) ? result.events : [];
      const unread = Array.isArray(result?.unread) ? result.unread : events;
      this.setUnread(unread);
      if (!events.length) {
        if (Platform.OS !== 'web' && (this.status === 'offline' || this.status === 'error')) void this.registerNativePushToken(false);
        return;
      }
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
      if (Platform.OS !== 'web') this.setStatus('offline');
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
    try {
      const notification = new BrowserNotification(copy.title, {
        body: copy.body,
        data: { intent_version: 1, target_type: 'attention', target_id: events[0]?.id, item_id: events[0]?.id, route: copy.url },
      });
      notification.onclick = () => {
        if (typeof window !== 'undefined') window.focus();
        events.forEach(event => { void this.markViewed(event.id); });
        openNotificationData({ intent_version: 1, target_type: 'attention', target_id: events[0]?.id, item_id: events[0]?.id, route: copy.url });
        notification.close?.();
      };
      return true;
    } catch { return false; }
  }
}

export const notificationManager = new NotificationManagerService();
