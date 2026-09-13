import '../src/global.css';
import { Stack, usePathname, useRouter } from 'expo-router';
import Head from 'expo-router/head';
import * as Linking from 'expo-linking';
import React, { useEffect, useState } from 'react';
import { Platform, Text, TextInput, TouchableOpacity, View, StyleSheet } from 'react-native';
import { notificationManager } from '../src/services/NotificationManager';
import { NotificationPermissionPrompt } from '../src/components/NotificationPermissionPrompt';
import { ErrorBoundary } from '../src/components/ErrorBoundary';
import {
  createGatewaySession,
  invalidateGatewaySession,
  logoutGatewaySession,
  restoreGatewaySession,
  updateUserProfile,
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
  const [displayName, setDisplayName] = useState('');
  const [onboardingError, setOnboardingError] = useState('');
  const [onboardingSubmitting, setOnboardingSubmitting] = useState(false);

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
    if (session.status !== 'authenticated' || session.session?.onboardingRequired || !pendingIntent) return;
    const intent = consumePendingIntent();
    if (!intent) return;
    // A cold push can arrive before authentication, so the initial
    // acknowledgement attempt may have failed. Retry it at the authenticated
    // routing boundary, immediately before opening the detailed destination.
    if (intent.targetType === 'attention') void notificationManager.markViewed(intent.params.item);
    if (intent.targetType === 'pull-request') void notificationManager.markViewed(`github-pr-${intent.params.number}`);
    router.push(pendingIntentPath(intent) as never);
  }, [pendingIntent, router, session.session?.onboardingRequired, session.status]);

  useEffect(() => {
    if (session.status === 'authenticated' && Platform.OS === 'web') {
      (document.activeElement as HTMLElement | null)?.blur();
      window.requestAnimationFrame(() => window.scrollTo(0, 0));
    }
  }, [session.status]);

  useEffect(() => {
    // Nothing protected is mounted until the session has been validated. This
    // effect therefore also provides the single cleanup boundary for polling.
    if (session.status !== 'authenticated' || session.session?.onboardingRequired) return;
    // Voice has its own permission-sensitive lifecycle and deliberately does
    // not poll attention events while the microphone screen is active.
    if (pathname === '/voice') return;
    notificationManager.startMonitoring();
    return () => notificationManager.stopMonitoring();
  }, [pathname, session.session?.onboardingRequired, session.status]);

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

  const submitProfile = async () => {
    const name = displayName.trim();
    if (!name || Array.from(name).length > 80) {
      setOnboardingError('Enter a display name of 80 characters or fewer.');
      return;
    }
    setOnboardingSubmitting(true);
    setOnboardingError('');
    try {
      await updateUserProfile({ name });
      await validateGatewaySession();
      setDisplayName('');
    } catch (error) {
      setOnboardingError(error instanceof Error ? error.message : 'Your account profile could not be saved.');
    } finally {
      setOnboardingSubmitting(false);
    }
  };

  const viewportHead = Platform.OS === 'web' ? <Head><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content" /></Head> : null;

  if (session.status === 'authenticated' && session.session?.onboardingRequired) {
    return (
      <>
        {viewportHead}
        <View style={styles.sessionOverlay} accessibilityViewIsModal>
          <View style={styles.sessionCard}>
            <Text testID="friend-beta-onboarding-title" style={styles.sessionTitle}>WELCOME TO MAGI</Text>
            <Text style={styles.sessionCopy}>Your access code is verified. Choose the name Magi should use for this beta account.</Text>
            <TextInput
              testID="friend-beta-display-name"
              value={displayName}
              onChangeText={setDisplayName}
              autoCapitalize="words"
              autoCorrect={false}
              maxLength={80}
              placeholder="Display name"
              placeholderTextColor="#899"
              style={styles.sessionInput}
              onSubmitEditing={() => void submitProfile()}
            />
            {onboardingError ? <Text testID="friend-beta-onboarding-error" accessibilityRole="alert" style={styles.sessionError}>{onboardingError}</Text> : null}
            <TouchableOpacity
              testID="friend-beta-complete-onboarding"
              disabled={onboardingSubmitting || !displayName.trim()}
              onPress={() => void submitProfile()}
              style={[styles.sessionButton, (onboardingSubmitting || !displayName.trim()) && styles.sessionButtonDisabled]}
            >
              <Text style={styles.sessionButtonText}>{onboardingSubmitting ? 'SAVING…' : 'CONTINUE'}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="friend-beta-cancel-onboarding"
              disabled={onboardingSubmitting}
              onPress={() => void logoutGatewaySession()}
              style={styles.retryButton}
            >
              <Text style={styles.retryText}>USE A DIFFERENT ACCESS CODE</Text>
            </TouchableOpacity>
          </View>
        </View>
      </>
    );
  }

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
              : 'Enter the Friend Beta access code supplied for this device. Deployment operators can also use their owner credential. Neither is stored in the app bundle.'}
          </Text>
          {!checking ? <>
            <TextInput
              testID="bootstrap-secret"
              value={bootstrapSecret}
              onChangeText={setBootstrapSecret}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="Friend Beta access code"
              placeholderTextColor="#899"
              style={styles.sessionInput}
              onSubmitEditing={() => void submitSession()}
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
        <NotificationPermissionPrompt />
        </ErrorBoundary>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  appRoot: { flex: 1, minHeight: 0, width: '100%', overflow: 'hidden' },
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
