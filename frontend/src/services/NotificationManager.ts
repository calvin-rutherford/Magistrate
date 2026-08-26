import * as Notifications from 'expo-notifications';
import { AppState, AppStateStatus, Platform } from 'react-native';
import { router } from 'expo-router';
import { acknowledgeNotificationEvents, fetchNotificationEvents, NotificationEvent } from '../api/client';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: false,
    shouldShowList: false,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

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

  startMonitoring() {
    if (Platform.OS === 'web' || this.intervalId) return;
    this.appStateSubscription = AppState.addEventListener('change', state => { this.appState = state; });
    this.responseSubscription = Notifications.addNotificationResponseReceivedListener(response => {
      const url = response.notification.request.content.data?.url;
      if (typeof url === 'string' && url.startsWith('/attention')) router.push(url as never);
    });
    const initialUrl = Notifications.getLastNotificationResponse()?.notification.request.content.data?.url;
    if (typeof initialUrl === 'string' && initialUrl.startsWith('/attention')) router.push(initialUrl as never);
    void this.poll();
    this.intervalId = setInterval(() => void this.poll(), 10_000);
  }

  stopMonitoring() {
    if (this.intervalId) clearInterval(this.intervalId);
    this.intervalId = null;
    this.appStateSubscription?.remove();
    this.responseSubscription?.remove();
  }

  private async poll() {
    if (this.polling) return;
    this.polling = true;
    try {
      const foreground = this.appState === 'active';
      const { events } = await fetchNotificationEvents(foreground);
      if (!events.length || foreground) return;

      // Ask only when there is an actual unresolved question or merge decision.
      const current = await Notifications.getPermissionsAsync();
      let granted = current.granted || current.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      if (!granted && current.canAskAgain) {
        const requested = await Notifications.requestPermissionsAsync({ ios: { allowAlert: true, allowBadge: false, allowSound: false } });
        granted = requested.granted || requested.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      }
      if (granted) {
        const copy = copyFor(events);
        await Notifications.scheduleNotificationAsync({
          identifier: events.length === 1 ? `attention-${events[0].id}` : 'attention-batch',
          content: { title: copy.title, body: copy.body, sound: false, data: { url: copy.url, item_ids: events.map(event => event.id) } },
          trigger: null,
        });
      }
      await acknowledgeNotificationEvents(events.map(event => event.id));
    } catch (error) {
      console.error('Notification monitoring error:', error);
    } finally {
      this.polling = false;
    }
  }
}

export const notificationManager = new NotificationManagerService();
