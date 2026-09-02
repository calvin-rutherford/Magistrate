import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { GlassSurface } from '../../src/components/GlassSurface';
import { AgentInfo, AgentMigration, ExecutionProfile, fetchAgentMigration, fetchAgents, fetchExecutionCapabilities, fetchGitHubPRs, fetchUnifiedAttention, interruptAgent, requestAgentMigration, sendAgentKey } from '../../src/api/client';
import { agentDisplayName, summarizeAgents } from '../../src/services/AgentStatus';

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
  const [executionProfiles, setExecutionProfiles] = useState<ExecutionProfile[]>([]);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [migrationAgent, setMigrationAgent] = useState<AgentInfo | null>(null);
  const [pendingMigration, setPendingMigration] = useState<{ agent: AgentInfo; profile: ExecutionProfile; idempotencyKey: string } | null>(null);
  const [migrationBusy, setMigrationBusy] = useState(false);
  const [migrations, setMigrations] = useState<Record<string, AgentMigration>>({});

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
    Promise.allSettled([fetchUnifiedAttention(), fetchGitHubPRs(), fetchExecutionCapabilities()]).then(([attentionResult, prsResult, capabilityResult]) => {
      if (attentionResult.status === 'fulfilled') setAttentionCount(attentionResult.value.filter(item => item.requires_action !== false).length);
      if (prsResult.status === 'fulfilled') setPrsCount(prsResult.value.items.length);
      if (capabilityResult.status === 'fulfilled') {
        setExecutionProfiles((capabilityResult.value.profiles || []).filter(profile => profile.available));
        setExecutionError(null);
      } else {
        setExecutionError(errorText(capabilityResult.reason));
      }
    });
  }, []);

  useEffect(() => {
    const pending = Object.values(migrations).filter(item => item.status === 'requested' || item.status === 'relaunching');
    if (!pending.length) return;
    let mounted = true;
    const refresh = () => Promise.all(pending.map(item => fetchAgentMigration(item.agent_id, item.request_id))).then(updated => {
      if (!mounted) return;
      setMigrations(current => updated.reduce((next, item) => ({ ...next, [item.agent_id]: item }), current));
    }).catch(error => {
      if (!mounted) return;
      const first = pending[0];
      setControlMessage({ agentId: first.agent_id, error: true, message: errorText(error) });
    });
    const interval = setInterval(refresh, 3000);
    return () => { mounted = false; clearInterval(interval); };
  }, [migrations]);

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
      setControlMessage({ agentId: agent.id, message: `${actionLabel(action)} sent to ${agentDisplayName(agent)}.` });
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

  const confirmMigration = async () => {
    if (!pendingMigration) return;
    setMigrationBusy(true);
    setControlMessage(null);
    try {
      const migration = await requestAgentMigration(pendingMigration.agent.id, pendingMigration.profile.id, pendingMigration.idempotencyKey);
      setMigrations(current => ({ ...current, [pendingMigration.agent.id]: migration }));
      setControlMessage({ agentId: pendingMigration.agent.id, message: 'Migration requested. No agent was stopped: operator confirmation and relaunch in the Firstmate terminal are still required.' });
      setPendingMigration(null);
      setMigrationAgent(null);
    } catch (cause) {
      setControlMessage({ agentId: pendingMigration.agent.id, error: true, message: `${errorText(cause)} Retry keeps the same request key.` });
    } finally {
      setMigrationBusy(false);
    }
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

        {executionError && <GlassSurface variant="card" style={styles.card}><Text testID="agents-execution-error" style={styles.errorText}>{executionError}</Text></GlassSurface>}
        {loading && <GlassSurface variant="card" style={styles.card}><Text style={styles.mutedText}>Loading live agent data…</Text></GlassSurface>}
        {!loading && error && <GlassSurface variant="card" style={styles.card}><Text style={styles.errorText}>{agents.length ? `Showing last known agents. ${error}` : error}</Text></GlassSurface>}
        {!loading && !error && agents.length === 0 && <GlassSurface variant="card" style={styles.card}><Text style={styles.mutedText}>No Herdr agent sessions are active.</Text></GlassSurface>}

        {agents.map(agent => {
          const selected = agent.id === agentId;
          return (
            <GlassSurface key={agent.id} variant="card" style={[styles.card, selected ? styles.selectedCard : undefined]}>
              <View style={styles.cardHeader}>
                <Text style={styles.agentName}>{agentDisplayName(agent)}</Text>
                <Text style={styles.statusText}>{agent.status ? agent.status.toUpperCase() : 'STATUS UNAVAILABLE'}</Text>
              </View>
              <Text testID={`agent-${agent.id}-runtime`} style={styles.detailText}>Harness: {agent.harness || 'unknown'} · Model: {agent.model || 'unknown'}</Text>
              <TouchableOpacity
                testID={`agent-${agent.id}-chat-link`}
                accessibilityRole="button"
                accessibilityLabel={`Open chat with ${agentDisplayName(agent)}`}
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
                      accessibilityLabel={`${actionLabel(action)} ${agentDisplayName(agent)}`}
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
              <TouchableOpacity
                testID={`agent-${agent.id}-move-runtime`}
                accessibilityRole="button"
                accessibilityLabel={`Plan stop and relaunch for ${agentDisplayName(agent)}`}
                accessibilityState={{ disabled: !['working', 'running', 'active', 'executing'].includes(String(agent.status || '').toLowerCase()) || executionProfiles.length === 0 || migrationBusy }}
                disabled={!['working', 'running', 'active', 'executing'].includes(String(agent.status || '').toLowerCase()) || executionProfiles.length === 0 || migrationBusy}
                onPress={() => setMigrationAgent(current => current?.id === agent.id ? null : agent)}
                style={[styles.moveButton, executionProfiles.length === 0 ? styles.controlButtonBusy : undefined]}
              >
                <Text style={styles.moveButtonText}>MOVE RUNTIME (STOP + RELAUNCH)</Text>
              </TouchableOpacity>
              {migrationAgent?.id === agent.id ? <View testID={`agent-${agent.id}-migration-targets`} style={styles.migrationTargets}>
                <Text style={styles.controlsHint}>Choose a verified target. This creates a terminal hand-off; it does not stop the agent.</Text>
                {executionProfiles.map(profile => <TouchableOpacity key={profile.id} testID={`agent-${agent.id}-migration-${profile.id.replace(/[^A-Za-z0-9_-]/g, '-')}`} accessibilityRole="button" onPress={() => setPendingMigration({ agent, profile, idempotencyKey: `move_${Date.now()}_${agent.id.replace(/[^A-Za-z0-9]/g, '')}` })} style={styles.migrationTarget}><Text style={styles.migrationTargetText}>{profile.harness.label} · {profile.model.label}</Text></TouchableOpacity>)}
              </View> : null}
              {migrations[agent.id] ? <Text testID={`agent-${agent.id}-migration-state`} style={styles.migrationState}>Migration: {migrations[agent.id].status} · {migrations[agent.id].status === 'running-on-new' ? `${migrations[agent.id].target.harness}/${migrations[agent.id].target.model} reported by terminal operator` : migrations[agent.id].status === 'failed' ? `${migrations[agent.id].error || 'Relaunch failed.'} Retry with the same request` : 'requires operator confirmation in terminal'}</Text> : null}
              {controlMessage?.agentId === agent.id ? (
                <Text testID={`agent-${agent.id}-control-message`} style={controlMessage.error ? styles.controlErrorText : styles.controlSuccessText}>{controlMessage.message}</Text>
              ) : null}
            </GlassSurface>
          );
        })}
      </ScrollView>

      {pendingMigration ? (
        <GlassSurface variant="alert" style={styles.confirmationCard}>
          <Text style={styles.confirmationTitle}>CONFIRM STOP + RELAUNCH PLAN</Text>
          <Text style={styles.confirmationText}>Move {agentDisplayName(pendingMigration.agent)} to {pendingMigration.profile.harness.label} / {pendingMigration.profile.model.label}?</Text>
          <Text style={styles.confirmationText}>Planned preservation: worktree, checked-out branch, original brief, and recorded progress. Not preserved: the in-flight turn.</Text>
          <Text style={styles.confirmationWarning}>This app cannot execute the relaunch yet. Confirming only records a requested hand-off; an operator must confirm and run it in the Firstmate terminal.</Text>
          <View style={styles.confirmationActions}>
            <TouchableOpacity testID="agent-migration-cancel" accessibilityRole="button" onPress={() => setPendingMigration(null)} style={styles.secondaryButton}><Text style={styles.secondaryButtonText}>CANCEL</Text></TouchableOpacity>
            <TouchableOpacity testID="agent-migration-confirm" accessibilityRole="button" disabled={migrationBusy} onPress={() => void confirmMigration()} style={styles.confirmButton}><Text style={styles.confirmButtonText}>{migrationBusy ? 'REQUESTING…' : 'CONFIRM REQUEST'}</Text></TouchableOpacity>
          </View>
        </GlassSurface>
      ) : pendingAction ? (
        <GlassSurface variant="alert" style={styles.confirmationCard}>
          <Text style={styles.confirmationTitle}>CONFIRM {actionLabel(pendingAction.action)}</Text>
          <Text style={styles.confirmationText}>
            {pendingAction.action === 'interrupt'
              ? `Send Ctrl-C to ${agentDisplayName(pendingAction.agent)}? This may stop its current work.`
              : `Send Enter to ${agentDisplayName(pendingAction.agent)}? This may approve or execute a pending terminal action.`}
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
  moveButton: { alignSelf: 'flex-start', minHeight: 38, marginTop: 14, paddingHorizontal: 12, borderRadius: 10, borderWidth: 1, borderColor: '#72F5B1', justifyContent: 'center' },
  moveButtonText: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  migrationTargets: { marginTop: 8, gap: 7 },
  migrationTarget: { minHeight: 38, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.25)', paddingHorizontal: 10, justifyContent: 'center' },
  migrationTargetText: { color: '#FFFFFF', fontSize: 11, fontWeight: 'bold' },
  migrationState: { color: '#FCD34D', fontSize: 11, lineHeight: 16, marginTop: 9 },
  confirmationCard: { position: 'absolute', left: 16, right: 16, bottom: 92, padding: 16, borderRadius: 18, borderColor: '#FCA5A5', backgroundColor: 'rgba(50, 12, 18, 0.94)' },
  confirmationTitle: { color: '#FCA5A5', fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', letterSpacing: 1 },
  confirmationText: { color: '#FFFFFF', fontSize: 13, lineHeight: 19, marginTop: 8 },
  confirmationWarning: { color: '#FCD34D', fontSize: 11, lineHeight: 16, marginTop: 9 },
  confirmationActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 14 },
  secondaryButton: { minHeight: 40, paddingHorizontal: 14, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)', justifyContent: 'center' },
  secondaryButtonText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  confirmButton: { minHeight: 40, paddingHorizontal: 14, borderRadius: 10, backgroundColor: '#FCA5A5', justifyContent: 'center' },
  confirmButtonText: { color: '#350A0A', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' }
});
