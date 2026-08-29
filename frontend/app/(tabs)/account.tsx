import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Image, Alert, ActivityIndicator } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { fetchUserProfile, uploadUserAvatar, fetchAuthProviders, updateUserProfile, updateNotificationPreferences, UserProfile, AuthProviderInfo } from '../../src/api/client';
import { setActiveBackground, WeatherSceneKey } from '../../src/services/environmentTheme';
import { loadChatPreferences, removeCustomBackground, saveChatBackground, saveCustomBackground } from '../../src/services/ChatPreferences';
import { ttsService } from '../../src/services/TextToSpeechService';
import { useRouter } from 'expo-router';

export default function AccountScreen() {
  const router = useRouter();

  const [profile, setProfile] = useState<UserProfile>({
    user_id: 'default_user',
    name: 'Spectre Operator',
    email: 'spectre@magistrate.io',
    avatar_url: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=400&auto=format&fit=crop',
    bio: 'Magistrate Operator'
  });

  const [providers, setProviders] = useState<AuthProviderInfo[]>([]);
  const [uploading, setUploading] = useState<boolean>(false);
  const [activeThemeKey, setActiveThemeKey] = useState<WeatherSceneKey>('dusk-mountain');
  const [customBackgroundUri, setCustomBackgroundUri] = useState<string | undefined>();

  const [voiceEnabled, setVoiceEnabled] = useState<boolean>(true);
  const [autoSpeak, setAutoSpeak] = useState<boolean>(true);
  const [autoListen, setAutoListen] = useState<boolean>(true);
  const [attentionNotifications, setAttentionNotifications] = useState<boolean>(true);
  const [quietHours, setQuietHours] = useState<boolean>(true);

  const loadAccountData = async () => {
    try {
      const [prof, provs] = await Promise.all([
        fetchUserProfile().catch(() => null),
        fetchAuthProviders().catch(() => [])
      ]);
      if (prof) {
        if (prof.avatar_url && prof.avatar_url.startsWith('/uploads')) {
          prof.avatar_url = 'http://100.84.181.23:8000' + prof.avatar_url;
        }
                if (prof.active_theme) {
          setActiveThemeKey(prof.active_theme as any);
          setActiveBackground(prof.active_theme as any);
        }
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
    loadAccountData();
    loadChatPreferences().then(preferences => { if (preferences.background !== 'auto' || preferences.customBackgroundUri) setActiveThemeKey(preferences.background); setCustomBackgroundUri(preferences.customBackgroundUri); }).catch(() => {});
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
            fullUrl = 'http://100.84.181.23:8000' + fullUrl;
          }
          setProfile(prev => ({ ...prev, avatar_url: fullUrl }));
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
    const authUrl = `http://100.84.181.23:8000/api/v1/auth/${providerInfo.provider}/connect?token=dummy&redirect_uri=${encodeURIComponent(returnUrl)}`;

    try {
      const result = await WebBrowser.openAuthSessionAsync(authUrl, returnUrl);
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

  const saveNotificationSettings = async (enabled: boolean, quiet: boolean) => {
    setAttentionNotifications(enabled);
    setQuietHours(quiet);
    try {
      await updateNotificationPreferences(enabled, quiet);
    } catch {
      Alert.alert('Settings unavailable', 'Notification preferences could not be saved.');
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
              <Image source={{ uri: profile.avatar_url }} style={styles.avatarImage} />
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
              <Text style={styles.profileName}>{profile.name}</Text>
              <Text style={styles.profileEmail}>{profile.email}</Text>
              <TouchableOpacity onPress={handlePickAvatar} style={styles.uploadBtn}>
                <Text style={styles.uploadBtnText}>CHANGE PHOTO ↗</Text>
              </TouchableOpacity>
            </View>
          </View>
        </GlassSurface>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>CAPTAIN ATTENTION NOTIFICATIONS</Text>
        </View>

        <GlassSurface variant="card" style={styles.settingsCard}>
          <View style={styles.settingToggleRow}>
            <View style={styles.settingCopy}>
              <Text style={styles.settingToggleLabel}>ACTIONABLE ITEMS</Text>
              <Text style={styles.settingHint}>Questions and PR merge decisions only</Text>
            </View>
            <TouchableOpacity
              style={[styles.toggleBtn, attentionNotifications ? styles.toggleBtnActive : undefined]}
              onPress={() => saveNotificationSettings(!attentionNotifications, quietHours)}
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
              style={[styles.toggleBtn, quietHours ? styles.toggleBtnActive : undefined]}
              onPress={() => saveNotificationSettings(attentionNotifications, !quietHours)}
            >
              <Text style={styles.toggleBtnText}>{quietHours ? 'ON ✓' : 'OFF'}</Text>
            </TouchableOpacity>
          </View>
        </GlassSurface>

        {/* VOICE & AUDIO SETTINGS */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>VOICE & SPEECH SYNTHESIS</Text>
        </View>

        <GlassSurface variant="card" style={styles.settingsCard}>
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
        </GlassSurface>

        {/* CONNECTED OAUTH PROVIDERS */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>CONNECTED OAUTH PROVIDERS</Text>
        </View>

        <GlassSurface variant="card" style={styles.socialCard}>
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
                style={[styles.socialToggleBtn, s.status === 'connected' ? styles.socialBtnConnected : undefined]}
                onPress={() => handleRealOAuthConnect(s)}
              >
                <Text style={styles.socialBtnText}>
                  {s.status === 'connected' ? 'CONNECTED ✓' : 'CONNECT +'}
                </Text>
              </TouchableOpacity>
            </View>
          ))}
        </GlassSurface>

        {/* BACKGROUNDS & APPEARANCE */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>BACKGROUNDS & APPEARANCE</Text>
        </View>

        <GlassSurface variant="card" style={styles.settingsCard}>
          <Text style={styles.settingLabel}>SELECTABLE BACKGROUND THEME</Text>
          <View style={styles.themeRow}>
            {[
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
  headerTitle: { fontFamily: 'monospace', fontSize: 13, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  profileCard: { padding: 18, marginVertical: 8, borderRadius: 18 },
  avatarRow: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  avatarTouch: { position: 'relative' },
  avatarImage: { width: 68, height: 68, borderRadius: 34, borderWidth: 2, borderColor: '#FFFFFF' },
  avatarOverlay: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(0,0,0,0.5)', borderRadius: 34, justifyContent: 'center', alignItems: 'center' },
  avatarBadge: { position: 'absolute', bottom: -2, right: -2, width: 22, height: 22, borderRadius: 11, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center' },
  avatarBadgeText: { fontSize: 11 },
  profileInfo: { flex: 1 },
  profileName: { fontSize: 16, fontWeight: 'bold', color: '#FFFFFF' },
  profileEmail: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', marginTop: 2 },
  uploadBtn: { marginTop: 6 },
  uploadBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 0.8 },
  sectionHeader: { marginTop: 14, marginBottom: 6 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4 },
  socialCard: { padding: 16, borderRadius: 18, gap: 12 },
  socialRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 },
  providerLeft: { flex: 1, paddingRight: 10 },
  socialName: { fontSize: 13.5, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 0.5 },
  socialHandle: { fontSize: 11, color: 'rgba(255, 255, 255, 0.5)', marginTop: 2 },
  socialToggleBtn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  socialBtnConnected: { backgroundColor: 'rgba(255, 255, 255, 0.15)', borderColor: '#FFFFFF' },
  socialBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  settingsCard: { padding: 18, borderRadius: 18 },
  settingLabel: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', marginBottom: 8 },
  settingToggleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginVertical: 6 },
  settingToggleLabel: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF' },
  settingCopy: { flex: 1, paddingRight: 12 },
  settingHint: { marginTop: 3, fontSize: 10, color: 'rgba(255, 255, 255, 0.55)' },
  toggleBtn: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  toggleBtnActive: { backgroundColor: 'rgba(255, 255, 255, 0.2)', borderColor: '#FFFFFF' },
  toggleBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  themeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  themePill: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 12 },
  themePillActive: { backgroundColor: 'rgba(255, 255, 255, 0.25)', borderColor: '#FFFFFF' },
  themeText: { fontFamily: 'monospace', fontSize: 11, color: 'rgba(255, 255, 255, 0.7)' },
  themeTextActive: { color: '#FFFFFF', fontWeight: 'bold' },
  customBgBtn: { marginTop: 14, backgroundColor: 'rgba(255, 255, 255, 0.12)', paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: '#FFFFFF', alignItems: 'center' }, customBackgroundPreview: { width: '100%', height: 110, borderRadius: 12, marginTop: 14 }, customBgRemoveBtn: { marginTop: 8, backgroundColor: 'rgba(255, 98, 95, 0.18)', paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: '#FFB4B2', alignItems: 'center' },
  customBgBtnText: { fontFamily: 'monospace', color: '#FFFFFF', fontWeight: 'bold', fontSize: 11 }
});
