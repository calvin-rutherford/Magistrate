import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { useRouter } from 'expo-router';

export default function GestureScreen() {
  const router = useRouter();

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>AR GLASSES GESTURE MODE</Text>

        <View style={{ width: 36 }} />
      </View>

      <View style={styles.container}>
        <GlassSurface variant="card" style={styles.card}>
          <Text style={styles.title}>SPATIAL GESTURE INTERFACE</Text>
          <Text style={styles.subtitle}>
            PAIRED WITH FIRSTMATE AR GLASSES MULTIMODAL HUB
          </Text>
          <View style={styles.divider} />
          <Text style={styles.status}>
            STATUS: AWAITING SPATIAL INPUTS (PLACEHOLDER MODE)
          </Text>
        </GlassSurface>
      </View>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 20 },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 12,
    marginBottom: 6
  },
  headerTitle: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  card: { padding: 24, borderRadius: 20, alignItems: 'center', width: '100%' },
  title: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5, marginBottom: 8 },
  subtitle: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', textAlign: 'center', marginBottom: 16 },
  divider: { height: 1, backgroundColor: 'rgba(255, 255, 255, 0.1)', width: '100%', marginBottom: 16 },
  status: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.5)', letterSpacing: 1 }
});
