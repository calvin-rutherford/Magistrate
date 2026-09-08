import React, { useMemo, useState } from 'react';
import {
  Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native';

import { ActivityIcon, CloseIcon } from '@/components/MagistrateIcons';
import { useTheme } from '@/hooks/use-theme';
import type {
  CanonicalActivityRecord, CanonicalActivitySnapshot, CanonicalWorkState,
} from '@/services/CanonicalActivity';
import { decisionAttentionItemId } from '@/services/CanonicalActivity';
import { openExternalUrl } from '@/utils/externalLinks';

const PAGE_SIZE = 20;

const stateLabel = (record: CanonicalActivityRecord): string => {
  switch (record.state) {
    case 'active': return 'Active';
    case 'awaiting-user': return 'Awaiting you';
    case 'completed': return 'Completed';
    case 'failed': return 'Failed';
    case 'cancelled': return 'Cancelled';
    case 'resolved': return 'Resolved';
  }
};

const kindLabel = (record: CanonicalActivityRecord): string => {
  if (record.kind.startsWith('objective.')) return 'Objective';
  if (record.kind.startsWith('decision.')) return 'Decision';
  if (record.kind.startsWith('worker.')) return 'Worker operation';
  if (record.kind.startsWith('primary.')) return 'Magi operation';
  return 'Supervision';
};

const recoveryLabel = (snapshot: CanonicalActivitySnapshot): string | null => {
  if (snapshot.recoveryState === 'hydrating' || snapshot.recoveryState === 'recovering') {
    return 'Recovering durable activity…';
  }
  if (snapshot.recoveryState === 'observability-interrupted') {
    return 'Observability is interrupted. Last confirmed state is retained; completion is not inferred.';
  }
  return null;
};

export interface CanonicalActivitySurfaceProps {
  visible: boolean;
  snapshot: CanonicalActivitySnapshot;
  work: CanonicalWorkState;
  hasMore: boolean;
  loadingMore: boolean;
  refreshing: boolean;
  onClose: () => void;
  onLoadMore: () => boolean | Promise<boolean>;
  onRefresh: () => void | Promise<void>;
  onOpenDecision: (itemId: string) => void;
}

export function CanonicalActivitySurface({
  visible, snapshot, work, hasMore, loadingMore, refreshing,
  onClose, onLoadMore, onRefresh, onOpenDecision,
}: CanonicalActivitySurfaceProps) {
  const colors = useTheme();
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const newest = useMemo(
    () => [...snapshot.records].sort((left, right) => right.sequence - left.sequence),
    [snapshot.records],
  );
  const shown = newest.slice(0, visibleCount);
  const canRevealLocal = visibleCount < newest.length;
  const banner = recoveryLabel(snapshot);

  const revealMore = async () => {
    if (canRevealLocal) {
      setVisibleCount(count => count + PAGE_SIZE);
      return;
    }
    if (hasMore) {
      if (await onLoadMore()) setVisibleCount(count => count + PAGE_SIZE);
    }
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <View style={styles.overlay} testID="canonical-activity-surface">
        <Pressable
          style={styles.scrim}
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel="Close activity"
        />
        <View style={[styles.sheet, { backgroundColor: colors.background, borderColor: colors.backgroundSelected }]}>
          <View style={[styles.handle, { backgroundColor: colors.backgroundSelected }]} />
          <View style={styles.header}>
            <View style={styles.headerCopy}>
              <Text style={[styles.title, { color: colors.text }]}>Magi activity</Text>
              <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
                {work.active
                  ? `${work.operationCount} recorded operation${work.operationCount === 1 ? '' : 's'} in progress`
                  : `${snapshot.records.length} durable record${snapshot.records.length === 1 ? '' : 's'}`}
              </Text>
              {work.objectiveIds[0] ? (
                <Text testID="active-objective-identity" selectable style={[styles.identity, { color: colors.textSecondary }]} numberOfLines={2}>
                  Objective {work.objectiveIds[0]}{work.runIds[0] ? ` · Run ${work.runIds[0]}` : ''}
                </Text>
              ) : null}
            </View>
            <Pressable
              testID="close-canonical-activity"
              onPress={onClose}
              accessibilityRole="button"
              accessibilityLabel="Close activity"
              style={({ pressed }) => [styles.iconButton, { opacity: pressed ? 0.55 : 1 }]}
            >
              <CloseIcon size={20} color={colors.text} />
            </Pressable>
          </View>
          {banner ? (
            <View
              testID="activity-recovery-banner"
              style={[styles.banner, { backgroundColor: colors.backgroundElement }]}
            >
              <ActivityIcon size={15} color={colors.textSecondary} />
              <Text style={[styles.bannerText, { color: colors.textSecondary }]}>{banner}</Text>
            </View>
          ) : null}
          <ScrollView
            style={styles.scroll}
            contentContainerStyle={styles.content}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          >
            {shown.length ? shown.map(record => {
              const decisionItemId = record.state === 'awaiting-user'
                ? decisionAttentionItemId(record.decisionKey) : null;
              return (
                <View
                  key={record.id}
                  testID={`canonical-activity-row-${record.id}`}
                  style={[styles.row, { borderColor: colors.backgroundSelected }]}
                >
                  <View style={styles.rowTop}>
                    <Text style={[styles.kind, { color: colors.textSecondary }]}>{kindLabel(record)}</Text>
                    <View style={[
                      styles.badge,
                      { backgroundColor: record.state === 'failed' || record.state === 'cancelled'
                        ? 'rgba(239,68,68,0.14)' : colors.backgroundElement },
                    ]}>
                      <Text style={[styles.badgeText, { color: colors.text }]}>{stateLabel(record)}</Text>
                    </View>
                  </View>
                  <Text style={[styles.rowTitle, { color: colors.text }]}>{record.title}</Text>
                  <Text style={[styles.summary, { color: colors.textSecondary }]} numberOfLines={5}>
                    {record.summary}{record.summaryTruncated ? '…' : ''}
                  </Text>
                  <Text
                    testID={`activity-causality-${record.id}`}
                    selectable
                    style={[styles.meta, { color: colors.textSecondary }]}
                    numberOfLines={2}
                  >
                    Objective {record.objectiveId}{record.runId ? ` · Run ${record.runId}` : ''}
                  </Text>
                  {record.project ? (
                    <Text style={[styles.meta, { color: colors.textSecondary }]}>{record.project}</Text>
                  ) : null}
                  <View style={styles.actions}>
                    {decisionItemId ? (
                      <Pressable
                        testID={`activity-decision-${decisionItemId}`}
                        onPress={() => onOpenDecision(decisionItemId)}
                        accessibilityRole="button"
                        accessibilityLabel="Open decision in Attention"
                        style={[styles.action, { backgroundColor: colors.backgroundElement }]}
                      >
                        <Text style={[styles.actionText, { color: colors.text }]}>Open in Attention</Text>
                      </Pressable>
                    ) : null}
                    {record.refs.map(reference => reference.kind === 'pull-request' ? (
                      <Pressable
                        key={reference.url}
                        onPress={() => { void openExternalUrl(reference.url); }}
                        accessibilityRole="link"
                        accessibilityLabel="Open pull request"
                        style={[styles.action, { backgroundColor: colors.backgroundElement }]}
                      >
                        <Text style={[styles.actionText, { color: colors.text }]}>Pull request ↗</Text>
                      </Pressable>
                    ) : (
                      <View key={reference.id} style={[styles.action, { backgroundColor: colors.backgroundElement }]}>
                        <Text style={[styles.actionText, { color: colors.text }]}>Report available</Text>
                      </View>
                    ))}
                  </View>
                </View>
              );
            }) : (
              <View testID="canonical-activity-empty" style={styles.empty}>
                <Text style={[styles.emptyTitle, { color: colors.text }]}>No recorded operations</Text>
                <Text style={[styles.emptyText, { color: colors.textSecondary }]}>
                  This surface shows only Gateway-confirmed activity.
                </Text>
              </View>
            )}
            {(canRevealLocal || hasMore) ? (
              <Pressable
                testID="load-more-canonical-activity"
                disabled={loadingMore}
                onPress={() => { void revealMore(); }}
                accessibilityRole="button"
                style={[styles.more, { borderColor: colors.backgroundSelected, opacity: loadingMore ? 0.55 : 1 }]}
              >
                <Text style={[styles.moreText, { color: colors.text }]}>
                  {loadingMore ? 'Loading…' : 'Show earlier activity'}
                </Text>
              </Pressable>
            ) : null}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: 'flex-end' },
  scrim: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, backgroundColor: 'rgba(0,0,0,0.42)' },
  sheet: {
    maxHeight: '86%', minHeight: 300, borderTopLeftRadius: 28, borderTopRightRadius: 28,
    borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden',
  },
  handle: { width: 40, height: 4, borderRadius: 2, alignSelf: 'center', marginTop: 10 },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 15 },
  headerCopy: { flex: 1, minWidth: 0 },
  title: { fontSize: 20, fontWeight: '700' },
  subtitle: { fontSize: 13, marginTop: 3 },
  identity: { fontSize: 11, lineHeight: 15, marginTop: 4 },
  iconButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  banner: { marginHorizontal: 16, marginBottom: 8, padding: 11, borderRadius: 12, flexDirection: 'row', gap: 8 },
  bannerText: { flex: 1, fontSize: 12, lineHeight: 17 },
  scroll: { flexShrink: 1 },
  content: { paddingHorizontal: 16, paddingTop: 4, paddingBottom: 36, gap: 10 },
  row: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, padding: 14 },
  rowTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  kind: { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6 },
  badge: { borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 },
  badgeText: { fontSize: 11, fontWeight: '700' },
  rowTitle: { fontSize: 15, fontWeight: '700', marginTop: 8 },
  summary: { fontSize: 13, lineHeight: 19, marginTop: 5 },
  meta: { fontSize: 11, marginTop: 8 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10 },
  action: { minHeight: 44, borderRadius: 10, paddingHorizontal: 11, flexDirection: 'row', alignItems: 'center', gap: 6 },
  actionText: { fontSize: 12, fontWeight: '700' },
  empty: { alignItems: 'center', paddingVertical: 42, paddingHorizontal: 18 },
  emptyTitle: { fontSize: 16, fontWeight: '700' },
  emptyText: { fontSize: 13, textAlign: 'center', marginTop: 6 },
  more: { minHeight: 46, borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  moreText: { fontSize: 13, fontWeight: '700' },
});
