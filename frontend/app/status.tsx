import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { useRouter } from 'expo-router';
import { fetchHealth, HealthInfo } from '../src/api/client';

export default function StatusScreen() {
  const router = useRouter();
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try { setHealth(await fetchHealth()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Gateway status could not be loaded.'); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const gatewayStatus = error ? 'UNAVAILABLE' : health?.status === 'healthy' ? 'HEALTHY' : health?.status?.toUpperCase() || 'CONNECTING';
  const herdrStatus = health?.herdr_socket_connected ? 'CONNECTED' : health ? 'UNAVAILABLE' : 'UNKNOWN';

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
          <Text style={styles.sectionTitle}>GATEWAY TELEMETRY</Text>
        </View>

        <GlassSurface variant="card" style={styles.card}>
          {error && <Text style={styles.errorText}>{error}</Text>}
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>GATEWAY CONNECTION</Text>
            <Text style={styles.metricValue}>{gatewayStatus}</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>HERDR SOCKET</Text>
            <Text style={styles.metricValue}>{herdrStatus}</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>SERVICE</Text>
            <Text style={styles.metricValue}>{health?.service || 'Unavailable'}</Text>
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>HERDR VERSION</Text>
            {/* Null whenever the gateway did not observe a live snapshot; a
                placeholder build number here would be an invented metric. */}
            <Text testID="status-herdr-version" style={styles.metricValue}>{health?.herdr_version || 'Not reported'}</Text>
          </View>
          {health?.degraded_sources?.length ? <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>UNAVAILABLE SOURCES</Text>
            <Text testID="status-degraded-sources" style={styles.metricValue}>{health.degraded_sources.join(', ').toUpperCase()}</Text>
          </View> : null}
        </GlassSurface>

        <TouchableOpacity onPress={load} accessibilityRole="button" accessibilityLabel="Refresh system status"><Text style={styles.refreshText}>REFRESH LIVE STATUS</Text></TouchableOpacity>
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
  ,errorText: { color: '#FCA5A5', fontSize: 12, marginBottom: 10 },
  refreshText: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 10, textAlign: 'center', marginTop: 18 }
});
