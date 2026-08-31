import React, { useEffect, useState } from 'react';
import { Alert, View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { openExternalUrl } from '../../src/utils/externalLinks';
import { fetchAgents, fetchGitHubPRs, fetchUnifiedAttention, UnifiedAttentionRecord } from '../../src/api/client';
import { notificationManager } from '../../src/services/NotificationManager';

function AttentionDetail({ item }: { item: UnifiedAttentionRecord }) {
  const openExternal = async () => {
    if (!item.external_url) return;
    const result = await openExternalUrl(item.external_url);
    if (!result.ok) Alert.alert('Unable to open attention item', result.message);
  };
  const context = Object.entries(item.context || {}).filter(([, value]) => value !== null && value !== undefined && value !== '');
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
    if (!focusedItem || !items.some(item => item.id === focusedItem)) return;
    // A focused route is the detailed acknowledgement boundary, not the
    // delivery/poll boundary. Keep the indicator until this view is loaded.
    void notificationManager.markViewed(focusedItem);
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
    <EnvironmentBackground>
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

        {focusedItem && items.find(item => item.id === focusedItem) ? <AttentionDetail item={items.find(item => item.id === focusedItem)!} /> : null}

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
  detailLink: { alignSelf: 'flex-start', marginTop: 16, paddingVertical: 8 }
});
