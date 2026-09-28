import '../src/global.css';
import { Stack, usePathname, useRouter } from 'expo-router';
import Head from 'expo-router/head';
import * as Linking from 'expo-linking';
import * as AppleAuthentication from 'expo-apple-authentication';
import * as WebBrowser from 'expo-web-browser';
import React, { useCallback, useEffect, useState } from 'react';
import { Platform, Text, TextInput, TouchableOpacity, View, StyleSheet } from 'react-native';
import { notificationManager } from '../src/services/NotificationManager';
import { NotificationPermissionPrompt } from '../src/components/NotificationPermissionPrompt';
import { ErrorBoundary } from '../src/components/ErrorBoundary';
import {
  acknowledgeAccountWelcome,
  AccountOnboardingState,
  connectAuthProvider,
  createBillingCheckout,
  createGatewaySession,
  fetchAccountOnboarding,
  fetchProviderAuthConfiguration,
  invalidateGatewaySession,
  logoutGatewaySession,
  restoreGatewaySession,
  updateUserProfile,
  useGatewaySession,
  validateGatewaySession,
  ProviderAuthConfiguration,
} from '../src/api/client';
import {
  consumePendingIntent,
  enqueuePendingIntent,
  pendingIntentPath,
  usePendingIntent,
} from '../src/services/PendingIntentRouter';
import {
  prepareProviderSignIn, providerSignInAvailable, signInWithProvider,
} from '../src/services/ProviderSignIn';

export default function RootLayout() {
  const pathname = usePathname();
  const router = useRouter();
  const session = useGatewaySession();
  const pendingIntent = usePendingIntent();
  const [friendBetaCode, setFriendBetaCode] = useState('');
  const [sessionError, setSessionError] = useState('');
  const [sessionSubmitting, setSessionSubmitting] = useState(false);
  const [providerSubmitting, setProviderSubmitting] = useState<'apple' | 'google' | null>(null);
  const [providerConfiguration, setProviderConfiguration] = useState<ProviderAuthConfiguration | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [onboarding, setOnboarding] = useState<AccountOnboardingState | null>(null);
  const [onboardingError, setOnboardingError] = useState('');
  const [onboardingSubmitting, setOnboardingSubmitting] = useState(false);

  useEffect(() => {
    let mounted = true;
    notificationManager.installNotificationRouting();
    void restoreGatewaySession();
    void fetchProviderAuthConfiguration()
      .then(value => { if (mounted) setProviderConfiguration(value); })
      .catch(() => { if (mounted) setProviderConfiguration(null); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (session.status !== 'authentication-required' || !providerConfiguration) return;
    if (providerConfiguration.apple && (
      (Platform.OS === 'web' && providerConfiguration.apple_web)
      || (Platform.OS === 'ios' && providerConfiguration.apple_native)
    )) prepareProviderSignIn('apple');
    if (providerConfiguration.google && (
      (Platform.OS === 'web' && providerConfiguration.google_web)
      || (Platform.OS === 'ios' && providerConfiguration.google_native)
    )) prepareProviderSignIn('google');
  }, [providerConfiguration, session.status]);

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
    if (!/^mgb_[A-Za-z0-9_-]{32,64}$/.test(friendBetaCode)) {
      setSessionError('Enter the Friend Beta access code from your invitation.');
      return;
    }
    setSessionSubmitting(true);
    setSessionError('');
    try {
      await createGatewaySession(friendBetaCode);
      // Issuance alone is not an authenticated app state. The protected
      // validation call is the transition that permits route mounting.
      await validateGatewaySession();
      if (Platform.OS === 'web') window.scrollTo(0, 0);
      setFriendBetaCode('');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Session could not be validated.';
      setSessionError(message);
      await invalidateGatewaySession(message);
    } finally {
      setSessionSubmitting(false);
    }
  };

  const submitProvider = async (provider: 'apple' | 'google') => {
    setProviderSubmitting(provider);
    setSessionError('');
    try {
      await signInWithProvider(provider);
      if (Platform.OS === 'web') window.scrollTo(0, 0);
    } catch (error) {
      const value = error as { code?: string };
      if (value?.code !== 'ERR_REQUEST_CANCELED') {
        setSessionError(error instanceof Error ? error.message : `${provider} sign-in could not be completed.`);
      }
    } finally {
      setProviderSubmitting(null);
    }
  };

  const refreshOnboarding = useCallback(async () => {
    const value = await fetchAccountOnboarding();
    setOnboarding(value);
    if (!value.required) await validateGatewaySession();
    return value;
  }, []);

  useEffect(() => {
    if (session.status !== 'authenticated' || !session.session?.onboardingRequired) return;
    void Promise.resolve().then(refreshOnboarding).catch(error => setOnboardingError(
      error instanceof Error ? error.message : 'Onboarding state could not be loaded.',
    ));
  }, [refreshOnboarding, session.session?.onboardingRequired, session.status]);

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
      await refreshOnboarding();
      setDisplayName('');
    } catch (error) {
      setOnboardingError(error instanceof Error ? error.message : 'Your account profile could not be saved.');
    } finally {
      setOnboardingSubmitting(false);
    }
  };

  const acknowledgeWelcome = async () => {
    setOnboardingSubmitting(true); setOnboardingError('');
    try { setOnboarding(await acknowledgeAccountWelcome()); }
    catch (error) { setOnboardingError(error instanceof Error ? error.message : 'Welcome could not be completed.'); }
    finally { setOnboardingSubmitting(false); }
  };

  const connectGitHub = async () => {
    setOnboardingSubmitting(true); setOnboardingError('');
    const returnUrl = Linking.createURL('/');
    try {
      const connection = await connectAuthProvider('github', returnUrl);
      const result = await WebBrowser.openAuthSessionAsync(connection.auth_url, returnUrl);
      if (result.type !== 'success') throw new Error('GitHub authorization was not completed.');
      if (/[?&]error=/.test(result.url || '')) throw new Error('GitHub did not authorize this account.');
      await refreshOnboarding();
    } catch (error) { setOnboardingError(error instanceof Error ? error.message : 'GitHub could not be connected.'); }
    finally { setOnboardingSubmitting(false); }
  };

  const startBilling = async () => {
    setOnboardingSubmitting(true); setOnboardingError('');
    try {
      const checkout = await createBillingCheckout();
      const result = await WebBrowser.openAuthSessionAsync(checkout.url, Linking.createURL('/'));
      if (result.type === 'cancel' || result.type === 'dismiss') throw new Error('Billing setup was not completed.');
      const value = await refreshOnboarding();
      if (!value.steps.billing.complete) throw new Error('Payment is still awaiting verified subscription confirmation. Try again shortly.');
    } catch (error) { setOnboardingError(error instanceof Error ? error.message : 'Billing setup could not be completed.'); }
    finally { setOnboardingSubmitting(false); }
  };

  const viewportHead = Platform.OS === 'web' ? <Head><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content" /></Head> : null;
  const appleSignInEnabled = Boolean(
    providerConfiguration?.apple
    && ((Platform.OS === 'web' && providerConfiguration.apple_web)
      || (Platform.OS === 'ios' && providerConfiguration.apple_native))
    && providerSignInAvailable('apple'),
  );
  const googleSignInEnabled = Boolean(
    providerConfiguration?.google
    && ((Platform.OS === 'web' && providerConfiguration.google_web)
      || (Platform.OS === 'ios' && providerConfiguration.google_native))
    && providerSignInAvailable('google'),
  );

  if (session.status === 'authenticated' && session.session?.onboardingRequired) {
    const step = onboarding?.next_step;
    return (
      <>
        {viewportHead}
        <View style={styles.sessionOverlay} accessibilityViewIsModal>
          <View style={styles.sessionCard}>
            <Text testID="onboarding-title" style={styles.sessionTitle}>{step === 'welcome' ? 'WELCOME TO MAGISTRATE' : step === 'profile' ? 'MEET MAGI' : step === 'github' ? 'CONNECT GITHUB' : step === 'billing' ? 'START YOUR PLAN' : 'PREPARING YOUR ACCOUNT'}</Text>
            <Text style={styles.onboardingProgress}>{step ? `${['welcome', 'profile', 'github', 'billing'].indexOf(step) + 1} OF 4` : 'CHECKING'}</Text>
            <Text style={styles.sessionCopy}>{step === 'welcome' ? 'Magi turns your goals into governed engineering work, keeps decisions visible, and brings you back only when judgment is needed.' : step === 'profile' ? 'Choose the name Magi should use when working with you.' : step === 'github' ? 'Connect GitHub to establish the verified integration required for your Magistrate account.' : step === 'billing' ? 'Activate your subscription. Access opens only after Magistrate receives signed confirmation from the billing provider.' : 'Reading verified account state…'}</Text>
            {step === 'profile' ? <TextInput testID="onboarding-display-name" value={displayName} onChangeText={setDisplayName} autoCapitalize="words" autoCorrect={false} maxLength={80} placeholder="Display name" placeholderTextColor="#899" style={styles.sessionInput} onSubmitEditing={() => void submitProfile()} /> : null}
            {step === 'github' && onboarding && !onboarding.steps.github.available ? <Text testID="onboarding-github-unavailable" style={styles.sessionError}>{onboarding.steps.github.unavailable_reason || 'GitHub is not configured for this deployment.'}</Text> : null}
            {step === 'billing' && onboarding && !onboarding.steps.billing.available ? <Text testID="onboarding-billing-unavailable" style={styles.sessionError}>Billing is not configured for this deployment. An operator must activate the documented Stripe configuration.</Text> : null}
            {onboardingError ? <Text testID="onboarding-error" accessibilityRole="alert" style={styles.sessionError}>{onboardingError}</Text> : null}
            {step ? <TouchableOpacity
              testID={`onboarding-${step}-continue`}
              disabled={onboardingSubmitting || (step === 'profile' && !displayName.trim()) || (step === 'github' && !onboarding?.steps.github.available) || (step === 'billing' && !onboarding?.steps.billing.available)}
              onPress={() => void (step === 'welcome' ? acknowledgeWelcome() : step === 'profile' ? submitProfile() : step === 'github' ? connectGitHub() : startBilling())}
              style={[styles.sessionButton, (onboardingSubmitting || (step === 'profile' && !displayName.trim()) || (step === 'github' && !onboarding?.steps.github.available) || (step === 'billing' && !onboarding?.steps.billing.available)) && styles.sessionButtonDisabled]}
            ><Text style={styles.sessionButtonText}>{onboardingSubmitting ? 'WORKING…' : step === 'welcome' ? 'GET STARTED' : step === 'profile' ? 'CONTINUE' : step === 'github' ? 'CONNECT GITHUB' : 'CONTINUE TO SECURE CHECKOUT'}</Text></TouchableOpacity> : null}
            <TouchableOpacity testID="onboarding-use-different-account" disabled={onboardingSubmitting} onPress={() => void logoutGatewaySession()} style={styles.retryButton}><Text style={styles.retryText}>USE A DIFFERENT ACCOUNT</Text></TouchableOpacity>
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
              ? 'Validating your saved session. Protected routes stay closed until validation succeeds.'
              : 'Sign in to continue your Magi conversation and work across this device.'}
          </Text>
          {!checking ? <>
            <View style={styles.providerButtons}>
              {Platform.OS === 'ios' && appleSignInEnabled ? <AppleAuthentication.AppleAuthenticationButton
                testID="sign-in-apple"
                buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN}
                buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.WHITE}
                cornerRadius={10}
                style={styles.appleButton}
                onPress={() => void submitProvider('apple')}
              /> : appleSignInEnabled ? <TouchableOpacity
                testID="sign-in-apple" accessibilityRole="button"
                disabled={providerSubmitting !== null}
                onPress={() => void submitProvider('apple')}
                style={[styles.providerButton, providerSubmitting && styles.sessionButtonDisabled]}
              ><Text style={styles.providerButtonText}>{providerSubmitting === 'apple' ? 'CONNECTING…' : 'CONTINUE WITH APPLE'}</Text></TouchableOpacity> : null}
              {googleSignInEnabled ? <TouchableOpacity
                testID="sign-in-google" accessibilityRole="button"
                disabled={providerSubmitting !== null}
                onPress={() => void submitProvider('google')}
                style={[styles.providerButton, providerSubmitting && styles.sessionButtonDisabled]}
              ><Text style={styles.providerButtonText}>{providerSubmitting === 'google' ? 'CONNECTING…' : 'CONTINUE WITH GOOGLE'}</Text></TouchableOpacity> : null}
            </View>
            <View style={styles.accessDivider}><View style={styles.accessDividerLine} /><Text style={styles.accessDividerText}>FRIEND BETA INVITATION</Text><View style={styles.accessDividerLine} /></View>
            <TextInput
              testID="friend-beta-access-code"
              value={friendBetaCode}
              onChangeText={setFriendBetaCode}
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
              testID="redeem-friend-beta"
              disabled={sessionSubmitting || providerSubmitting !== null || !friendBetaCode}
              onPress={() => void submitSession()}
              style={[styles.sessionButton, (sessionSubmitting || providerSubmitting !== null || !friendBetaCode) && styles.sessionButtonDisabled]}
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
  onboardingProgress: { color: '#8edfff', fontFamily: 'monospace', fontSize: 10, fontWeight: '700', letterSpacing: 1, marginBottom: 10 },
  sessionInput: { color: '#fff', borderWidth: 1, borderColor: '#6d8490', borderRadius: 10, padding: 12, marginBottom: 10, fontSize: 16 },
  sessionError: { color: '#ffaaa5', marginBottom: 10 },
  providerButtons: { gap: 10, marginBottom: 16 },
  providerButton: { minHeight: 46, borderRadius: 10, borderWidth: 1, borderColor: '#dce3e7', alignItems: 'center', justifyContent: 'center', backgroundColor: '#fff' },
  providerButtonText: { color: '#101820', fontFamily: 'monospace', fontSize: 12, fontWeight: '700' },
  appleButton: { width: '100%', height: 46 },
  accessDivider: { flexDirection: 'row', alignItems: 'center', gap: 9, marginBottom: 14 },
  accessDividerLine: { flex: 1, height: StyleSheet.hairlineWidth, backgroundColor: '#6d8490' },
  accessDividerText: { color: '#8999a3', fontFamily: 'monospace', fontSize: 9, fontWeight: '700', letterSpacing: 0.6 },
  sessionButton: { padding: 13, borderRadius: 10, backgroundColor: '#fff', alignItems: 'center' },
  sessionButtonDisabled: { opacity: 0.55 },
  sessionButtonText: { color: '#101820', fontFamily: 'monospace', fontWeight: '700' },
  retryButton: { padding: 12, alignItems: 'center' },
  retryText: { color: '#8edfff', fontFamily: 'monospace', fontSize: 11, fontWeight: '700' },
});
