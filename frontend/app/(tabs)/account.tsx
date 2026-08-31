import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Image, Alert, ActivityIndicator } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { fetchUserProfile, uploadUserAvatar, fetchAuthProviders, connectAuthProvider, updateUserProfile, updateNotificationPreferences, fetchNotificationPreferences, fetchVoiceInputCapabilities, GATEWAY_URL, UserProfile, AuthProviderInfo } from '../../src/api/client';
import { setActiveBackground, WeatherSceneKey } from '../../src/services/environmentTheme';
import { loadChatPreferences, removeCustomBackground, saveChatBackground, saveCustomBackground, saveVoiceInputMode } from '../../src/services/ChatPreferences';
import { ttsService } from '../../src/services/TextToSpeechService';
import { useRouter } from 'expo-router';
import { capabilityFor, getLocalVoiceCapabilities, VOICE_INPUT_MODE_OPTIONS, VoiceInputCapabilities, VoiceInputMode } from '../../src/services/VoiceInputModes';
import { loadOperatingPermissionMode, saveOperatingPermissionMode, OPERATING_PERMISSION_MODE_OPTIONS, OperatingPermissionMode } from '../../src/services/OperatingPermissionModes';
import { notificationManager, NativePushStatus } from '../../src/services/NotificationManager';

type AccountSectionKey = 'notifications' | 'voice' | 'connections' | 'appearance';
function AccountSectionHeader({ id, title, expanded, onPress }: { id: AccountSectionKey; title: string; expanded: boolean; onPress: () => void }) {
  return <TouchableOpacity testID={`account-section-${id}`} accessibilityRole="button" accessibilityLabel={`${title} settings`} accessibilityState={{ expanded }} {...({ 'aria-expanded': expanded } as any)} onPress={onPress} style={styles.sectionHeader} activeOpacity={0.75}>
    <Text style={styles.sectionTitle}>{title}</Text><Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.sectionChevron}>{expanded ? '⌄' : '›'}</Text>
  </TouchableOpacity>;
}

export default function AccountScreen() {
  const router = useRouter();

  const [profile, setProfile] = useState<UserProfile | null>(null);

  const [providers, setProviders] = useState<AuthProviderInfo[]>([]);
  const [uploading, setUploading] = useState<boolean>(false);
  const [activeThemeKey, setActiveThemeKey] = useState<WeatherSceneKey>('dusk-mountain');
  const [customBackgroundUri, setCustomBackgroundUri] = useState<string | undefined>();

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
    try {
      const [prof, provs] = await Promise.all([
        fetchUserProfile().catch(() => null),
        fetchAuthProviders().catch(() => [])
      ]);
      if (prof) {
        if (prof.avatar_url && prof.avatar_url.startsWith('/uploads')) {
          prof.avatar_url = GATEWAY_URL.replace(/\/api\/v1$/, '') + prof.avatar_url;
        }
        // The locally persisted appearance is authoritative. The profile
        // value is legacy metadata and must not overwrite an explicit device
        // choice during hydration.
        setProfile(prof);
      }
      if (provs && provs.length > 0) {
        setProviders(provs);
      }
    } catch (e) {
      console.error('Error loading account data:', e);
    }
  };

  useEffect(() => {
    const unsubscribePushStatus = notificationManager.subscribePushStatus(setNativePushStatus);
    Promise.allSettled([loadChatPreferences(), fetchVoiceInputCapabilities(), fetchNotificationPreferences(), loadOperatingPermissionMode()]).then(([preferencesResult, capabilityResult, notificationResult, localModeResult]) => {
      if (preferencesResult.status === 'fulfilled') {
        setActiveThemeKey(preferencesResult.value.background);
        setCustomBackgroundUri(preferencesResult.value.customBackgroundUri);
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
        console.error('Avatar upload error:', e);
        Alert.alert('Upload Error', 'Failed to upload profile photo to server.');
      } finally {
        setUploading(false);
      }
    }
  };

  const handlePickCustomBackground = async () => {
    const permResult = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permResult.granted) {
      Alert.alert('Permission Required', 'Media library access is needed to select a custom background photo.');
      return;
    }

    const pickerResult = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: true,
      quality: 0.85
    });

    if (!pickerResult.canceled && pickerResult.assets && pickerResult.assets.length > 0) {
      const asset = pickerResult.assets[0];
      if (asset.fileSize && asset.fileSize > 10 * 1024 * 1024) { Alert.alert('Photo Too Large', 'Choose an image smaller than 10 MB.'); return; }
      if (asset.mimeType && !asset.mimeType.startsWith('image/')) { Alert.alert('Unsupported File', 'Choose a supported image file.'); return; }
      const customUri = asset.uri;
      setActiveThemeKey('custom');
      setCustomBackgroundUri(customUri);
      await saveCustomBackground(customUri);
    }
  };

  const handleSelectBackground = async (key: WeatherSceneKey) => {
    setActiveThemeKey(key);
    setCustomBackgroundUri(undefined);
    setActiveBackground(key);
    try {
      await saveChatBackground(key);
      await updateUserProfile({ active_theme: key });
    } catch (e) {
      console.error('Failed to save background theme');
    }
  };

  // REAL OAUTH BROWSER AUTHENTICATION FLOW WITH AUTO DISMISSAL
  const handleRealOAuthConnect = async (providerInfo: any) => {
    const returnUrl = Linking.createURL('/account');

    try {
      const connect = await connectAuthProvider(providerInfo.provider, returnUrl);
      const result = await WebBrowser.openAuthSessionAsync(connect.auth_url, returnUrl);
      // The browser automatically dismisses when the returnUrl is hit.
      if (result.type === 'success') {
        loadAccountData();
      } else {
        WebBrowser.dismissBrowser();
      }
    } catch (e) {
      console.error('OAuth browser error:', e);
      WebBrowser.dismissBrowser();
    }
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
              <Text style={styles.profileName}>{profile?.name || 'Account profile unavailable'}</Text>
              <Text style={styles.profileEmail}>{profile?.email || 'Connect an account to see profile details.'}</Text>
              <TouchableOpacity onPress={handlePickAvatar} style={styles.uploadBtn}>
                <Text style={styles.uploadBtnText}>CHANGE PHOTO ↗</Text>
              </TouchableOpacity>
            </View>
          </View>
        </GlassSurface>

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
          {providers.length === 0 ? (
          <Text style={{ fontFamily: 'monospace', color: 'rgba(255,255,255,0.5)', fontSize: 11, textAlign: 'center', marginVertical: 10 }}>No integrations connected.</Text>
        ) : providers.map(s => (
            <View key={s.provider} style={styles.socialRow}>
              <View style={styles.providerLeft}>
                <Text style={styles.socialName}>{s.provider.toUpperCase()}</Text>
                <Text style={styles.socialHandle}>
                  {s.username ? s.username : (s.capabilities ? s.capabilities.join(' • ') : 'No capabilities')}
                </Text>
              </View>
              <TouchableOpacity
                disabled={!s.available}
                style={[styles.socialToggleBtn, s.status === 'connected' ? styles.socialBtnConnected : undefined, !s.available ? { opacity: 0.55 } : undefined]}
                onPress={() => handleRealOAuthConnect(s)}
              >
                <Text style={styles.socialBtnText}>
                  {s.status === 'connected' ? 'CONNECTED ✓' : s.available ? 'CONNECT +' : 'UNAVAILABLE'}
                </Text>
              </TouchableOpacity>
            </View>
          ))}
        </GlassSurface> : null}

        {/* BACKGROUNDS & APPEARANCE */}
        <AccountSectionHeader id="appearance" title="BACKGROUNDS & APPEARANCE" expanded={expandedSection === 'appearance'} onPress={() => toggleSection('appearance')} />

        {expandedSection === 'appearance' ? <GlassSurface variant="card" style={styles.settingsCard}>
          <Text style={styles.settingLabel}>SELECTABLE BACKGROUND THEME</Text>
          <View style={styles.themeRow}>
            {[
              { key: 'auto', label: 'Automatic' },
              { key: 'dusk-mountain', label: 'Dusk Mountain' },
              { key: 'clear-day', label: 'Clear Day' },
              { key: 'clear-night', label: 'Clear Night' },
              { key: 'clouds', label: 'Cloudy Sky' },
              { key: 'rain', label: 'Rain Storm' },
              { key: 'sunset', label: 'Sunset Glow' },
              { key: 'minimal-dark', label: 'Minimal Dark' }
            ].map(t => (
              <TouchableOpacity key={t.key} onPress={() => handleSelectBackground(t.key as WeatherSceneKey)}>
                <GlassSurface variant="control" style={[styles.themePill, activeThemeKey === t.key ? styles.themePillActive : undefined]}>
                  <Text style={[styles.themeText, activeThemeKey === t.key ? styles.themeTextActive : undefined]}>{t.label}</Text>
                </GlassSurface>
              </TouchableOpacity>
            ))}
          </View>

          {customBackgroundUri ? <Image source={{ uri: customBackgroundUri }} style={styles.customBackgroundPreview} resizeMode="cover" accessibilityLabel="Custom background preview" /> : null}
          <TouchableOpacity testID="account-custom-background-upload" style={styles.customBgBtn} onPress={handlePickCustomBackground}>
            <Text style={styles.customBgBtnText}>{customBackgroundUri ? 'REPLACE CUSTOM PHOTO 📷' : 'UPLOAD CUSTOM PHOTO 📷'}</Text>
          </TouchableOpacity>
          {customBackgroundUri ? <TouchableOpacity testID="account-custom-background-remove" style={styles.customBgRemoveBtn} onPress={async () => { setCustomBackgroundUri(undefined); setActiveThemeKey('auto'); await removeCustomBackground(); }}><Text style={styles.customBgBtnText}>REMOVE CUSTOM PHOTO</Text></TouchableOpacity> : null}
        </GlassSurface> : null}
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
  headerTitle: { fontFamily: 'monospace', fontSize: 13, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
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
  profileName: { fontSize: 16, fontWeight: 'bold', color: '#FFFFFF' },
  profileEmail: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', marginTop: 2 },
  uploadBtn: { marginTop: 6 },
  uploadBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 0.8 },
  sectionHeader: { marginTop: 14, marginBottom: 6, minHeight: 44, paddingVertical: 8, paddingHorizontal: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  sectionTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4, flex: 1 },
  sectionChevron: { color: '#FFFFFF', fontSize: 22, lineHeight: 24, width: 30, textAlign: 'center' },
  socialCard: { padding: 16, borderRadius: 18, gap: 12 },
  socialRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 },
  providerLeft: { flex: 1, paddingRight: 10 },
  socialName: { fontSize: 13.5, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 0.5 },
  socialHandle: { fontSize: 11, color: 'rgba(255, 255, 255, 0.5)', marginTop: 2 },
  socialToggleBtn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  socialBtnConnected: { backgroundColor: 'rgba(255, 255, 255, 0.15)', borderColor: '#FFFFFF' },
  socialBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  // Settings cards have a little more breathing room for the mode controls.
  settingsCard: { padding: 21, borderRadius: 18 },
  settingLabel: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', marginBottom: 8 },
  settingToggleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginVertical: 6 },
  settingToggleLabel: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF' },
  settingCopy: { flex: 1, paddingRight: 12 },
  settingHint: { marginTop: 3, fontSize: 10, color: 'rgba(255, 255, 255, 0.55)' },
  toggleBtn: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  toggleBtnActive: { backgroundColor: 'rgba(255, 255, 255, 0.2)', borderColor: '#FFFFFF' },
  toggleBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  permissionModeColumn: { gap: 7, marginTop: 10 },
  permissionModeOption: { padding: 10, borderRadius: 11, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  permissionModeOptionActive: { backgroundColor: 'rgba(36, 216, 255, 0.22)', borderColor: '#24D8FF' },
  permissionModeTitle: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: 'rgba(255,255,255,0.75)' },
  permissionModeTitleActive: { color: '#FFFFFF' },
  pushStatusText: { marginTop: 12, fontSize: 10, lineHeight: 15, color: 'rgba(255,255,255,0.72)' },
  enablePushButton: { alignSelf: 'flex-start', marginTop: 10, paddingVertical: 8, paddingHorizontal: 12, borderWidth: 1, borderColor: '#24D8FF', borderRadius: 999 },
  voiceModeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 10 },
  voiceModePill: { paddingHorizontal: 10, paddingVertical: 8, borderRadius: 11, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  voiceModePillActive: { backgroundColor: 'rgba(36, 216, 255, 0.28)', borderColor: '#24D8FF' },
  voiceModePillDisabled: { opacity: 0.45 },
  voiceModeText: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255,255,255,0.7)' },
  voiceModeTextActive: { color: '#FFFFFF', fontWeight: 'bold' },
  themeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  themePill: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 12 },
  themePillActive: { backgroundColor: 'rgba(255, 255, 255, 0.25)', borderColor: '#FFFFFF' },
  themeText: { fontFamily: 'monospace', fontSize: 11, color: 'rgba(255, 255, 255, 0.7)' },
  themeTextActive: { color: '#FFFFFF', fontWeight: 'bold' },
  customBgBtn: { marginTop: 14, backgroundColor: 'rgba(255, 255, 255, 0.12)', paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: '#FFFFFF', alignItems: 'center' }, customBackgroundPreview: { width: '100%', height: 110, borderRadius: 12, marginTop: 14 }, customBgRemoveBtn: { marginTop: 8, backgroundColor: 'rgba(255, 98, 95, 0.18)', paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: '#FFB4B2', alignItems: 'center' },
  customBgBtnText: { fontFamily: 'monospace', color: '#FFFFFF', fontWeight: 'bold', fontSize: 11 }
});
