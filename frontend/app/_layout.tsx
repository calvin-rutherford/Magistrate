import { Stack } from 'expo-router';
import React, { useEffect } from 'react';
import { notificationManager } from '../src/services/NotificationManager';
import { InAppNotificationStack } from '../src/components/InAppNotificationStack';

import { ErrorBoundary } from '../src/components/ErrorBoundary';

export default function RootLayout() {
  useEffect(() => {
    notificationManager.startMonitoring();
    return () => notificationManager.stopMonitoring();
  }, []);

  return (
    <ErrorBoundary>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
      </Stack>
      <InAppNotificationStack />
    </ErrorBoundary>
  );
}
