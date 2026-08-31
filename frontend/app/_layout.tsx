import { Stack, usePathname, useRouter } from 'expo-router';
import Head from 'expo-router/head';
import * as Linking from 'expo-linking';
import React, { useEffect, useState } from 'react';
import { Platform, Text, TextInput, TouchableOpacity, View, StyleSheet } from 'react-native';
import { notificationManager } from '../src/services/NotificationManager';
import { InAppNotificationStack } from '../src/components/InAppNotificationStack';
import { NotificationPermissionPrompt } from '../src/components/NotificationPermissionPrompt';
import { ErrorBoundary } from '../src/components/ErrorBoundary';
import {
  createGatewaySession,
  invalidateGatewaySession,
  restoreGatewaySession,
  useGatewaySession,
  validateGatewaySession,
} from '../src/api/client';
import {
  consumePendingIntent,
  enqueuePendingIntent,
  pendingIntentPath,
  usePendingIntent,
} from '../src/services/PendingIntentRouter';

export default function RootLayout() {
  const pathname = usePathname();
  const router = useRouter();
  const session = useGatewaySession();
  const pendingIntent = usePendingIntent();
  const [bootstrapSecret, setBootstrapSecret] = useState('');
  const [sessionError, setSessionError] = useState('');
  const [sessionSubmitting, setSessionSubmitting] = useState(false);

  useEffect(() => {
    notificationManager.installNotificationRouting();
    void restoreGatewaySession();
  }, []);

  useEffect(() => {
    // Capture URL launches before auth validation. This covers terminated and
    // unauthenticated launches without mounting a protected destination early.
    void Linking.getInitialURL().then(url => enqueuePendingIntent(url)).catch(() => undefined);
    const subscription = Linking.addEventListener('url', event => enqueuePendingIntent(event.url));
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (session.status !== 'authenticated' || !pendingIntent) return;
    const intent = consumePendingIntent();
    if (intent) router.push(pendingIntentPath(intent) as never);
  }, [pendingIntent, router, session.status]);

  useEffect(() => {
    if (session.status === 'authenticated' && Platform.OS === 'web') {
      (document.activeElement as HTMLElement | null)?.blur();
      window.requestAnimationFrame(() => window.scrollTo(0, 0));
    }
  }, [session.status]);

  useEffect(() => {
    // Nothing protected is mounted until the session has been validated. This
    // effect therefore also provides the single cleanup boundary for polling.
    if (session.status !== 'authenticated') return;
    // Voice has its own permission-sensitive lifecycle and deliberately does
    // not poll attention events while the microphone screen is active.
    if (pathname === '/voice') return;
    notificationManager.startMonitoring();
    return () => notificationManager.stopMonitoring();
  }, [pathname, session.status]);

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

  const submitSession = async () => {
    setSessionSubmitting(true);
    setSessionError('');
    try {
      await createGatewaySession(bootstrapSecret);
      // Issuance alone is not an authenticated app state. The protected
      // validation call is the transition that permits route mounting.
      await validateGatewaySession();
      if (Platform.OS === 'web') window.scrollTo(0, 0);
      setBootstrapSecret('');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Session could not be validated.';
      setSessionError(message);
      await invalidateGatewaySession(message);
    } finally {
      setSessionSubmitting(false);
    }
  };

  const viewportHead = Platform.OS === 'web' ? <Head><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" /></Head> : null;

  if (session.status !== 'authenticated') {
    const checking = session.status === 'checking';
    const error = sessionError || session.error;
    return (
      <>
        {viewportHead}
        <View style={styles.sessionOverlay} accessibilityViewIsModal>
          <View style={styles.sessionCard}>
          <Text testID="session-status" style={styles.sessionTitle}>
            {checking ? 'CHECKING MAGISTRATE SESSION' : 'MAGISTRATE SESSION REQUIRED'}
          </Text>
          <Text style={styles.sessionCopy}>
            {checking
              ? 'Validating the saved server session. Protected routes stay closed until validation succeeds.'
              : 'Enter the beta bootstrap credential supplied by the deployment operator. It is never stored in the app bundle.'}
          </Text>
          {!checking ? <>
            <TextInput
              testID="bootstrap-secret"
              value={bootstrapSecret}
              onChangeText={setBootstrapSecret}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="Bootstrap credential"
              placeholderTextColor="#899"
              style={styles.sessionInput}
            />
            {error ? <Text testID="session-error" style={styles.sessionError}>{error}</Text> : null}
            <TouchableOpacity
              testID="connect-session"
              disabled={sessionSubmitting || !bootstrapSecret}
              onPress={() => void submitSession()}
              style={[styles.sessionButton, (sessionSubmitting || !bootstrapSecret) && styles.sessionButtonDisabled]}
            >
              <Text style={styles.sessionButtonText}>{sessionSubmitting ? 'VALIDATING…' : 'CONNECT SECURELY'}</Text>
            </TouchableOpacity>
            {session.error && !sessionError ? <TouchableOpacity testID="retry-session" onPress={() => { setSessionError(''); void restoreGatewaySession(); }} style={styles.retryButton}><Text style={styles.retryText}>RETRY VALIDATION</Text></TouchableOpacity> : null}
          </> : null}
          </View>
        </View>
      </>
    );
  }

  return (
    <>
      {viewportHead}
      <View style={styles.appRoot}>
      <ErrorBoundary>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(tabs)" />
        </Stack>
        <InAppNotificationStack />
        <NotificationPermissionPrompt />
        </ErrorBoundary>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  appRoot: { flex: 1, minHeight: '100vh' as any, width: '100%' },
  sessionOverlay: { ...StyleSheet.absoluteFill, zIndex: 20, backgroundColor: '#101820', justifyContent: 'center', alignItems: 'center', padding: 24 },
  sessionCard: { width: '100%', maxWidth: 420, padding: 24, borderRadius: 18, backgroundColor: '#1c2933' },
  sessionTitle: { color: '#fff', fontFamily: 'monospace', fontWeight: '700', letterSpacing: 1, marginBottom: 12 },
  sessionCopy: { color: '#b5c1c8', lineHeight: 20, marginBottom: 18 },
  sessionInput: { color: '#fff', borderWidth: 1, borderColor: '#6d8490', borderRadius: 10, padding: 12, marginBottom: 10, fontSize: 16 },
  sessionError: { color: '#ffaaa5', marginBottom: 10 },
  sessionButton: { padding: 13, borderRadius: 10, backgroundColor: '#fff', alignItems: 'center' },
  sessionButtonDisabled: { opacity: 0.55 },
  sessionButtonText: { color: '#101820', fontFamily: 'monospace', fontWeight: '700' },
  retryButton: { padding: 12, alignItems: 'center' },
  retryText: { color: '#8edfff', fontFamily: 'monospace', fontSize: 11, fontWeight: '700' },
});
