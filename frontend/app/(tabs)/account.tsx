import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Image, Alert, ActivityIndicator, Platform, TextInput } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import Constants from 'expo-constants';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { AccountOnboardingState, AuthProviderInfo, BillingAccount, connectAuthProvider, createBillingPortal, deleteGatewayAccount, fetchAccountOnboarding, fetchAuthProviders, fetchBillingAccount, fetchNotificationPreferences, fetchProviderAuthConfiguration, fetchProviderLoginMethods, fetchUserProfile, fetchVoiceInputCapabilities, GATEWAY_URL, logoutGatewaySession, ProviderAuthConfiguration, ProviderLoginMethod, unlinkProviderLoginMethod, updateNotificationPreferences, updateUserProfile, uploadUserAvatar, UserProfile } from '../../src/api/client';
import { linkProviderIdentity, providerSignInAvailable, SignInProvider } from '../../src/services/ProviderSignIn';
import { loadChatPreferences, saveVoiceInputMode } from '../../src/services/ChatPreferences';
import { ttsService } from '../../src/services/TextToSpeechService';
import { useRouter } from 'expo-router';
import { openExternalUrl } from '../../src/utils/externalLinks';
import { capabilityFor, getLocalVoiceCapabilities, VOICE_INPUT_MODE_OPTIONS, VoiceInputCapabilities, VoiceInputMode } from '../../src/services/VoiceInputModes';
import { loadOperatingPermissionMode, saveOperatingPermissionMode, OPERATING_PERMISSION_MODE_OPTIONS, OperatingPermissionMode } from '../../src/services/OperatingPermissionModes';
import { notificationManager, NativePushStatus } from '../../src/services/NotificationManager';

const errorText = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback;

/**
 * The button copy is derived only from the decoded provider state, so a
 * CONNECTED label cannot appear without a gateway-confirmed live credential.
 */
function providerActionLabel(provider: AuthProviderInfo): string {
  if (provider.status === 'connected') return 'CONNECTED ✓';
  if (provider.status === 'expired') return 'RECONNECT';
  if (provider.deferred) return 'DEFERRED';
  if (!provider.available) return 'UNAVAILABLE';
  return 'CONNECT +';
}

type AccountSectionKey = 'sign-in' | 'notifications' | 'voice' | 'connections' | 'appearance';
function AccountSectionHeader({ id, title, expanded, onPress }: { id: AccountSectionKey; title: string; expanded: boolean; onPress: () => void }) {
  return <TouchableOpacity testID={`account-section-${id}`} accessibilityRole="button" accessibilityLabel={`${title} settings`} accessibilityState={{ expanded }} {...({ 'aria-expanded': expanded } as any)} onPress={onPress} style={styles.sectionHeader} activeOpacity={0.75}>
    <Text style={styles.sectionTitle}>{title}</Text><Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.sectionChevron}>{expanded ? '⌄' : '›'}</Text>
  </TouchableOpacity>;
}

export default function AccountScreen() {
  const router = useRouter();

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileName, setProfileName] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [billing, setBilling] = useState<BillingAccount | null>(null);
  const [billingError, setBillingError] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState('');
  const [deletingAccount, setDeletingAccount] = useState(false);

  const [providers, setProviders] = useState<AuthProviderInfo[]>([]);
  const [loginMethods, setLoginMethods] = useState<ProviderLoginMethod[]>([]);
  const [providerConfiguration, setProviderConfiguration] = useState<ProviderAuthConfiguration | null>(null);
  const [onboarding, setOnboarding] = useState<AccountOnboardingState | null>(null);
  const [identityBusy, setIdentityBusy] = useState<SignInProvider | 'billing' | null>(null);
  const [identityNotice, setIdentityNotice] = useState<string | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [providersLoaded, setProvidersLoaded] = useState<boolean>(false);
  const [connectNotice, setConnectNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);

  const [voiceEnabled, setVoiceEnabled] = useState<boolean>(true);
  const [voiceInputMode, setVoiceInputMode] = useState<VoiceInputMode>('automatic');
  const [voiceCapabilities, setVoiceCapabilities] = useState<VoiceInputCapabilities>(() => getLocalVoiceCapabilities());
  const [autoSpeak, setAutoSpeak] = useState<boolean>(true);
  const [autoListen, setAutoListen] = useState<boolean>(true);
  const [attentionNotifications, setAttentionNotifications] = useState<boolean>(true);
  const [quietHours, setQuietHours] = useState<boolean>(true);
  const [operatingPermissionMode, setOperatingPermissionMode] = useState<OperatingPermissionMode>('moderate');
  const [nativePushStatus, setNativePushStatus] = useState<NativePushStatus>(() => notificationManager.getPushStatus());
  const [expandedSection, setExpandedSection] = useState<AccountSectionKey | null>(null);
  const toggleSection = (section: AccountSectionKey) => setExpandedSection(current => current === section ? null : section);

  const loadAccountData = async () => {
    // Each source is settled independently and its failure is shown, not
    // swallowed: an unreachable provider list must read as unavailable rather
    // than as an account with no integrations.
    const [profileResult, providerResult, methodsResult, configurationResult, onboardingResult, billingResult] = await Promise.allSettled([
      fetchUserProfile(), fetchAuthProviders(), fetchProviderLoginMethods(),
      fetchProviderAuthConfiguration(), fetchAccountOnboarding(), fetchBillingAccount(),
    ]);
    if (profileResult.status === 'fulfilled') {
      const prof = profileResult.value;
      if (prof.avatar_url && prof.avatar_url.startsWith('/uploads')) {
        prof.avatar_url = GATEWAY_URL.replace(/\/api\/v1$/, '') + prof.avatar_url;
      }
      // The locally persisted appearance is authoritative. The profile
      // value is legacy metadata and must not overwrite an explicit device
      // choice during hydration.
      setProfile(prof);
      setProfileName(prof.name || '');
      setProfileError(null);
    } else {
      setProfileError(errorText(profileResult.reason, 'Account profile could not be loaded.'));
    }
    if (providerResult.status === 'fulfilled') {
      setProviders(providerResult.value);
      setProvidersError(null);
    } else {
      // Never keep showing a previous list beside a failed refresh: a stale
      // CONNECTED row is exactly the fake state this screen must not render.
      setProviders([]);
      setProvidersError(errorText(providerResult.reason, 'Connected accounts could not be loaded.'));
    }
    if (methodsResult.status === 'fulfilled') setLoginMethods(methodsResult.value);
    if (configurationResult.status === 'fulfilled') setProviderConfiguration(configurationResult.value);
    if (onboardingResult.status === 'fulfilled') setOnboarding(onboardingResult.value);
    if (billingResult.status === 'fulfilled') { setBilling(billingResult.value); setBillingError(null); }
    else { setBilling(null); setBillingError(errorText(billingResult.reason, 'Plan, credits, and billing could not be loaded.')); }
    setProvidersLoaded(true);
  };

  useEffect(() => {
    const unsubscribePushStatus = notificationManager.subscribePushStatus(setNativePushStatus);
    Promise.allSettled([loadChatPreferences(), fetchVoiceInputCapabilities(), fetchNotificationPreferences(), loadOperatingPermissionMode()]).then(([preferencesResult, capabilityResult, notificationResult, localModeResult]) => {
      if (preferencesResult.status === 'fulfilled') {
        setVoiceInputMode(preferencesResult.value.voiceInputMode);
      }
      if (capabilityResult.status === 'fulfilled') {
        const local = getLocalVoiceCapabilities(capabilityResult.value.serverConfigured);
        const serverOpenai = capabilityFor(capabilityResult.value, 'openai');
        setVoiceCapabilities({ ...local, serverProvider: capabilityResult.value.serverProvider, serverConfigured: capabilityResult.value.serverConfigured, modes: local.modes.map(item => item.id === 'openai' ? serverOpenai : item) });
      }
      if (notificationResult.status === 'fulfilled') {
        setAttentionNotifications(notificationResult.value.enabled);
        setQuietHours(notificationResult.value.quiet_start !== null && notificationResult.value.quiet_end !== null);
        setOperatingPermissionMode(notificationResult.value.mode);
      } else if (localModeResult.status === 'fulfilled') {
        setOperatingPermissionMode(localModeResult.value);
      }
    });
    loadAccountData();
    return unsubscribePushStatus;
  }, []);

  const handlePickAvatar = async () => {
    const permResult = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permResult.granted) {
      Alert.alert('Permission Required', 'Media library access is needed to upload a profile photo.');
      return;
    }

    const pickerResult = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: true,
      aspect: [1, 1],
      quality: 0.85
    });

    if (!pickerResult.canceled && pickerResult.assets && pickerResult.assets.length > 0) {
      const selectedUri = pickerResult.assets[0].uri;
      setUploading(true);
      try {
        const res = await uploadUserAvatar(selectedUri);
        if (res.avatar_url) {
          let fullUrl = res.avatar_url;
          if (fullUrl.startsWith('/uploads')) {
            fullUrl = GATEWAY_URL.replace(/\/api\/v1$/, '') + fullUrl;
          }
          setProfile(prev => prev ? { ...prev, avatar_url: fullUrl } : prev);
        }
      } catch (e) {
        Alert.alert('Upload Error', errorText(e, 'Failed to upload profile photo to server.'));
      } finally {
        setUploading(false);
      }
    }
  };

  const saveProfileName = async () => {
    const name = profileName.trim();
    if (!name) { Alert.alert('Name required', 'Enter the name Magi should use.'); return; }
    setSavingProfile(true);
    try { const updated = await updateUserProfile({ name }); setProfile(updated); setProfileName(updated.name); }
    catch (error) { Alert.alert('Profile not saved', errorText(error, 'Your profile could not be updated.')); }
    finally { setSavingProfile(false); }
  };

  const openAccountLink = async (url: string) => {
    const result = await openExternalUrl(url);
    if (!result.ok) Alert.alert('Unable to open link', result.message);
  };

  const requestAccountDeletion = () => {
    if (!profile || deleteConfirmation !== `DELETE ${profile.user_id}`) {
      Alert.alert('Confirmation required', `Type DELETE ${profile?.user_id || 'your account ID'} exactly before deleting this account.`);
      return;
    }
    Alert.alert('Permanently delete account?', 'Projects, messages, uploads, sessions, and account data will be erased. This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete account', style: 'destructive', onPress: () => {
        setDeletingAccount(true);
        void deleteGatewayAccount(deleteConfirmation).catch(error => Alert.alert('Account not deleted', errorText(error, 'Your data was preserved. Try again later.'))).finally(() => setDeletingAccount(false));
      } },
    ]);
  };

  // REAL OAUTH BROWSER AUTHENTICATION FLOW WITH AUTO DISMISSAL
  const handleRealOAuthConnect = async (providerInfo: AuthProviderInfo) => {
    if (!providerInfo.available) {
      setConnectNotice(providerInfo.unavailable_reason || `${providerInfo.provider.toUpperCase()} is unavailable on this gateway.`);
      return;
    }
    const returnUrl = Linking.createURL('/account');
    setConnectNotice(null);

    try {
      const connect = await connectAuthProvider(providerInfo.provider, returnUrl);
      const result = await WebBrowser.openAuthSessionAsync(connect.auth_url, returnUrl);
      // The browser dismisses itself when the returnUrl is hit. The provider
      // state is then re-read from the gateway: a completed browser round trip
      // is not by itself evidence that a credential was stored.
      if (result.type === 'success') {
        const callbackError = /[?&]error=([^&]+)/.exec(result.url || '')?.[1];
        if (callbackError) setConnectNotice(`${providerInfo.provider.toUpperCase()} did not authorize this device (${decodeURIComponent(callbackError)}).`);
        await loadAccountData();
      } else {
        WebBrowser.dismissBrowser();
        setConnectNotice(`${providerInfo.provider.toUpperCase()} authorization was not completed.`);
      }
    } catch (e) {
      WebBrowser.dismissBrowser();
      setConnectNotice(errorText(e, `${providerInfo.provider.toUpperCase()} could not be connected.`));
    }
  };

  const providerEnabled = (provider: SignInProvider) => Boolean(
    providerConfiguration?.[provider]
    && (providerSignInAvailable(provider))
    && (provider === 'apple'
      ? (Platform.OS === 'web' ? providerConfiguration.apple_web : providerConfiguration.apple_native)
      : (Platform.OS === 'web' ? providerConfiguration.google_web : providerConfiguration.google_native)),
  );

  const linkLoginMethod = async (provider: SignInProvider) => {
    setIdentityBusy(provider); setIdentityNotice(null);
    try {
      await linkProviderIdentity(provider);
      await loadAccountData();
      setIdentityNotice(`${provider === 'apple' ? 'Apple' : 'Google'} is now a recovery sign-in method.`);
    } catch (error) { setIdentityNotice(errorText(error, 'The sign-in method could not be linked.')); }
    finally { setIdentityBusy(null); }
  };

  const unlinkLoginMethod = async (provider: SignInProvider) => {
    setIdentityBusy(provider); setIdentityNotice(null);
    try {
      await unlinkProviderLoginMethod(provider);
      await loadAccountData();
      setIdentityNotice(`${provider === 'apple' ? 'Apple' : 'Google'} was removed as a sign-in method.`);
    } catch (error) { setIdentityNotice(errorText(error, 'The sign-in method could not be removed.')); }
    finally { setIdentityBusy(null); }
  };

  const openBillingPortal = async () => {
    setIdentityBusy('billing'); setIdentityNotice(null);
    try {
      const returnUrl = Platform.OS === 'web' && typeof window !== 'undefined'
        ? `${window.location.origin}/account` : 'magistrate://chat';
      const portalUrl = await createBillingPortal(returnUrl, `billing-portal-${Date.now()}`);
      await WebBrowser.openBrowserAsync(portalUrl);
    } catch (error) { setIdentityNotice(errorText(error, 'Billing management is unavailable.')); }
    finally { setIdentityBusy(null); }
  };

  const handleToggleVoiceOutput = (enabled: boolean) => {
    setVoiceEnabled(enabled);
    ttsService.setSettings({ enabled });
  };

  const saveNotificationSettings = async (enabled: boolean, quiet: boolean, mode: OperatingPermissionMode = operatingPermissionMode) => {
    setAttentionNotifications(enabled);
    setQuietHours(quiet);
    setOperatingPermissionMode(mode);
    await saveOperatingPermissionMode(mode).catch(() => undefined);
    try {
      await updateNotificationPreferences(enabled, quiet, mode);
    } catch {
      Alert.alert('Settings unavailable', 'Notification preferences could not be saved. The previous server policy remains active.');
    }
  };

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>ACCOUNT & SETTINGS</Text>

        <View style={{ width: 36 }} />
      </View>

      <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 120 }}>
        {/* PROFILE SECTION */}
        <GlassSurface variant="card" style={styles.profileCard}>
          <View style={styles.avatarRow}>
            <TouchableOpacity onPress={handlePickAvatar} activeOpacity={0.8} style={styles.avatarTouch}>
              {profile?.avatar_url ? <Image source={{ uri: profile.avatar_url }} style={styles.avatarImage} /> : <View style={[styles.avatarImage, styles.avatarPlaceholder]}><Text style={styles.avatarPlaceholderText}>?</Text></View>}
              {uploading ? (
                <View style={styles.avatarOverlay}>
                  <ActivityIndicator color="#FFFFFF" />
                </View>
              ) : (
                <View style={styles.avatarBadge}>
                  <Text style={styles.avatarBadgeText}>📷</Text>
                </View>
              )}
            </TouchableOpacity>

            <View style={styles.profileInfo}>
              <Text testID="account-profile-name" style={styles.profileName}>{profile?.name || (profileError ? 'Account profile unavailable' : 'No profile name set')}</Text>
              <Text testID="account-profile-detail" style={styles.profileEmail}>{profileError || profile?.email || 'No profile email set.'}</Text>
              <TouchableOpacity onPress={handlePickAvatar} style={styles.uploadBtn}>
                <Text style={styles.uploadBtnText}>CHANGE PHOTO ↗</Text>
              </TouchableOpacity>
            </View>
          </View>
          <View style={styles.profileEditRow}>
            <TextInput testID="account-profile-name-input" accessibilityLabel="Account display name" maxLength={80} value={profileName} onChangeText={setProfileName} placeholder="Display name" placeholderTextColor="rgba(255,255,255,0.5)" style={styles.profileNameInput} />
            <TouchableOpacity testID="account-profile-save" accessibilityRole="button" accessibilityLabel="Save account display name" accessibilityState={{ disabled: savingProfile || !profileName.trim(), busy: savingProfile }} disabled={savingProfile || !profileName.trim()} onPress={() => void saveProfileName()} style={styles.profileSaveButton}><Text style={styles.profileSaveText}>{savingProfile ? 'SAVING…' : 'SAVE'}</Text></TouchableOpacity>
          </View>
        </GlassSurface>

        <AccountSectionHeader id="sign-in" title="SIGN-IN & BILLING" expanded={expandedSection === 'sign-in'} onPress={() => toggleSection('sign-in')} />
        {expandedSection === 'sign-in' ? <GlassSurface variant="card" style={styles.settingsCard}>
          <Text style={styles.settingLabel}>RECOVERY SIGN-IN METHODS</Text>
          <Text style={styles.settingHint}>Link a second verified identity so you can recover this same account. Email addresses are never used to merge accounts.</Text>
          {loginMethods.map(method => <View key={method.provider} testID={`login-method-${method.provider}`} style={styles.socialRow}><View style={styles.providerLeft}><Text style={styles.socialName}>{method.provider.toUpperCase()}</Text><Text style={styles.socialHandle}>{method.label || 'Verified provider identity'}{method.current ? ' · current session' : ' · recovery ready'}</Text></View><TouchableOpacity testID={`unlink-login-${method.provider}`} disabled={identityBusy !== null || method.current || loginMethods.length <= 1} accessibilityState={{ disabled: identityBusy !== null || method.current || loginMethods.length <= 1 }} onPress={() => void unlinkLoginMethod(method.provider)} style={[styles.socialToggleBtn, (method.current || loginMethods.length <= 1) && { opacity: 0.45 }]}><Text style={styles.socialBtnText}>REMOVE</Text></TouchableOpacity></View>)}
          {(['apple', 'google'] as const).filter(provider => !loginMethods.some(method => method.provider === provider)).map(provider => <TouchableOpacity key={provider} testID={`link-login-${provider}`} disabled={identityBusy !== null || !providerEnabled(provider)} onPress={() => void linkLoginMethod(provider)} style={[styles.identityAction, (!providerEnabled(provider) || identityBusy !== null) && { opacity: 0.5 }]}><Text style={styles.socialBtnText}>{identityBusy === provider ? 'LINKING…' : `LINK ${provider.toUpperCase()}`}</Text></TouchableOpacity>)}
          {identityNotice ? <Text testID="identity-notice" accessibilityRole="alert" style={styles.providerError}>{identityNotice}</Text> : null}
          <View style={styles.billingBlock}>
            <Text style={styles.settingLabel}>PLAN, CREDITS & BILLING</Text>
            {billingError ? <Text accessibilityRole="alert" style={styles.providerError}>{billingError}</Text> : billing ? <>
              <Text testID="billing-status" style={styles.settingToggleLabel}>{billing.plan_name} · {(billing.balance_microcredits / 1_000_000).toLocaleString()} credits</Text>
              <Text style={styles.settingHint}>{(billing.reserved_microcredits / 1_000_000).toLocaleString()} reserved · {(billing.period_spend_microcredits / 1_000_000).toLocaleString()} used this period · {billing.subscription_status}</Text>
              <Text style={styles.settingHint}>{billing.low_credit_warning ? 'Credit balance is low. ' : ''}Concurrency limit: {billing.limits.concurrency}.</Text>
            </> : <Text style={styles.settingHint}>Verified billing data is unavailable.</Text>}
            {onboarding?.steps.billing.customer_portal_available ? <TouchableOpacity testID="open-billing-portal" disabled={identityBusy !== null} onPress={() => void openBillingPortal()} style={styles.identityAction}><Text style={styles.socialBtnText}>{identityBusy === 'billing' ? 'OPENING…' : 'MANAGE BILLING ↗'}</Text></TouchableOpacity> : <Text style={styles.settingHint}>Billing management is unavailable for this account.</Text>}
          </View>
        </GlassSurface> : null}

        <AccountSectionHeader id="notifications" title="CAPTAIN ATTENTION NOTIFICATIONS" expanded={expandedSection === 'notifications'} onPress={() => toggleSection('notifications')} />

        {expandedSection === 'notifications' ? <GlassSurface variant="card" style={styles.settingsCard}>
          <View style={styles.settingToggleRow}>
            <View style={styles.settingCopy}>
              <Text style={styles.settingToggleLabel}>CAPTAIN ATTENTION</Text>
              <Text style={styles.settingHint}>Remote native push or open-browser fallback.</Text>
            </View>
            <TouchableOpacity
              testID="account-attention-notifications-toggle"
              style={[styles.toggleBtn, attentionNotifications ? styles.toggleBtnActive : undefined]}
              onPress={() => void saveNotificationSettings(!attentionNotifications, quietHours)}
            >
              <Text style={styles.toggleBtnText}>{attentionNotifications ? 'ON ✓' : 'OFF'}</Text>
            </TouchableOpacity>
          </View>
          <View style={styles.settingToggleRow}>
            <View style={styles.settingCopy}>
              <Text style={styles.settingToggleLabel}>QUIET HOURS</Text>
              <Text style={styles.settingHint}>10 PM–7 AM, device local time</Text>
            </View>
            <TouchableOpacity
              testID="account-quiet-hours-toggle"
              style={[styles.toggleBtn, quietHours ? styles.toggleBtnActive : undefined]}
              onPress={() => void saveNotificationSettings(attentionNotifications, !quietHours)}
            >
              <Text style={styles.toggleBtnText}>{quietHours ? 'ON ✓' : 'OFF'}</Text>
            </TouchableOpacity>
          </View>
          <Text testID="account-operating-permission-label" style={[styles.settingLabel, { marginTop: 14 }]}>OPERATING PERMISSION MODE</Text>
          <Text style={styles.settingHint}>Alert volume only. This never grants merge, destructive, irreversible, security-sensitive, or external-public authority; Firstmate and captain confirmation rules still apply.</Text>
          <View testID="account-operating-permission-options" style={styles.permissionModeColumn}>
            {OPERATING_PERMISSION_MODE_OPTIONS.map(option => {
              const selected = operatingPermissionMode === option.id;
              return <TouchableOpacity key={option.id} testID={`account-operating-permission-${option.id}`} accessibilityRole="button" accessibilityLabel={`${option.label}: ${option.description}`} accessibilityState={{ selected }} onPress={() => void saveNotificationSettings(attentionNotifications, quietHours, option.id)} style={[styles.permissionModeOption, selected ? styles.permissionModeOptionActive : undefined]}><Text style={[styles.permissionModeTitle, selected ? styles.permissionModeTitleActive : undefined]}>{option.label}</Text><Text style={styles.settingHint}>{option.description}</Text></TouchableOpacity>;
            })}
          </View>
          <Text testID="account-native-push-status" style={styles.pushStatusText}>
            {nativePushStatus === 'registered' ? 'Native push: registered with Gateway.' : nativePushStatus === 'permission-required' ? 'Native push permission has not been requested.' : nativePushStatus === 'permission-denied' ? 'Native push permission denied. In-app attention remains available.' : nativePushStatus === 'unavailable' ? 'Native push unavailable here (use a physical device/release build with EAS push credentials).' : nativePushStatus === 'offline' ? 'Gateway or push service offline. Retrying while connected.' : 'Native push status: ' + nativePushStatus + '.'}
          </Text>
          {nativePushStatus !== 'registered' && <TouchableOpacity testID="account-enable-native-push" onPress={() => void notificationManager.registerNativePushToken(true)} style={styles.enablePushButton}><Text style={styles.toggleBtnText}>ENABLE NATIVE PUSH</Text></TouchableOpacity>}
          <Text style={styles.settingHint}>Web notifications require an open, eligible browser tab; native push is the beta background channel.</Text>
        </GlassSurface> : null}

        {/* VOICE & AUDIO SETTINGS */}
        <AccountSectionHeader id="voice" title="VOICE & SPEECH SYNTHESIS" expanded={expandedSection === 'voice'} onPress={() => toggleSection('voice')} />

        {expandedSection === 'voice' ? <GlassSurface variant="card" style={styles.settingsCard}>
          <View style={styles.settingToggleRow}>
            <Text style={styles.settingToggleLabel}>VOICE OUTPUT</Text>
            <TouchableOpacity
              style={[styles.toggleBtn, voiceEnabled ? styles.toggleBtnActive : undefined]}
              onPress={() => handleToggleVoiceOutput(!voiceEnabled)}
            >
              <Text style={styles.toggleBtnText}>{voiceEnabled ? 'ON ✓' : 'OFF'}</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.settingToggleRow}>
            <Text style={styles.settingToggleLabel}>AUTO-SPEAK MAGISTRATE</Text>
            <TouchableOpacity
              style={[styles.toggleBtn, autoSpeak ? styles.toggleBtnActive : undefined]}
              onPress={() => setAutoSpeak(!autoSpeak)}
            >
              <Text style={styles.toggleBtnText}>{autoSpeak ? 'ON ✓' : 'OFF'}</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.settingToggleRow}>
            <Text style={styles.settingToggleLabel}>CONTINUOUS LISTEN AFTER RESPONSE</Text>
            <TouchableOpacity
              style={[styles.toggleBtn, autoListen ? styles.toggleBtnActive : undefined]}
              onPress={() => setAutoListen(!autoListen)}
            >
              <Text style={styles.toggleBtnText}>{autoListen ? 'ON ✓' : 'OFF'}</Text>
            </TouchableOpacity>
          </View>
          <Text testID="account-voice-input-label" style={styles.settingLabel}>VOICE INPUT MODE</Text>
          <Text style={styles.settingHint}>Speech is placed in the chat composer for review. OpenAI credentials never leave the gateway.</Text>
          <View testID="account-voice-input-options" style={styles.voiceModeRow}>{VOICE_INPUT_MODE_OPTIONS.map(option => { const capability = capabilityFor(voiceCapabilities, option.id); const selected = voiceInputMode === option.id; const disabled = capability.available === 'unavailable'; return <TouchableOpacity key={option.id} testID={`account-voice-mode-${option.id}`} accessibilityRole="button" accessibilityLabel={`${option.label}: ${capability.reason || option.description}`} accessibilityState={{ selected, disabled }} disabled={disabled} onPress={() => { setVoiceInputMode(option.id); void saveVoiceInputMode(option.id); }} style={[styles.voiceModePill, selected ? styles.voiceModePillActive : undefined, disabled ? styles.voiceModePillDisabled : undefined]}><Text style={[styles.voiceModeText, selected ? styles.voiceModeTextActive : undefined]}>{option.label}</Text></TouchableOpacity>; })}</View>
        </GlassSurface> : null}

        {/* CONNECTED OAUTH PROVIDERS */}
        <AccountSectionHeader id="connections" title="CONNECTED OAUTH PROVIDERS" expanded={expandedSection === 'connections'} onPress={() => toggleSection('connections')} />

        {expandedSection === 'connections' ? <GlassSurface variant="card" style={styles.socialCard}>
          {providersError ? <Text testID="account-providers-error" accessibilityRole="alert" style={styles.providerError}>{providersError}</Text> : null}
          {connectNotice ? <Text testID="account-connect-notice" accessibilityRole="alert" style={styles.providerError}>{connectNotice}</Text> : null}
          {!providersLoaded ? (
            <Text testID="account-providers-loading" style={styles.providerPlaceholder}>Reading connected accounts…</Text>
          ) : providersError ? null : providers.length === 0 ? (
            <Text testID="account-providers-empty" style={styles.providerPlaceholder}>The gateway reported no integrations for this account.</Text>
        ) : providers.map(s => (
            <View key={s.provider} testID={`account-provider-${s.provider}`} style={styles.socialRow}>
              <View style={styles.providerLeft}>
                <Text style={styles.socialName}>{s.provider.toUpperCase()}</Text>
                <Text style={styles.socialHandle}>
                  {s.status === 'connected' && s.username ? s.username : (s.capabilities.length ? s.capabilities.join(' • ') : 'No capabilities')}
                </Text>
                {/* The reason a provider is not connected is always stated. */}
                {s.unavailable_reason ? <Text testID={`account-provider-reason-${s.provider}`} style={styles.providerReason}>{s.unavailable_reason}</Text> : null}
              </View>
              <TouchableOpacity
                testID={`account-provider-action-${s.provider}`}
                accessibilityRole="button"
                accessibilityLabel={`${s.provider} ${providerActionLabel(s)}`}
                accessibilityState={{ disabled: !s.available }}
                disabled={!s.available}
                style={[styles.socialToggleBtn, s.status === 'connected' ? styles.socialBtnConnected : undefined, !s.available ? { opacity: 0.55 } : undefined]}
                onPress={() => void handleRealOAuthConnect(s)}
              >
                <Text style={styles.socialBtnText}>{providerActionLabel(s)}</Text>
              </TouchableOpacity>
            </View>
          ))}
        </GlassSurface> : null}

        <AccountSectionHeader id="appearance" title="APPEARANCE" expanded={expandedSection === 'appearance'} onPress={() => toggleSection('appearance')} />
        {expandedSection === 'appearance' ? <GlassSurface variant="card" style={styles.settingsCard}>
          <Text style={styles.settingToggleLabel}>SYSTEM, DARK OR LIGHT</Text>
          <Text style={styles.settingHint}>Appearance is managed in the main Settings sheet. Magistrate uses a flat productivity canvas without wallpaper or transparency effects.</Text>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Return to Chat settings" onPress={() => router.replace('/chat' as any)} style={styles.enablePushButton}><Text style={styles.toggleBtnText}>OPEN CHAT SETTINGS</Text></TouchableOpacity>
        </GlassSurface> : null}

        <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>PRIVACY, SUPPORT & ABOUT</Text></View>
        <GlassSurface variant="card" style={styles.settingsCard}>
          <Text style={styles.settingToggleLabel}>MAGISTRATE</Text>
          <Text testID="account-version" style={styles.settingHint}>Version {Constants.expoConfig?.version || 'unavailable'} · build {Constants.nativeBuildVersion || Constants.expoConfig?.ios?.buildNumber || 'unavailable'}</Text>
          <Text style={styles.settingHint}>CALM AT REST. SPECTRAL WHEN ALIVE.</Text>
          <View style={styles.accountLinks}>
            <TouchableOpacity accessibilityRole="link" accessibilityLabel="Open privacy and security documentation" onPress={() => void openAccountLink('https://github.com/calvin-rutherford/Magistrate/blob/main/docs/friend-beta-security.md')} style={styles.accountLink}><Text style={styles.accountLinkText}>Privacy & security</Text></TouchableOpacity>
            <TouchableOpacity accessibilityRole="link" accessibilityLabel="Open Magistrate license" onPress={() => void openAccountLink('https://github.com/calvin-rutherford/Magistrate/blob/main/LICENSE')} style={styles.accountLink}><Text style={styles.accountLinkText}>Legal & license</Text></TouchableOpacity>
            <TouchableOpacity accessibilityRole="link" accessibilityLabel="Open support" onPress={() => void openAccountLink('https://github.com/calvin-rutherford/Magistrate/issues')} style={styles.accountLink}><Text style={styles.accountLinkText}>Support</Text></TouchableOpacity>
          </View>
          <TouchableOpacity testID="account-logout" accessibilityRole="button" accessibilityLabel="Sign out of Magistrate" onPress={() => void logoutGatewaySession()} style={styles.accountLogout}><Text style={styles.accountLogoutText}>SIGN OUT</Text></TouchableOpacity>
          <View style={styles.deleteSection}>
            <Text style={styles.settingToggleLabel}>DELETE ACCOUNT</Text>
            <Text style={styles.settingHint}>Permanently erases account data. Type DELETE {profile?.user_id || 'your account ID'} to enable this irreversible action.</Text>
            <TextInput testID="account-delete-confirmation" accessibilityLabel="Account deletion confirmation" autoCapitalize="characters" value={deleteConfirmation} onChangeText={setDeleteConfirmation} placeholder={profile ? `DELETE ${profile.user_id}` : 'Account unavailable'} placeholderTextColor="rgba(255,255,255,0.45)" style={styles.deleteInput} />
            <TouchableOpacity testID="account-delete" accessibilityRole="button" accessibilityLabel="Permanently delete Magistrate account" accessibilityState={{ disabled: deletingAccount || !profile || deleteConfirmation !== `DELETE ${profile.user_id}`, busy: deletingAccount }} disabled={deletingAccount || !profile || deleteConfirmation !== `DELETE ${profile.user_id}`} onPress={requestAccountDeletion} style={[styles.accountDelete, (deletingAccount || !profile || deleteConfirmation !== `DELETE ${profile.user_id}`) && styles.disabled]}><Text style={styles.accountDeleteText}>{deletingAccount ? 'DELETING…' : 'DELETE ACCOUNT'}</Text></TouchableOpacity>
          </View>
        </GlassSurface>
      </ScrollView>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 16 },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 12,
    marginBottom: 8
  },
  headerTitle: { fontSize: 13, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  profileCard: { padding: 18, marginVertical: 8, borderRadius: 18 },
  avatarRow: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  avatarTouch: { position: 'relative' },
  avatarImage: { width: 68, height: 68, borderRadius: 34, borderWidth: 2, borderColor: '#FFFFFF' }, avatarPlaceholder: { backgroundColor: 'rgba(255,255,255,0.12)', justifyContent: 'center', alignItems: 'center' }, avatarPlaceholderText: { color: '#FFFFFF', fontSize: 24, fontWeight: '700' },
  avatarOverlay: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(0,0,0,0.5)', borderRadius: 34, justifyContent: 'center', alignItems: 'center' },
  avatarBadge: { position: 'absolute', bottom: -2, right: -2, width: 22, height: 22, borderRadius: 11, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center' },
  avatarBadgeText: { fontSize: 11 },
  profileInfo: { flex: 1 },
  profileEditRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 16 },
  profileNameInput: { flex: 1, minHeight: 44, borderWidth: 1, borderColor: 'rgba(255,255,255,0.28)', borderRadius: 10, color: '#FFFFFF', paddingHorizontal: 12, fontSize: 14 },
  profileSaveButton: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 10, backgroundColor: '#FFFFFF' },
  profileSaveText: { color: '#11151B', fontSize: 11, fontWeight: '800' },
  profileName: { fontSize: 16, fontWeight: 'bold', color: '#FFFFFF' },
  profileEmail: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', marginTop: 2 },
  uploadBtn: { marginTop: 6 },
  uploadBtnText: { fontSize: 10, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 0.8 },
  sectionHeader: { marginTop: 14, marginBottom: 6, minHeight: 44, paddingVertical: 8, paddingHorizontal: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  sectionTitle: { fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4, flex: 1 },
  sectionChevron: { color: '#FFFFFF', fontSize: 22, lineHeight: 24, width: 30, textAlign: 'center' },
  socialCard: { padding: 16, borderRadius: 18, gap: 12 },
  socialRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 },
  providerLeft: { flex: 1, paddingRight: 10 },
  socialName: { fontSize: 13.5, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 0.5 },
  socialHandle: { fontSize: 11, color: 'rgba(255, 255, 255, 0.5)', marginTop: 2 },
  providerReason: { fontSize: 10, lineHeight: 14, color: 'rgba(255, 255, 255, 0.62)', marginTop: 4 },
  providerError: { fontSize: 11, lineHeight: 16, color: '#FFB4B2', marginBottom: 8 },
  providerPlaceholder: { color: 'rgba(255,255,255,0.5)', fontSize: 11, textAlign: 'center', marginVertical: 10 },
  socialToggleBtn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  socialBtnConnected: { backgroundColor: 'rgba(255, 255, 255, 0.15)', borderColor: '#FFFFFF' },
  socialBtnText: { fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  identityAction: { marginTop: 10, minHeight: 40, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.4)', justifyContent: 'center', alignItems: 'center' },
  billingBlock: { marginTop: 20, paddingTop: 16, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(255,255,255,0.3)' },
  // Settings cards have a little more breathing room for the mode controls.
  settingsCard: { padding: 21, borderRadius: 18 },
  settingLabel: { fontSize: 10, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', marginBottom: 8 },
  settingToggleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginVertical: 6 },
  settingToggleLabel: { fontSize: 11, fontWeight: 'bold', color: '#FFFFFF' },
  settingCopy: { flex: 1, paddingRight: 12 },
  settingHint: { marginTop: 3, fontSize: 10, color: 'rgba(255, 255, 255, 0.55)' },
  toggleBtn: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  toggleBtnActive: { backgroundColor: 'rgba(255, 255, 255, 0.2)', borderColor: '#FFFFFF' },
  toggleBtnText: { fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  permissionModeColumn: { gap: 7, marginTop: 10 },
  permissionModeOption: { padding: 10, borderRadius: 11, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  permissionModeOptionActive: { backgroundColor: 'rgba(36, 216, 255, 0.22)', borderColor: '#24D8FF' },
  permissionModeTitle: { fontSize: 10, fontWeight: 'bold', color: 'rgba(255,255,255,0.75)' },
  permissionModeTitleActive: { color: '#FFFFFF' },
  pushStatusText: { marginTop: 12, fontSize: 10, lineHeight: 15, color: 'rgba(255,255,255,0.72)' },
  enablePushButton: { alignSelf: 'flex-start', marginTop: 10, paddingVertical: 8, paddingHorizontal: 12, borderWidth: 1, borderColor: '#24D8FF', borderRadius: 999 },
  voiceModeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 10 },
  voiceModePill: { paddingHorizontal: 10, paddingVertical: 8, borderRadius: 11, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  voiceModePillActive: { backgroundColor: 'rgba(36, 216, 255, 0.28)', borderColor: '#24D8FF' },
  voiceModePillDisabled: { opacity: 0.45 },
  voiceModeText: { fontSize: 10, color: 'rgba(255,255,255,0.7)' },
  voiceModeTextActive: { color: '#FFFFFF', fontWeight: 'bold' },
  accountLinks: { marginTop: 12, gap: 2 },
  accountLink: { minHeight: 44, justifyContent: 'center', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'rgba(255,255,255,0.15)' },
  accountLinkText: { color: '#FFFFFF', fontSize: 14, fontWeight: '600' },
  accountLogout: { minHeight: 44, marginTop: 18, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.35)', alignItems: 'center', justifyContent: 'center' },
  accountLogoutText: { color: '#FFFFFF', fontSize: 11, fontWeight: '800', letterSpacing: 0.7 },
  deleteSection: { marginTop: 22, paddingTop: 18, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(255,255,255,0.2)' },
  deleteInput: { minHeight: 44, marginTop: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.28)', borderRadius: 10, color: '#FFFFFF', paddingHorizontal: 12, fontSize: 13 },
  accountDelete: { minHeight: 44, marginTop: 8, borderRadius: 10, borderWidth: 1, borderColor: '#FF625F', alignItems: 'center', justifyContent: 'center' },
  accountDeleteText: { color: '#FF8E8B', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  disabled: { opacity: 0.45 },
});
