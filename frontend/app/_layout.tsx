import { Stack } from 'expo-router';
import React, { useEffect } from 'react';
import { notificationManager } from '../src/services/NotificationManager';

import { ErrorBoundary } from '../src/components/ErrorBoundary';

export default function RootLayout() {
  useEffect(() => {
    notificationManager.requestPermissions().then(() => {
      notificationManager.startMonitoring();
    });
  }, []);

  return (
    <ErrorBoundary>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
      </Stack>
    </ErrorBoundary>
  );
}
