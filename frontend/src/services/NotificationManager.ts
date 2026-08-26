import * as Notifications from 'expo-notifications';
import { Platform, AppState, AppStateStatus } from 'react-native';
import { fetchAgents } from '../api/client';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

class NotificationManagerService {
  private lastNotifiedState: Record<string, string> = {};
  private intervalId: any = null;
  private appState: AppStateStatus = AppState.currentState;

  async requestPermissions() {
    if (Platform.OS === 'web') return;
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') {
      console.warn('Failed to get push token for push notification!');
      return;
    }
  }

  startMonitoring() {
    if (Platform.OS === 'web') return; // Notifications not fully supported on web
    
    AppState.addEventListener('change', (nextAppState) => {
      this.appState = nextAppState;
    });

    this.intervalId = setInterval(async () => {
      try {
        const agents = await fetchAgents();
        if (!agents || agents.length === 0) return;

        for (const agent of agents) {
          const status = agent.status.toLowerCase();
          const prevStatus = this.lastNotifiedState[agent.id];
          
          if (status !== prevStatus) {
            this.lastNotifiedState[agent.id] = status;
            
            // Trigger notification if blocked or idle (and it wasn't just starting up)
            if ((status === 'blocked' || status === 'idle') && prevStatus !== undefined) {
              await this.sendLocalNotification(
                `Firstmate needs your attention!`,
                `Agent ${agent.name} is now ${status.toUpperCase()}.`
              );
            }
          }
        }
      } catch (e) {
        console.error('Notification monitoring error:', e);
      }
    }, 10000); // Check every 10 seconds
  }

  async sendLocalNotification(title: string, body: string) {
    if (this.appState === 'active') return; // Don't annoy if they are already in the app
    
    await Notifications.scheduleNotificationAsync({
      content: {
        title,
        body,
        sound: true,
      },
      trigger: null, // trigger immediately
    });
  }
}

export const notificationManager = new NotificationManagerService();
