import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { GlassSurface } from '../../src/components/GlassSurface';
import { AgentInfo, fetchAgents, fetchGitHubPRs, fetchUnifiedAttention, interruptAgent, sendAgentKey } from '../../src/api/client';
import { summarizeAgents } from '../../src/services/AgentStatus';

const errorText = (error: unknown) => error instanceof Error ? error.message : 'Agent data could not be loaded.';
type AgentAction = 'interrupt' | 'Enter' | 'Escape';

const actionLabel = (action: AgentAction) => action === 'interrupt' ? 'INTERRUPT' : action.toUpperCase();

export default function AgentsScreen() {
  const router = useRouter();
  const { agentId } = useLocalSearchParams<{ agentId?: string }>();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDrawer, setShowDrawer] = useState(false);
  const [pendingAction, setPendingAction] = useState<{ agent: AgentInfo; action: AgentAction } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [controlMessage, setControlMessage] = useState<{ agentId: string; message: string; error?: boolean } | null>(null);
  const [attentionCount, setAttentionCount] = useState(0);
  const [prsCount, setPrsCount] = useState(0);

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
    Promise.allSettled([fetchUnifiedAttention(), fetchGitHubPRs()]).then(([attentionResult, prsResult]) => {
      if (attentionResult.status === 'fulfilled') setAttentionCount(attentionResult.value.filter(item => item.requires_action !== false).length);
      if (prsResult.status === 'fulfilled') setPrsCount(prsResult.value.items.length);
    });
  }, []);

  const runAgentAction = async (agent: AgentInfo, action: AgentAction) => {
    const actionKey = `${agent.id}:${action}`;
    setBusyAction(actionKey);
    setControlMessage(null);
    try {
      const result = action === 'interrupt'
        ? await interruptAgent(agent.id)
        : await sendAgentKey(agent.id, action);
      if (result.status === 'error' || result.error) {
        throw new Error(result.error || `The ${actionLabel(action).toLowerCase()} action was not accepted.`);
      }
      setControlMessage({ agentId: agent.id, message: `${actionLabel(action)} sent to ${agent.name || agent.id}.` });
      if (action === 'interrupt') {
        await loadAgents();
      }
    } catch (cause) {
      setControlMessage({ agentId: agent.id, error: true, message: errorText(cause) });
    } finally {
      setBusyAction(null);
      setPendingAction(null);
    }
  };

  const requestAgentAction = (agent: AgentInfo, action: AgentAction) => {
    setControlMessage(null);
    if (action === 'interrupt' || action === 'Enter') {
      setPendingAction({ agent, action });
      return;
    }
    void runAgentAction(agent, action);
  };

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
              <TouchableOpacity
                testID={`agent-${agent.id}-chat-link`}
                accessibilityRole="button"
                accessibilityLabel={`Open chat with ${agent.name || agent.id}`}
                onPress={() => router.push({ pathname: '/chat', params: { agentId: agent.id } } as any)}
                style={styles.chatLink}
              >
                <Text style={styles.chatLinkText}>OPEN CHAT TARGET →</Text>
              </TouchableOpacity>
              <View style={styles.controlsHeader}>
                <Text style={styles.controlsLabel}>AGENT CONTROLS</Text>
                <Text style={styles.controlsHint}>Actions are sent to this live pane.</Text>
              </View>
              <View style={styles.controlsRow}>
                {(['interrupt', 'Enter', 'Escape'] as AgentAction[]).map(action => {
                  const actionKey = `${agent.id}:${action}`;
                  const busy = busyAction === actionKey;
                  return (
                    <TouchableOpacity
                      key={action}
                      testID={`agent-${agent.id}-${action.toLowerCase()}-control`}
                      accessibilityRole="button"
                      accessibilityLabel={`${actionLabel(action)} ${agent.name || agent.id}`}
                      accessibilityState={{ disabled: busyAction !== null || pendingAction !== null, busy }}
                      disabled={busyAction !== null || pendingAction !== null}
                      onPress={() => requestAgentAction(agent, action)}
                      style={[styles.controlButton, action === 'interrupt' ? styles.interruptButton : undefined, busy ? styles.controlButtonBusy : undefined]}
                    >
                      <Text style={[styles.controlButtonText, action === 'interrupt' ? styles.interruptButtonText : undefined]}>{busy ? '…' : actionLabel(action)}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              {controlMessage?.agentId === agent.id ? (
                <Text testID={`agent-${agent.id}-control-message`} style={controlMessage.error ? styles.controlErrorText : styles.controlSuccessText}>{controlMessage.message}</Text>
              ) : null}
            </GlassSurface>
          );
        })}
      </ScrollView>

      {pendingAction ? (
        <GlassSurface variant="alert" style={styles.confirmationCard}>
          <Text style={styles.confirmationTitle}>CONFIRM {actionLabel(pendingAction.action)}</Text>
          <Text style={styles.confirmationText}>
            {pendingAction.action === 'interrupt'
              ? `Send Ctrl-C to ${pendingAction.agent.name || pendingAction.agent.id}? This may stop its current work.`
              : `Send Enter to ${pendingAction.agent.name || pendingAction.agent.id}? This may approve or execute a pending terminal action.`}
          </Text>
          <View style={styles.confirmationActions}>
            <TouchableOpacity testID="agent-control-cancel" accessibilityRole="button" onPress={() => setPendingAction(null)} style={styles.secondaryButton}>
              <Text style={styles.secondaryButtonText}>CANCEL</Text>
            </TouchableOpacity>
            <TouchableOpacity testID="agent-control-confirm" accessibilityRole="button" onPress={() => void runAgentAction(pendingAction.agent, pendingAction.action)} style={styles.confirmButton}>
              <Text style={styles.confirmButtonText}>CONFIRM</Text>
            </TouchableOpacity>
          </View>
        </GlassSurface>
      ) : null}

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={route => router.push((route === 'index' ? '/' : '/' + route) as any)}
        activeAgentsCount={summarizeAgents(agents).activeCount}
        attentionCount={attentionCount}
        prsCount={prsCount}
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
  errorText: { color: '#FCA5A5', fontSize: 12 },
  chatLink: { alignSelf: 'flex-start', marginTop: 12, paddingVertical: 6 },
  chatLinkText: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  controlsHeader: { marginTop: 16, marginBottom: 8 },
  controlsLabel: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', letterSpacing: 1 },
  controlsHint: { color: 'rgba(255, 255, 255, 0.45)', fontSize: 10, marginTop: 3 },
  controlsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  controlButton: { minHeight: 40, minWidth: 78, paddingHorizontal: 12, borderRadius: 11, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.24)', backgroundColor: 'rgba(255, 255, 255, 0.1)', justifyContent: 'center', alignItems: 'center' },
  controlButtonBusy: { opacity: 0.55 },
  controlButtonText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', letterSpacing: 0.6 },
  interruptButton: { borderColor: 'rgba(252, 165, 165, 0.7)', backgroundColor: 'rgba(127, 29, 29, 0.35)' },
  interruptButtonText: { color: '#FCA5A5' },
  controlSuccessText: { color: '#72F5B1', fontSize: 11, marginTop: 8 },
  controlErrorText: { color: '#FCA5A5', fontSize: 11, marginTop: 8 },
  confirmationCard: { position: 'absolute', left: 16, right: 16, bottom: 92, padding: 16, borderRadius: 18, borderColor: '#FCA5A5', backgroundColor: 'rgba(50, 12, 18, 0.94)' },
  confirmationTitle: { color: '#FCA5A5', fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', letterSpacing: 1 },
  confirmationText: { color: '#FFFFFF', fontSize: 13, lineHeight: 19, marginTop: 8 },
  confirmationActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 14 },
  secondaryButton: { minHeight: 40, paddingHorizontal: 14, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)', justifyContent: 'center' },
  secondaryButtonText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  confirmButton: { minHeight: 40, paddingHorizontal: 14, borderRadius: 10, backgroundColor: '#FCA5A5', justifyContent: 'center' },
  confirmButtonText: { color: '#350A0A', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' }
});
