import { Stack } from 'expo-router';
import React, { useEffect } from 'react';
import { Platform, Text, TextInput, TouchableOpacity, View, StyleSheet } from 'react-native';
import { notificationManager } from '../src/services/NotificationManager';
import { InAppNotificationStack } from '../src/components/InAppNotificationStack';
import { NotificationPermissionPrompt } from '../src/components/NotificationPermissionPrompt';

import { ErrorBoundary } from '../src/components/ErrorBoundary';
import { usePathname } from 'expo-router';
import { createGatewaySession, getGatewaySessionToken } from '../src/api/client';

export default function RootLayout() {
  const pathname = usePathname();
  const [sessionRequired, setSessionRequired] = React.useState(false);
  const [bootstrapSecret, setBootstrapSecret] = React.useState('');
  const [sessionError, setSessionError] = React.useState('');
  const [sessionSubmitting, setSessionSubmitting] = React.useState(false);
  useEffect(() => {
    // Voice has its own permission-sensitive lifecycle and deliberately does
    // not poll attention events while the microphone screen is active.
    if (pathname === '/voice') return;
    notificationManager.startMonitoring();
    return () => notificationManager.stopMonitoring();
  }, [pathname]);
  useEffect(() => {
    getGatewaySessionToken().then(token => {
      const localBrowser = typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname);
      if (!token && !localBrowser) setSessionRequired(true);
    });
  }, []);
  const submitSession = async () => {
    setSessionSubmitting(true); setSessionError('');
    try { await createGatewaySession(bootstrapSecret); setSessionRequired(false); setBootstrapSecret(''); }
    catch (error) { setSessionError(error instanceof Error ? error.message : 'Session could not be created.'); }
    finally { setSessionSubmitting(false); }
  };
  useEffect(() => {
    // Without this, a horizontal right-swipe near the left edge (e.g. to open
    // the chat drawer) is captured by the browser's touch-based back-navigation
    // gesture instead of reaching our gesture handlers.
    if (Platform.OS !== 'web') return;
    const html = document.documentElement;
    const previous = html.style.overscrollBehaviorX;
    html.style.overscrollBehaviorX = 'none';
    return () => { html.style.overscrollBehaviorX = previous; };
  }, []);

  return (
    <ErrorBoundary>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
      </Stack>
      <InAppNotificationStack />
      <NotificationPermissionPrompt />
      {sessionRequired ? <View style={styles.sessionOverlay} accessibilityViewIsModal>
        <View style={styles.sessionCard}>
          <Text style={styles.sessionTitle}>MAGISTRATE SESSION REQUIRED</Text>
          <Text style={styles.sessionCopy}>Enter the beta bootstrap credential supplied by the deployment operator. It is never stored in the app bundle.</Text>
          <TextInput value={bootstrapSecret} onChangeText={setBootstrapSecret} secureTextEntry autoCapitalize="none" autoCorrect={false} placeholder="Bootstrap credential" placeholderTextColor="#899" style={styles.sessionInput} />
          {sessionError ? <Text style={styles.sessionError}>{sessionError}</Text> : null}
          <TouchableOpacity disabled={sessionSubmitting || !bootstrapSecret} onPress={submitSession} style={styles.sessionButton}>
            <Text style={styles.sessionButtonText}>{sessionSubmitting ? 'CONNECTING…' : 'CONNECT SECURELY'}</Text>
          </TouchableOpacity>
        </View>
      </View> : null}
    </ErrorBoundary>
  );
}

const styles = StyleSheet.create({
  sessionOverlay: { ...StyleSheet.absoluteFill, zIndex: 20, backgroundColor: '#101820', justifyContent: 'center', alignItems: 'center', padding: 24 },
  sessionCard: { width: '100%', maxWidth: 420, padding: 24, borderRadius: 18, backgroundColor: '#1c2933' },
  sessionTitle: { color: '#fff', fontFamily: 'monospace', fontWeight: '700', letterSpacing: 1, marginBottom: 12 },
  sessionCopy: { color: '#b5c1c8', lineHeight: 20, marginBottom: 18 },
  sessionInput: { color: '#fff', borderWidth: 1, borderColor: '#6d8490', borderRadius: 10, padding: 12, marginBottom: 10 },
  sessionError: { color: '#ffaaa5', marginBottom: 10 },
  sessionButton: { padding: 13, borderRadius: 10, backgroundColor: '#fff', alignItems: 'center' },
  sessionButtonText: { color: '#101820', fontFamily: 'monospace', fontWeight: '700' },
});
