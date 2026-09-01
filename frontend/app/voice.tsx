import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useId, useReducer, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Platform, ScrollView, StyleSheet, Text, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Defs, G, LinearGradient as SvgLinearGradient, Path, Polygon, Stop } from 'react-native-svg';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { submitVoiceMove, transcribeVoiceAudio, VoiceMoveResult } from '../src/api/client';
import { useVoiceInputAdapter } from '../src/input/VoiceInputAdapter';
import { reconcileCanonicalMessages } from '../src/services/CanonicalConversation';
import { appendConversationMessage, getConversationMessages, resetConversationMessages, useConversationMessages } from '../src/services/ConversationSession';
import { ttsService } from '../src/services/TextToSpeechService';
import { transitionVoiceState, VoiceState } from '../src/services/VoiceSessionReducer';
import { loadChatPreferences } from '../src/services/ChatPreferences';
import { capabilityFor, getLocalVoiceCapabilities, resolveVoiceInputMode, VoiceInputCapabilities, VoiceInputMode } from '../src/services/VoiceInputModes';
import { audioEnergyScale, clampAudioPeak, ENVELOPE_SILENCE_FLOOR, ringPhaseOffset, ringSpeedScale, updateAudioEnvelope, ACTIVE_MARK_SPIRAL, ACTIVE_MARK_TRIANGLE } from '../src/services/VoiceVisuals';

/** Test-only amplitude injection so browser evidence can be captured without a
 * real microphone. Web-only by construction (native never defines `window`),
 * so the native capture path is unaffected. */
declare global {
  var __voiceSetTestAmplitude: ((value: number | null) => void) | undefined;
}

const SPECTRAL_RING_COUNT = 5;

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

// Single thin stroke carrying the spectral gradient itself, rather than three
// offset monochrome copies plus a white core: the approved reference is one
// line, not a stack of them.
function ActiveMark({ size }: { size: number }) {
  return <Svg testID="voice-active-mark" width={size} height={size} viewBox="0 0 512 512" accessibilityLabel="Magistrate active voice mark">
    <Defs>
      <SvgLinearGradient id="magistrateSpectralStroke" x1="0%" y1="0%" x2="100%" y2="100%">
        <Stop offset="0%" stopColor={brand.violet} />
        <Stop offset="50%" stopColor={brand.cyan} />
        <Stop offset="100%" stopColor={brand.green} />
      </SvgLinearGradient>
    </Defs>
    <G fill="none" stroke="url(#magistrateSpectralStroke)" strokeWidth={7} strokeLinecap="round" strokeLinejoin="round">
      <Polygon points={ACTIVE_MARK_TRIANGLE} /><Path d={ACTIVE_MARK_SPIRAL} />
    </G>
  </Svg>;
}

const RIPPLE_PALETTE = [brand.violet, brand.cyan, brand.green, brand.cyan, brand.violet];
const RIPPLE_BASE_DURATION_MS = 2600;
// These are deliberately irregular filament paths rather than perfect circles:
// the approved mark sits in a spectral/cosmic field, not a target reticle.
const RIPPLE_FILAMENTS = [
  'M50 4 C70 2 96 17 98 45 C100 72 79 97 51 96 C23 96 2 77 4 49 C6 22 28 5 50 4',
  'M50 10 C74 7 91 24 92 49 C93 72 73 91 49 90 C25 89 9 72 10 49 C11 27 28 12 50 10',
  'M50 17 C69 15 84 29 84 49 C84 69 69 83 50 83 C31 83 17 69 17 50 C17 31 31 19 50 17',
];

/**
 * The cosmic/spectral field behind the mark. Each layer is a thin, imperfect
 * filament loop with its own phase and speed, so it reads as a living wave
 * field rather than synchronized target rings. Amplitude changes displacement,
 * scale, opacity, and glow here only; the triangle and spiral never change
 * geometry or scale.
 */
function VoiceRippleField({ audioPeak, reducedMotion }: { audioPeak: Animated.Value; reducedMotion: boolean }) {
  const [phases] = useState(() => Array.from({ length: SPECTRAL_RING_COUNT }, () => new Animated.Value(0)));
  useEffect(() => {
    if (reducedMotion) { phases.forEach(phase => phase.setValue(0.5)); return; }
    const loops = phases.map((phase, index) => {
      phase.setValue(0);
      return Animated.loop(Animated.timing(phase, { toValue: 1, duration: RIPPLE_BASE_DURATION_MS * ringSpeedScale(index), useNativeDriver: false }));
    });
    const timers = loops.map((loop, index) => setTimeout(() => loop.start(), ringPhaseOffset(index) * RIPPLE_BASE_DURATION_MS));
    return () => { timers.forEach(clearTimeout); loops.forEach(loop => loop.stop()); };
  }, [phases, reducedMotion]);
  const ampScale = audioPeak.interpolate({ inputRange: [0, 1], outputRange: [1, audioEnergyScale(1)] });
  const ampGlow = audioPeak.interpolate({ inputRange: [0, 1], outputRange: [1, 2.2] });
  const ampDisplacement = audioPeak.interpolate({ inputRange: [0, 1], outputRange: [0, 3] });
  return <View testID="voice-ripple-field" pointerEvents="none" style={styles.rippleField}>
    {phases.map((phase, index) => {
      const spreadPercent = 48 + index * 12;
      const breathe = phase.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0.94, 1, 0.94] });
      const baseOpacity = phase.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0.06, 0.18, 0.06] });
      const path = RIPPLE_FILAMENTS[index % RIPPLE_FILAMENTS.length];
      return <Animated.View key={index} testID={`voice-ripple-layer-${index}`} style={[styles.rippleLayer, {
        width: `${spreadPercent}%`, height: `${spreadPercent}%`,
        opacity: Animated.multiply(baseOpacity, ampGlow),
        shadowColor: RIPPLE_PALETTE[index % RIPPLE_PALETTE.length], shadowRadius: audioPeak.interpolate({ inputRange: [0, 1], outputRange: [3, 16] }),
        transform: [{ translateX: index % 2 ? ampDisplacement : Animated.multiply(ampDisplacement, -1) }, { translateY: index % 2 ? Animated.multiply(ampDisplacement, -1) : ampDisplacement }, { scale: breathe }, { scale: ampScale }],
      }]}>
        <Svg width="100%" height="100%" viewBox="0 0 100 100">
          <Path d={path} fill="none" stroke={RIPPLE_PALETTE[index % RIPPLE_PALETTE.length]} strokeWidth="0.65" strokeLinecap="round" opacity="0.9" />
          <Path d={path} fill="none" stroke={RIPPLE_PALETTE[(index + 1) % RIPPLE_PALETTE.length]} strokeWidth="0.35" strokeLinecap="round" opacity="0.5" transform="rotate(3 50 50)" />
        </Svg>
      </Animated.View>;
    })}
  </View>;
}

export default function VoiceScreen() {
  const router = useRouter();
  const { width, height } = useWindowDimensions();
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
  // The submission id of the turn awaiting a decision, so a confirmed move is
  // recorded under the same canonical turn the optimistic row already shows.
  const clientMessageIdRef = useRef('');
  const sessionId = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const [audioPeak] = useState(() => new Animated.Value(0));
  const [hoverProgress] = useState(() => new Animated.Value(0));
  // The smoothed envelope's own state, stepped deterministically each tick by
  // updateAudioEnvelope; audioPeak is only its Animated tween for rendering.
  const envelopeRef = useRef(ENVELOPE_SILENCE_FLOOR);
  // A test can call window.__voiceSetTestAmplitude(0..1) to drive the ripple
  // field without a real microphone; null restores the real capture reading.
  const testAmplitudeRef = useRef<number | null>(null);
  useEffect(() => {
    captureRef.current = capture;
    stateRef.current = voiceState;
    intermediateRef.current = intermediate;
    amplitudeRef.current = capture.amplitude;
  });
  useEffect(() => {
    if (Platform.OS !== 'web') return;
    globalThis.__voiceSetTestAmplitude = value => { testAmplitudeRef.current = value; };
    return () => { globalThis.__voiceSetTestAmplitude = undefined; };
  }, []);

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
    envelopeRef.current = ENVELOPE_SILENCE_FLOOR;
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
    // Voice Mode shares the captain thread, and the gateway records a completed
    // voice turn canonically (see CHAT_ARCHITECTURE_FIX.md). Applying the
    // returned turn is what keeps one record behind both surfaces instead of a
    // locally minted voice row that chat would later have to reconcile.
    const canonical = result.conversation?.messages || [];
    if (canonical.length) resetConversationMessages('captain', reconcileCanonicalMessages(getConversationMessages('captain'), canonical));
    else appendConversationMessage('captain', { id: result.move_id ? `move-${result.move_id}` : `voice-a-${Date.now()}`, role: 'assistant', text: responseText, sentAt: Date.now(), source: 'voice', audience: 'primary', runId: result.move_id });
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
      // This id is the submission identity the gateway records the turn under,
      // so the optimistic row and the canonical user message are one row.
      const clientMessageId = `voice-u-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      clientMessageIdRef.current = clientMessageId;
      appendConversationMessage('captain', { id: clientMessageId, role: 'user', text: utterance, sentAt: Date.now(), source: 'voice', audience: 'captain', delivery: 'sending' });
      setVoiceState('THINKING');
      sequenceRef.current += 1;
      const key = `voice-${sessionId}-${sequenceRef.current}`;
      const move = await submitVoiceMove(utterance, 'captain', key, false, undefined, clientMessageId);
      if (move.status === 'prohibited' || move.status === 'error' || move.status === 'confirmation_expired') throw new Error(move.error || 'That request cannot be completed in Voice Mode.');
      if (move.status === 'confirmation_required') {
        setPendingMove(move); setPendingKey(key); setVoiceState('CONFIRMING'); turnInFlightRef.current = false;
        return;
      }
      if (move.status !== 'ready') throw new Error(move.error || 'The voice request could not be prepared.');
      const result = await submitVoiceMove(utterance, 'captain', key, true, undefined, clientMessageId);
      deliverResponse(result);
    } catch (cause) { fail(cause); }
  }, [beginListening, deliverResponse, fail, sessionId, voiceMode]);

  useEffect(() => {
    if (voiceState !== 'LISTENING') return;
    const TICK_MS = 160;
    const timer = setInterval(() => {
      const now = Date.now();
      const rawAmplitude = testAmplitudeRef.current ?? amplitudeRef.current;
      envelopeRef.current = updateAudioEnvelope(envelopeRef.current, clampAudioPeak(rawAmplitude * 7), TICK_MS);
      if (!reducedMotion) Animated.timing(audioPeak, { toValue: envelopeRef.current, duration: 120, useNativeDriver: false }).start();
      if (rawAmplitude > 0.026) {
        heardSpeechRef.current = true;
        lastSpeechAtRef.current = now;
      }
      const elapsed = now - listeningStartedAtRef.current;
      const quietFor = now - lastSpeechAtRef.current;
      if ((heardSpeechRef.current && elapsed >= MIN_TURN_MS && quietFor >= QUIET_AFTER_SPEECH_MS) ||
          (elapsed >= MAX_TURN_MS && Boolean(intermediateRef.current.trim()))) void finishTurn();
    }, TICK_MS);
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
      const result = await submitVoiceMove(finalTranscript, 'captain', pendingKey, true, pendingMove.confirmation_token, clientMessageIdRef.current || undefined);
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
  // Voice Mode is a dedicated near-black ceremonial canvas regardless of the
  // captain's chat theme/environment choice, so its palette is fixed rather
  // than tracking the account's light/dark preference.
  const textColor = brand.paper;
  const mutedColor = brand.mutedDark;
  const surfaceColor = 'rgba(17,23,34,0.78)';
  const borderColor = brand.borderDark;
  // useWindowDimensions can report 0x0 on the first web render; clamp so SVG sizes stay valid.
  // Keep the control compact while giving the mark more visual weight.
  const markSize = (compact ? Math.min(Math.max(width * 0.54, 140), 220) : Math.min(width * 0.28, 270)) * 1.2;
  const stageSize = (compact ? Math.min(Math.max(width - 34, 200), 360) : Math.min(width * 0.46, 520)) * 0.95;
  const visibleMessages = messages.slice(-3);

  return <EnvironmentBackground hideBottomControls voiceMode>
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
          <VoiceRippleField audioPeak={audioPeak} reducedMotion={reducedMotion} />
          <Animated.View style={[styles.markHalo, { shadowColor: voiceState === 'THINKING' ? brand.violet : brand.cyan, transform: [{ translateY: hoverProgress.interpolate({ inputRange: [0, 1], outputRange: [0, -4] }) }, { scale: hoverProgress.interpolate({ inputRange: [0, 1], outputRange: [1, 1.015] }) }] }]}><ActiveMark size={markSize} /></Animated.View>
        </TouchableOpacity>

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
  </EnvironmentBackground>;
}

const interfaceFont = Platform.select({ web: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", default: undefined });
const styles = StyleSheet.create({
  safeArea: { flex: 1 },
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
  stage: { position: 'relative', alignItems: 'center', justifyContent: 'center', marginTop: 3 },
  rippleField: { position: 'absolute', width: '100%', height: '100%', alignItems: 'center', justifyContent: 'center' },
  rippleLayer: { position: 'absolute', alignItems: 'center', justifyContent: 'center', shadowOpacity: 0.3, pointerEvents: 'none' },
  markHalo: { alignItems: 'center', justifyContent: 'center', shadowOpacity: 0.45, shadowRadius: 32, shadowOffset: { width: 0, height: 10 } },
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
