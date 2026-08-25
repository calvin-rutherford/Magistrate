import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { StatusRing } from '../../src/components/StatusRing';
import { FleetMetric } from '../../src/components/FleetMetric';
import { fetchAgents, fetchGitHubPRs } from '../../src/api/client';
import { useRouter } from 'expo-router';

export default function HomeScreen() {
  const router = useRouter();

  const [runningCount, setRunningCount] = useState<number>(1);
  const [blockedCount, setBlockedCount] = useState<number>(0);
  const [prsCount, setPrsCount] = useState<number>(2);
  const [needsCount, setNeedsCount] = useState<number>(0);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [showDrawer, setShowDrawer] = useState<boolean>(false);

  const loadData = async () => {
    setRefreshing(true);
    try {
      const [agents, prs] = await Promise.all([
        fetchAgents().catch(() => []),
        fetchGitHubPRs().catch(() => [])
      ]);

      if (Array.isArray(agents)) {
        const active = agents.filter((a: any) => a.status === 'RUNNING' || a.status === 'active');
        const blocked = agents.filter((a: any) => a.status === 'BLOCKED');
        setRunningCount(Math.max(1, active.length));
        setBlockedCount(blocked.length);
      }

      if (Array.isArray(prs)) {
        setPrsCount(prs.length);
      }
    } catch (e) {
      console.error('Home load error:', e);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.headerIconText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>MAGISTRATE</Text>

        <TouchableOpacity onPress={() => router.push('/account' as any)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.headerIconText}>👤</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <ScrollView
        style={styles.container}
        contentContainerStyle={{ paddingBottom: 110 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={loadData} tintColor="#FFFFFF" />}
      >
        <View style={styles.sphereContainer}>
          <TouchableOpacity onPress={() => router.push('/status' as any)} activeOpacity={0.85}>
            <StatusRing statusText="OPERATIONAL" subText="All Systems Operational" />
          </TouchableOpacity>
          <Text style={styles.sphereHint}>TAP SPHERE FOR SYSTEM TELEMETRY ↗</Text>
        </View>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>FLEET COMMAND</Text>
        </View>

        <FleetMetric
          runningCount={runningCount}
          blockedCount={blockedCount}
          prsOpenCount={prsCount}
          needsYouCount={needsCount}
        />

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>REPORTS & OVERVIEW</Text>
        </View>

        <GlassSurface variant="card" style={styles.briefCard}>
          <Text style={styles.briefTitle}>SYSTEM SUMMARY BRIEF</Text>
          <Text style={styles.briefBody}>
            • {runningCount} Active agent worker executing Firstmate tasks on melkezic.
          </Text>
          <Text style={styles.briefBody}>
            • {prsCount} Open Pull Requests ready for code review.
          </Text>
          <Text style={styles.briefBody}>
            • {blockedCount} Blocked tasks requiring manual intervention.
          </Text>
          <TouchableOpacity style={styles.viewCrewBtn} onPress={() => router.push('/agents' as any)}>
            <Text style={styles.viewCrewBtnText}>VIEW CREW DASHBOARD ↗</Text>
          </TouchableOpacity>
        </GlassSurface>
      </ScrollView>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={(r) => router.push('/' + r as any)}
        activeAgentsCount={runningCount}
        attentionCount={needsCount}
        prsCount={prsCount}
      />
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 16 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12, marginBottom: 4 },
  headerTitle: { fontFamily: 'monospace', fontSize: 15, fontWeight: '300', color: '#FFFFFF', letterSpacing: 3 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  headerIconText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  sphereContainer: { alignItems: 'center', marginVertical: 14 },
  sphereHint: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.5)', marginTop: 8, letterSpacing: 1 },
  sectionHeader: { marginTop: 14, marginBottom: 8 },
  sectionTitle: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.5 },
  briefCard: { padding: 18, borderRadius: 18 },
  briefTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF', marginBottom: 8, letterSpacing: 1 },
  briefBody: { fontSize: 12, color: 'rgba(255, 255, 255, 0.7)', lineHeight: 18, marginBottom: 4 },
  viewCrewBtn: { borderTopWidth: 1, borderTopColor: 'rgba(255, 255, 255, 0.1)', paddingTop: 10, marginTop: 8, alignItems: 'flex-end' },
  viewCrewBtnText: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1 }
});
