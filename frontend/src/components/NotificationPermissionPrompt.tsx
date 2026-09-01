import React, { useEffect, useState } from 'react';
import { Platform, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { GlassSurface } from './GlassSurface';
import {
  hasAskedWebNotificationPermission,
  markWebNotificationPermissionAsked,
} from '../services/NotificationPermissionPreferences';

function getBrowserNotification(): typeof Notification | undefined {
  return (globalThis as any).Notification;
}

export function NotificationPermissionPrompt() {
  const [visible, setVisible] = useState(false);

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
      <GlassSurface variant="alert" style={styles.card}>
        <Text style={styles.eyebrow}>STAY IN THE LOOP</Text>
        <Text style={styles.title}>Enable browser notifications?</Text>
        <Text style={styles.body}>
          Magistrate can alert you while this tab is open and your browser permits it. Native
          devices use authenticated remote push in the beta build.
        </Text>
        <View style={styles.actions}>
          <TouchableOpacity
            testID="notification-permission-dismiss"
            accessibilityRole="button"
            accessibilityLabel="Not now"
            onPress={dismiss}
            style={styles.dismissButton}
          >
            <Text style={styles.dismissText}>NOT NOW</Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="notification-permission-enable"
            accessibilityRole="button"
            accessibilityLabel="Enable notifications"
            onPress={enable}
            style={styles.enableButton}
          >
            <Text style={styles.enableText}>ENABLE</Text>
          </TouchableOpacity>
        </View>
      </GlassSurface>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', top: 52, left: 16, right: 16, zIndex: 101 },
  card: { padding: 14, borderRadius: 16, borderColor: '#38BDF8', backgroundColor: 'rgba(8, 24, 42, 0.96)' },
  eyebrow: { color: '#38BDF8', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', letterSpacing: 1 },
  title: { color: '#FFFFFF', fontSize: 14, fontWeight: 'bold', marginTop: 5 },
  body: { color: 'rgba(255, 255, 255, 0.72)', fontSize: 12, lineHeight: 17, marginTop: 3 },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 10 },
  enableButton: { minHeight: 34, paddingHorizontal: 12, borderRadius: 9, backgroundColor: '#38BDF8', justifyContent: 'center' },
  enableText: { color: '#04121F', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  dismissButton: { minHeight: 34, paddingHorizontal: 12, borderRadius: 9, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.35)', justifyContent: 'center' },
  dismissText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
});
