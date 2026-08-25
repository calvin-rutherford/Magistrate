import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { useRouter } from 'expo-router';

export default function StatusScreen() {
  const router = useRouter();
  const [latency, setLatency] = useState<number>(18);
  const [tailscaleStatus, setTailscaleStatus] = useState<string>('CONNECTED (100.84.181.23)');

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>SYSTEM STATUS & TELEMETRY</Text>

        <View style={{ width: 36 }} />
      </View>

      <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 110 }}>
        {/* GATEWAY SERVER TELEMETRY */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>MELKEZIC GATEWAY TELEMETRY</Text>
        </View>

        <GlassSurface variant="card" style={styles.card}>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>GATEWAY CONNECTION</Text>
            <Text style={styles.metricValue}>HEALTHY ✓</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>SERVER LATENCY</Text>
            <Text style={styles.metricValue}>{latency} ms</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>TAILSCALE NETWORK</Text>
            <Text style={styles.metricValue}>{tailscaleStatus}</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>CONNECTED PEERS</Text>
            <Text style={styles.metricValue}>melkezic • spectre-iphone</Text>
          </View>
        </GlassSurface>

        {/* AGENT MODEL API QUOTAS (QUOTA AXI) */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>LLM MODEL API QUOTAS (QUOTA AXI)</Text>
        </View>

        <GlassSurface variant="card" style={styles.card}>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>CLAUDE 3.7 SONNET</Text>
            <Text style={styles.metricValue}>84% CAPACITY</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>GPT-4O / CODEX</Text>
            <Text style={styles.metricValue}>92% CAPACITY</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>HERDR LOCAL WORKERS</Text>
            <Text style={styles.metricValue}>UNLIMITED (LOCAL)</Text>
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
    marginBottom: 6
  },
  headerTitle: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  sectionHeader: { marginTop: 14, marginBottom: 6 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4 },
  card: { padding: 16, borderRadius: 18, gap: 10 },
  metricRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 },
  metricLabel: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.65)' },
  metricValue: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF' }
});
