import { Stack } from 'expo-router';
import React, { useEffect } from 'react';
import { notificationManager } from '../src/services/NotificationManager';
import { InAppNotificationStack } from '../src/components/InAppNotificationStack';
import { NotificationPermissionPrompt } from '../src/components/NotificationPermissionPrompt';

import { ErrorBoundary } from '../src/components/ErrorBoundary';
import { usePathname } from 'expo-router';

export default function RootLayout() {
  const pathname = usePathname();
  useEffect(() => {
    // Voice has its own permission-sensitive lifecycle and deliberately does
    // not poll attention events while the microphone screen is active.
    if (pathname === '/voice') return;
    notificationManager.startMonitoring();
    return () => notificationManager.stopMonitoring();
  }, [pathname]);

  return (
    <ErrorBoundary>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
      </Stack>
      <InAppNotificationStack />
      <NotificationPermissionPrompt />
    </ErrorBoundary>
  );
}
