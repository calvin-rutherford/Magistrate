import React, { useEffect, useState } from 'react';
import { Alert, View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { openExternalUrl } from '../../src/utils/externalLinks';
import { AttentionAction, AttentionActionConfirmation, AttentionActionOutcome, executeAttentionAction, fetchAgents, fetchAttentionActionForItem, fetchGitHubPRs, fetchUnifiedAttention, prepareAttentionAction, UnifiedAttentionRecord } from '../../src/api/client';
import { notificationManager } from '../../src/services/NotificationManager';

function AttentionDetail({ item, confirmation, outcome, actionError, actionBusy, onPrepare, onCancel, onExecute }: {
  item: UnifiedAttentionRecord;
  confirmation: AttentionActionConfirmation | null;
  outcome: AttentionActionOutcome | null;
  actionError: string | null;
  actionBusy: boolean;
  onPrepare: (action: 'approve' | 'reject') => void;
  onCancel: () => void;
  onExecute: () => void;
}) {
  const openExternal = async () => {
    if (!item.external_url) return;
    const result = await openExternalUrl(item.external_url);
    if (!result.ok) Alert.alert('Unable to open attention item', result.message);
  };
  const context = Object.entries(item.context || {}).filter(([, value]) => value !== null && value !== undefined && value !== '');
  const action = item.action;
  return <View testID={`attention-detail-${item.id}`}><GlassSurface variant="card" style={styles.detailCard}>
    <Text style={styles.detailEyebrow}>ATTENTION DETAIL · {item.provider.toUpperCase()}</Text>
    <Text style={styles.detailTitle}>{item.title}</Text>
    <Text style={styles.detailSubtitle}>{item.subtitle}</Text>
    <View style={styles.detailDivider} />
    <Text style={styles.detailLabel}>STATUS</Text>
    <Text style={styles.detailValue}>{item.status || 'ACTION REQUIRED'}{item.priority ? ` · ${item.priority}` : ''}</Text>
    {item.project ? <><Text style={styles.detailLabel}>PROJECT</Text><Text style={styles.detailValue}>{item.project}</Text></> : null}
    {item.target_id ? <><Text style={styles.detailLabel}>TARGET</Text><Text selectable style={styles.detailValue}>{item.target_id}</Text></> : null}
    {context.map(([key, value]) => <View key={key}><Text style={styles.detailLabel}>{key.replaceAll('_', ' ').toUpperCase()}</Text><Text style={styles.detailValue}>{String(value)}</Text></View>)}
    {action?.status === 'available' ? <View testID={`attention-actions-${item.id}`} style={styles.actionBox}>
      <Text style={styles.detailLabel}>CAPTAIN DECISION</Text>
      <Text style={styles.actionBoundary}>This is a keyed Firstmate decision record, not notification acknowledgement.</Text>
      {!confirmation && !outcome ? <View style={styles.actionButtons}>
        <TouchableOpacity testID={`attention-approve-${item.id}`} accessibilityRole="button" accessibilityLabel={`Approve ${item.title}`} onPress={() => onPrepare('approve')} disabled={actionBusy} style={[styles.decisionButton, styles.approveButton]}><Text style={styles.decisionButtonText}>APPROVE</Text></TouchableOpacity>
        <TouchableOpacity testID={`attention-reject-${item.id}`} accessibilityRole="button" accessibilityLabel={`Reject ${item.title}`} onPress={() => onPrepare('reject')} disabled={actionBusy} style={[styles.decisionButton, styles.rejectButton]}><Text style={styles.decisionButtonText}>REJECT</Text></TouchableOpacity>
      </View> : null}
      {confirmation ? <View testID="attention-confirmation" style={styles.confirmationBox}>
        <Text style={styles.confirmationTitle}>CONFIRM {confirmation.action.toUpperCase()}</Text>
        <Text style={styles.confirmationText}>Target: Firstmate task {confirmation.target.task_id} · decision {confirmation.decision_key}</Text>
        <Text style={styles.confirmationText}>Consequence: {confirmation.consequence}</Text>
        <Text style={styles.confirmationText}>Reversible: {confirmation.reversible ? 'Yes' : 'No'}</Text>
        <View style={styles.actionButtons}><TouchableOpacity testID="attention-action-cancel" accessibilityRole="button" accessibilityLabel="Cancel Attention action" onPress={onCancel} style={styles.cancelButton}><Text style={styles.cancelButtonText}>CANCEL</Text></TouchableOpacity><TouchableOpacity testID="attention-action-confirm" accessibilityRole="button" accessibilityLabel={`Confirm ${confirmation.action}`} onPress={onExecute} disabled={actionBusy} style={styles.decisionButton}><Text style={styles.decisionButtonText}>{actionBusy ? 'WORKING…' : 'CONFIRM'}</Text></TouchableOpacity></View>
      </View> : null}
      {actionError ? <Text testID="attention-action-error" accessibilityRole="alert" style={styles.errorText}>{actionError}</Text> : null}
      {outcome ? <View testID="attention-action-evidence" style={styles.evidenceBox}><Text style={styles.confirmationTitle}>{outcome.action.toUpperCase()} · {outcome.status.toUpperCase()}</Text><Text style={styles.confirmationText}>Evidence recorded at {new Date(outcome.timestamp * 1000).toISOString()}.</Text><Text style={styles.confirmationText}>Firstmate keyed decision: {outcome.decision_key} · target: {outcome.target.task_id}.</Text></View> : null}
    </View> : null}
    {action?.status === 'unsupported' ? <Text testID="attention-action-boundary" style={styles.boundaryText}>{action.reason || 'This item is outside the supported Attention action boundary.'}</Text> : null}
    {item.external_url ? <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open source attention item" onPress={() => void openExternal()} style={styles.detailLink}><Text style={styles.actionPrompt}>OPEN SOURCE ↗</Text></TouchableOpacity> : null}
  </GlassSurface></View>;
}

export default function AttentionScreen() {
  const router = useRouter();
  const { item: focusedItemParam } = useLocalSearchParams<{ item?: string }>();
  const focusedItem = Array.isArray(focusedItemParam) ? focusedItemParam[0] : focusedItemParam;
  const [items, setItems] = useState<UnifiedAttentionRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [activeAgentsCount, setActiveAgentsCount] = useState(0);
  const [prsCount, setPrsCount] = useState(0);
  const [confirmation, setConfirmation] = useState<AttentionActionConfirmation | null>(null);
  const [actionOutcome, setActionOutcome] = useState<AttentionActionOutcome | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [lastActionItem, setLastActionItem] = useState<UnifiedAttentionRecord | null>(null);

  const loadAttention = async () => {
    setLoading(true);
    setError(null);
    const [attentionResult, agentsResult, prsResult] = await Promise.allSettled([fetchUnifiedAttention(), fetchAgents(), fetchGitHubPRs()]);
    if (attentionResult.status === 'fulfilled') setItems(attentionResult.value);
    else setError(attentionResult.reason instanceof Error ? attentionResult.reason.message : 'Needs-your-attention items could not be loaded.');
    if (agentsResult.status === 'fulfilled') setActiveAgentsCount(agentsResult.value.filter(agent => ['active', 'busy', 'executing', 'processing', 'running', 'working'].includes(String(agent.status || '').toLowerCase())).length);
    if (prsResult.status === 'fulfilled') setPrsCount(prsResult.value.items.length);
    if (attentionResult.status === 'rejected') {
      console.error('Error fetching unified attention:', attentionResult.reason);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadAttention();
  }, []);

  useEffect(() => {
    if (!focusedItem) return;
    const current = items.find(item => item.id === focusedItem);
    if (current) {
      // A focused route is the detailed acknowledgement boundary, not the
      // delivery/poll boundary. Keep the indicator until this view is loaded.
      void notificationManager.markViewed(focusedItem);
    }
    let cancelled = false;
    void fetchAttentionActionForItem(focusedItem).then(result => {
      if (cancelled || !('evidence' in result)) return;
      const resolved = result as AttentionActionOutcome;
      setActionOutcome(resolved);
      if (!current) {
        const resolvedAction: AttentionAction = {
          schema_version: 'attention-action.v1', action_key: resolved.action_key, decision_key: resolved.decision_key, source_revision: String(resolved.evidence.source_revision || ''),
          target: resolved.target, allowed_actions: [], confirmation_required: true,
          consequence: 'This decision has already been recorded.', reversible: true, status: 'succeeded',
        };
        setLastActionItem({ id: focusedItem, provider: resolved.target.provider, title: 'Resolved Attention decision', subtitle: 'The live Attention item has been resolved.', status: resolved.status, target_id: resolved.target.task_id, url: `/attention?item=${focusedItem}`, action: resolvedAction });
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [focusedItem, items]);

  const openItem = async (item: UnifiedAttentionRecord) => {
    if (item.url.startsWith('/')) {
      void notificationManager.markViewed(item.id);
      router.push(item.url as any);
    } else {
      void notificationManager.markViewed(item.id);
      const result = await openExternalUrl(item.external_url || item.url);
      if (!result.ok) Alert.alert('Unable to open link', result.message);
    }
  };

  const prepareAction = async (selectedAction: 'approve' | 'reject') => {
    const item = focusedItem ? items.find(candidate => candidate.id === focusedItem) : null;
    if (!item?.action) return;
    setActionBusy(true);
    setActionError(null);
    setActionOutcome(null);
    setLastActionItem(item);
    try {
      const prepared = await prepareAttentionAction(item.action.action_key, selectedAction, item.action.target.task_id);
      setConfirmation(prepared);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'This Attention action could not be prepared.');
    } finally { setActionBusy(false); }
  };

  const executeAction = async () => {
    const item = focusedItem ? items.find(candidate => candidate.id === focusedItem) : null;
    if (!item?.action || !confirmation) return;
    setActionBusy(true);
    setActionError(null);
    try {
      const result = await executeAttentionAction(item.action.action_key, confirmation.action, confirmation.target.task_id, confirmation.confirmation_token);
      setConfirmation(null);
      setActionOutcome(result);
      await loadAttention();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'This Attention action was rejected by the Gateway.');
    } finally { setActionBusy(false); }
  };

  const getProviderColor = (provider: string) => {
    switch (provider) {
      case 'firstmate': return '#72F5B1';
      case 'github': return '#38BDF8';
      case 'jira': return '#F59E0B';
      case 'teams': return '#A855F7';
      default: return '#FFFFFF';
    }
  };

  return (
    <EnvironmentBackground hideBottomControls>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>UNIFIED ATTENTION</Text>

        <TouchableOpacity onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <ScrollView
        style={styles.container}
        contentContainerStyle={{ paddingBottom: 110 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={loadAttention} tintColor="#72F5B1" />}
      >
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>UNIFIED ATTENTION QUEUE ({items.length})</Text>
        </View>

        {(focusedItem && (items.find(item => item.id === focusedItem) || (lastActionItem?.id === focusedItem && actionOutcome))) ? <AttentionDetail item={items.find(item => item.id === focusedItem) || lastActionItem!} confirmation={confirmation} outcome={actionOutcome} actionError={actionError} actionBusy={actionBusy} onPrepare={prepareAction} onCancel={() => { setConfirmation(null); setActionError(null); }} onExecute={() => void executeAction()} /> : null}

        {loading && <GlassSurface variant="card" style={styles.card}><Text style={styles.itemSubtitle}>Loading live attention data…</Text></GlassSurface>}
        {error && <GlassSurface variant="card" style={styles.card}><Text style={styles.errorText}>{error}</Text><TouchableOpacity onPress={loadAttention}><Text style={styles.actionPrompt}>TRY AGAIN</Text></TouchableOpacity></GlassSurface>}
        {!loading && !error && items.length === 0 && <GlassSurface variant="card" style={styles.card}><Text style={styles.itemSubtitle}>Nothing requires your attention.</Text></GlassSurface>}
        {[...items].filter(item => item.requires_action !== false).sort((a, b) => Number(b.id === focusedItem) - Number(a.id === focusedItem)).map(item => {
          const badgeColor = getProviderColor(item.provider);
          return (
            <TouchableOpacity key={item.id} testID={`attention-item-${item.id}`} accessibilityRole="button" accessibilityLabel={`Open attention detail for ${item.title}`} onPress={() => openItem(item)} activeOpacity={0.85}>
              <GlassSurface variant="card" style={[styles.card, item.id === focusedItem ? styles.focusedCard : undefined]}>
                <View style={styles.cardHeader}>
                  <View style={styles.providerBadgeGroup}>
                    <View style={[styles.providerDot, { backgroundColor: badgeColor }]} />
                    <Text style={[styles.providerName, { color: badgeColor }]}>{item.provider.toUpperCase()}</Text>
                  </View>
                  <View style={[styles.statusBadge, { borderColor: badgeColor }]}>
                    <Text style={[styles.statusBadgeText, { color: badgeColor }]}>{item.priority || item.status}</Text>
                  </View>
                </View>

                <Text style={styles.itemTitle}>{item.title}</Text>
                <Text style={styles.itemSubtitle}>{item.subtitle}</Text>

                <View style={styles.cardFooter}>
                  <Text style={styles.actionPrompt}>RESOLVE ACTION ↗</Text>
                </View>
              </GlassSurface>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={(r) => router.push((r === 'index' ? '/' : '/' + r) as any)}
        activeAgentsCount={activeAgentsCount}
        attentionCount={items.filter(item => item.requires_action !== false).length}
        prsCount={prsCount}
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
  headerTitle: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  sectionHeader: { marginTop: 14, marginBottom: 8 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4 },
  card: { padding: 16, marginVertical: 6, borderRadius: 18, backgroundColor: 'rgba(12, 22, 34, 0.35)', borderColor: 'rgba(255, 255, 255, 0.15)' },
  focusedCard: { borderColor: '#72F5B1', borderWidth: 2 },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  providerBadgeGroup: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  providerDot: { width: 6, height: 6, borderRadius: 3 },
  providerName: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold' },
  statusBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1, backgroundColor: 'rgba(255, 255, 255, 0.05)' },
  statusBadgeText: { fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  itemTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 4 },
  itemSubtitle: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', lineHeight: 16, marginBottom: 12 },
  cardFooter: { borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.08)', paddingTop: 8, alignItems: 'flex-end' },
  actionPrompt: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#72F5B1' },
  errorText: { color: '#FCA5A5', fontSize: 12, marginBottom: 8 },
  detailCard: { padding: 18, marginVertical: 6, borderRadius: 18, borderColor: '#72F5B1', borderWidth: 1 },
  detailEyebrow: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', letterSpacing: 1.2 },
  detailTitle: { color: '#FFFFFF', fontSize: 20, fontWeight: 'bold', lineHeight: 26, marginTop: 10 },
  detailSubtitle: { color: 'rgba(255,255,255,0.78)', fontSize: 14, lineHeight: 21, marginTop: 6 },
  detailDivider: { height: 1, backgroundColor: 'rgba(255,255,255,0.12)', marginVertical: 16 },
  detailLabel: { color: 'rgba(255,255,255,0.5)', fontFamily: 'monospace', fontSize: 9, letterSpacing: 1.2, marginTop: 10 },
  detailValue: { color: 'rgba(255,255,255,0.88)', fontSize: 13, lineHeight: 19, marginTop: 3 },
  detailLink: { alignSelf: 'flex-start', marginTop: 16, paddingVertical: 8 },
  actionBox: { marginTop: 18, paddingTop: 14, borderTopWidth: 1, borderTopColor: 'rgba(114,245,177,0.28)' },
  actionBoundary: { color: 'rgba(255,255,255,0.68)', fontSize: 12, lineHeight: 18, marginTop: 5 },
  actionButtons: { flexDirection: 'row', gap: 9, marginTop: 12, flexWrap: 'wrap' },
  decisionButton: { minHeight: 38, paddingHorizontal: 14, borderRadius: 9, justifyContent: 'center', alignItems: 'center', backgroundColor: '#72F5B1' },
  approveButton: { backgroundColor: '#72F5B1' },
  rejectButton: { backgroundColor: '#C084FC' },
  decisionButtonText: { color: '#07101D', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  confirmationBox: { marginTop: 12, padding: 12, borderWidth: 1, borderColor: '#F5C542', borderRadius: 10, backgroundColor: 'rgba(245,197,66,0.08)' },
  confirmationTitle: { color: '#F5C542', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', letterSpacing: 1 },
  confirmationText: { color: 'rgba(255,255,255,0.86)', fontSize: 12, lineHeight: 18, marginTop: 6 },
  cancelButton: { minHeight: 38, paddingHorizontal: 14, borderRadius: 9, borderWidth: 1, borderColor: 'rgba(255,255,255,0.35)', justifyContent: 'center', alignItems: 'center' },
  cancelButtonText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  evidenceBox: { marginTop: 12, padding: 12, borderRadius: 10, backgroundColor: 'rgba(114,245,177,0.1)' },
  boundaryText: { color: '#FCA5A5', fontSize: 12, lineHeight: 18, marginTop: 16 }
});
