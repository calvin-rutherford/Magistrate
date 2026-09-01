import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from 'react-native';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { useRouter } from 'expo-router';
import { GATEWAY_URL, getGatewaySessionToken } from '../src/api/client';

export default function ARGlassesSetupScreen() {
  const router = useRouter();
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [logs, setLogs] = useState<string[]>([]);
  const wsUrl = GATEWAY_URL.replace(/^http/, 'ws').replace(/\/api\/v1$/, '') + '/ws/ar-interface';

  useEffect(() => {
    let ws: WebSocket;
    getGatewaySessionToken().then(token => {
      if (!token) return;
      try {
        ws = new WebSocket(wsUrl);
        ws.onopen = () => {
          ws.send(JSON.stringify({ type: 'auth', token }));
          setWsConnected(true);
          setLogs(prev => [...prev, '[SYSTEM] Connected to AR Interface WebSocket']);
        };
        ws.onmessage = (e) => {
          setLogs(prev => [...prev, `[RX] ${e.data}`]);
        };
        ws.onclose = () => {
          setWsConnected(false);
          setLogs(prev => [...prev, '[SYSTEM] WebSocket Disconnected']);
        };
      } catch (e) {
        console.error('WS Error:', e);
      }
    });
    return () => {
      if (ws) ws.close();
    };
  }, []);

  const sendTestCommand = async (modality: string, payload: string) => {
    if (!wsConnected) return;
    try {
      const token = await getGatewaySessionToken();
      if (!token) return;
      const ws = new WebSocket(wsUrl);
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'auth', token }));
        ws.send(JSON.stringify({ type: 'input', modality, payload }));
        setLogs(prev => [...prev, `[TX] Sent ${modality} command: ${payload}`]);
        setTimeout(() => ws.close(), 1000);
      };
    } catch (e) {
      console.error(e);
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
        <Text style={styles.headerTitle}>AR GLASSES SYNC</Text>
        <View style={{ width: 36 }} />
      </View>

      <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 120 }}>
        <GlassSurface variant="card" style={styles.qrCard}>
          <Text style={styles.qrTitle}>PAIRING CODE</Text>
          <View style={styles.qrBox}>
            <Text style={styles.qrText}>Authenticated gateway pairing</Text>
          </View>
          <Text style={styles.qrSubText}>Scan or enter this payload into your AR device to authenticate with Magistrate.</Text>
        </GlassSurface>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>CONNECTION STATUS</Text>
        </View>
        <GlassSurface variant="card" style={styles.statusCard}>
          <View style={styles.statusRow}>
            <Text style={styles.statusLabel}>WEBSOCKET (ar-interface)</Text>
            <View style={[styles.statusDot, { backgroundColor: wsConnected ? '#34D399' : '#EF4444' }]} />
            <Text style={styles.statusText}>{wsConnected ? 'CONNECTED' : 'OFFLINE'}</Text>
          </View>
        </GlassSurface>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>TEST INPUT CONTRACT</Text>
        </View>
        <View style={styles.btnRow}>
          <TouchableOpacity style={styles.testBtn} onPress={() => sendTestCommand('gesture', 'swipe_right')}>
            <Text style={styles.testBtnText}>TEST GESTURE</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.testBtn} onPress={() => sendTestCommand('speech', 'status report')}>
            <Text style={styles.testBtnText}>TEST SPEECH</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>INTERFACE LOGS</Text>
        </View>
        <GlassSurface variant="card" style={styles.logsCard}>
          {logs.length === 0 && <Text style={styles.logText}>Waiting for connection...</Text>}
          {logs.map((l, i) => (
            <Text key={i} style={styles.logText}>{l}</Text>
          ))}
        </GlassSurface>
      </ScrollView>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 16 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12, marginBottom: 12 },
  headerTitle: { fontFamily: 'monospace', fontSize: 13, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  qrCard: { padding: 24, borderRadius: 18, alignItems: 'center', marginTop: 12 },
  qrTitle: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2, marginBottom: 16 },
  qrBox: { borderWidth: 2, borderColor: '#FFFFFF', padding: 24, borderRadius: 12, backgroundColor: '#FFFFFF', marginBottom: 16 },
  qrText: { fontFamily: 'monospace', fontSize: 12, color: '#000000', fontWeight: 'bold', textAlign: 'center' },
  qrSubText: { fontSize: 11, color: 'rgba(255, 255, 255, 0.6)', textAlign: 'center', lineHeight: 16 },
  sectionHeader: { marginTop: 18, marginBottom: 8 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4 },
  statusCard: { padding: 16, borderRadius: 16 },
  statusRow: { flexDirection: 'row', alignItems: 'center' },
  statusLabel: { fontFamily: 'monospace', fontSize: 11, color: '#FFFFFF', flex: 1 },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 8 },
  statusText: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF' },
  btnRow: { flexDirection: 'row', gap: 12 },
  testBtn: { flex: 1, paddingVertical: 12, borderRadius: 12, borderWidth: 1, borderColor: '#FFFFFF', backgroundColor: 'rgba(255, 255, 255, 0.1)', alignItems: 'center' },
  testBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' },
  logsCard: { padding: 16, borderRadius: 16, minHeight: 120 },
  logText: { fontFamily: 'monospace', fontSize: 9, color: 'rgba(255, 255, 255, 0.7)', marginBottom: 6 }
});
