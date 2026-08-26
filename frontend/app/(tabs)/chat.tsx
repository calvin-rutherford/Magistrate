import React, { useCallback, useEffect, useState, useRef } from 'react';
import { View, Text, StyleSheet, FlatList, ScrollView, TouchableOpacity, TextInput, NativeSyntheticEvent, NativeScrollEvent } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { TerminusControlBar } from '../../src/components/TerminusControlBar';
import { AgentInfo, fetchAgentOutput, fetchAgents, sendCaptainPrompt, sendAgentKey } from '../../src/api/client';
import { useLocalSearchParams, useRouter } from 'expo-router';

export default function ChatScreen() {
  const router = useRouter();
  const { pane } = useLocalSearchParams<{ pane?: string | string[] }>();
  const deepLinkedPane = Array.isArray(pane) ? pane[0] : pane;

  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<string>(deepLinkedPane || 'captain');
  const [promptText, setPromptText] = useState<string>('');
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [isScrolledUp, setIsScrolledUp] = useState<boolean>(false);
  const [hasNewMessages, setHasNewMessages] = useState<boolean>(false);
  const [isThinking, setIsThinking] = useState<boolean>(false);

  const scrollRef = useRef<FlatList<string>>(null);
  const inputRef = useRef<TextInput>(null);
  const isScrolledUpRef = useRef(false);
  const inFlightTargetRef = useRef<string | null>(null);
  const selectedTargetRef = useRef(selectedTarget);

  const loadOutput = useCallback(async () => {
    const target = selectedTarget;
    if (inFlightTargetRef.current === target) return;
    inFlightTargetRef.current = target;
    try {
      const data = await fetchAgentOutput(target);
      if (selectedTargetRef.current !== target) return;
      const newText = data?.output || 'No output.';
      const newLines = newText.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
      if (newLines.length > 1 && newLines[newLines.length - 1] === '') newLines.pop();
      setOutputLines(prev => {
        const unchanged = prev.length === newLines.length && prev.every((line, index) => line === newLines[index]);
        if (!unchanged) {
          if (isScrolledUpRef.current) setHasNewMessages(true);
          return newLines;
        }
        return prev;
      });
    } catch (e) {
      console.error('Chat output load error:', e);
    } finally {
      if (inFlightTargetRef.current === target) inFlightTargetRef.current = null;
    }
  }, [selectedTarget]);

  useEffect(() => {
    fetchAgents().then(agentData => {
      const sorted = [...(agentData || [])].sort((left, right) => {
        const leftFirstmate = /firstmate/i.test(`${left.name} ${left.terminal_title || ''}`) ? 0 : 1;
        const rightFirstmate = /firstmate/i.test(`${right.name} ${right.terminal_title || ''}`) ? 0 : 1;
        return leftFirstmate - rightFirstmate;
      });
      setAgents(sorted);
      if (deepLinkedPane) {
        selectedTargetRef.current = deepLinkedPane;
        setSelectedTarget(deepLinkedPane);
      } else if (sorted.length > 0) {
        const target = sorted[0].pane_id || sorted[0].id;
        selectedTargetRef.current = target;
        setSelectedTarget(target);
      }
    }).catch(e => console.error('Agent pane load error:', e));
  }, [deepLinkedPane]);

  useEffect(() => {
    loadOutput();
    const interval = setInterval(loadOutput, 2000);
    return () => clearInterval(interval);
  }, [loadOutput]);

  const selectTarget = (target: string) => {
    selectedTargetRef.current = target;
    setSelectedTarget(target);
    setOutputLines([]);
    isScrolledUpRef.current = false;
    setIsScrolledUp(false);
    setHasNewMessages(false);
    router.setParams({ pane: target });
  };

  const paneTabs = agents.some(agent => (agent.pane_id || agent.id) === selectedTarget)
    ? agents
    : [{ id: selectedTarget, pane_id: selectedTarget, name: selectedTarget, harness: 'agent', status: 'unknown' as const }, ...agents];

  const tabLabel = (agent: AgentInfo) => {
    const target = agent.pane_id || agent.id;
    if (/firstmate/i.test(`${agent.name} ${agent.terminal_title || ''}`)) return 'Firstmate';
    return `${agent.name || agent.harness} · ${target}`;
  };



  const handleScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const isAtBottom = contentOffset.y + layoutMeasurement.height >= contentSize.height - 35;
    isScrolledUpRef.current = !isAtBottom;
    if (isAtBottom) {
      setIsScrolledUp(false);
      setHasNewMessages(false);
    } else {
      setIsScrolledUp(true);
    }
  };

  const scrollToBottom = () => {
    isScrolledUpRef.current = false;
    setIsScrolledUp(false);
    setHasNewMessages(false);
    // A non-animated jump avoids intermediate scroll events switching follow
    // mode back off while a virtualized list is measuring its final rows.
    scrollRef.current?.scrollToEnd({ animated: false });
  };

  const handleSend = async () => {
    const text = promptText.trim();
    if (!text) {
      await sendAgentKey(selectedTarget, 'Enter');
      loadOutput();
      return;
    }

    setPromptText('');
    setIsThinking(true);
    try {
      await sendCaptainPrompt(text, 'iphone', selectedTarget);
      setTimeout(() => {
        loadOutput();
        setIsThinking(false);
        scrollToBottom();
      }, 600);
    } catch (e) {
      console.error('Send prompt error:', e);
      setIsThinking(false);
    }
  };

  const handleNavigate = (route: string) => {
    if (route === 'chat') return;
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

        <Text style={styles.headerTitle}>FIRSTMATE CHAT</Text>

        <TouchableOpacity onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <View style={styles.chatContainer}>
        <GlassSurface
          variant="surface"
          intensity={60}
          style={styles.terminalGlassBox}
          contentStyle={styles.terminalGlassContent}
        >
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            style={styles.paneTabs}
            contentContainerStyle={styles.paneTabsContent}
            accessibilityRole="tablist"
          >
            {paneTabs.map(agent => {
              const target = agent.pane_id || agent.id;
              const selected = target === selectedTarget;
              return (
                <TouchableOpacity
                  key={target}
                  testID={`pane-tab-${target}`}
                  accessibilityRole="tab"
                  accessibilityState={{ selected }}
                  accessibilityLabel={`${tabLabel(agent)} pane ${target}`}
                  {...({ 'aria-selected': selected } as any)}
                  onPress={() => selectTarget(target)}
                  style={[styles.paneTab, selected ? styles.paneTabSelected : undefined]}
                >
                  <Text style={[styles.paneTabText, selected ? styles.paneTabTextSelected : undefined]}>
                    {tabLabel(agent)}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>

          <View style={styles.terminalHeaderRow}>
            <View style={[styles.statusDot, isThinking ? styles.dotThinking : undefined]} />
            <Text style={styles.terminalTitle}>
              {`<${selectedTarget}>`}
            </Text>
          </View>

          <View style={styles.terminalScrollContainer}>
            <FlatList
              ref={scrollRef}
              testID="terminal-scroll"
              style={styles.terminalScroll}
              contentContainerStyle={styles.terminalScrollContent}
              data={outputLines}
              renderItem={({ item }) => <Text style={styles.terminalText}>{item || ' '}</Text>}
              keyExtractor={(_, index) => String(index)}
              getItemLayout={(_, index) => ({ length: 18, offset: 18 * index, index })}
              initialNumToRender={40}
              maxToRenderPerBatch={40}
              updateCellsBatchingPeriod={25}
              windowSize={15}
              onScroll={handleScroll}
              scrollEventThrottle={16}
              nestedScrollEnabled
              keyboardShouldPersistTaps="handled"
              accessibilityLabel="Captain terminal output"
              // Makes the web terminal keyboard-scrollable without changing
              // touch or native accessibility behavior.
              {...({ tabIndex: 0 } as any)}
              onContentSizeChange={() => {
                if (!isScrolledUpRef.current) {
                  scrollRef.current?.scrollToEnd({ animated: false });
                }
              }}
            />

            {(hasNewMessages || isScrolledUp) ? (
              <TouchableOpacity
                testID="jump-to-latest"
                accessibilityRole="button"
                accessibilityLabel="Jump to latest terminal output"
                style={styles.scrollBadgeBtn}
                onPress={scrollToBottom}
                activeOpacity={0.8}
              >
                <GlassSurface variant="control" style={styles.scrollBadgeSurface}>
                  <Text style={styles.scrollBadgeText}>
                    {hasNewMessages ? '↓ NEW MESSAGES (TAP TO SCROLL)' : '↓ SCROLL TO BOTTOM'}
                  </Text>
                </GlassSurface>
              </TouchableOpacity>
            ) : null}
          </View>
        </GlassSurface>

        <TerminusControlBar target={selectedTarget} onKeySent={() => setTimeout(loadOutput, 400)} />

        <View style={styles.inputComposerRow}>
          <View style={styles.inputWrapper}>
            <TextInput
              ref={inputRef}
              testID="captain-prompt"
              style={styles.textInputInner}
              placeholder="Ask AI to generate a command"
              accessibilityLabel="Command for captain"
              placeholderTextColor="rgba(255, 255, 255, 0.45)"
              value={promptText}
              onChangeText={setPromptText}
              onSubmitEditing={() => handleSend()}
            />
            <View style={styles.inputAccessories}>
              <View style={styles.inputPill}><Text style={styles.inputPillText}>Paste</Text></View>
              <View style={styles.inputPill}><Text style={styles.inputPillText}>AI</Text></View>
            </View>
          </View>


        </View>
      </View>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={handleNavigate}
        activeAgentsCount={1}
        attentionCount={0}
        prsCount={2}
      />
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12, marginBottom: 6 },
  headerTitle: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  chatContainer: { flex: 1, paddingHorizontal: 16, paddingBottom: 16 },
  terminalGlassBox: { flex: 1, minHeight: 0, padding: 14, borderRadius: 20, marginBottom: 8 },
  terminalGlassContent: { flex: 1, minHeight: 0 },
  paneTabs: { flexGrow: 0, marginBottom: 8 },
  paneTabsContent: { gap: 4 },
  paneTab: { minHeight: 30, justifyContent: 'center', paddingHorizontal: 10, borderRadius: 5, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.14)', backgroundColor: 'rgba(0, 0, 0, 0.32)' },
  paneTabSelected: { borderColor: 'rgba(255, 255, 255, 0.7)', backgroundColor: 'rgba(255, 255, 255, 0.12)' },
  paneTabText: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255, 255, 255, 0.62)' },
  paneTabTextSelected: { color: '#FFFFFF' },
  terminalHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  statusDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#FFFFFF' },
  dotThinking: { backgroundColor: '#AAAAAA' },
  terminalTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.2 },
  terminalScrollContainer: { flex: 1, minHeight: 0, position: 'relative' },
  terminalScroll: { flex: 1, minHeight: 0, backgroundColor: '#000000', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  terminalScrollContent: { padding: 12 },
  terminalText: { fontFamily: 'monospace', fontSize: 12, color: '#FFFFFF', lineHeight: 18 },
  scrollBadgeBtn: { position: 'absolute', bottom: 10, alignSelf: 'center', zIndex: 10 },
  scrollBadgeSurface: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 16, backgroundColor: 'rgba(255, 255, 255, 0.2)', borderColor: '#FFFFFF', borderWidth: 1 },
  scrollBadgeText: { fontFamily: 'monospace', color: '#000000', fontWeight: 'bold', fontSize: 10, letterSpacing: 0.8 },
  inputComposerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
  inputWrapper: { flex: 1, flexDirection: 'row', alignItems: 'center', backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 18, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.15)', paddingHorizontal: 12, paddingVertical: 8 },
  textInputInner: { flex: 1, color: '#FFFFFF', fontSize: 14, paddingVertical: 4 },
  inputAccessories: { flexDirection: 'row', gap: 6, marginLeft: 8 },
  inputPill: { backgroundColor: 'rgba(255, 255, 255, 0.15)', borderRadius: 12, paddingHorizontal: 10, paddingVertical: 4 },
  inputPillText: { fontSize: 10, color: 'rgba(255, 255, 255, 0.7)' },
  textInput: { flex: 1, backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.2)', paddingHorizontal: 12, paddingVertical: 10, color: '#FFFFFF', fontSize: 14 },
  sendBtn: { backgroundColor: '#FFFFFF', borderRadius: 12, paddingVertical: 11, paddingHorizontal: 14 },
  sendBtnText: { fontFamily: 'monospace', color: '#000000', fontWeight: 'bold', fontSize: 12 }
});
