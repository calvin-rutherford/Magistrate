import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { fetchAgents, AgentInfo } from '../../src/api/client';
import { agentDisplayName } from '../../src/services/AgentStatus';

export default function FleetScreen() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<boolean>(false);

  useEffect(() => {
    const loadFleet = async () => {
      try {
        const ag = await fetchAgents();
        setAgents(ag || []);
        setError(null);
      } catch (e) {
        // An unreachable fleet is not an empty fleet. Clear the list and say so.
        setAgents([]);
        setError(e instanceof Error && e.message ? e.message : 'The agent fleet could not be loaded.');
      } finally {
        setLoaded(true);
      }
    };
    loadFleet();
  }, []);

  return (
    <ScrollView style={styles.container}>
      <Text style={styles.header}>MAGISTRATE AGENT FLEET</Text>
      <Text style={styles.subHeader}>Live execution sessions</Text>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>HERDR ACTIVE SESSIONS ({!loaded ? '…' : error ? '—' : agents.length})</Text>
        {error ? (
          <Text testID="fleet-error" accessibilityRole="alert" style={styles.errorText}>{error}</Text>
        ) : !loaded ? (
          <Text style={styles.emptyText}>Reading live Herdr sessions…</Text>
        ) : agents.length === 0 ? (
          <Text style={styles.emptyText}>No Herdr agent sessions active.</Text>
        ) : (
          agents.map((ag) => (
            <View key={ag.id} style={styles.card}>
              <Text style={styles.cardTitle}>{agentDisplayName(ag)}</Text>
              <Text style={styles.cardDetail}>Harness: {ag.harness || 'unknown'} | Model: {ag.model || 'unknown'} | Status: {String(ag.status || 'unknown').toUpperCase()}</Text>
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
  errorText: { color: '#FCA5A5', fontSize: 13 },
  card: { backgroundColor: '#161F33', borderRadius: 8, padding: 14, marginBottom: 8, borderWidth: 1, borderColor: '#23304D' },
  cardTitle: { fontSize: 15, fontWeight: 'bold', color: '#F8FAFC' },
  cardDetail: { fontSize: 12, color: '#94A3B8', marginTop: 4 }
});
