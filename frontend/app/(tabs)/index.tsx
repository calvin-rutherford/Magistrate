import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl, Platform } from 'react-native';
import * as Linking from 'expo-linking';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { StatusRing } from '../../src/components/StatusRing';
import { fetchAgents, fetchGitHubPRs, AgentInfo, GitHubPR } from '../../src/api/client';
import { useRouter } from 'expo-router';

export default function HomeScreen() {
  const router = useRouter();

  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [prs, setPrs] = useState<GitHubPR[]>([]);
  const [refreshing, setRefreshing] = useState<boolean>(false);

  const loadData = async () => {
    setRefreshing(true);
    try {
      const [agentData, prData] = await Promise.all([
        fetchAgents().catch(() => []),
        fetchGitHubPRs().catch(() => [])
      ]);
      setAgents(agentData);
      setPrs(prData);
    } catch (e) {
      console.error('Home load error:', e);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const openPR = (url?: string) => {
    if (!url) return;
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      window.open(url, '_blank');
    } else {
      Linking.openURL(url).catch(() => {});
    }
  };

  const openAgentChat = (agent: AgentInfo) => {
    router.push({
      pathname: '/chat',
      params: { pane: agent.pane_id || agent.id },
    } as any);
  };

  const activeAgents = agents.filter((a: any) => a.status === 'working' || a.status === 'RUNNING' || !a.status);
  const blockedAgents = agents.filter((a: any) => a.status === 'blocked' || a.status === 'BLOCKED');
  const onHoldAgents = agents.filter((a: any) => a.status === 'idle' || a.status === 'IDLE');

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <Text style={styles.headerTitle}>MAGISTRATE</Text>
      </View>

      <ScrollView
        style={styles.container}
        contentContainerStyle={{ paddingBottom: 110 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={loadData} tintColor="#FFFFFF" />}
      >
        <View style={styles.sphereContainer}>
          <TouchableOpacity onPress={() => router.push('/status' as any)} activeOpacity={0.85}>
            <StatusRing statusText="OPERATIONAL" subText="All Systems Operational" />
          </TouchableOpacity>
          <Text style={styles.sphereHint}>TAP SPHERE FOR SYSTEM TELEMETRY ↗</Text>
        </View>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>ACTIVE AGENTS ({activeAgents.length || 1})</Text>
        </View>

        {activeAgents.length === 0 ? (
          <GlassSurface variant="card" style={styles.emptyCard}>
            <Text style={styles.emptyText}>No active agents.</Text>
          </GlassSurface>
        ) : activeAgents.map((a: any) => (
          <TouchableOpacity accessibilityRole="link" accessibilityLabel={`Open ${a.name} pane in Chat`} key={a.id} onPress={() => openAgentChat(a)} activeOpacity={0.85}>
            <GlassSurface variant="card" style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.agentName}>{a.name}</Text>
                <View style={styles.statusBadge}>
                  <Text style={styles.statusBadgeText}>ACTIVE ✓</Text>
                </View>
              </View>
              <Text style={styles.taskText}>Firstmate Autonomous Control Loop</Text>
              <Text style={styles.harnessText}>Harness: {a.harness || 'Claude 3.7 Sonnet'}</Text>
            </GlassSurface>
          </TouchableOpacity>
        ))}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>NEEDS ATTENTION ({blockedAgents.length})</Text>
        </View>

        {blockedAgents.length === 0 ? (
          <GlassSurface variant="card" style={styles.emptyCard}>
            <Text style={styles.emptyText}>No blocked agents or pending decisions.</Text>
          </GlassSurface>
        ) : (
          blockedAgents.map((a: any) => (
            <TouchableOpacity accessibilityRole="link" accessibilityLabel={`Open ${a.name} pane in Chat`} key={a.id} onPress={() => openAgentChat(a)} activeOpacity={0.85}>
              <GlassSurface variant="card" style={styles.card}>
                <Text style={styles.agentName}>{a.name}</Text>
                <Text style={styles.taskText}>Blocked waiting for input</Text>
              </GlassSurface>
            </TouchableOpacity>
          ))
        )}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>ON HOLD ({onHoldAgents.length})</Text>
        </View>

        {onHoldAgents.length === 0 ? (
          <GlassSurface variant="card" style={styles.emptyCard}>
            <Text style={styles.emptyText}>No queued or paused agents.</Text>
          </GlassSurface>
        ) : (
          onHoldAgents.map((a: any) => (
            <TouchableOpacity accessibilityRole="link" accessibilityLabel={`Open ${a.name} pane in Chat`} key={a.id} onPress={() => openAgentChat(a)} activeOpacity={0.85}>
              <GlassSurface variant="card" style={styles.card}>
                <Text style={styles.agentName}>{a.name}</Text>
                <Text style={styles.taskText}>Queued idle worker</Text>
              </GlassSurface>
            </TouchableOpacity>
          ))
        )}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>PULL REQUESTS ({prs.length})</Text>
        </View>

        {prs.map(pr => (
          <TouchableOpacity key={pr.pr_number} onPress={() => openPR(pr.url)} activeOpacity={0.85}>
            <GlassSurface variant="card" style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.prNumber}>PR #{pr.pr_number}</Text>
                <View style={styles.prBadge}>
                  <Text style={styles.prBadgeText}>{pr.review_status}</Text>
                </View>
              </View>
              <Text style={styles.prTitle}>{pr.title}</Text>
              <Text style={styles.prRepo}>{pr.repository} • {pr.branch}</Text>
              <View style={styles.prFooter}>
                <Text style={styles.prLinkText}>VIEW ON GITHUB ↗</Text>
              </View>
            </GlassSurface>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 16 },
  headerRow: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12, marginBottom: 4 },
  headerTitle: { fontFamily: 'monospace', fontSize: 15, fontWeight: '300', color: '#FFFFFF', letterSpacing: 3 },
  sphereContainer: { alignItems: 'center', marginVertical: 14 },
  sphereHint: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.5)', marginTop: 8, letterSpacing: 1 },
  sectionHeader: { marginTop: 14, marginBottom: 8 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  card: { padding: 16, borderRadius: 18, marginVertical: 4 },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  agentName: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF' },
  statusBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  statusBadgeText: { fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', color: '#FFFFFF' },
  taskText: { fontSize: 12, color: 'rgba(255, 255, 255, 0.7)', lineHeight: 16 },
  harnessText: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.5)', marginTop: 8 },
  emptyCard: { padding: 14, borderRadius: 14, marginVertical: 4 },
  emptyText: { fontSize: 12, color: 'rgba(255, 255, 255, 0.5)' },
  prNumber: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF' },
  prBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  prBadgeText: { fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', color: '#FFFFFF' },
  prTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 4 },
  prRepo: { fontSize: 11, color: 'rgba(255, 255, 255, 0.6)', marginBottom: 8 },
  prFooter: { borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.08)', paddingTop: 6, alignItems: 'flex-end' },
  prLinkText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' }
});
