import React, { useCallback, useEffect, useState } from 'react';
import { Alert, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { fetchGitHubPR, GitHubPR } from '../src/api/client';
import { openExternalUrl } from '../src/utils/externalLinks';

function timestamp(value: string | null) {
  if (!value) return 'Not available';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function PullRequestDetailScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ number?: string }>();
  const number = Number(params.number);
  const [pr, setPr] = useState<GitHubPR | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (!Number.isInteger(number) || number < 1) {
      setError('This pull request number is invalid.');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try { setPr(await fetchGitHubPR(number, refresh)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Pull request details could not be loaded.'); }
    finally { setLoading(false); }
  }, [number]);

  useEffect(() => { load(); }, [load]);

  const openGitHub = async () => {
    const result = await openExternalUrl(pr?.url);
    if (!result.ok) Alert.alert('Unable to open GitHub', result.message);
  };

  return (
    <EnvironmentBackground hideBottomControls>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()}><Text style={styles.headerButton}>← BACK</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>PULL REQUEST</Text>
        <View style={styles.headerSpacer} />
      </View>
      <ScrollView style={styles.container} contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={() => load(true)} tintColor="#72F5B1" />}>
        {error && <GlassSurface variant="card" style={styles.card}><Text style={styles.error}>{error}</Text><TouchableOpacity onPress={() => load(true)}><Text style={styles.action}>TRY AGAIN</Text></TouchableOpacity></GlassSurface>}
        {!error && loading && !pr && <Text style={styles.muted}>Loading pull request…</Text>}
        {pr && <>
          <GlassSurface variant="card" style={styles.card}>
            <View style={styles.row}><Text style={styles.number}>{pr.repository} · #{pr.number}</Text><Text style={styles.badge}>{pr.is_draft ? 'DRAFT' : pr.state}</Text></View>
            <Text style={styles.title}>{pr.title}</Text>
            <Text style={styles.meta}>by {pr.author}</Text>
            <View style={styles.divider} />
            <Text style={styles.label}>READINESS</Text>
            <Text style={styles.value}>Review: {pr.review_status}  ·  Mergeability: {pr.mergeable}</Text>
            <Text style={styles.value}>Checks: {pr.checks.summary}</Text>
            {pr.reviews.length > 0 && <Text style={styles.value}>Reviews: {pr.reviews.map(review => `${review.author} (${review.state})`).join(', ')}</Text>}
          </GlassSurface>
          <GlassSurface variant="card" style={styles.card}>
            <Text style={styles.label}>SUMMARY</Text>
            <Text style={styles.body}>{pr.body || pr.summary || 'No description provided.'}</Text>
          </GlassSurface>
          <GlassSurface variant="card" style={styles.card}>
            <Text style={styles.label}>TIMELINE</Text>
            <Text style={styles.value}>Created: {timestamp(pr.created_at)}</Text>
            <Text style={styles.value}>Updated: {timestamp(pr.updated_at)}</Text>
            {pr.merged_at && <Text style={styles.value}>Merged: {timestamp(pr.merged_at)}</Text>}
          </GlassSurface>
          <TouchableOpacity onPress={openGitHub} activeOpacity={0.8}><GlassSurface variant="control" style={styles.githubButton}><Text style={styles.action}>OPEN ON GITHUB ↗</Text></GlassSurface></TouchableOpacity>
        </>}
      </ScrollView>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 16 },
  headerTitle: { color: '#FFF', fontFamily: 'monospace', fontWeight: 'bold', letterSpacing: 2 },
  headerButton: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 11 }, headerSpacer: { width: 48 },
  container: { flex: 1, paddingHorizontal: 16 }, content: { paddingBottom: 80 },
  card: { padding: 18, marginBottom: 12, borderRadius: 18 }, row: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
  number: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 11, flex: 1 },
  badge: { color: '#FFF', fontFamily: 'monospace', fontSize: 10 },
  title: { color: '#FFF', fontSize: 20, fontWeight: 'bold', marginTop: 12, lineHeight: 26 },
  meta: { color: 'rgba(255,255,255,.6)', marginTop: 6 }, divider: { height: 1, backgroundColor: 'rgba(255,255,255,.1)', marginVertical: 16 },
  label: { color: 'rgba(255,255,255,.55)', fontFamily: 'monospace', fontSize: 10, letterSpacing: 1.5, marginBottom: 8 },
  value: { color: 'rgba(255,255,255,.82)', fontSize: 12, lineHeight: 20 },
  body: { color: 'rgba(255,255,255,.82)', fontSize: 13, lineHeight: 20 }, muted: { color: 'rgba(255,255,255,.6)', textAlign: 'center', marginTop: 40 },
  error: { color: '#FCA5A5', marginBottom: 12 }, action: { color: '#72F5B1', fontFamily: 'monospace', fontWeight: 'bold', fontSize: 11, textAlign: 'center' },
  githubButton: { padding: 16, borderRadius: 16 },
});
