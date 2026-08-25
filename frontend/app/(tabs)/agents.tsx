import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { fetchAgents, fetchFleet, fetchAttention, AgentInfo } from '../../src/api/client';
import { GlassTokens } from '../../src/theme/glass';
import { useRouter } from 'expo-router';

export default function ActiveAgentsScreen() {
  const router = useRouter();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [filter, setFilter] = useState<string>('All');
  const [showDrawer, setShowDrawer] = useState<boolean>(false);

  const [fleetData, setFleetData] = useState<any>({ tasks: [] });
  const [attentionItems, setAttentionItems] = useState<any[]>([]);

  useEffect(() => {
    fetchAgents().then(setAgents).catch(() => []);
    fetchFleet().then(setFleetData).catch(() => ({ tasks: [] }));
    fetchAttention().then(setAttentionItems).catch(() => []);
  }, []);

  const handleNavigate = (route: string) => {
    if (route === 'agents') return;
    router.push('/' + route as any);
  };

  const filteredAgents = filter === 'All'
    ? agents
    : agents.filter(a => a.status.toLowerCase() === filter.toLowerCase());

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>ACTIVE AGENTS</Text>

        <TouchableOpacity onPress={() => setShowDrawer(!showDrawer)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.menuIconText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 100 }}>
        <Text style={styles.subtitle}>Herdr & Codex harness telemetry</Text>

        <View style={styles.filterRow}>
          {['All', 'Working', 'Blocked', 'Done', 'Idle'].map(f => (
            <TouchableOpacity key={f} onPress={() => setFilter(f)}>
              <GlassSurface
                variant="control"
                style={[styles.filterPill, filter === f ? styles.filterPillActive : undefined]}
              >
                <Text style={[styles.filterText, filter === f ? styles.filterTextActive : undefined]}>{f}</Text>
              </GlassSurface>
            </TouchableOpacity>
          ))}
        </View>

        {filteredAgents.length === 0 ? (
          <GlassSurface variant="card" style={styles.emptyCard}>
            <Text style={styles.emptyText}>No active agents in this filter.</Text>
          </GlassSurface>
        ) : (
          filteredAgents.map(a => (
            <GlassSurface key={a.id} variant="card" style={styles.agentRow}>
              <View style={[styles.statusDot, { backgroundColor: a.status === 'working' ? '#72F5B1' : '#FFAA20' }]} />
              <View style={styles.infoCol}>
                <Text style={styles.agentName}>{a.name}</Text>
                <Text style={styles.agentHarness}>{a.harness} • {a.status.toUpperCase()}</Text>
              </View>
              <Text style={styles.paneId}>{a.pane_id}</Text>
            </GlassSurface>
          ))
        )}
      </ScrollView>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={handleNavigate}
        activeAgentsCount={agents.length}
        attentionCount={attentionItems.length}
        prsCount={fleetData?.tasks?.length || 3}
      />
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
    marginBottom: 8
  },
  headerTitle: {
    fontSize: 15,
    fontWeight: 'bold',
    color: '#FFFFFF',
    letterSpacing: 2
  },
  headerCircleBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center'
  },
  backText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold'
  },
  menuIconText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold'
  },
  subtitle: { fontSize: 13, color: GlassTokens.colors.textSecondary, marginBottom: 16 },
  filterRow: { flexDirection: 'row', gap: 8, marginBottom: 16 },
  filterPill: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20 },
  filterPillActive: { backgroundColor: 'rgba(114, 245, 177, 0.25)', borderColor: '#72F5B1' },
  filterText: { color: GlassTokens.colors.textSecondary, fontSize: 12, fontWeight: 'bold' },
  filterTextActive: { color: '#72F5B1' },
  emptyCard: { padding: 20, alignItems: 'center' },
  emptyText: { color: GlassTokens.colors.textMuted },
  agentRow: { padding: 16, flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  statusDot: { width: 10, height: 10, borderRadius: 5, marginRight: 12 },
  infoCol: { flex: 1 },
  agentName: { color: '#FFFFFF', fontWeight: 'bold', fontSize: 15 },
  agentHarness: { color: GlassTokens.colors.textSecondary, fontSize: 12, marginTop: 2 },
  paneId: { color: GlassTokens.colors.textMuted, fontSize: 12, fontFamily: 'monospace' }
});
