import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { fetchAgents, AgentInfo } from '../../src/api/client';

export default function FleetScreen() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  useEffect(() => {
    const loadFleet = async () => {
      try {
        const ag = await fetchAgents();
        setAgents(ag || []);
      } catch (e) {
        console.error(e);
      }
    };
    loadFleet();
  }, []);

  return (
    <ScrollView style={styles.container}>
      <Text style={styles.header}>FIRSTMATE CREW & AGENT FLEET</Text>
      <Text style={styles.subHeader}>Harness: Codex CLI | Server: melkezic-dev-01</Text>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>HERDR ACTIVE SESSIONS ({agents.length})</Text>
        {agents.length === 0 ? (
          <Text style={styles.emptyText}>No Herdr agent sessions active.</Text>
        ) : (
          agents.map((ag) => (
            <View key={ag.id} style={styles.card}>
              <Text style={styles.cardTitle}>{ag.name}</Text>
              <Text style={styles.cardDetail}>Harness: {ag.harness || 'Unavailable'} | Status: {String(ag.status || 'unknown').toUpperCase()}</Text>
              <Text style={styles.cardDetail}>Pane ID: {ag.pane_id || ag.id}</Text>
            </View>
          ))
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0B0F19', padding: 16 },
  header: { fontSize: 18, fontWeight: 'bold', color: '#38BDF8', letterSpacing: 1.5 },
  subHeader: { fontSize: 12, color: '#64748B', marginBottom: 16, marginTop: 2 },
  section: { marginBottom: 20 },
  sectionTitle: { fontSize: 14, fontWeight: 'bold', color: '#94A3B8', borderBottomWidth: 1, borderBottomColor: '#23304D', paddingBottom: 6, marginBottom: 10 },
  emptyText: { color: '#64748B', fontSize: 13, fontStyle: 'italic' },
  card: { backgroundColor: '#161F33', borderRadius: 8, padding: 14, marginBottom: 8, borderWidth: 1, borderColor: '#23304D' },
  cardTitle: { fontSize: 15, fontWeight: 'bold', color: '#F8FAFC' },
  cardDetail: { fontSize: 12, color: '#94A3B8', marginTop: 4 }
});
