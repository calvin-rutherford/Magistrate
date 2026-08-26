import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { GlassSurface } from '../../src/components/GlassSurface';
import { AgentInfo, fetchAgents } from '../../src/api/client';

const errorText = (error: unknown) => error instanceof Error ? error.message : 'Agent data could not be loaded.';

export default function AgentsScreen() {
  const router = useRouter();
  const { agentId } = useLocalSearchParams<{ agentId?: string }>();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDrawer, setShowDrawer] = useState(false);

  const loadAgents = async () => {
    setRefreshing(true);
    setError(null);
    try {
      setAgents(await fetchAgents());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadAgents();
  }, []);

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>
        <Text style={styles.headerTitle}>HERDR AGENTS</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open navigation" onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <ScrollView
        style={styles.container}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={loadAgents} tintColor="#72F5B1" />}
      >
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>LIVE AGENT SESSIONS ({agents.length})</Text>
        </View>

        {loading && <GlassSurface variant="card" style={styles.card}><Text style={styles.mutedText}>Loading live agent data…</Text></GlassSurface>}
        {!loading && error && <GlassSurface variant="card" style={styles.card}><Text style={styles.errorText}>{agents.length ? `Showing last known agents. ${error}` : error}</Text></GlassSurface>}
        {!loading && !error && agents.length === 0 && <GlassSurface variant="card" style={styles.card}><Text style={styles.mutedText}>No Herdr agent sessions are active.</Text></GlassSurface>}

        {agents.map(agent => {
          const selected = agent.id === agentId;
          return (
            <GlassSurface key={agent.id} variant="card" style={[styles.card, selected ? styles.selectedCard : undefined]}>
              <View style={styles.cardHeader}>
                <Text style={styles.agentName}>{agent.name || agent.id}</Text>
                <Text style={styles.statusText}>{agent.status ? agent.status.toUpperCase() : 'STATUS UNAVAILABLE'}</Text>
              </View>
              {agent.harness ? <Text style={styles.detailText}>Harness: {agent.harness}</Text> : null}
              <Text style={styles.detailText}>Pane: {agent.pane_id || agent.id}</Text>
              {agent.tab_id ? <Text style={styles.detailText}>Tab: {agent.tab_id}</Text> : null}
            </GlassSurface>
          );
        })}
      </ScrollView>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={route => router.push('/' + route as any)}
        activeAgentsCount={agents.filter(agent => ['working', 'running'].includes(String(agent.status || '').toLowerCase())).length}
        attentionCount={0}
        prsCount={0}
      />
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 16 },
  content: { paddingBottom: 110 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12, marginBottom: 8 },
  headerTitle: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  sectionHeader: { marginTop: 14, marginBottom: 8 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4 },
  card: { padding: 16, marginVertical: 6, borderRadius: 18 },
  selectedCard: { borderColor: '#72F5B1', borderWidth: 2 },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  agentName: { flex: 1, color: '#FFFFFF', fontSize: 15, fontWeight: 'bold', marginRight: 12 },
  statusText: { fontFamily: 'monospace', color: '#72F5B1', fontSize: 10, fontWeight: 'bold' },
  detailText: { color: 'rgba(255, 255, 255, 0.65)', fontFamily: 'monospace', fontSize: 11, marginTop: 4 },
  mutedText: { color: 'rgba(255, 255, 255, 0.55)', fontSize: 12 },
  errorText: { color: '#FCA5A5', fontSize: 12 }
});
