import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useId, useReducer, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Platform, ScrollView, StyleSheet, Text, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { G, Path, Polygon } from 'react-native-svg';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { submitVoiceMove, transcribeVoiceAudio, VoiceMoveResult } from '../src/api/client';
import { useVoiceInputAdapter } from '../src/input/VoiceInputAdapter';
import { appendConversationMessage, useConversationMessages } from '../src/services/ConversationSession';
import { ttsService } from '../src/services/TextToSpeechService';
import { transitionVoiceState, VoiceState } from '../src/services/VoiceSessionReducer';
import { loadChatPreferences, useChatColorScheme } from '../src/services/ChatPreferences';
import { capabilityFor, getLocalVoiceCapabilities, resolveVoiceInputMode, VoiceInputCapabilities, VoiceInputMode } from '../src/services/VoiceInputModes';
import { ACTIVE_MARK_SPIRAL, ACTIVE_MARK_TRIANGLE, audioEnergyScale, clampAudioPeak, waveformBarHeight } from '../src/services/VoiceVisuals';

const brand = {
  obsidian: '#05070A', command: '#111722', paper: '#F7F8FA', ink: '#11151B',
  mutedDark: '#8E99AA', mutedLight: '#667180', borderDark: '#2A3542', borderLight: '#D5DAE2',
  green: '#54FF87', cyan: '#24D8FF', violet: '#8B6CFF', magenta: '#FF3FD1', critical: '#FF625F',
};
const QUIET_AFTER_SPEECH_MS = 1200;
const MAX_TURN_MS = 30_000;
const MIN_TURN_MS = 450;

const stateCopy: Record<VoiceState, { title: string; detail: string }> = {
  READY: { title: 'Voice ready', detail: 'Tap the mark to begin' },
  STARTING: { title: 'Starting', detail: 'Connecting to your microphone' },
  LISTENING: { title: 'Listening', detail: 'Speak naturally — your turn ends when you pause' },
  TRANSCRIBING: { title: 'Transcribing', detail: 'Finishing your words' },
  THINKING: { title: 'Thinking', detail: 'Firstmate is responding' },
  CONFIRMING: { title: 'Confirm action', detail: 'Voice control is paused for your review' },
  SPEAKING: { title: 'Speaking', detail: 'Tap the mark to interrupt' },
  ERROR: { title: 'Voice paused', detail: 'Tap the mark to try again' },
};

function ActiveMark({ size, coreColor }: { size: number; coreColor: string }) {
  return <Svg width={size} height={size} viewBox="0 0 512 512" accessibilityLabel="Magistrate active voice mark">
    <G fill="none" strokeWidth={16} strokeLinecap="round" strokeLinejoin="round" opacity={0.72}>
      <G stroke={brand.magenta} transform="translate(-5 2)"><Polygon points={ACTIVE_MARK_TRIANGLE} /><Path d={ACTIVE_MARK_SPIRAL} /></G>
      <G stroke={brand.cyan} transform="translate(5 -2)"><Polygon points={ACTIVE_MARK_TRIANGLE} /><Path d={ACTIVE_MARK_SPIRAL} /></G>
      <G stroke={brand.green}><Polygon points={ACTIVE_MARK_TRIANGLE} /><Path d={ACTIVE_MARK_SPIRAL} /></G>
    </G>
    <G fill="none" stroke={coreColor} strokeWidth={7} strokeLinecap="round" strokeLinejoin="round" opacity={0.94}>
      <Polygon points={ACTIVE_MARK_TRIANGLE} /><Path d={ACTIVE_MARK_SPIRAL} />
    </G>
  </Svg>;
}

function EnergyWaves({ amplitude, active }: { amplitude: number; active: boolean }) {
  const levels = [0.55, 0.82, 1, 0.72, 0.48];
  return <View testID="voice-energy-waves" pointerEvents="none" style={styles.energyWaves}>{levels.map((level, index) => <View key={index} style={[styles.energyBar, { height: 22 + (active ? amplitude * 74 * level : 8), opacity: active ? 0.22 + amplitude * 0.65 : 0.08, backgroundColor: [brand.green, brand.cyan, brand.violet, brand.cyan, brand.magenta][index] }]} />)}</View>;
}

function Waveform({ samples, listening }: { samples: number[]; listening: boolean }) {
  return <View testID="voice-waveform" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.waveform}>
    {samples.map((sample, index) => {
      const color = [brand.green, brand.cyan, brand.violet, brand.magenta][index % 4];
      return <View key={index} style={[styles.waveBar, { height: waveformBarHeight(sample, listening), backgroundColor: color, opacity: listening ? 0.92 : 0.24 }]} />;
    })}
  </View>;
}

export default function VoiceScreen() {
  const router = useRouter();
  const { width, height } = useWindowDimensions();
  const dark = useChatColorScheme() !== 'light';
  const compact = width < 680 || height < 720;
  const [voiceState, setVoiceState] = useReducer(transitionVoiceState, 'READY' as VoiceState);
  const [intermediate, setIntermediate] = useState('');
  const [finalTranscript, setFinalTranscript] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [voiceMode, setVoiceMode] = useState<VoiceInputMode>('automatic');
  const [voiceCapabilities, setVoiceCapabilities] = useState<VoiceInputCapabilities>(() => getLocalVoiceCapabilities());
  const [voiceSetupReady, setVoiceSetupReady] = useState(false);
  const modeNoticeRef = useRef('');
  const [pendingMove, setPendingMove] = useState<VoiceMoveResult | null>(null);
  const [pendingKey, setPendingKey] = useState('');
  const [waveSamples, setWaveSamples] = useState<number[]>(() => new Array(38).fill(0.04));
  const [reducedMotion, setReducedMotion] = useState(false);
  const [, setBackgroundReady] = useState(false);
  const messages = useConversationMessages('captain');
  useEffect(() => { void loadChatPreferences().then(() => setBackgroundReady(true)); }, []);
  const capture = useVoiceInputAdapter(setIntermediate, voiceMode);
  const captureRef = useRef(capture);
  const stateRef = useRef<VoiceState>(voiceState);
  const intermediateRef = useRef(intermediate);
  const amplitudeRef = useRef(capture.amplitude);
  const endingRef = useRef(false);
  const turnInFlightRef = useRef(false);
  const heardSpeechRef = useRef(false);
  const listeningStartedAtRef = useRef(0);
  const lastSpeechAtRef = useRef(0);
  const sequenceRef = useRef(0);
  const sessionId = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const [ripple] = useState(() => new Animated.Value(0));
  const [audioPeak] = useState(() => new Animated.Value(0));
  const [hoverProgress] = useState(() => new Animated.Value(0));
  useEffect(() => {
    captureRef.current = capture;
    stateRef.current = voiceState;
    intermediateRef.current = intermediate;
    amplitudeRef.current = capture.amplitude;
  });

  useEffect(() => {
    // Voice is a deep-linkable page, so apply the persisted account background
    // here too rather than relying on the chat screen having mounted first.
    let mounted = true;
    Promise.all([loadChatPreferences()]).then(([preferences]) => {
      if (!mounted) return;
      const selected = preferences.voiceInputMode;
      const capabilities = getLocalVoiceCapabilities();
      const resolved = resolveVoiceInputMode(selected, capabilities);
      setVoiceCapabilities(capabilities); setVoiceMode(resolved.mode); modeNoticeRef.current = resolved.fallbackReason || '';
      setVoiceSetupReady(true);
    }).catch(() => { if (mounted) setVoiceSetupReady(true); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion);
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion);
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (reducedMotion) { ripple.setValue(0.34); audioPeak.setValue(0); return; }
    const animation = Animated.loop(Animated.timing(ripple, { toValue: 1, duration: 2100, useNativeDriver: Platform.OS !== 'web' }));
    ripple.setValue(0); animation.start();
    return () => animation.stop();
  }, [audioPeak, reducedMotion, ripple]);

  useEffect(() => {
    if (voiceState !== 'LISTENING' || reducedMotion) audioPeak.setValue(0);
  }, [audioPeak, reducedMotion, voiceState]);

  const setHover = useCallback((value: number) => {
    if (reducedMotion) { hoverProgress.setValue(0); return; }
    Animated.spring(hoverProgress, { toValue: value, damping: 18, stiffness: 180, mass: 0.7, useNativeDriver: Platform.OS !== 'web' }).start();
  }, [hoverProgress, reducedMotion]);
  const hoverHandlers = Platform.OS === 'web' ? { onMouseEnter: () => setHover(1), onMouseLeave: () => setHover(0) } : {};

  useEffect(() => {
    if (voiceState !== 'LISTENING' || !intermediate.trim()) return;
    heardSpeechRef.current = true;
    lastSpeechAtRef.current = Date.now();
  }, [intermediate, voiceState]);

  const fail = useCallback((cause: unknown) => {
    if (endingRef.current) return;
    turnInFlightRef.current = false;
    setError(cause instanceof Error ? cause.message : 'Voice Mode encountered an unexpected error.');
    setVoiceState('ERROR');
  }, []);

  const beginListening = useCallback(async () => {
    if (endingRef.current || turnInFlightRef.current || !voiceSetupReady) return;
    const capability = capabilityFor(voiceCapabilities, voiceMode);
    if (capability.available === 'unavailable') { fail(capability.reason || `${capability.label} is unavailable.`); return; }
    ttsService.stop();
    setError(''); setNotice(modeNoticeRef.current); setIntermediate(''); setFinalTranscript(''); setPendingMove(null); setPendingKey('');
    setWaveSamples(new Array(38).fill(0.04));
    setVoiceState('STARTING');
    try {
      await captureRef.current.start();
      listeningStartedAtRef.current = Date.now();
      lastSpeechAtRef.current = Date.now();
      heardSpeechRef.current = false;
      setVoiceState('LISTENING');
    } catch (cause) { fail(cause); }
  }, [fail, voiceCapabilities, voiceMode, voiceSetupReady]);

  const deliverResponse = useCallback((result: VoiceMoveResult) => {
    if (result.status !== 'completed') throw new Error(result.error || 'Firstmate did not complete the request.');
    const responseText = result.response?.trim() || `Request completed by ${result.target}.`;
    // The move id is the gateway's stable identity for this completed turn; a
    // local timestamp id would invent one for an entity the server already knows.
    appendConversationMessage('captain', { id: result.move_id ? `move-${result.move_id}` : `voice-a-${Date.now()}`, role: 'assistant', text: responseText, sentAt: Date.now(), source: 'voice', audience: 'primary', runId: result.move_id });
    setVoiceState('SPEAKING');
    turnInFlightRef.current = false;
    ttsService.speakChunk(responseText, () => { if (!endingRef.current) void beginListening(); });
  }, [beginListening]);

  const finishTurn = useCallback(async () => {
    if (endingRef.current || stateRef.current !== 'LISTENING' || turnInFlightRef.current) return;
    turnInFlightRef.current = true;
    setVoiceState('TRANSCRIBING');
    try {
      const recording = await captureRef.current.stop();
      if (recording.durationMillis < MIN_TURN_MS) {
        turnInFlightRef.current = false;
        await beginListening();
        setNotice('Keep speaking a little longer so Magistrate can hear the full turn.');
        return;
      }
      const transcription = voiceMode === 'browser' ? { text: recording.transcript || '', is_final: true } : await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename);
      const utterance = transcription.text?.trim() || intermediateRef.current.trim();
      if (!utterance) {
        turnInFlightRef.current = false;
        await beginListening();
        setNotice('I didn’t catch that. Listening again…');
        return;
      }
      setFinalTranscript(utterance); setIntermediate('');
      appendConversationMessage('captain', { id: `voice-u-${Date.now()}`, role: 'user', text: utterance, sentAt: Date.now(), source: 'voice', audience: 'captain' });
      setVoiceState('THINKING');
      sequenceRef.current += 1;
      const key = `voice-${sessionId}-${sequenceRef.current}`;
      const move = await submitVoiceMove(utterance, 'captain', key);
      if (move.status === 'prohibited' || move.status === 'error' || move.status === 'confirmation_expired') throw new Error(move.error || 'That request cannot be completed in Voice Mode.');
      if (move.status === 'confirmation_required') {
        setPendingMove(move); setPendingKey(key); setVoiceState('CONFIRMING'); turnInFlightRef.current = false;
        return;
      }
      if (move.status !== 'ready') throw new Error(move.error || 'The voice request could not be prepared.');
      const result = await submitVoiceMove(utterance, 'captain', key, true);
      deliverResponse(result);
    } catch (cause) { fail(cause); }
  }, [beginListening, deliverResponse, fail, sessionId, voiceMode]);

  useEffect(() => {
    if (voiceState !== 'LISTENING') return;
    const timer = setInterval(() => {
      const now = Date.now();
      const peak = clampAudioPeak(amplitudeRef.current * 7);
      setWaveSamples(current => [...current.slice(1), Math.max(0.04, peak)]);
      if (!reducedMotion) Animated.timing(audioPeak, { toValue: peak, duration: 120, useNativeDriver: false }).start();
      if (amplitudeRef.current > 0.026) {
        heardSpeechRef.current = true;
        lastSpeechAtRef.current = now;
      }
      const elapsed = now - listeningStartedAtRef.current;
      const quietFor = now - lastSpeechAtRef.current;
      if ((heardSpeechRef.current && elapsed >= MIN_TURN_MS && quietFor >= QUIET_AFTER_SPEECH_MS) ||
          (elapsed >= MAX_TURN_MS && Boolean(intermediateRef.current.trim()))) void finishTurn();
    }, 160);
    return () => clearInterval(timer);
  }, [audioPeak, finishTurn, reducedMotion, voiceState]);

  useEffect(() => {
    const timer = setTimeout(() => { void beginListening(); }, voiceSetupReady ? 180 : 0);
    if (!voiceSetupReady) return () => clearTimeout(timer);
    return () => {
      clearTimeout(timer); endingRef.current = true;
      void captureRef.current.cancel();
      ttsService.stop();
    };
  }, [beginListening, voiceSetupReady]);

  const confirmMove = async () => {
    if (!pendingMove || !pendingKey || turnInFlightRef.current) return;
    turnInFlightRef.current = true; setVoiceState('THINKING');
    try {
      const result = await submitVoiceMove(finalTranscript, 'captain', pendingKey, true, pendingMove.confirmation_token);
      setPendingMove(null); setPendingKey(''); deliverResponse(result);
    } catch (cause) { fail(cause); }
  };

  const cancelConfirmation = () => {
    turnInFlightRef.current = false; setPendingMove(null); setPendingKey('');
    void beginListening();
  };

  const handleMainControl = () => {
    if (voiceState === 'LISTENING') void finishTurn();
    else if (voiceState === 'SPEAKING' || voiceState === 'READY' || voiceState === 'ERROR') void beginListening();
  };

  const endConversation = () => {
    endingRef.current = true; turnInFlightRef.current = true; ttsService.stop();
    // Voice mode can be deep-linked (or reloaded) with no history behind it,
    // where router.back() is a no-op and would trap the captain here.
    void captureRef.current.cancel().finally(() => { if (router.canGoBack()) router.back(); else router.replace('/chat' as any); });
  };

  const currentCopy = stateCopy[voiceState];
  const textColor = dark ? brand.paper : brand.ink;
  const mutedColor = dark ? brand.mutedDark : brand.mutedLight;
  const surfaceColor = dark ? 'rgba(17,23,34,0.78)' : 'rgba(255,255,255,0.82)';
  const borderColor = dark ? brand.borderDark : brand.borderLight;
  // useWindowDimensions can report 0x0 on the first web render; clamp so SVG sizes stay valid.
  // Keep the control compact while giving the mark more visual weight.
  const markSize = (compact ? Math.min(Math.max(width * 0.54, 140), 220) : Math.min(width * 0.28, 270)) * 1.2;
  const stageSize = (compact ? Math.min(Math.max(width - 34, 200), 360) : Math.min(width * 0.46, 520)) * 0.95;
  const visibleMessages = messages.slice(-3);
  const rippleScale = Animated.multiply(
    ripple.interpolate({ inputRange: [0, 1], outputRange: [0.78, 1.34] }),
    audioPeak.interpolate({ inputRange: [0, 1], outputRange: [1, audioEnergyScale(1)] }),
  );
  const rippleOpacity = ripple.interpolate({ inputRange: [0, 0.28, 1], outputRange: [0, voiceState === 'READY' ? 0.1 : 0.42, 0] });

  // Keep this layer translucent: EnvironmentBackground owns the persisted
  // scene/custom image underneath, while this gradient supplies Voice Mode's
  // branded contrast treatment instead of replacing that user choice.
  const gradientColors: [string, string, string] = dark
    ? ['rgba(5,7,10,0.68)', 'rgba(10,15,23,0.58)', 'rgba(5,7,10,0.68)']
    : ['rgba(247,248,250,0.58)', 'rgba(238,241,244,0.48)', 'rgba(247,248,250,0.58)'];

  return <EnvironmentBackground hideBottomControls voiceMode><LinearGradient colors={gradientColors} style={styles.screen}>
    <SafeAreaView style={styles.safeArea}>
      <View style={[styles.header, compact && styles.headerCompact]}>
        <View><Text style={[styles.eyebrow, { color: brand.cyan }]}>FIRSTMATE / VOICE</Text><Text style={[styles.continuity, { color: mutedColor }]}>One continuous thread</Text></View>
        <TouchableOpacity testID="end-voice-conversation" accessibilityRole="button" accessibilityLabel="End voice conversation and return to chat" onPress={endConversation} style={[styles.endButton, { borderColor, backgroundColor: surfaceColor }]}>
          <View style={styles.endIcon} /><Text style={[styles.endText, { color: textColor }]}>End conversation</Text>
        </TouchableOpacity>
      </View>

      <ScrollView contentContainerStyle={[styles.content, compact && styles.contentCompact]} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
        <View style={styles.statusArea}>
          <View style={[styles.statusDot, { backgroundColor: voiceState === 'THINKING' ? brand.violet : voiceState === 'ERROR' ? brand.critical : brand.cyan }]} />
          <Text testID="voice-state" accessibilityRole="header" accessibilityLiveRegion="polite" style={[styles.stateTitle, compact && styles.stateTitleCompact, { color: textColor }]}>{currentCopy.title}</Text>
          <Text style={[styles.stateDetail, { color: mutedColor }]}>{currentCopy.detail}</Text>
          <Text testID="voice-input-mode" style={[styles.modeLabel, { color: mutedColor }]}>Input: {capabilityFor(voiceCapabilities, voiceMode).label}</Text>
        </View>

        <TouchableOpacity testID="voice-control" accessibilityRole="button" accessibilityLabel={voiceState === 'LISTENING' ? 'Finish speaking' : voiceState === 'SPEAKING' ? 'Interrupt response and listen' : 'Start listening'} accessibilityState={{ busy: ['STARTING','TRANSCRIBING','THINKING'].includes(voiceState), disabled: ['STARTING','TRANSCRIBING','THINKING','CONFIRMING'].includes(voiceState) }} onPress={handleMainControl} {...(hoverHandlers as any)} disabled={['STARTING','TRANSCRIBING','THINKING','CONFIRMING'].includes(voiceState)} activeOpacity={0.88} style={[styles.stage, { width: stageSize, height: stageSize }]}>
          <Svg width={stageSize} height={stageSize} viewBox="0 0 512 512" style={styles.stageTriangle}>
            <Polygon points="256,484 34,78 478,78" fill={dark ? 'rgba(36,216,255,0.035)' : 'rgba(139,108,255,0.035)'} stroke={borderColor} strokeWidth={1.2} />
          </Svg>
          <Animated.View style={[styles.ripple, { borderColor: brand.green, opacity: rippleOpacity, transform: [{ scale: rippleScale }] }]} />
          <Animated.View style={[styles.ripple, styles.rippleMid, { borderColor: brand.cyan, opacity: rippleOpacity, transform: [{ scale: rippleScale }] }]} />
          <Animated.View style={[styles.ripple, styles.rippleInner, { borderColor: brand.magenta, opacity: rippleOpacity, transform: [{ scale: rippleScale }] }]} />
          <EnergyWaves amplitude={capture.amplitude} active={voiceState === 'LISTENING'} />
          <Animated.View style={[styles.markHalo, { shadowColor: voiceState === 'THINKING' ? brand.violet : brand.cyan, transform: [{ translateY: hoverProgress.interpolate({ inputRange: [0, 1], outputRange: [0, -4] }) }, { scale: hoverProgress.interpolate({ inputRange: [0, 1], outputRange: [1, 1.015] }) }] }]}><ActiveMark size={markSize} coreColor={dark ? brand.paper : brand.ink} /></Animated.View>
        </TouchableOpacity>

        <Waveform samples={waveSamples} listening={voiceState === 'LISTENING'} />
        <View style={styles.liveTranscript} accessibilityLiveRegion="polite">
          <Text testID="voice-live-transcript" style={[styles.liveTranscriptText, { color: intermediate || finalTranscript ? textColor : mutedColor }]}>
            {intermediate || finalTranscript || (voiceState === 'LISTENING' ? 'Your words will appear here…' : ' ')}
          </Text>
          {voiceState === 'LISTENING' ? <Text style={[styles.turnHint, { color: mutedColor }]}>{(capture.durationMillis / 1000).toFixed(1)}s · tap the mark to finish now</Text> : null}
        </View>

        {pendingMove?.confirmation_message && voiceState === 'CONFIRMING' ? <View testID="voice-confirmation" style={[styles.confirmation, { backgroundColor: surfaceColor, borderColor }]}>
          <Text style={[styles.confirmationLabel, { color: brand.violet }]}>REVIEW BEFORE CONTINUING</Text>
          <Text style={[styles.confirmationText, { color: textColor }]}>{pendingMove.confirmation_message}</Text>
          <View style={styles.confirmationActions}>
            <TouchableOpacity testID="confirm-voice-move" onPress={() => void confirmMove()} style={styles.confirmButton}><Text style={styles.confirmButtonText}>Confirm</Text></TouchableOpacity>
            <TouchableOpacity onPress={cancelConfirmation} style={[styles.cancelButton, { borderColor }]}><Text style={[styles.cancelButtonText, { color: textColor }]}>Cancel</Text></TouchableOpacity>
          </View>
        </View> : null}

        {error ? <View testID="voice-error" accessibilityLiveRegion="assertive" style={[styles.feedback, { borderColor: brand.critical }]}><Text style={styles.errorText}>{error}</Text></View> : null}
        {notice ? <Text accessibilityLiveRegion="polite" style={[styles.noticeText, { color: mutedColor }]}>{notice}</Text> : null}

        {visibleMessages.length ? <View testID="voice-conversation" style={[styles.conversation, { borderTopColor: borderColor }]}>
          {visibleMessages.map(message => <View key={message.id} style={styles.turn}>
            <Text style={[styles.turnRole, { color: message.role === 'user' ? brand.cyan : brand.violet }]}>{message.role === 'user' ? 'YOU' : 'FIRSTMATE'}</Text>
            <Text style={[styles.turnText, { color: textColor }]} numberOfLines={3}>{message.text}</Text>
          </View>)}
        </View> : null}
      </ScrollView>
    </SafeAreaView>
  </LinearGradient></EnvironmentBackground>;
}

const interfaceFont = Platform.select({ web: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", default: undefined });
const styles = StyleSheet.create({
  screen: { flex: 1 }, safeArea: { flex: 1 },
  header: { minHeight: 72, paddingHorizontal: 28, paddingTop: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 18 },
  headerCompact: { minHeight: 62, paddingHorizontal: 17, paddingTop: 7 },
  eyebrow: { fontFamily: interfaceFont, fontSize: 11, lineHeight: 16, fontWeight: '700', letterSpacing: 1.4 },
  continuity: { fontFamily: interfaceFont, fontSize: 12, lineHeight: 17, marginTop: 2 },
  endButton: { minHeight: 42, borderWidth: 1, borderRadius: 999, paddingHorizontal: 15, flexDirection: 'row', alignItems: 'center', gap: 9 },
  endIcon: { width: 9, height: 9, borderRadius: 2, backgroundColor: brand.critical },
  endText: { fontFamily: interfaceFont, fontSize: 13, fontWeight: '600' },
  content: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 22, paddingTop: 10, paddingBottom: 34 },
  contentCompact: { justifyContent: 'flex-start', paddingTop: 14, paddingBottom: 24 },
  statusArea: { alignItems: 'center', minHeight: 92 }, statusDot: { width: 7, height: 7, borderRadius: 4, marginBottom: 9 },
  stateTitle: { fontFamily: interfaceFont, fontSize: 36, lineHeight: 42, fontWeight: '600', letterSpacing: -0.7 },
  stateTitleCompact: { fontSize: 30, lineHeight: 35 },
  stateDetail: { fontFamily: interfaceFont, fontSize: 13, lineHeight: 19, marginTop: 5, textAlign: 'center' },
  modeLabel: { fontFamily: interfaceFont, fontSize: 10, lineHeight: 15, marginTop: 5, textAlign: 'center', textTransform: 'uppercase', letterSpacing: 0.8 },
  stage: { position: 'relative', alignItems: 'center', justifyContent: 'center', marginTop: 3 }, stageTriangle: { position: 'absolute', pointerEvents: 'none' },
  ripple: { position: 'absolute', width: '54%', height: '54%', borderRadius: 999, borderWidth: 2, pointerEvents: 'none' },
  rippleMid: { width: '44%', height: '44%', borderWidth: 1.5 }, rippleInner: { width: '34%', height: '34%', borderWidth: 1 },
  markHalo: { alignItems: 'center', justifyContent: 'center', shadowOpacity: 0.45, shadowRadius: 32, shadowOffset: { width: 0, height: 10 } },
  energyWaves: { position: 'absolute', width: '48%', height: '34%', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, zIndex: 0 }, energyBar: { width: 3, borderRadius: 999 },
  waveform: { width: '100%', maxWidth: 560, height: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, paddingHorizontal: 10, marginTop: -12 },
  waveBar: { width: 3, borderRadius: 999 },
  liveTranscript: { minHeight: 66, width: '100%', maxWidth: 720, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 10 },
  liveTranscriptText: { fontFamily: interfaceFont, fontSize: 17, lineHeight: 25, textAlign: 'center' },
  turnHint: { fontFamily: interfaceFont, fontSize: 11, lineHeight: 16, marginTop: 5 },
  confirmation: { width: '100%', maxWidth: 620, borderWidth: 1, borderRadius: 18, padding: 18, marginTop: 14 },
  confirmationLabel: { fontFamily: interfaceFont, fontSize: 10, lineHeight: 15, fontWeight: '700', letterSpacing: 1.1 },
  confirmationText: { fontFamily: interfaceFont, fontSize: 16, lineHeight: 23, marginTop: 8 }, confirmationActions: { flexDirection: 'row', gap: 10, marginTop: 15 },
  confirmButton: { minHeight: 44, borderRadius: 999, backgroundColor: brand.cyan, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 22 },
  confirmButtonText: { color: brand.obsidian, fontFamily: interfaceFont, fontSize: 13, fontWeight: '700' },
  cancelButton: { minHeight: 44, borderRadius: 999, borderWidth: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 22 },
  cancelButtonText: { fontFamily: interfaceFont, fontSize: 13, fontWeight: '600' },
  feedback: { width: '100%', maxWidth: 620, borderWidth: 1, borderRadius: 12, padding: 12, marginTop: 12 },
  errorText: { color: brand.critical, fontFamily: interfaceFont, fontSize: 13, lineHeight: 19, textAlign: 'center' },
  noticeText: { fontFamily: interfaceFont, fontSize: 12, lineHeight: 18, textAlign: 'center', marginTop: 8 },
  conversation: { width: '100%', maxWidth: 720, borderTopWidth: 1, marginTop: 16, paddingTop: 14, gap: 11 },
  turn: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  turnRole: { width: 76, fontFamily: interfaceFont, fontSize: 9, lineHeight: 17, fontWeight: '700', letterSpacing: 0.9 },
  turnText: { flex: 1, fontFamily: interfaceFont, fontSize: 13, lineHeight: 19 },
});
