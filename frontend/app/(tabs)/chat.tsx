import React, { useEffect, useState, useRef } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput, NativeSyntheticEvent, NativeScrollEvent } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { TerminusControlBar } from '../../src/components/TerminusControlBar';
import { fetchCaptainOutput, sendCaptainPrompt, sendAgentKey } from '../../src/api/client';
import { voiceInputAdapter } from '../../src/input/VoiceInputAdapter';
import { ttsService } from '../../src/services/TextToSpeechService';
import { voiceStateMachine, VoiceState } from '../../src/services/VoiceSessionStateMachine';
import { VoiceIntentRouter } from '../../src/services/VoiceIntentRouter';
import { useRouter, useLocalSearchParams } from 'expo-router';

export default function ChatScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();

  const [output, setOutput] = useState<string>('');
  const [promptText, setPromptText] = useState<string>('');
  const [voiceState, setVoiceState] = useState<VoiceState>('IDLE');
  const [micLevel, setMicLevel] = useState<number>(0.3);
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [isScrolledUp, setIsScrolledUp] = useState<boolean>(false);
  const [hasNewMessages, setHasNewMessages] = useState<boolean>(false);

  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);

  useEffect(() => {
    const unsub = voiceStateMachine.subscribe((st) => setVoiceState(st));
    return unsub;
  }, []);

  const loadOutput = async () => {
    try {
      const data = await fetchCaptainOutput(100);
      const newText = data?.output || 'No output.';
      setOutput(prev => {
        if (prev !== newText) {
          if (isScrolledUp) setHasNewMessages(true);
          if (voiceState === 'THINKING' || ttsService.getSettings().autoSpeak) {
            voiceStateMachine.setState('SPEAKING');
            const lines = newText.trim().split('\n').filter((l: string) => l.trim().length > 0);
            const lastLine = lines[lines.length - 1] || newText;
            const speakText = lastLine.replace(/^\\[.*?\\]/, '').trim();
            ttsService.speakChunk(speakText, () => {
              voiceStateMachine.setState('LISTENING');
              startListeningLoop();
            });
          }
        }
        return newText;
      });
    } catch (e) {
      console.error('Chat output load error:', e);
    }
  };

  useEffect(() => {
    loadOutput();
    const interval = setInterval(loadOutput, 2000);
    return () => clearInterval(interval);
  }, [isScrolledUp]);

  useEffect(() => {
    if (params.record === 'true') {
      startListeningLoop();
    }
  }, [params.record]);

  useEffect(() => {
    if (!isScrolledUp) {
      scrollRef.current?.scrollToEnd({ animated: true });
    }
  }, [output]);

  const handleScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
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
    scrollRef.current?.scrollToEnd({ animated: true });
    setIsScrolledUp(false);
    setHasNewMessages(false);
  };

  const startListeningLoop = async () => {
    if (ttsService.isSpeaking()) {
      ttsService.stop();
    }
    voiceStateMachine.setState('LISTENING');
    inputRef.current?.focus();
    await voiceInputAdapter.startRecording((lvl) => {
      setMicLevel(lvl);
    });
  };

  const stopListeningLoop = async () => {
    voiceStateMachine.setState('TRANSCRIBING');
    const payload = await voiceInputAdapter.stopRecording();
    if (payload.text) {
      setPromptText(payload.text);
      voiceStateMachine.setState('THINKING');
      handleSend(payload.text);
    } else {
      voiceStateMachine.setState('IDLE');
    }
  };

  const handleSend = async (customText?: string) => {
    const text = customText || promptText;
    if (!text.trim()) {
      await sendAgentKey('captain', 'Enter');
      loadOutput();
      return;
    }

    const classified = VoiceIntentRouter.classify(text);
    if (classified.requiresHighImpactConfirmation) {
      alert(classified.confirmationMessage);
    }

    if (classified.targetPath !== '/chat') {
      router.push(classified.targetPath as any);
      return;
    }

    setPromptText('');
    voiceStateMachine.setState('THINKING');
    try {
      await sendCaptainPrompt(text, 'iphone', 'captain');
      setTimeout(loadOutput, 600);
      scrollToBottom();
    } catch (e) {
      console.error('Send prompt error:', e);
      voiceStateMachine.setState('IDLE');
    }
  };

  const handleToggleVoiceBtn = () => {
    if (ttsService.isSpeaking()) {
      ttsService.stop();
      startListeningLoop();
    } else if (voiceState === 'LISTENING') {
      stopListeningLoop();
    } else {
      startListeningLoop();
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

        <Text style={styles.headerTitle}>FIRSTMATE TERMINAL</Text>

        <TouchableOpacity onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <View style={styles.chatContainer}>
        <GlassSurface variant="surface" intensity={60} style={styles.terminalGlassBox}>
          <View style={styles.terminalHeaderRow}>
            <View style={[styles.statusDot, voiceState === 'SPEAKING' ? styles.dotSpeaking : undefined]} />
            <Text style={styles.terminalTitle}>
              CODEX CAPTAIN ACTIVE SESSION • [{voiceState}]
            </Text>
          </View>

          <View style={styles.terminalScrollContainer}>
            <ScrollView
              ref={scrollRef}
              style={styles.terminalScroll}
              onScroll={handleScroll}
              scrollEventThrottle={16}
            >
              <Text style={styles.terminalText}>{output}</Text>
            </ScrollView>

            {(hasNewMessages || isScrolledUp) ? (
              <TouchableOpacity style={styles.scrollBadgeBtn} onPress={scrollToBottom} activeOpacity={0.8}>
                <GlassSurface variant="control" style={styles.scrollBadgeSurface}>
                  <Text style={styles.scrollBadgeText}>
                    {hasNewMessages ? '↓ NEW MESSAGES (TAP TO SCROLL)' : '↓ SCROLL TO BOTTOM'}
                  </Text>
                </GlassSurface>
              </TouchableOpacity>
            ) : null}
          </View>
        </GlassSurface>

        <TerminusControlBar target="captain" onKeySent={() => setTimeout(loadOutput, 400)} />

        {(voiceState === 'LISTENING' || voiceState === 'SPEAKING') ? (
          <GlassSurface variant="control" style={styles.waveformBox}>
            <Text style={styles.recordingLabel}>
              {voiceState === 'SPEAKING' ? 'SPEAKING ALOUD 🔊' : 'REC LIVE 🎙️'}
            </Text>
            <View style={styles.waveBarGroup}>
              {[0.4, 0.7, 1.0, 0.6, 0.9, 0.5, 0.8, 0.3].map((mult, idx) => (
                <View
                  key={idx}
                  style={[
                    styles.waveBar,
                    {
                      height: Math.max(6, micLevel * 28 * mult),
                      backgroundColor: voiceState === 'SPEAKING' ? '#38BDF8' : '#72F5B1'
                    }
                  ]}
                />
              ))}
            </View>
            <Text style={styles.recordingHint}>
              {voiceState === 'SPEAKING' ? 'TAP TO BARGE IN' : 'TAP TO TRANSMIT'}
            </Text>
          </GlassSurface>
        ) : null}

        <View style={styles.inputComposerRow}>
          <TouchableOpacity
            onPress={handleToggleVoiceBtn}
            style={[
              styles.micBtn,
              voiceState === 'LISTENING' ? styles.micBtnRecording : undefined,
              voiceState === 'SPEAKING' ? styles.micBtnSpeaking : undefined
            ]}
          >
            <Text style={styles.micIconText}>
              {voiceState === 'SPEAKING' ? '🔊' : '🎤'}
            </Text>
          </TouchableOpacity>

          <TextInput
            ref={inputRef}
            style={styles.textInput}
            placeholder="Command Codex Captain..."
            placeholderTextColor="rgba(255, 255, 255, 0.45)"
            value={promptText}
            onChangeText={setPromptText}
            onSubmitEditing={() => handleSend()}
          />

          <TouchableOpacity style={styles.sendBtn} onPress={() => handleSend()}>
            <Text style={styles.sendBtnText}>SEND ↵</Text>
          </TouchableOpacity>
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
  terminalGlassBox: { flex: 1, padding: 14, borderRadius: 20, marginBottom: 8 },
  terminalHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  statusDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#72F5B1' },
  dotSpeaking: { backgroundColor: '#38BDF8' },
  terminalTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#72F5B1', letterSpacing: 1.2 },
  terminalScrollContainer: { flex: 1, position: 'relative' },
  terminalScroll: { flex: 1, backgroundColor: '#000000', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(114, 245, 177, 0.3)', padding: 12 },
  terminalText: { fontFamily: 'monospace', fontSize: 12, color: '#72F5B1', lineHeight: 18 },
  scrollBadgeBtn: { position: 'absolute', bottom: 10, alignSelf: 'center', zIndex: 10 },
  scrollBadgeSurface: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 16, backgroundColor: 'rgba(114, 245, 177, 0.35)', borderColor: '#72F5B1', borderWidth: 1 },
  scrollBadgeText: { fontFamily: 'monospace', color: '#0D1322', fontWeight: 'bold', fontSize: 10, letterSpacing: 0.8 },
  waveformBox: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 10, borderRadius: 14, marginBottom: 8, borderColor: '#72F5B1', borderWidth: 1 },
  recordingLabel: { fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', color: '#72F5B1' },
  waveBarGroup: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 28 },
  waveBar: { width: 4, borderRadius: 2 },
  recordingHint: { fontFamily: 'monospace', fontSize: 9, color: 'rgba(255, 255, 255, 0.6)' },
  inputComposerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
  micBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: 'rgba(255, 255, 255, 0.1)', justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.2)' },
  micBtnRecording: { backgroundColor: 'rgba(114, 245, 177, 0.25)', borderColor: '#72F5B1' },
  micBtnSpeaking: { backgroundColor: 'rgba(56, 189, 248, 0.25)', borderColor: '#38BDF8' },
  micIconText: { fontSize: 18 },
  textInput: { flex: 1, backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.2)', paddingHorizontal: 12, paddingVertical: 10, color: '#FFFFFF', fontSize: 14 },
  sendBtn: { backgroundColor: '#72F5B1', borderRadius: 12, paddingVertical: 11, paddingHorizontal: 14 },
  sendBtnText: { fontFamily: 'monospace', color: '#0D1322', fontWeight: 'bold', fontSize: 12 }
});
