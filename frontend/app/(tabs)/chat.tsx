import React, { useCallback, useEffect, useState, useRef } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, TextInput, NativeSyntheticEvent, NativeScrollEvent, Platform } from 'react-native';
import { GlassSurface } from '../../src/components/GlassSurface';
import { TerminusControlBar } from '../../src/components/TerminusControlBar';
import { CapabilitySelect } from '../../src/components/CapabilitySelect';
import { ExecutionHarness, fetchCaptainOutput, fetchExecutionCapabilities, sendCaptainPrompt, sendAgentKey } from '../../src/api/client';
const WorkspaceShell = React.lazy(() => import('../../src/components/WorkspaceShell').then(module => ({ default: module.WorkspaceShell })));

export function ChatCanvas({ target = 'captain' }: { target?: string }) {

  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [promptText, setPromptText] = useState<string>('');
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

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    // A cold Expo web mount can briefly restore focus to body while the
    // terminal and sidebar settle. Keep ordinary typing directed to Chat so
    // the composer remains usable even if that focus handoff occurs.
    const recoverComposerFocus = (event: KeyboardEvent) => {
      const active = document.activeElement;
      if (active && active !== document.body) return;
      if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) return;
      inputRef.current?.focus();
      setPromptText(value => value + event.key);
      event.preventDefault();
    };
    window.addEventListener('keydown', recoverComposerFocus, true);
    const focusComposer = (event: MouseEvent | PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.getAttribute('data-testid') !== 'captain-prompt') return;
      requestAnimationFrame(() => target.focus());
    };
    document.addEventListener('mousedown', focusComposer, true);
    document.addEventListener('pointerdown', focusComposer, true);
    return () => {
      window.removeEventListener('keydown', recoverComposerFocus, true);
      document.removeEventListener('mousedown', focusComposer, true);
      document.removeEventListener('pointerdown', focusComposer, true);
    };
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
      const response = await sendCaptainPrompt(text, 'iphone', target, selectedHarness, selectedModel);
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

  const selectedHarnessOption = harnesses.find(harness => harness.id === selectedHarness);
  const models = selectedHarnessOption?.models || [];

  return (
    <View style={styles.canvas}>
      <View testID="chat-canvas" style={styles.chatContainer}>
        <View style={styles.canvasTitleRow}>
          <View style={styles.canvasTitleGroup}>
            <Text style={styles.canvasEyebrow}>AI CHAT / COMMAND CONSOLE</Text>
            <Text testID="chat-target" style={styles.canvasTitle}>{target === 'captain' ? 'Captain' : target}</Text>
          </View>
          <View style={styles.targetBadge}><Text style={styles.targetBadgeText}>{target === 'captain' ? 'CAPTAIN' : 'AGENT TARGET'}</Text></View>
        </View>
        <GlassSurface
          variant="surface"
          intensity={60}
          style={styles.terminalGlassBox}
          contentStyle={styles.terminalGlassContent}
        >
          <View style={styles.terminalHeaderRow}>
            <View style={[styles.statusDot, isThinking ? styles.dotThinking : undefined]} />
            <View>
              <Text style={styles.terminalTitle}>ACTIVITY / TERMINAL INSPECTOR</Text>
              <Text style={styles.terminalSubtitle}>Raw Herdr output · not a conversation transcript</Text>
            </View>
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

        {capabilityLoading ? (
          <View testID="capability-loading" style={styles.executionSelection}><Text style={styles.capabilityLoadingText}>Loading verified execution options…</Text></View>
        ) : <View style={styles.executionSelection}>
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
        </View>}

        <TerminusControlBar target="captain" onKeySent={() => setTimeout(loadOutput, 400)} />

        <View style={styles.inputComposerRow}>
          <View style={styles.inputWrapper}>
            <TextInput
              ref={inputRef}
              testID="captain-prompt"
              style={styles.textInputInner}
              placeholder={`Message ${target === 'captain' ? 'Captain' : target}`}
              accessibilityLabel={`Message ${target === 'captain' ? 'Captain' : target}`}
              placeholderTextColor="rgba(255, 255, 255, 0.45)"
              value={promptText}
              onChangeText={setPromptText}
              onSubmitEditing={handleSend}
              {...({ onPointerDown: Platform.OS === 'web' ? (event: any) => event.currentTarget.focus() : undefined } as any)}
              {...({ onTouchStart: Platform.OS === 'web' ? (event: any) => event.currentTarget.focus() : undefined } as any)}
              {...({ onClick: Platform.OS === 'web' ? (event: any) => event.currentTarget.focus() : undefined } as any)}
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
            // React Native's responder can lose a web click while the
            // virtualized terminal is settling; keep the browser composer
            // action explicit without changing native behavior.
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

    </View>
  );
}

export default function ChatScreen() {
  return <React.Suspense fallback={null}><WorkspaceShell /></React.Suspense>;
}

const styles = StyleSheet.create({
  canvas: { flex: 1, minWidth: 0 },
  chatContainer: { flex: 1, paddingHorizontal: 16, paddingBottom: 16, minWidth: 0 },
  canvasTitleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingTop: 4, paddingBottom: 10, gap: 10 },
  canvasTitleGroup: { minWidth: 0, flex: 1 },
  canvasEyebrow: { fontFamily: 'monospace', fontSize: 9, color: 'rgba(255, 255, 255, 0.52)', letterSpacing: 1.4 },
  canvasTitle: { color: '#FFFFFF', fontSize: 24, fontWeight: '300', marginTop: 3 },
  targetBadge: { borderColor: '#72F5B1', borderWidth: 1, borderRadius: 12, paddingHorizontal: 9, paddingVertical: 5 },
  targetBadgeText: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  terminalGlassBox: { flex: 1, minHeight: 0, padding: 14, borderRadius: 20, marginBottom: 8 },
  terminalGlassContent: { flex: 1, minHeight: 0 },
  terminalHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  statusDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#FFFFFF' },
  dotThinking: { backgroundColor: '#AAAAAA' },
  terminalTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.2 },
  terminalSubtitle: { fontFamily: 'monospace', fontSize: 9, color: 'rgba(255, 255, 255, 0.48)', marginTop: 3 },
  terminalScrollContainer: { flex: 1, minHeight: 0, position: 'relative' },
  terminalScroll: { flex: 1, minHeight: 0, backgroundColor: '#000000', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)' },
  terminalScrollContent: { padding: 12 },
  terminalText: { fontFamily: 'monospace', fontSize: 12, color: '#FFFFFF', lineHeight: 18 },
  scrollBadgeBtn: { position: 'absolute', bottom: 10, alignSelf: 'center', zIndex: 10 },
  scrollBadgeSurface: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 16, backgroundColor: 'rgba(255, 255, 255, 0.2)', borderColor: '#FFFFFF', borderWidth: 1 },
  scrollBadgeText: { fontFamily: 'monospace', color: '#000000', fontWeight: 'bold', fontSize: 10, letterSpacing: 0.8 },
  inputComposerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
  executionSelection: { flexDirection: 'row', gap: 8, marginTop: 4, marginBottom: 2 },
  capabilityLoadingText: { color: 'rgba(255,255,255,0.58)', fontFamily: 'monospace', fontSize: 10, paddingVertical: 9 },
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
