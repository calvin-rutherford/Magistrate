import React, { useEffect, useState } from 'react';
import { Alert, View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { useRouter } from 'expo-router';
import { openExternalUrl } from '../../src/utils/externalLinks';

export interface UnifiedAttentionItem {
  id: string;
  provider: 'firstmate' | 'github' | 'jira' | 'teams';
  title: string;
  subtitle: string;
  priority: string;
  status: string;
  url: string;
  requires_action: boolean;
}

export default function AttentionScreen() {
  const router = useRouter();
  const [items, setItems] = useState<UnifiedAttentionItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [showDrawer, setShowDrawer] = useState<boolean>(false);

  const loadAttention = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://100.84.181.23:8000/api/v1/attention/unified', {
        headers: { 'X-Magistrate-Token': 'magistrate-device-token-12345' }
      });
      const data = await res.json();
      if (Array.isArray(data)) {
        setItems(data);
      }
    } catch (e) {
      console.error('Error fetching unified attention:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAttention();
  }, []);

  const openItem = async (item: UnifiedAttentionItem) => {
    if (item.url.startsWith('/')) {
      router.push(item.url as any);
    } else {
      const result = await openExternalUrl(item.url);
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

        {items.map(item => {
          const badgeColor = getProviderColor(item.provider);
          return (
            <TouchableOpacity key={item.id} onPress={() => openItem(item)} activeOpacity={0.85}>
              <GlassSurface variant="card" style={styles.card}>
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
        onNavigate={(r) => router.push('/' + r as any)}
        activeAgentsCount={1}
        attentionCount={items.length}
        prsCount={2}
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
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  providerBadgeGroup: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  providerDot: { width: 6, height: 6, borderRadius: 3 },
  providerName: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold' },
  statusBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1, backgroundColor: 'rgba(255, 255, 255, 0.05)' },
  statusBadgeText: { fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  itemTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 4 },
  itemSubtitle: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', lineHeight: 16, marginBottom: 12 },
  cardFooter: { borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.08)', paddingTop: 8, alignItems: 'flex-end' },
  actionPrompt: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#72F5B1' }
});
