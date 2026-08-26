import React, { useCallback, useEffect, useState, useRef } from 'react';
import { View, Text, StyleSheet, FlatList, ScrollView, TouchableOpacity, TextInput, NativeSyntheticEvent, NativeScrollEvent, Platform } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { TerminusControlBar } from '../../src/components/TerminusControlBar';
import { CapabilitySelect } from '../../src/components/CapabilitySelect';
import { ExecutionHarness, fetchCaptainOutput, fetchExecutionCapabilities, sendCaptainPrompt, sendAgentKey } from '../../src/api/client';
import { usePathname, useRouter } from 'expo-router';

const CHAT_NAV_ITEMS = [
  { id: 'home', label: 'HOME', route: '/' },
  { id: 'agents', label: 'AGENTS', route: '/agents' },
  { id: 'attention', label: 'ATTENTION', route: '/attention' },
  { id: 'prs', label: 'PULL REQUESTS', route: '/prs' },
  { id: 'chat', label: 'CHAT', route: '/chat' }
];

export default function ChatScreen() {
  const router = useRouter();
  const pathname = usePathname();

  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [promptText, setPromptText] = useState<string>('');
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [isScrolledUp, setIsScrolledUp] = useState<boolean>(false);
  const [hasNewMessages, setHasNewMessages] = useState<boolean>(false);
  const [isThinking, setIsThinking] = useState<boolean>(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [harnesses, setHarnesses] = useState<ExecutionHarness[]>([]);
  const [selectedHarness, setSelectedHarness] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [capabilityLoading, setCapabilityLoading] = useState(true);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);

  const scrollRef = useRef<FlatList<string>>(null);
  const inputRef = useRef<TextInput>(null);
  const isScrolledUpRef = useRef(false);
  const touchYRef = useRef<number | null>(null);
  const requestInFlightRef = useRef(false);

  const loadOutput = useCallback(async () => {
    if (requestInFlightRef.current) return;
    requestInFlightRef.current = true;
    try {
      const data = await fetchCaptainOutput();
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
      requestInFlightRef.current = false;
    }
  }, []);

  useEffect(() => {
    loadOutput();
    const interval = setInterval(loadOutput, 2000);
    return () => clearInterval(interval);
  }, [loadOutput]);

  useEffect(() => {
    let mounted = true;
    fetchExecutionCapabilities()
      .then(data => {
        if (!Array.isArray(data.harnesses)) throw new Error('Gateway returned an invalid execution inventory.');
        const verifiedHarnesses = data.harnesses.filter(harness => harness.verified);
        if (!mounted) return;
        setHarnesses(verifiedHarnesses);
        const firstHarness = verifiedHarnesses[0];
        setSelectedHarness(firstHarness?.id || '');
        setSelectedModel(firstHarness?.models[0]?.id || '');
      })
      .catch(error => {
        if (mounted) setCapabilityError(error instanceof Error ? error.message : 'Execution options could not be loaded.');
      })
      .finally(() => {
        if (mounted) setCapabilityLoading(false);
      });
    return () => { mounted = false; };
  }, []);

  const handleTerminalWheel = (event: any) => {
    if (Platform.OS !== 'web') return;
    const terminal = event.currentTarget as HTMLElement | null;
    const deltaY = event.nativeEvent?.deltaY ?? event.deltaY;
    if (!terminal || typeof deltaY !== 'number') return;
    event.preventDefault?.();
    isScrolledUpRef.current = true;
    terminal.scrollTop += deltaY;
  };

  const handleTerminalKeyDown = (event: any) => {
    if (Platform.OS !== 'web' || !['PageUp', 'PageDown'].includes(event.key)) return;
    const terminal = event.currentTarget as HTMLElement | null;
    if (!terminal) return;
    event.preventDefault?.();
    isScrolledUpRef.current = true;
    const direction = event.key === 'PageUp' ? -1 : 1;
    terminal.scrollTop = Math.max(0, Math.min(terminal.scrollHeight - terminal.clientHeight, terminal.scrollTop + direction * terminal.clientHeight));
  };

  const handleTerminalTouchStart = (event: any) => {
    const touch = event.nativeEvent?.touches?.[0] ?? event.nativeEvent?.changedTouches?.[0] ?? event.touches?.[0];
    const touchY = touch?.pageY ?? touch?.clientY;
    touchYRef.current = typeof touchY === 'number' ? touchY : null;
  };

  const handleTerminalTouchMove = (event: any) => {
    const terminal = (event.currentTarget || event.target) as HTMLElement | null;
    const touch = event.nativeEvent?.touches?.[0] ?? event.nativeEvent?.changedTouches?.[0] ?? event.touches?.[0];
    const touchY = touch?.pageY ?? touch?.clientY;
    if (!terminal || typeof touchY !== 'number' || touchYRef.current === null) return;
    const deltaY = touchYRef.current - touchY;
    if (deltaY === 0) return;
    touchYRef.current = touchY;
    isScrolledUpRef.current = true;
    terminal.scrollTop = Math.max(0, Math.min(terminal.scrollHeight - terminal.clientHeight, terminal.scrollTop + deltaY));
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
      try {
        const response = await sendAgentKey('captain', 'Enter');
        if (response?.status === 'error' || response?.error) {
          throw new Error(response.error || 'The terminal did not accept Enter.');
        }
        setSendError(null);
        await loadOutput();
      } catch (e) {
        setSendError(e instanceof Error ? e.message : 'The terminal did not accept Enter.');
      }
      return;
    }

    if (capabilityLoading) {
      setSendError('Execution options are still loading.');
      return;
    }
    if (capabilityError) {
      setSendError('Execution options are unavailable.');
      return;
    }
    if (!selectedHarness || !selectedModel) {
      setSendError('Select a verified harness and model before sending.');
      return;
    }

    setPromptText('');
    setSendError(null);
    setIsThinking(true);
    try {
      const response = await sendCaptainPrompt(text, 'iphone', 'captain', selectedHarness, selectedModel);
      if (response?.status === 'error' || response?.error) {
        throw new Error(response.error || 'The prompt was not accepted.');
      }
      setTimeout(() => {
        loadOutput();
        setIsThinking(false);
        scrollToBottom();
      }, 600);
    } catch (e) {
      console.error('Send prompt error:', e);
      setPromptText(text);
      setSendError(e instanceof Error ? e.message : 'The prompt could not be sent.');
      setIsThinking(false);
    }
  };

  const handleNavigate = (route: string) => {
    if (route === 'chat') return;
    router.push('/' + route as any);
  };

  const selectedHarnessOption = harnesses.find(harness => harness.id === selectedHarness);
  const models = selectedHarnessOption?.models || [];

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
          <View style={styles.terminalHeaderRow}>
            <View style={[styles.statusDot, isThinking ? styles.dotThinking : undefined]} />
            <Text style={styles.terminalTitle}>
              {'<firstmate> <melkezic/firstmate>'}
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
              {...({ onWheel: handleTerminalWheel } as any)}
              {...({ onKeyDown: handleTerminalKeyDown } as any)}
              {...({ onTouchStart: handleTerminalTouchStart, onTouchMove: handleTerminalTouchMove } as any)}
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

        <View style={styles.navigationRow}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.navigationContent}>
            {CHAT_NAV_ITEMS.map(item => {
              const active = item.route === pathname || (item.route === '/' && (pathname === '/' || pathname === '/(tabs)'));
              return (
                <TouchableOpacity
                  key={item.id}
                  testID={`chat-nav-${item.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Navigate to ${item.label.toLowerCase()}`}
                  accessibilityState={{ selected: active }}
                  {...({ 'aria-current': active ? 'page' : undefined } as any)}
                  onPress={() => router.push(item.route as any)}
                  style={[styles.navigationButton, active ? styles.navigationButtonActive : undefined]}
                >
                  <Text style={[styles.navigationButtonText, active ? styles.navigationButtonTextActive : undefined]}>{item.label}</Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </View>

        <View style={styles.executionSelection}>
          <CapabilitySelect
            testID="harness-select"
            label="HARNESS"
            value={selectedHarness}
            options={harnesses.map(harness => ({ id: harness.id, label: harness.label }))}
            loading={capabilityLoading}
            error={capabilityError}
            emptyMessage="No verified harnesses configured."
            disabled={isThinking}
            onChange={value => {
              const harness = harnesses.find(option => option.id === value);
              setSelectedHarness(value);
              setSelectedModel(harness?.models[0]?.id || '');
              setSendError(null);
            }}
          />
          <CapabilitySelect
            testID="model-select"
            label="MODEL"
            value={selectedModel}
            options={models}
            loading={capabilityLoading}
            error={capabilityError || (selectedHarness && harnesses.length > 0 && models.length === 0 ? 'No models are available for this harness.' : null)}
            emptyMessage={selectedHarness ? 'No models available for this harness.' : 'Select a harness first.'}
            disabled={isThinking || !selectedHarness}
            onChange={value => { setSelectedModel(value); setSendError(null); }}
          />
        </View>

        <TerminusControlBar target="captain" onKeySent={() => setTimeout(loadOutput, 400)} />

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
              onSubmitEditing={handleSend}
              returnKeyType="send"
              editable={!isThinking}
            />
            <View style={styles.inputAccessories}>
              <View style={styles.inputPill}><Text style={styles.inputPillText}>Paste</Text></View>
              <View style={styles.inputPill}><Text style={styles.inputPillText}>AI</Text></View>
            </View>
          </View>
          <TouchableOpacity
            testID="send-captain-prompt"
            accessibilityRole="button"
            accessibilityLabel="Send command to captain"
            accessibilityState={{ disabled: isThinking, busy: isThinking }}
            onPress={handleSend}
            disabled={isThinking}
            style={[styles.sendBtn, isThinking ? styles.sendBtnDisabled : undefined]}
            activeOpacity={0.8}
          >
            <Text style={styles.sendBtnText}>{isThinking ? '…' : 'SEND'}</Text>
          </TouchableOpacity>
        </View>
        {sendError ? <Text testID="captain-send-error" style={styles.sendError}>{sendError}</Text> : null}
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
  navigationRow: { marginTop: 2, marginBottom: 2 },
  navigationContent: { gap: 6, paddingVertical: 2 },
  navigationButton: { minHeight: 40, paddingHorizontal: 12, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.18)', backgroundColor: 'rgba(255, 255, 255, 0.06)', justifyContent: 'center' },
  navigationButtonActive: { borderColor: '#72F5B1', backgroundColor: 'rgba(114, 245, 177, 0.16)' },
  navigationButtonText: { color: 'rgba(255, 255, 255, 0.62)', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', letterSpacing: 0.8 },
  navigationButtonTextActive: { color: '#72F5B1' },
  executionSelection: { flexDirection: 'row', gap: 8, marginTop: 4, marginBottom: 2 },
  inputWrapper: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 18, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.15)', paddingHorizontal: 12, paddingVertical: 8 },
  textInputInner: { flex: 1, color: '#FFFFFF', fontSize: 14, paddingVertical: 4 },
  inputAccessories: { flexDirection: 'row', gap: 6, marginLeft: 8 },
  inputPill: { backgroundColor: 'rgba(255, 255, 255, 0.15)', borderRadius: 12, paddingHorizontal: 10, paddingVertical: 4 },
  inputPillText: { fontSize: 10, color: 'rgba(255, 255, 255, 0.7)' },
  textInput: { flex: 1, backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.2)', paddingHorizontal: 12, paddingVertical: 10, color: '#FFFFFF', fontSize: 14 },
  sendBtn: { minWidth: 60, minHeight: 44, backgroundColor: '#FFFFFF', borderRadius: 12, paddingVertical: 11, paddingHorizontal: 14, justifyContent: 'center', alignItems: 'center' },
  sendBtnDisabled: { opacity: 0.55 },
  sendBtnText: { fontFamily: 'monospace', color: '#000000', fontWeight: 'bold', fontSize: 12 },
  sendError: { color: '#FCA5A5', fontSize: 12, marginTop: 6, marginHorizontal: 4 }
});
