import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { StatusRing } from '../../src/components/StatusRing';
import { fetchAgents, fetchGitHubPRs, fetchHealth, AgentInfo, GitHubPR, HealthInfo } from '../../src/api/client';
import { useRouter } from 'expo-router';

const isStatus = (agent: AgentInfo, ...statuses: string[]) => statuses.includes(String(agent.status || '').toLowerCase());

const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

export default function HomeScreen() {
  const router = useRouter();

  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [prs, setPrs] = useState<GitHubPR[]>([]);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [prError, setPrError] = useState<string | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  const loadData = async (forceRefresh = false) => {
    if (!agents.length && !prs.length && !health) setLoading(true);
    setRefreshing(true);
    setAgentError(null);
    setPrError(null);
    setHealthError(null);

    const [agentResult, prResult, healthResult] = await Promise.allSettled([
      fetchAgents(),
      fetchGitHubPRs(1, forceRefresh),
      fetchHealth()
    ]);

    if (agentResult.status === 'fulfilled') {
      setAgents(agentResult.value);
    } else {
      setAgentError(errorText(agentResult.reason, 'Agent data could not be loaded.'));
    }

    if (prResult.status === 'fulfilled') {
      setPrs(prResult.value.items.filter(pr => pr.requires_attention));
    } else {
      setPrError(errorText(prResult.reason, 'Pull requests could not be loaded.'));
    }

    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value);
    } else {
      setHealthError(errorText(healthResult.reason, 'Gateway status could not be loaded.'));
    }

    setLoading(false);
    setRefreshing(false);
  };

  useEffect(() => {
    loadData(false);
  }, []);

  const activeAgents = agents.filter(a => isStatus(a, 'working', 'running'));
  const blockedAgents = agents.filter(a => isStatus(a, 'blocked'));
  const onHoldAgents = agents.filter(a => isStatus(a, 'idle', 'paused', 'queued'));
  const otherAgents = agents.filter(a => !activeAgents.includes(a) && !blockedAgents.includes(a) && !onHoldAgents.includes(a));

  const healthStatus = loading ? 'CONNECTING' : healthError ? 'UNAVAILABLE' : health?.status === 'healthy' ? (health.herdr_socket_connected ? 'OPERATIONAL' : 'DEGRADED') : (health?.status || 'UNKNOWN').toUpperCase();
  const healthColor = healthError || healthStatus === 'UNAVAILABLE' ? '#FCA5A5' : healthStatus === 'OPERATIONAL' ? '#34D399' : '#F59E0B';
  const healthSubtext = loading ? 'Loading gateway status' : healthError ? 'Gateway status unavailable' : health?.herdr_socket_connected ? 'Gateway and Herdr connected' : 'Gateway reachable; Herdr unavailable';

  const renderAgentCard = (agent: AgentInfo) => (
    <TouchableOpacity
      key={agent.id}
      testID={`agent-card-${agent.id}`}
      onPress={() => router.push({ pathname: '/agents', params: { agentId: agent.id } } as any)}
      activeOpacity={0.85}
    >
      <GlassSurface variant="card" style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.agentName}>{agent.name || agent.id}</Text>
          <View style={styles.statusBadge}>
            <Text style={styles.statusBadgeText}>{String(agent.status || 'UNKNOWN').toUpperCase()}</Text>
          </View>
        </View>
        {agent.harness ? <Text style={styles.harnessText}>Harness: {agent.harness}</Text> : null}
        <Text style={styles.agentLinkText}>VIEW AGENT DETAILS →</Text>
      </GlassSurface>
    </TouchableOpacity>
  );

  const renderAgentGroup = (items: AgentInfo[], emptyMessage: string) => {
    if (loading) return <GlassSurface variant="card" style={styles.emptyCard}><Text style={styles.emptyText}>Loading live agent data…</Text></GlassSurface>;
    if (items.length === 0) return <GlassSurface variant="card" style={styles.emptyCard}><Text style={styles.emptyText}>{emptyMessage}</Text></GlassSurface>;
    return items.map(renderAgentCard);
  };

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <Text style={styles.headerTitle}>MAGISTRATE</Text>
      </View>

      <ScrollView
        style={styles.container}
        contentContainerStyle={{ paddingBottom: 110 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => loadData(true)} tintColor="#FFFFFF" />}
      >
        <View style={styles.sphereContainer}>
          <TouchableOpacity onPress={() => router.push('/status' as any)} activeOpacity={0.85}>
            <StatusRing statusText={healthStatus} statusColor={healthColor} subText={healthSubtext} />
          </TouchableOpacity>
          <Text style={styles.sphereHint}>TAP SPHERE FOR SYSTEM TELEMETRY ↗</Text>
        </View>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>ACTIVE AGENTS ({activeAgents.length})</Text>
        </View>
        {agentError && <GlassSurface variant="card" style={styles.emptyCard}><Text style={styles.errorText}>{agents.length ? `Showing last known agents. ${agentError}` : agentError}</Text></GlassSurface>}
        {renderAgentGroup(activeAgents, 'No active agents reported by Herdr.')}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>NEEDS ATTENTION ({blockedAgents.length})</Text>
        </View>

        {renderAgentGroup(blockedAgents, 'No blocked agents reported by Herdr.')}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>ON HOLD ({onHoldAgents.length})</Text>
        </View>

        {renderAgentGroup(onHoldAgents, 'No idle, paused, or queued agents reported by Herdr.')}

        {otherAgents.length > 0 && <>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>OTHER AGENTS ({otherAgents.length})</Text>
          </View>
          {otherAgents.map(renderAgentCard)}
        </>}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>PULL REQUESTS ({prs.length})</Text>
        </View>

        {loading && <GlassSurface variant="card" style={styles.emptyCard}><Text style={styles.emptyText}>Loading live pull requests…</Text></GlassSurface>}
        {prError && <GlassSurface variant="card" style={styles.emptyCard}><Text style={styles.errorText}>{prs.length ? `Showing last known pull requests. ${prError}` : prError}</Text></GlassSurface>}
        {!prError && !loading && prs.length === 0 && <GlassSurface variant="card" style={styles.emptyCard}><Text style={styles.emptyText}>No open pull requests need your attention.</Text></GlassSurface>}
        {prs.map(pr => (
          <TouchableOpacity key={pr.number} onPress={() => router.push(`/pr-detail?number=${pr.number}` as any)} activeOpacity={0.85}>
            <GlassSurface variant="card" style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.prNumber}>PR #{pr.number}</Text>
                <View style={styles.prBadge}>
                  <Text style={styles.prBadgeText}>{pr.review_status}</Text>
                </View>
              </View>
              <Text style={styles.prTitle}>{pr.title}</Text>
              <Text style={styles.prRepo}>{pr.repository}{pr.branch ? ` • ${pr.branch}` : ''}</Text>
              <View style={styles.prFooter}>
                <Text style={styles.prLinkText}>VIEW DETAILS →</Text>
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
  harnessText: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.5)', marginTop: 8 },
  agentLinkText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#72F5B1', marginTop: 12 },
  emptyCard: { padding: 14, borderRadius: 14, marginVertical: 4 },
  emptyText: { fontSize: 12, color: 'rgba(255, 255, 255, 0.5)' },
  errorText: { fontSize: 12, color: '#FCA5A5' },
  prNumber: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF' },
  prBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  prBadgeText: { fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', color: '#FFFFFF' },
  prTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 4 },
  prRepo: { fontSize: 11, color: 'rgba(255, 255, 255, 0.6)', marginBottom: 8 },
  prFooter: { borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.08)', paddingTop: 6, alignItems: 'flex-end' },
  prLinkText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF' }
});
