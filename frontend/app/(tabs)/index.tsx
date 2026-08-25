import React, { useEffect, useState, useRef } from 'react';
import { View, Text, TouchableOpacity, ScrollView, StyleSheet, Modal, TextInput, Linking, Platform, NativeSyntheticEvent, NativeScrollEvent } from 'react-native';
import { Svg, Circle, Path } from 'react-native-svg';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { StatusRing } from '../../src/components/StatusRing';
import { FleetMetric } from '../../src/components/FleetMetric';
import { SummarySectionCard } from '../../src/components/SummarySectionCard';
import { BottomControls } from '../../src/components/BottomControls';
import { TerminusControlBar } from '../../src/components/TerminusControlBar';
import { fetchAttention, fetchAgents, fetchFleet, fetchCaptainOutput, sendCaptainPrompt, interruptAgent, sendAgentKey, fetchGitHubPRs, fetchUserProfile, AttentionItem, AgentInfo, GitHubPR } from '../../src/api/client';
import { realtimeClient } from '../../src/realtime/socket';
import { voiceInputAdapter } from '../../src/input/VoiceInputAdapter';
import { gestureInputAdapter } from '../../src/input/GestureInputAdapter';
import { useRouter } from 'expo-router';

export default function MagistrateHomeScreen() {
  const router = useRouter();

  const [attentionItems, setAttentionItems] = useState<AttentionItem[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [fleetData, setFleetData] = useState<any>({ tasks: [] });
  const [githubPRs, setGithubPRs] = useState<GitHubPR[]>([]);
  const [userAvatar, setUserAvatar] = useState<string>('');
  const [captainOutput, setCaptainOutput] = useState<string>('');
  const [promptText, setPromptText] = useState<string>('');
  const [isRecording, setIsRecording] = useState<boolean>(false);

  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [showChatModal, setShowChatModal] = useState<boolean>(false);
  const [showGestureModal, setShowGestureModal] = useState<boolean>(false);
  const [activeGesture, setActiveGesture] = useState<string>('');

  const [isScrolledUp, setIsScrolledUp] = useState<boolean>(false);
  const [hasNewMessages, setHasNewMessages] = useState<boolean>(false);
  const modalScrollRef = useRef<ScrollView>(null);

  const loadData = async () => {
    try {
      const [att, ag, fl, prsData, profData, out] = await Promise.all([
        fetchAttention().catch(() => []),
        fetchAgents().catch(() => []),
        fetchFleet().catch(() => ({ tasks: [] })),
        fetchGitHubPRs().catch(() => []),
        fetchUserProfile().catch(() => null),
        fetchCaptainOutput(100).catch(() => ({ output: '' }))
      ]);
      setAttentionItems(att || []);
      setAgents(ag || []);
      setFleetData(fl || { tasks: [] });
      if (prsData && Array.isArray(prsData)) setGithubPRs(prsData);
      if (profData && profData.avatar_url) {
        let avatarUrl = profData.avatar_url;
        if (avatarUrl.startsWith('/uploads')) avatarUrl = 'http://100.84.181.23:8000' + avatarUrl;
        setUserAvatar(avatarUrl);
      }
      setCaptainOutput(prev => {
        const newText = out?.output || 'No recent Codex Captain output.';
        if (prev !== newText && isScrolledUp) {
          setHasNewMessages(true);
        }
        return newText;
      });
    } catch (e) {
      console.error('Error loading Magistrate telemetry:', e);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 2500);
    realtimeClient.connect();
    const unsubscribe = realtimeClient.subscribe((evt) => {
      if (evt.event === 'state_update') {
        setAgents(evt.agents || []);
        setAttentionItems(evt.attention_items || []);
      }
    });
    return () => {
      clearInterval(interval);
      unsubscribe();
    };
  }, [isScrolledUp]);

  useEffect(() => {
    if (showChatModal && !isScrolledUp) {
      modalScrollRef.current?.scrollToEnd({ animated: true });
    }
  }, [captainOutput, showChatModal]);

  const handleModalScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, layoutMeasurement, contentSize } = event.nativeEvent;
    const isAtBottom = contentOffset.y + layoutMeasurement.height >= contentSize.height - 35;
    if (isAtBottom) {
      setIsScrolledUp(false);
      setHasNewMessages(false);
    } else {
      setIsScrolledUp(true);
    }
  };

  const scrollToBottom = () => {
    modalScrollRef.current?.scrollToEnd({ animated: true });
    setIsScrolledUp(false);
    setHasNewMessages(false);
  };

  const handleSendPrompt = async (customText?: string) => {
    const text = customText || promptText;
    if (!text.trim()) {
      await sendAgentKey('captain', 'Enter');
      loadData();
      return;
    }
    setPromptText('');
    try {
      await sendCaptainPrompt(text, 'iphone', 'captain');
      setTimeout(loadData, 600);
      scrollToBottom();
    } catch (e) {
      console.error('Prompt send error:', e);
    }
  };

  const handleToggleVoice = async () => {
    router.push({ pathname: '/(tabs)/chat' as any, params: { record: 'true' } });
  };

  const handleTriggerGesture = (action: string) => {
    setActiveGesture(action);
    gestureInputAdapter.handleGesture(action as any);
    if (action === 'interrupt' && agents.length > 0) {
      interruptAgent(agents[0].id);
    }
    setTimeout(() => setActiveGesture(''), 1500);
  };

  const handleNavigate = (route: string) => {
    try {
      if (route === 'index') return;
      router.push('/' + route as any);
    } catch (e) {
      console.log('Navigation:', route);
    }
  };

  const openGitHubPRs = () => {
    router.push('/(tabs)/prs' as any);
  };

  // REAL LIVE DATA FROM GATEWAY TELEMETRY (ZERO PLACEHOLDERS)
  const runningCount = agents.filter(a => a.status === 'working').length;
  const blockedCount = attentionItems.filter(i => i.status === 'blocked').length;
  const prsCount = githubPRs.length || fleetData?.tasks?.length || 0;
  const needsYouCount = attentionItems.length;

  const systemStatusText = blockedCount > 0 ? 'ATTENTION NEEDED' : 'OPERATIONAL';
  const systemStatusColor = blockedCount > 0 ? '#FFAA20' : '#34D399';

  const primaryAgent = agents[0] || null;
  const primaryAlert = attentionItems[0] || null;
  const topPR = githubPRs[0] || null;

  return (
    <EnvironmentBackground>
      <View style={{ flex: 1 }}>
        <ScrollView style={styles.scrollContainer} contentContainerStyle={styles.scrollContent}>
          {/* TOP NAV HEADER (FLIPPED: HAMBURGER ON LEFT, USER AVATAR ON RIGHT) */}
          <View style={styles.headerRow}>
            {/* TOP LEFT: HAMBURGER EMBLEM */}
            <TouchableOpacity onPress={() => setShowDrawer(!showDrawer)}>
              <GlassSurface variant="control" style={styles.headerCircleBtn}>
                <Text style={styles.menuIconText}>≡</Text>
              </GlassSurface>
            </TouchableOpacity>

            {/* CENTER: MAGISTRATE TITLE */}
            <Text style={styles.headerTitle}>MAGISTRATE</Text>

            {/* TOP RIGHT: ACCOUNT CIRCLE BUTTON / AVATAR */}
            <TouchableOpacity onPress={() => handleNavigate('account')}>
              <GlassSurface variant="control" style={styles.headerCircleBtn}>
                {userAvatar ? (
                  <View style={styles.headerAvatarWrapper}>
                    <Text style={styles.headerAvatarText}>👤</Text>
                  </View>
                ) : (
                  <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                    <Path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                    <Circle cx={12} cy={7} r={4} />
                  </Svg>
                )}
              </GlassSurface>
            </TouchableOpacity>
          </View>

          {/* HERO ORBITAL STATUS RING */}
          <StatusRing
            statusText={systemStatusText}
            statusColor={systemStatusColor}
            subText={blockedCount > 0 ? 'Action Required' : 'All Systems Operational'}
          />

          {/* PRIMARY FLEET SUMMARY RECTANGLE TABS (INFORMATIONAL NON-INTERACTIVE INDICATORS) */}
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionLabel}>FLEET COMMAND</Text>
          </View>
          <FleetMetric
            runningCount={runningCount}
            blockedCount={blockedCount}
            prsCount={prsCount}
            needsYouCount={needsYouCount}
          />

          {/* APPLE HEALTH STYLE MONOCHROME SUBTLER BRIEF CARDS */}
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionLabel}>REPORTS & OVERVIEW</Text>
          </View>

          {/* 1. RUNNING AGENTS BRIEF */}
          <SummarySectionCard
            title="RUNNING AGENTS BRIEF"
            countText={runningCount + ' Active'}
            itemTitle={primaryAgent ? primaryAgent.name + ' • ' + primaryAgent.harness : 'No active workers'}
            itemSubtitle={primaryAgent ? 'Herdr worker active on pane ' + primaryAgent.pane_id : 'All agent harnesses idle'}
            statusBadge="RUNNING"
            onPress={() => handleNavigate('agents')}
          />

          {/* 2. BLOCKED AGENTS BRIEF */}
          <SummarySectionCard
            title="BLOCKED AGENTS BRIEF"
            countText={blockedCount + ' Blocked'}
            itemTitle={primaryAlert ? primaryAlert.title : 'Zero active blockers'}
            itemSubtitle={primaryAlert ? primaryAlert.subtitle : 'All agent tasks operating smoothly'}
            statusBadge={blockedCount > 0 ? 'BLOCKED' : 'CLEAR'}
            onPress={() => handleNavigate('attention')}
          />

          {/* 3. PULL REQUESTS BRIEF */}
          <SummarySectionCard
            title="PULL REQUESTS BRIEF"
            countText={prsCount + ' Open PRs'}
            itemTitle={topPR ? 'PR #' + topPR.pr_number + ' • ' + topPR.title : 'melkezic/firstmate repository'}
            itemSubtitle={topPR ? topPR.repository : 'View all open GitHub PRs'}
            statusBadge="REVIEW ↗"
            onPress={openGitHubPRs}
          />

          {/* 4. NEEDS ATTENTION BRIEF */}
          <SummarySectionCard
            title="HUMAN ATTENTION BRIEF"
            countText={needsYouCount + ' Item'}
            itemTitle={needsYouCount > 0 ? 'Codex Captain Decision Required' : 'Zero items in attention queue'}
            itemSubtitle={needsYouCount > 0 ? 'Review agent prompt output or send resolution key.' : 'Fleet operational without intervention'}
            statusBadge={needsYouCount > 0 ? 'ACTION' : 'NORMAL'}
            onPress={() => handleNavigate('attention')}
          />
        </ScrollView>

        {/* FIXED FLOATING ACTION BOTTOM CONTROLS */}
        <BottomControls
          isRecording={isRecording}
          onToggleChat={() => router.push('/(tabs)/chat' as any)}
          onToggleVoice={handleToggleVoice}
          onToggleGesture={() => setShowGestureModal(true)}
        />
      </View>

      {/* HAMBURGER GLASS DRAWER */}
      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={handleNavigate}
        activeAgentsCount={runningCount}
        attentionCount={needsYouCount}
        prsCount={prsCount}
      />

      {/* GESTURE CONTROL MODAL */}
      <Modal visible={showGestureModal} animationType="fade" transparent onRequestClose={() => setShowGestureModal(false)}>
        <View style={styles.modalOverlay}>
          <GlassSurface variant="surface" intensity={60} style={styles.modalGlassCard}>
            <View style={styles.modalHeaderRow}>
              <Text style={styles.modalTitle}>GESTURE CONTRACT ADAPTER</Text>
              <TouchableOpacity onPress={() => setShowGestureModal(false)}>
                <Text style={styles.modalCloseBtn}>✕</Text>
              </TouchableOpacity>
            </View>
            <Text style={styles.gestureDescription}>
              Universal spatial gesture inputs mapping 1:1 to Firstmate crew actions.
            </Text>

            {activeGesture !== '' ? (
              <View style={styles.gestureFeedbackBadge}>
                <Text style={styles.gestureFeedbackText}>EXECUTED: {activeGesture.toUpperCase()}</Text>
              </View>
            ) : null}

            <View style={styles.gestureGrid}>
              <TouchableOpacity style={styles.gestureTile} onPress={() => handleTriggerGesture('select')}>
                <Text style={styles.gestureTileIcon}>👆</Text>
                <Text style={styles.gestureTileLabel}>SELECT</Text>
              </TouchableOpacity>

              <TouchableOpacity style={styles.gestureTile} onPress={() => handleTriggerGesture('activate')}>
                <Text style={styles.gestureTileIcon}>👌</Text>
                <Text style={styles.gestureTileLabel}>ACTIVATE</Text>
              </TouchableOpacity>

              <TouchableOpacity style={styles.gestureTile} onPress={() => handleTriggerGesture('scroll_up')}>
                <Text style={styles.gestureTileIcon}>🖐️</Text>
                <Text style={styles.gestureTileLabel}>SCROLL</Text>
              </TouchableOpacity>

              <TouchableOpacity style={styles.gestureTile} onPress={() => handleTriggerGesture('interrupt')}>
                <Text style={styles.gestureTileIcon}>✊</Text>
                <Text style={styles.gestureTileLabel}>INTERRUPT</Text>
              </TouchableOpacity>
            </View>
          </GlassSurface>
        </View>
      </Modal>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  scrollContainer: { flex: 1 },
  scrollContent: { paddingHorizontal: 16, paddingTop: 10, paddingBottom: 120, justifyContent: 'flex-start' },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  headerTitle: { fontFamily: 'monospace', fontSize: 16, fontWeight: '300', color: 'rgba(255, 255, 255, 0.96)', letterSpacing: 3.5 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  menuIconText: { color: 'rgba(255, 255, 255, 0.96)', fontSize: 16, fontWeight: 'bold' },
  headerAvatarWrapper: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  headerAvatarText: { fontSize: 16 },
  sectionHeaderRow: { marginTop: 16, marginBottom: 6 },
  sectionLabel: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: 'rgba(255, 255, 255, 0.70)', letterSpacing: 1.6 },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.75)', justifyContent: 'flex-end' },
  modalGlassCard: { padding: 20, borderTopLeftRadius: 24, borderTopRightRadius: 24, maxHeight: '84%' },
  modalHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  modalTitle: { fontFamily: 'monospace', color: '#72F5B1', fontWeight: 'bold', fontSize: 14, letterSpacing: 1.2 },
  modalCloseBtn: { color: 'rgba(255, 255, 255, 0.96)', fontSize: 18, fontWeight: 'bold' },
  gestureDescription: { color: 'rgba(255, 255, 255, 0.68)', fontSize: 13, marginBottom: 16 },
  gestureFeedbackBadge: { backgroundColor: 'rgba(114, 245, 177, 0.2)', borderRadius: 8, padding: 8, marginBottom: 12, alignItems: 'center' },
  gestureFeedbackText: { fontFamily: 'monospace', color: '#72F5B1', fontWeight: 'bold', fontSize: 12 },
  gestureGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, justifyContent: 'space-between' },
  gestureTile: { width: '48%', backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.18)', padding: 16, alignItems: 'center' },
  gestureTileIcon: { fontSize: 28, marginBottom: 8 },
  gestureTileLabel: { fontFamily: 'monospace', color: '#FFFFFF', fontWeight: 'bold', fontSize: 12, letterSpacing: 1 }
});
