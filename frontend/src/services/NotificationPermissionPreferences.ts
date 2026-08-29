import AsyncStorage from '@react-native-async-storage/async-storage';

export const WEB_NOTIFICATION_PROMPT_ASKED_KEY = 'magistrate.notifications.web-permission-asked';

export async function hasAskedWebNotificationPermission(): Promise<boolean> {
  return (await AsyncStorage.getItem(WEB_NOTIFICATION_PROMPT_ASKED_KEY)) === 'true';
}

export async function markWebNotificationPermissionAsked(): Promise<void> {
  await AsyncStorage.setItem(WEB_NOTIFICATION_PROMPT_ASKED_KEY, 'true');
}
