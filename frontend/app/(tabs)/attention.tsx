import React, { useEffect, useState } from 'react';
import { Alert, View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { openExternalUrl } from '../../src/utils/externalLinks';
import { compareDecisionDestination, currentDecisionDestination, DecisionAnswerConfirmation, DecisionAnswerOutcome, decisionDestinationAllowsAction, executeDecisionAnswer, fetchMagiChatConversation, fetchUnifiedAttention, prepareDecisionAnswer, UnifiedAttentionRecord } from '../../src/api/client';
import { notificationManager } from '../../src/services/NotificationManager';

function AttentionDetail({ item, confirmation, outcome, answerPreview, actionError, actionBusy, navigationMessage, navigationBlocked, onOpenChat, onPrepare, onCancel, onExecute }: {
  item: UnifiedAttentionRecord;
  confirmation: DecisionAnswerConfirmation | null;
  outcome: DecisionAnswerOutcome | null;
  answerPreview: string | null;
  actionError: string | null;
  actionBusy: boolean;
  navigationMessage?: string;
  navigationBlocked: boolean;
  onOpenChat: () => void;
  onPrepare: () => void;
  onCancel: () => void;
  onExecute: () => void;
}) {
  const openExternal = async () => {
    if (!item.external_url) return;
    const result = await openExternalUrl(item.external_url);
    if (!result.ok) Alert.alert('Unable to open attention item', result.message);
  };
  const context = Object.entries(item.context || {}).filter(([, value]) => value !== null && value !== undefined && value !== '');
  const decision = currentDecisionDestination(item);
  const answerAvailable = item.provider === 'firstmate' && item.requires_action !== false
    && decision.status === 'current' && !navigationBlocked;
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
    {navigationMessage ? <Text testID="attention-navigation-status" accessibilityRole={navigationBlocked ? 'alert' : undefined} style={navigationBlocked ? styles.boundaryText : styles.actionBoundary}>{navigationMessage}</Text> : null}
    {answerAvailable ? <View testID={`attention-actions-${item.id}`} style={styles.actionBox}>
      <Text style={styles.detailLabel}>DECISION ANSWER</Text>
      <Text style={styles.actionBoundary}>Write your approval, decline, or other answer in Chat. Only the latest saved user message can be reviewed and sent; notification text and button labels are never used as an answer.</Text>
      {!confirmation && !outcome ? <View style={styles.actionButtons}>
        <TouchableOpacity testID={`attention-answer-in-chat-${item.id}`} accessibilityRole="button" accessibilityLabel={`Answer ${item.title} in Chat`} onPress={onOpenChat} style={styles.cancelButton}><Text style={styles.cancelButtonText}>ANSWER IN CHAT</Text></TouchableOpacity>
        <TouchableOpacity testID={`attention-review-answer-${item.id}`} accessibilityRole="button" accessibilityLabel={`Review latest Chat answer for ${item.title}`} onPress={onPrepare} disabled={actionBusy} style={styles.decisionButton}><Text style={styles.decisionButtonText}>{actionBusy ? 'CHECKING…' : 'REVIEW LATEST CHAT ANSWER'}</Text></TouchableOpacity>
      </View> : null}
      {confirmation ? <View testID="attention-confirmation" style={styles.confirmationBox}>
        <Text style={styles.confirmationTitle}>CONFIRM EXACT CHAT ANSWER</Text>
        <Text testID="attention-answer-preview" selectable style={styles.answerPreview}>{answerPreview}</Text>
        <Text style={styles.confirmationText}>Decision: {confirmation.target.title} · revision {confirmation.decision_revision}</Text>
        <Text style={styles.confirmationText}>Consequence: {confirmation.consequence}</Text>
        <Text style={styles.confirmationText}>Reversible: No</Text>
        <View style={styles.actionButtons}><TouchableOpacity testID="attention-action-cancel" accessibilityRole="button" accessibilityLabel="Cancel decision answer" onPress={onCancel} style={styles.cancelButton}><Text style={styles.cancelButtonText}>CANCEL</Text></TouchableOpacity><TouchableOpacity testID="attention-action-confirm" accessibilityRole="button" accessibilityLabel="Confirm exact Chat answer" onPress={onExecute} disabled={actionBusy} style={styles.decisionButton}><Text style={styles.decisionButtonText}>{actionBusy ? 'WORKING…' : 'CONFIRM ANSWER'}</Text></TouchableOpacity></View>
      </View> : null}
      {actionError ? <Text testID="attention-action-error" accessibilityRole="alert" style={styles.errorText}>{actionError}</Text> : null}
      {outcome ? <View testID="attention-action-evidence" style={styles.evidenceBox}><Text style={styles.confirmationTitle}>ANSWER · {outcome.status.toUpperCase()}</Text><Text style={styles.confirmationText}>Gateway evidence recorded at {new Date(outcome.timestamp).toISOString()}.</Text><Text style={styles.confirmationText}>Decision {outcome.decision_id} · revision {outcome.decision_revision} · {outcome.evidence.answer_bytes} canonical answer bytes.</Text><Text style={styles.confirmationText}>Release requested: Yes. Later execution progress is not implied.</Text></View> : null}
    </View> : decision.status !== 'current' && item.provider === 'firstmate' ? <Text testID="attention-action-boundary" style={styles.boundaryText}>{decision.status === 'unpinned' ? 'This decision has no current canonical identity.' : decision.reason}</Text> : null}
    {item.external_url ? <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open source attention item" onPress={() => void openExternal()} style={styles.detailLink}><Text style={styles.actionPrompt}>OPEN SOURCE ↗</Text></TouchableOpacity> : null}
  </GlassSurface></View>;
}

export default function AttentionScreen() {
  const router = useRouter();
  const { item: focusedItemParam, decision: decisionParam, revision: revisionParam } = useLocalSearchParams<{ item?: string | string[]; decision?: string | string[]; revision?: string | string[] }>();
  const focusedItem = Array.isArray(focusedItemParam) ? focusedItemParam[0] : focusedItemParam;
  const [items, setItems] = useState<UnifiedAttentionRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [confirmation, setConfirmation] = useState<DecisionAnswerConfirmation | null>(null);
  const [actionOutcome, setActionOutcome] = useState<DecisionAnswerOutcome | null>(null);
  const [answerMessage, setAnswerMessage] = useState<{ id: string; content: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [lastActionItem, setLastActionItem] = useState<UnifiedAttentionRecord | null>(null);
  const focusedRecord = focusedItem ? items.find(item => item.id === focusedItem) || null : null;
  const decisionDestination = compareDecisionDestination(decisionParam, revisionParam, focusedRecord);
  const navigationBlocked = !decisionDestinationAllowsAction(decisionDestination);
  const navigationMessage = decisionDestination.status === 'current'
    ? 'Opened from a notification for the current revision. No decision was sent; use the explicit controls below if available.'
    : decisionDestination.status !== 'unpinned' ? decisionDestination.reason : undefined;

  const loadAttention = async () => {
    setLoading(true);
    setError(null);
    const [attentionResult] = await Promise.allSettled([fetchUnifiedAttention()]);
    if (attentionResult.status === 'fulfilled') setItems(attentionResult.value);
    else setError(attentionResult.reason instanceof Error ? attentionResult.reason.message : 'Needs-your-attention items could not be loaded.');
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
      // Opening the detail acknowledges only navigation. It never prepares or
      // executes a decision answer.
      void notificationManager.markViewed(focusedItem);
    }
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

  const currentDecisionItem = async (): Promise<{ item: UnifiedAttentionRecord; decisionId: string; revision: number }> => {
    const latest = await fetchUnifiedAttention();
    setItems(latest);
    const latestItem = focusedItem ? latest.find(candidate => candidate.id === focusedItem) || null : null;
    if (decisionDestination.status !== 'unpinned') {
      const comparison = compareDecisionDestination(decisionParam, revisionParam, latestItem);
      if (comparison.status !== 'current') throw new Error(comparison.status === 'unpinned' ? 'Decision navigation context is unavailable.' : comparison.reason);
    }
    const current = currentDecisionDestination(latestItem);
    if (!latestItem || current.status !== 'current' || latestItem.requires_action === false) {
      throw new Error(current.status === 'current' ? 'This decision is no longer awaiting an answer.' : current.status === 'unpinned' ? 'Decision identity is unavailable.' : current.reason);
    }
    return { item: latestItem, decisionId: current.decisionId, revision: current.revision };
  };

  const prepareAction = async () => {
    setActionBusy(true);
    setActionError(null);
    setActionOutcome(null);
    setConfirmation(null);
    setAnswerMessage(null);
    try {
      const current = await currentDecisionItem();
      const conversation = await fetchMagiChatConversation();
      const message = [...conversation.messages]
        .filter(candidate => candidate.role === 'user' && candidate.status === 'completed'
          && (candidate.source === 'text' || candidate.source === 'voice'))
        .sort((left, right) => right.sequence_index - left.sequence_index)[0];
      if (!message) throw new Error('No saved Chat answer is available. Write an approval, decline, or other answer in Chat first.');
      setLastActionItem(current.item);
      const prepared = await prepareDecisionAnswer(current.decisionId, current.revision, message.id);
      if (prepared.status !== 'confirmation_required') {
        setActionOutcome(prepared);
        await loadAttention();
        return;
      }
      setAnswerMessage({ id: message.id, content: message.content });
      setConfirmation(prepared);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'This decision answer could not be prepared.');
    } finally { setActionBusy(false); }
  };

  const executeAction = async () => {
    if (!confirmation || !answerMessage) return;
    setActionBusy(true);
    setActionError(null);
    try {
      const current = await currentDecisionItem();
      if (confirmation.decision_id !== current.decisionId || confirmation.decision_revision !== current.revision) {
        throw new Error('The decision changed after confirmation. Review the current revision before acting.');
      }
      if (Date.now() >= confirmation.expires_at) throw new Error('The confirmation expired. Review the latest Chat answer again.');
      const result = await executeDecisionAnswer(current.decisionId, current.revision, answerMessage.id, confirmation.confirmation_token);
      if (result.status === 'confirmation_required') throw new Error('The Gateway did not record the confirmed answer. Review it again.');
      setConfirmation(null);
      setAnswerMessage(null);
      setActionOutcome(result);
      await loadAttention();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'This decision answer was rejected by the Gateway.');
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

        {(focusedItem && (focusedRecord || (lastActionItem?.id === focusedItem && (actionOutcome || confirmation || actionError)))) ? <AttentionDetail item={focusedRecord || lastActionItem!} confirmation={confirmation} outcome={actionOutcome} answerPreview={answerMessage?.content || null} actionError={actionError} actionBusy={actionBusy} navigationMessage={navigationMessage} navigationBlocked={navigationBlocked} onOpenChat={() => router.push('/chat')} onPrepare={() => void prepareAction()} onCancel={() => { setConfirmation(null); setAnswerMessage(null); setActionError(null); }} onExecute={() => void executeAction()} /> : null}
        {focusedItem && !loading && !focusedRecord && navigationBlocked ? <GlassSurface variant="card" style={styles.card}><Text testID="attention-navigation-status" accessibilityRole="alert" style={styles.boundaryText}>{navigationMessage}</Text></GlassSurface> : null}

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
        attentionCount={items.filter(item => item.requires_action !== false).length}
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
  headerTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  sectionHeader: { marginTop: 14, marginBottom: 8 },
  sectionTitle: { fontSize: 11, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.6)', letterSpacing: 1.4 },
  card: { padding: 16, marginVertical: 6, borderRadius: 18, backgroundColor: 'rgba(12, 22, 34, 0.35)', borderColor: 'rgba(255, 255, 255, 0.15)' },
  focusedCard: { borderColor: '#72F5B1', borderWidth: 2 },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  providerBadgeGroup: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  providerDot: { width: 6, height: 6, borderRadius: 3 },
  providerName: { fontSize: 11, fontWeight: 'bold' },
  statusBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1, backgroundColor: 'rgba(255, 255, 255, 0.05)' },
  statusBadgeText: { fontSize: 9, fontWeight: 'bold' },
  itemTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 4 },
  itemSubtitle: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', lineHeight: 16, marginBottom: 12 },
  cardFooter: { borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.08)', paddingTop: 8, alignItems: 'flex-end' },
  actionPrompt: { fontSize: 10, fontWeight: 'bold', color: '#72F5B1' },
  errorText: { color: '#FCA5A5', fontSize: 12, marginBottom: 8 },
  detailCard: { padding: 18, marginVertical: 6, borderRadius: 18, borderColor: '#72F5B1', borderWidth: 1 },
  detailEyebrow: { color: '#72F5B1', fontSize: 10, fontWeight: 'bold', letterSpacing: 1.2 },
  detailTitle: { color: '#FFFFFF', fontSize: 20, fontWeight: 'bold', lineHeight: 26, marginTop: 10 },
  detailSubtitle: { color: 'rgba(255,255,255,0.78)', fontSize: 14, lineHeight: 21, marginTop: 6 },
  detailDivider: { height: 1, backgroundColor: 'rgba(255,255,255,0.12)', marginVertical: 16 },
  detailLabel: { color: 'rgba(255,255,255,0.5)', fontSize: 9, letterSpacing: 1.2, marginTop: 10 },
  detailValue: { color: 'rgba(255,255,255,0.88)', fontSize: 13, lineHeight: 19, marginTop: 3 },
  detailLink: { alignSelf: 'flex-start', marginTop: 16, paddingVertical: 8 },
  actionBox: { marginTop: 18, paddingTop: 14, borderTopWidth: 1, borderTopColor: 'rgba(114,245,177,0.28)' },
  actionBoundary: { color: 'rgba(255,255,255,0.68)', fontSize: 12, lineHeight: 18, marginTop: 5 },
  actionButtons: { flexDirection: 'row', gap: 9, marginTop: 12, flexWrap: 'wrap' },
  decisionButton: { minHeight: 38, paddingHorizontal: 14, borderRadius: 9, justifyContent: 'center', alignItems: 'center', backgroundColor: '#72F5B1' },
  decisionButtonText: { color: '#07101D', fontSize: 10, fontWeight: 'bold' },
  confirmationBox: { marginTop: 12, padding: 12, borderWidth: 1, borderColor: '#F5C542', borderRadius: 10, backgroundColor: 'rgba(245,197,66,0.08)' },
  confirmationTitle: { color: '#F5C542', fontSize: 10, fontWeight: 'bold', letterSpacing: 1 },
  confirmationText: { color: 'rgba(255,255,255,0.86)', fontSize: 12, lineHeight: 18, marginTop: 6 },
  answerPreview: { color: '#FFFFFF', fontSize: 14, lineHeight: 21, marginTop: 10, padding: 10, borderRadius: 8, backgroundColor: 'rgba(255,255,255,0.08)' },
  cancelButton: { minHeight: 38, paddingHorizontal: 14, borderRadius: 9, borderWidth: 1, borderColor: 'rgba(255,255,255,0.35)', justifyContent: 'center', alignItems: 'center' },
  cancelButtonText: { color: '#FFFFFF', fontSize: 10, fontWeight: 'bold' },
  evidenceBox: { marginTop: 12, padding: 12, borderRadius: 10, backgroundColor: 'rgba(114,245,177,0.1)' },
  boundaryText: { color: '#FCA5A5', fontSize: 12, lineHeight: 18, marginTop: 16 }
});
