import { useLocalSearchParams, useRouter } from 'expo-router';
import * as Network from 'expo-network';
import React, { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, AppState, AppStateStatus, Linking, Platform, ScrollView, StyleSheet, Text, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import {
  cancelMagiChatTurn, fetchMagiChatConversation, getGatewaySessionRevision, sendMagiChatPrompt,
  transcribeVoiceAudio,
} from '../src/api/client';
import { useVoiceInputAdapter } from '../src/input/VoiceInputAdapter';
import { hasMagiReconciliationConflict, reconcileMagiMessages } from '../src/services/MagiConversation';
import { appendMagiMessage, getMagiConversationPrincipal, getMagiMessages, resetMagiMessages, updateMagiMessage, useMagiMessages } from '../src/services/MagiConversationSession';
import { ttsService } from '../src/services/TextToSpeechService';
import { transitionVoiceState, VoiceState } from '../src/services/VoiceSessionReducer';
import { loadChatPreferences } from '../src/services/ChatPreferences';
import { capabilityFor, getLocalVoiceCapabilities, resolveVoiceInputMode, VoiceInputCapabilities, VoiceInputMode } from '../src/services/VoiceInputModes';
import { clampAudioPeak, ENVELOPE_SILENCE_FLOOR, updateAudioEnvelope } from '../src/services/VoiceVisuals';
import { VoiceTetrahedron } from '../src/components/VoiceTetrahedron';

/** Test-only amplitude injection so browser evidence can be captured without a
 * real microphone. Web-only by construction (native never defines `window`),
 * so the native capture path is unaffected. */
declare global {
  var __voiceSetTestAmplitude: ((value: number | null) => void) | undefined;
}

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
  THINKING: { title: 'Thinking', detail: 'Magi is responding' },
  CONFIRMING: { title: 'Confirm action', detail: 'Voice control is paused for your review' },
  SPEAKING: { title: 'Speaking', detail: 'Tap the mark to interrupt' },
  ERROR: { title: 'Voice paused', detail: 'Tap the mark to try again' },
};

export default function VoiceScreen() {
  const router = useRouter();
  const { autostart } = useLocalSearchParams<{ autostart?: string | string[] }>();
  const requestedAutostart = (Array.isArray(autostart) ? autostart[0] : autostart) === 'true';
  const networkState = Network.useNetworkState();
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
  const [reducedMotion, setReducedMotion] = useState(false);
  const [visualAmplitude, setVisualAmplitude] = useState(ENVELOPE_SILENCE_FLOOR);
  const [muted, setMuted] = useState(false);
  const [speakResponses, setSpeakResponses] = useState(true);
  const [autoContinue, setAutoContinue] = useState(true);
  const [, setBackgroundReady] = useState(false);
  const messages = useMagiMessages();
  useEffect(() => { void loadChatPreferences().then(() => setBackgroundReady(true)); }, []);
  const refreshConversation = useCallback(async () => {
    const owner = getMagiConversationPrincipal();
    const sessionRevision = getGatewaySessionRevision();
    if (!owner) return false;
    const result = await fetchMagiChatConversation();
    if (getMagiConversationPrincipal() !== owner || getGatewaySessionRevision() !== sessionRevision) return false;
    const current = getMagiMessages();
    if (hasMagiReconciliationConflict(current, result.messages)) throw new Error('Gateway returned conflicting Magi identity.');
    resetMagiMessages(reconcileMagiMessages(current, result.messages, { authoritative: true }));
    return true;
  }, []);
  useEffect(() => { void refreshConversation().catch(() => { /* Submission or reconnect retries this read. */ }); }, [refreshConversation]);
  const capture = useVoiceInputAdapter(setIntermediate, voiceMode);
  const captureRef = useRef(capture);
  const stateRef = useRef<VoiceState>(voiceState);
  const intermediateRef = useRef(intermediate);
  const amplitudeRef = useRef(capture.amplitude);
  const endingRef = useRef(false);
  const turnInFlightRef = useRef(false);
  const turnOwnerRef = useRef<string | null>(null);
  const turnSessionRevisionRef = useRef(-1);
  const requestControllerRef = useRef<AbortController | null>(null);
  const submittedMessageIdRef = useRef('');
  const cancelledTurnRef = useRef(false);
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);
  const wasOfflineRef = useRef(false);
  const autostartConsumedRef = useRef(false);
  const heardSpeechRef = useRef(false);
  const listeningStartedAtRef = useRef(0);
  const lastSpeechAtRef = useRef(0);
  const [hoverProgress] = useState(() => new Animated.Value(0));
  // The smoothed envelope is stepped deterministically at a capped cadence.
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
      setSpeakResponses(preferences.voiceOutputEnabled && preferences.voiceAutoSpeak);
      setAutoContinue(preferences.voiceAutoListen);
      ttsService.setSettings({ enabled: preferences.voiceOutputEnabled, autoSpeak: preferences.voiceAutoSpeak });
      setVoiceSetupReady(true);
    }).catch(() => { if (mounted) setVoiceSetupReady(true); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion);
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion);
    return () => subscription.remove();
  }, []);

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
    if (networkState.isConnected === false || networkState.isInternetReachable === false) {
      fail(new Error('You are offline. Reconnect before starting a voice turn.'));
      return;
    }
    const owner = getMagiConversationPrincipal();
    const sessionRevision = getGatewaySessionRevision();
    if (!owner) return;
    const ownsTurn = () => !endingRef.current && !cancelledTurnRef.current
      && appStateRef.current === 'active'
      && getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === sessionRevision;
    const capability = capabilityFor(voiceCapabilities, voiceMode);
    if (capability.available === 'unavailable') { fail(capability.reason || `${capability.label} is unavailable.`); return; }
    ttsService.stop();
    setError(''); setNotice(modeNoticeRef.current); setIntermediate(''); setFinalTranscript('');
    envelopeRef.current = ENVELOPE_SILENCE_FLOOR;
    setVisualAmplitude(ENVELOPE_SILENCE_FLOOR);
    cancelledTurnRef.current = false;
    submittedMessageIdRef.current = '';
    turnOwnerRef.current = owner;
    turnSessionRevisionRef.current = sessionRevision;
    setVoiceState('STARTING');
    try {
      await captureRef.current.start();
      if (!ownsTurn()) { await captureRef.current.cancel(); return; }
      listeningStartedAtRef.current = Date.now();
      lastSpeechAtRef.current = Date.now();
      heardSpeechRef.current = false;
      setVoiceState('LISTENING');
    } catch (cause) { if (ownsTurn()) fail(cause); }
  }, [fail, networkState.isConnected, networkState.isInternetReachable, voiceCapabilities, voiceMode, voiceSetupReady]);

  const deliverNativeResponse = useCallback((result: Awaited<ReturnType<typeof sendMagiChatPrompt>>) => {
    const current = getMagiMessages();
    if (hasMagiReconciliationConflict(current, result.messages)) {
      throw new Error('Gateway returned conflicting Magi identity.');
    }
    resetMagiMessages(reconcileMagiMessages(current, result.messages));
    if (result.status !== 'completed') throw new Error(result.error || 'Magi did not complete the response.');
    const assistant = [...result.messages].reverse().find(message => message.role === 'assistant' && message.content.trim());
    if (!assistant) throw new Error('Magi returned no complete response.');
    turnInFlightRef.current = false;
    if (appStateRef.current !== 'active') {
      setVoiceState('READY');
      setNotice('Response received while Voice Mode was paused. Tap the tetrahedron to continue.');
      return;
    }
    setVoiceState('SPEAKING');
    const afterResponse = () => {
      if (endingRef.current) return;
      if (autoContinue) void beginListening();
      else { setVoiceState('READY'); setNotice('Response complete. Tap the tetrahedron when you want to speak again.'); }
    };
    if (muted || !speakResponses) afterResponse();
    else ttsService.speakChunk(assistant.content, afterResponse);
  }, [autoContinue, beginListening, muted, speakResponses]);

  const finishTurn = useCallback(async () => {
    if (endingRef.current || stateRef.current !== 'LISTENING' || turnInFlightRef.current) return;
    const owner = turnOwnerRef.current;
    const sessionRevision = turnSessionRevisionRef.current;
    const ownsTurn = () => !endingRef.current && !cancelledTurnRef.current && !!owner
      && getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === sessionRevision;
    if (!ownsTurn()) return;
    turnInFlightRef.current = true;
    setVoiceState('TRANSCRIBING');
    let submittedMessageId = '';
    try {
      const recording = await captureRef.current.stop();
      if (!ownsTurn()) return;
      if (recording.durationMillis < MIN_TURN_MS) {
        turnInFlightRef.current = false;
        await beginListening();
        if (ownsTurn()) setNotice('Keep speaking a little longer so Magistrate can hear the full turn.');
        return;
      }
      const transcription = voiceMode === 'browser' ? { text: recording.transcript || '', is_final: true } : await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename);
      if (!ownsTurn() || cancelledTurnRef.current) return;
      const utterance = transcription.text?.trim() || intermediateRef.current.trim();
      if (!utterance) {
        turnInFlightRef.current = false;
        await beginListening();
        if (ownsTurn()) setNotice('I didn’t catch that. Listening again…');
        return;
      }
      setFinalTranscript(utterance); setIntermediate('');
      const clientMessageId = `voice-u-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      submittedMessageId = clientMessageId;
      submittedMessageIdRef.current = clientMessageId;
      appendMagiMessage({ id: clientMessageId, role: 'user', text: utterance, sentAt: Date.now(), source: 'voice', delivery: 'sending', progress: 'working' });
      setVoiceState('THINKING');
      const controller = new AbortController();
      requestControllerRef.current = controller;
      const result = await sendMagiChatPrompt(
        utterance, clientMessageId, 'voice', undefined, { signal: controller.signal },
      );
      if (!ownsTurn() || cancelledTurnRef.current) return;
      deliverNativeResponse(result);
    } catch (cause) {
      if (!ownsTurn() || cancelledTurnRef.current || requestControllerRef.current?.signal.aborted) return;
      if (submittedMessageId) updateMagiMessage(submittedMessageId, { delivery: 'failed', progress: 'failed' });
      fail(cause);
    } finally {
      requestControllerRef.current = null;
    }
  }, [beginListening, deliverNativeResponse, fail, voiceMode]);

  useEffect(() => {
    if (voiceState !== 'LISTENING') return;
    const TICK_MS = 160;
    const timer = setInterval(() => {
      const now = Date.now();
      const rawAmplitude = testAmplitudeRef.current ?? amplitudeRef.current;
      envelopeRef.current = updateAudioEnvelope(envelopeRef.current, clampAudioPeak(rawAmplitude * 7), TICK_MS);
      setVisualAmplitude(reducedMotion ? ENVELOPE_SILENCE_FLOOR : envelopeRef.current);
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
  }, [finishTurn, reducedMotion, voiceState]);

  useEffect(() => {
    if (!voiceSetupReady || !requestedAutostart || autostartConsumedRef.current) return;
    autostartConsumedRef.current = true;
    const timer = setTimeout(() => { void beginListening(); }, 180);
    return () => clearTimeout(timer);
  }, [beginListening, requestedAutostart, voiceSetupReady]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', next => {
      const previous = appStateRef.current;
      appStateRef.current = next;
      if (next !== 'active') {
        ttsService.stop();
        if (stateRef.current === 'LISTENING' || stateRef.current === 'STARTING') {
          cancelledTurnRef.current = true;
          turnInFlightRef.current = false;
          void captureRef.current.cancel();
          setVoiceState('READY');
          setNotice('Voice paused when Magistrate left the foreground. Nothing was sent.');
        } else if (stateRef.current === 'SPEAKING') {
          setVoiceState('READY');
          setNotice('Speech paused when Magistrate left the foreground.');
        }
      } else if (previous !== 'active') {
        void refreshConversation().then(() => setNotice('Voice is ready again.')).catch(() => setNotice('Conversation will refresh when the Gateway is reachable.'));
      }
    });
    return () => subscription.remove();
  }, [refreshConversation]);

  useEffect(() => {
    const offline = networkState.isConnected === false || networkState.isInternetReachable === false;
    if (offline) {
      wasOfflineRef.current = true;
      if (stateRef.current === 'LISTENING' || stateRef.current === 'STARTING') {
        cancelledTurnRef.current = true;
        turnInFlightRef.current = false;
        void captureRef.current.cancel();
        setError('Network connection was lost. The recording was not sent.');
        setVoiceState('ERROR');
      } else setNotice('Offline. The conversation remains available and will refresh after reconnection.');
      return;
    }
    if (wasOfflineRef.current && networkState.isConnected !== undefined) {
      wasOfflineRef.current = false;
      void refreshConversation().then(() => {
        setError(''); setNotice('Connection restored. Tap the tetrahedron to continue.');
        if (stateRef.current === 'ERROR') setVoiceState('READY');
      }).catch(() => setNotice('Connected, but the Gateway is still unavailable.'));
    }
  }, [networkState.isConnected, networkState.isInternetReachable, refreshConversation]);

  useEffect(() => {
    if (capture.routeRevision > 0 && capture.audioInput && stateRef.current === 'LISTENING') {
      setNotice(`Audio input changed to ${capture.audioInput.name}. Listening continues.`);
    }
  }, [capture.audioInput, capture.routeRevision]);

  useEffect(() => {
    if (!capture.mediaServicesDidReset || stateRef.current !== 'LISTENING') return;
    cancelledTurnRef.current = true; turnInFlightRef.current = false;
    void captureRef.current.cancel();
    setError('The system audio service restarted. Tap the tetrahedron to reconnect the microphone.');
    setVoiceState('ERROR');
  }, [capture.mediaServicesDidReset]);

  useEffect(() => () => {
    endingRef.current = true;
    requestControllerRef.current?.abort();
    void captureRef.current.cancel();
    ttsService.stop();
  }, []);

  const handleMainControl = () => {
    if (voiceState === 'LISTENING') void finishTurn();
    else if (voiceState === 'SPEAKING' || voiceState === 'READY' || voiceState === 'ERROR') void beginListening();
  };

  const cancelCurrentTurn = async () => {
    cancelledTurnRef.current = true;
    turnInFlightRef.current = false;
    requestControllerRef.current?.abort();
    requestControllerRef.current = null;
    ttsService.stop();
    await captureRef.current.cancel();
    const messageId = submittedMessageIdRef.current;
    submittedMessageIdRef.current = '';
    if (messageId) {
      updateMagiMessage(messageId, { delivery: 'cancelled', progress: 'cancelled' });
      await cancelMagiChatTurn(messageId).catch(() => undefined);
      void refreshConversation().catch(() => undefined);
    }
    setIntermediate(''); setError(''); setNotice('Current turn cancelled.'); setVoiceState('READY');
  };

  const toggleMute = () => {
    setMuted(value => {
      const next = !value;
      if (next && stateRef.current === 'SPEAKING') { ttsService.stop(); setVoiceState('READY'); setNotice('Voice output muted.'); }
      return next;
    });
  };

  const endConversation = () => {
    endingRef.current = true; turnInFlightRef.current = true; cancelledTurnRef.current = true;
    requestControllerRef.current?.abort(); ttsService.stop();
    // End always returns to the canonical conversation. Browser history can
    // contain an external Shortcut/notification origin and is not a safe target.
    void captureRef.current.cancel().finally(() => router.replace('/chat' as any));
  };

  const currentCopy = stateCopy[voiceState];
  // Voice Mode is a dedicated near-black ceremonial canvas regardless of the
  // conversation theme/environment choice, so its palette is fixed rather
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
        <View><Text style={[styles.eyebrow, { color: brand.cyan }]}>MAGI / VOICE</Text><Text style={[styles.continuity, { color: mutedColor }]}>One continuous thread</Text></View>
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

        <TouchableOpacity testID="voice-control" accessibilityRole="button" accessibilityHint="Voice uses the same Magi conversation as typed chat." accessibilityLabel={voiceState === 'LISTENING' ? 'Finish speaking' : voiceState === 'SPEAKING' ? 'Interrupt response and listen' : 'Start listening'} accessibilityState={{ busy: ['STARTING','TRANSCRIBING','THINKING'].includes(voiceState), disabled: ['STARTING','TRANSCRIBING','THINKING'].includes(voiceState) }} onPress={handleMainControl} {...(hoverHandlers as any)} disabled={['STARTING','TRANSCRIBING','THINKING'].includes(voiceState)} activeOpacity={0.88} style={[styles.stage, { width: stageSize, height: stageSize }]}>
          <Animated.View style={[styles.markHalo, { shadowColor: voiceState === 'THINKING' ? brand.violet : brand.cyan, transform: [{ translateY: hoverProgress.interpolate({ inputRange: [0, 1], outputRange: [0, -4] }) }, { scale: hoverProgress.interpolate({ inputRange: [0, 1], outputRange: [1, 1.015] }) }] }]}>
            <VoiceTetrahedron size={markSize} state={voiceState} amplitude={visualAmplitude} reducedMotion={reducedMotion} />
          </Animated.View>
        </TouchableOpacity>

        <View style={styles.sessionControls} accessibilityRole="toolbar">
          <TouchableOpacity testID="voice-mute" accessibilityRole="button" accessibilityLabel={muted ? 'Unmute Magi voice output' : 'Mute Magi voice output'} accessibilityState={{ selected: muted }} onPress={toggleMute} style={[styles.sessionButton, muted && styles.sessionButtonActive]}><Text style={[styles.sessionButtonText, { color: textColor }]}>{muted ? 'UNMUTE' : 'MUTE'}</Text></TouchableOpacity>
          <TouchableOpacity testID="voice-cancel" accessibilityRole="button" accessibilityLabel="Cancel current voice turn" accessibilityState={{ disabled: voiceState === 'READY' }} disabled={voiceState === 'READY'} onPress={() => void cancelCurrentTurn()} style={[styles.sessionButton, voiceState === 'READY' && styles.sessionButtonDisabled]}><Text style={[styles.sessionButtonText, { color: textColor }]}>CANCEL TURN</Text></TouchableOpacity>
        </View>

        <View style={styles.liveTranscript} accessibilityLiveRegion="polite">
          <Text testID="voice-live-transcript" style={[styles.liveTranscriptText, { color: intermediate || finalTranscript ? textColor : mutedColor }]}>
            {intermediate || finalTranscript || (voiceState === 'LISTENING' ? 'Your words will appear here…' : ' ')}
          </Text>
          {voiceState === 'LISTENING' ? <Text style={[styles.turnHint, { color: mutedColor }]}>{(capture.durationMillis / 1000).toFixed(1)}s · tap the mark to finish now</Text> : null}
        </View>



        {error ? <View testID="voice-error" accessibilityLiveRegion="assertive" style={[styles.feedback, { borderColor: brand.critical }]}><Text style={styles.errorText}>{error}</Text>{capture.error?.code === 'permission-denied' && Platform.OS !== 'web' ? <TouchableOpacity testID="voice-open-settings" accessibilityRole="button" accessibilityLabel="Open system settings for microphone permission" onPress={() => void Linking.openSettings()} style={styles.openSettingsButton}><Text style={styles.openSettingsText}>OPEN SETTINGS</Text></TouchableOpacity> : null}</View> : null}
        {notice ? <Text accessibilityLiveRegion="polite" style={[styles.noticeText, { color: mutedColor }]}>{notice}</Text> : null}

        {visibleMessages.length ? <View testID="voice-conversation" style={[styles.conversation, { borderTopColor: borderColor }]}>
          {visibleMessages.map(message => <View key={message.id} style={styles.turn}>
            <Text style={[styles.turnRole, { color: message.role === 'user' ? brand.cyan : brand.violet }]}>{message.role === 'user' ? 'YOU' : 'MAGI'}</Text>
            <Text style={[styles.turnText, { color: textColor }]}>{message.text}</Text>
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
  markHalo: { alignItems: 'center', justifyContent: 'center', shadowOpacity: 0.45, shadowRadius: 32, shadowOffset: { width: 0, height: 10 } },
  sessionControls: { flexDirection: 'row', gap: 10, alignItems: 'center', justifyContent: 'center', marginTop: -8, marginBottom: 5 },
  sessionButton: { minHeight: 44, minWidth: 92, borderWidth: 1, borderColor: 'rgba(142,153,170,0.55)', borderRadius: 999, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 14 },
  sessionButtonActive: { borderColor: brand.cyan, backgroundColor: 'rgba(36,216,255,0.12)' },
  sessionButtonDisabled: { opacity: 0.38 },
  sessionButtonText: { fontFamily: interfaceFont, fontSize: 10, lineHeight: 14, fontWeight: '700', letterSpacing: 0.8 },
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
  openSettingsButton: { minHeight: 44, marginTop: 8, alignSelf: 'center', justifyContent: 'center', paddingHorizontal: 14, borderWidth: 1, borderColor: brand.critical, borderRadius: 999 },
  openSettingsText: { color: brand.paper, fontFamily: interfaceFont, fontSize: 10, fontWeight: '700', letterSpacing: 0.8 },
  noticeText: { fontFamily: interfaceFont, fontSize: 12, lineHeight: 18, textAlign: 'center', marginTop: 8 },
  conversation: { width: '100%', maxWidth: 720, borderTopWidth: 1, marginTop: 16, paddingTop: 14, gap: 11 },
  turn: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  turnRole: { width: 76, fontFamily: interfaceFont, fontSize: 9, lineHeight: 17, fontWeight: '700', letterSpacing: 0.9 },
  turnText: { flex: 1, fontFamily: interfaceFont, fontSize: 13, lineHeight: 19 },
});
