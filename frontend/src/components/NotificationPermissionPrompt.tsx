import React, { useEffect, useState } from 'react';
import { Platform, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { GlassSurface } from './GlassSurface';
import {
  hasAskedWebNotificationPermission,
  markWebNotificationPermissionAsked,
} from '../services/NotificationPermissionPreferences';
import { useChatColorScheme } from '../services/ChatPreferences';

function getBrowserNotification(): typeof Notification | undefined {
  return (globalThis as any).Notification;
}

export function NotificationPermissionPrompt() {
  const [visible, setVisible] = useState(false);
  const dark = useChatColorScheme() === 'dark';
  const text = dark ? '#F4F5F7' : '#11151B';
  const muted = dark ? '#A5ACB8' : '#667180';
  const edge = dark ? '#303238' : '#D9DCE1';

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const BrowserNotification = getBrowserNotification();
    if (typeof BrowserNotification !== 'function' || BrowserNotification.permission !== 'default') return;
    hasAskedWebNotificationPermission().then(asked => {
      if (!asked) setVisible(true);
    });
  }, []);

  if (!visible) return null;

  const dismiss = () => {
    setVisible(false);
    void markWebNotificationPermissionAsked();
  };

  const enable = async () => {
    const BrowserNotification = getBrowserNotification();
    try {
      await BrowserNotification?.requestPermission();
    } finally {
      dismiss();
    }
  };

  return (
    <View pointerEvents="box-none" style={styles.wrap}>
      <GlassSurface variant="alert" style={[styles.card, { borderColor: edge, backgroundColor: dark ? '#1A1B1E' : '#FFFFFF' }]}>
        <Text style={[styles.eyebrow, { color: muted }]}>Notifications</Text>
        <Text style={[styles.title, { color: text }]}>Enable browser notifications?</Text>
        <Text style={[styles.body, { color: muted }]}>Receive attention updates while this tab is open and your browser permits it. Native devices use authenticated remote push.</Text>
        <View style={styles.actions}>
          <TouchableOpacity
            testID="notification-permission-dismiss"
            accessibilityRole="button"
            accessibilityLabel="Not now"
            onPress={dismiss}
            style={[styles.dismissButton, { borderColor: edge }]}
          >
            <Text style={[styles.dismissText, { color: text }]}>Not now</Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="notification-permission-enable"
            accessibilityRole="button"
            accessibilityLabel="Enable notifications"
            onPress={enable}
            style={styles.enableButton}
          >
            <Text style={styles.enableText}>Enable</Text>
          </TouchableOpacity>
        </View>
      </GlassSurface>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', top: 52, left: 16, right: 16, zIndex: 101, alignItems: 'flex-start' },
  card: { width: '100%', maxWidth: 420, padding: 16, borderRadius: 16 },
  eyebrow: { fontSize: 12, lineHeight: 17, fontWeight: '600' },
  title: { fontSize: 17, lineHeight: 23, fontWeight: '700', marginTop: 4 },
  body: { fontSize: 14, lineHeight: 20, marginTop: 4 },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 10 },
  enableButton: { minHeight: 34, paddingHorizontal: 12, borderRadius: 9, backgroundColor: '#38BDF8', justifyContent: 'center' },
  enableText: { color: '#04121F', fontSize: 13, fontWeight: '700' },
  dismissButton: { minHeight: 34, paddingHorizontal: 12, borderRadius: 9, borderWidth: 1, justifyContent: 'center' },
  dismissText: { fontSize: 13, fontWeight: '600' },
});
