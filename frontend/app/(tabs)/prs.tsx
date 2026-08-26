import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { fetchGitHubPRs, GitHubPR } from '../../src/api/client';
import { useRouter } from 'expo-router';

export default function PRsScreen() {
  const router = useRouter();
  const [prs, setPrs] = useState<GitHubPR[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);

  const loadPRs = async (nextPage = 1, refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchGitHubPRs(nextPage, refresh);
      setPrs(current => nextPage === 1 ? data.items : [...current, ...data.items]);
      setPage(nextPage);
      setHasMore(data.has_more);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'GitHub pull requests could not be loaded.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPRs(1);
  }, []);

  const handleNavigate = (route: string) => {
    if (route === 'prs') return;
    router.push('/' + route as any);
  };

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>LIVE GITHUB PRs</Text>

        <TouchableOpacity onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <ScrollView
        style={styles.container}
        contentContainerStyle={{ paddingBottom: 110 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={() => loadPRs(1, true)} tintColor="#72F5B1" />}
      >
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>PULL REQUESTS ({prs.length})</Text>
        </View>

        {error && <GlassSurface variant="card" style={styles.prCard}><Text style={styles.errorText}>{error}</Text><TouchableOpacity onPress={() => loadPRs(1, true)}><Text style={styles.linkText}>TRY AGAIN</Text></TouchableOpacity></GlassSurface>}
        {!error && !loading && prs.length === 0 && <GlassSurface variant="card" style={styles.prCard}><Text style={styles.prSummary}>No open pull requests.</Text></GlassSurface>}
        {prs.map(pr => (
          <TouchableOpacity key={pr.id} onPress={() => router.push(`/pr-detail?number=${pr.number}` as any)} activeOpacity={0.85}>
            <GlassSurface variant="card" style={styles.prCard}>
              <View style={styles.prHeaderRow}>
                <View style={styles.prTagGroup}>
                  <Text style={styles.prNumber}>PR #{pr.number}</Text>
                  <Text style={styles.prRepo}>{pr.repository}</Text>
                </View>
                <View style={[styles.badge, pr.review_status === 'APPROVED' ? styles.badgeApproved : styles.badgePending]}>
                  <Text style={[styles.badgeText, pr.review_status === 'APPROVED' ? styles.badgeTextApproved : styles.badgeTextPending]}>
                    {pr.review_status || 'OPEN'}
                  </Text>
                </View>
              </View>

              <Text style={styles.prTitle}>{pr.title}</Text>
              <Text style={styles.prSummary}>{pr.summary}</Text>

              <View style={styles.prFooterRow}>
                <Text style={styles.prMeta}>
                  {pr.branch ? <>Branch: <Text style={styles.metaHighlight}>{pr.branch}</Text> • </> : null}Author: <Text style={styles.metaHighlight}>{pr.author}</Text>
                </Text>
                <Text style={styles.linkText}>DETAILS →</Text>
              </View>
            </GlassSurface>
          </TouchableOpacity>
        ))}
        {hasMore && <TouchableOpacity onPress={() => loadPRs(page + 1)} disabled={loading}><Text style={styles.loadMore}>LOAD MORE</Text></TouchableOpacity>}
      </ScrollView>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={handleNavigate}
        activeAgentsCount={1}
        attentionCount={0}
        prsCount={prs.length}
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
  prCard: { padding: 16, marginVertical: 6, borderRadius: 18, backgroundColor: 'rgba(12, 22, 34, 0.35)', borderColor: 'rgba(255, 255, 255, 0.15)' },
  prHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  prTagGroup: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  prNumber: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#72F5B1' },
  prRepo: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.5)' },
  badge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, borderWidth: 1 },
  badgeApproved: { backgroundColor: 'rgba(114, 245, 177, 0.15)', borderColor: '#72F5B1' },
  badgePending: { backgroundColor: 'rgba(245, 158, 11, 0.15)', borderColor: '#F59E0B' },
  badgeText: { fontFamily: 'monospace', fontSize: 9.5, fontWeight: 'bold' },
  badgeTextApproved: { color: '#72F5B1' },
  badgeTextPending: { color: '#F59E0B' },
  prTitle: { fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 4 },
  prSummary: { fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', lineHeight: 16, marginBottom: 12 },
  prFooterRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.08)', paddingTop: 8 },
  prMeta: { fontSize: 10.5, color: 'rgba(255, 255, 255, 0.5)' },
  metaHighlight: { color: 'rgba(255, 255, 255, 0.85)', fontWeight: '500' },
  linkText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#72F5B1' },
  errorText: { color: '#FCA5A5', fontSize: 12, marginBottom: 8 },
  loadMore: { color: '#72F5B1', textAlign: 'center', padding: 18, fontFamily: 'monospace', fontWeight: 'bold' }
});
