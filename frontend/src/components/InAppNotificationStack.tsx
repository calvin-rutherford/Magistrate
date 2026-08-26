import React, { useEffect, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { router } from 'expo-router';
import { GlassSurface } from './GlassSurface';
import { NotificationEvent } from '../api/client';
import { notificationManager } from '../services/NotificationManager';

export function InAppNotificationStack() {
  const [events, setEvents] = useState<NotificationEvent[]>([]);

  useEffect(() => notificationManager.subscribeFallback(setEvents), []);

  if (!events.length) return null;
  return (
    <View pointerEvents="box-none" style={styles.stack}>
      {events.map(event => (
        <GlassSurface key={event.id} variant="alert" style={styles.card}>
          <View style={styles.copy}>
            <Text style={styles.eyebrow}>NEEDS YOUR ATTENTION</Text>
            <Text style={styles.title}>{event.title}</Text>
            <Text style={styles.subtitle}>{event.subtitle}</Text>
          </View>
          <View style={styles.actions}>
            <TouchableOpacity
              testID={`notification-open-${event.id}`}
              accessibilityRole="button"
              accessibilityLabel={`Open ${event.title}`}
              onPress={() => router.push(event.url as never)}
              style={styles.openButton}
            >
              <Text style={styles.openText}>OPEN</Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID={`notification-dismiss-${event.id}`}
              accessibilityRole="button"
              accessibilityLabel={`Dismiss ${event.title}`}
              onPress={() => notificationManager.dismissFallback(event.id)}
              style={styles.dismissButton}
            >
              <Text style={styles.dismissText}>DISMISS</Text>
            </TouchableOpacity>
          </View>
        </GlassSurface>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  stack: { position: 'absolute', top: 52, left: 16, right: 16, zIndex: 100, gap: 8 },
  card: { padding: 14, borderRadius: 16, borderColor: '#F59E0B', backgroundColor: 'rgba(42, 28, 8, 0.96)' },
  copy: { minWidth: 0 },
  eyebrow: { color: '#F59E0B', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', letterSpacing: 1 },
  title: { color: '#FFFFFF', fontSize: 14, fontWeight: 'bold', marginTop: 5 },
  subtitle: { color: 'rgba(255, 255, 255, 0.72)', fontSize: 12, lineHeight: 17, marginTop: 3 },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 10 },
  openButton: { minHeight: 34, paddingHorizontal: 12, borderRadius: 9, backgroundColor: '#F59E0B', justifyContent: 'center' },
  openText: { color: '#261500', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  dismissButton: { minHeight: 34, paddingHorizontal: 12, borderRadius: 9, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.35)', justifyContent: 'center' },
  dismissText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
});
