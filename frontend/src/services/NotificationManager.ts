import * as Notifications from 'expo-notifications';
import { AppState, AppStateStatus, Platform } from 'react-native';
import { router } from 'expo-router';
import { acknowledgeNotificationEvents, fetchNotificationEvents, NotificationEvent } from '../api/client';

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

function copyFor(events: NotificationEvent[]) {
  if (events.length > 1) return { title: `${events.length} items need your attention`, body: 'Questions or merge decisions are waiting.', url: '/attention' };
  const event = events[0];
  return event.notification_kind === 'pr_ready'
    ? { title: 'A pull request is ready', body: event.subtitle, url: event.url }
    : { title: 'Your answer is needed', body: event.subtitle, url: event.url };
}

class NotificationManagerService {
  private intervalId: ReturnType<typeof setInterval> | null = null;
  private appState: AppStateStatus = AppState.currentState;
  private appStateSubscription: { remove(): void } | null = null;
  private responseSubscription: { remove(): void } | null = null;
  private polling = false;
  private fallbackEvents: NotificationEvent[] = [];
  private fallbackListeners = new Set<(events: NotificationEvent[]) => void>();

  subscribeFallback(listener: (events: NotificationEvent[]) => void) {
    this.fallbackListeners.add(listener);
    listener(this.fallbackEvents);
    return () => { this.fallbackListeners.delete(listener); };
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

  startMonitoring() {
    if (this.intervalId) return;
    this.appStateSubscription = AppState.addEventListener('change', state => { this.appState = state; });
    if (Platform.OS !== 'web') {
      this.responseSubscription = Notifications.addNotificationResponseReceivedListener(response => {
        const url = response.notification.request.content.data?.url;
        if (typeof url === 'string' && url.startsWith('/attention')) router.push(url as never);
      });
      const initialUrl = Notifications.getLastNotificationResponse()?.notification.request.content.data?.url;
      if (typeof initialUrl === 'string' && initialUrl.startsWith('/attention')) router.push(initialUrl as never);
    }
    void this.poll();
    this.intervalId = setInterval(() => void this.poll(), 10_000);
  }

  stopMonitoring() {
    if (this.intervalId) clearInterval(this.intervalId);
    this.intervalId = null;
    this.appStateSubscription?.remove();
    this.responseSubscription?.remove();
    this.responseSubscription = null;
  }

  private async poll() {
    if (this.polling) return;
    this.polling = true;
    try {
      // Do not ask the gateway to suppress foreground events: the client needs
      // the transition to choose a real browser/native notification or fallback.
      const { events } = await fetchNotificationEvents(false);
      if (!events.length) return;

      const delivered = Platform.OS === 'web'
        ? await this.deliverBrowser(events)
        : await this.deliverNative(events);
      if (!delivered) this.showFallback(events);
      await acknowledgeNotificationEvents(events.map(event => event.id));
    } catch (error) {
      console.error('Notification monitoring error:', error);
    } finally {
      this.polling = false;
    }
  }

  private async deliverBrowser(events: NotificationEvent[]): Promise<boolean> {
    const BrowserNotification = (globalThis as any).Notification;
    if (typeof BrowserNotification !== 'function') return false;

    let permission = BrowserNotification.permission;
    if (permission === 'default' && typeof BrowserNotification.requestPermission === 'function') {
      try {
        permission = await BrowserNotification.requestPermission();
      } catch {
        return false;
      }
    }
    if (permission !== 'granted') return false;

    const copy = copyFor(events);
    try {
      const notification = new BrowserNotification(copy.title, { body: copy.body, data: { url: copy.url } });
      notification.onclick = () => {
        if (typeof window !== 'undefined') window.focus();
        router.push(copy.url as never);
        notification.close?.();
      };
      return true;
    } catch {
      return false;
    }
  }

  private async deliverNative(events: NotificationEvent[]): Promise<boolean> {
    try {
      // Ask only when there is an actual unresolved question or merge decision.
      const current = await Notifications.getPermissionsAsync();
      let granted = current.granted || current.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      if (!granted && current.canAskAgain) {
        const requested = await Notifications.requestPermissionsAsync({ ios: { allowAlert: true, allowBadge: false, allowSound: false } });
        granted = requested.granted || requested.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      }
      if (!granted) return false;

      const copy = copyFor(events);
      await Notifications.scheduleNotificationAsync({
        identifier: events.length === 1 ? `attention-${events[0].id}` : 'attention-batch',
        content: { title: copy.title, body: copy.body, sound: false, data: { url: copy.url, item_ids: events.map(event => event.id) } },
        trigger: null,
      });
      return true;
    } catch {
      return false;
    }
  }
}

export const notificationManager = new NotificationManagerService();
