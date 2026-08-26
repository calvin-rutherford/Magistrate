import React, { useCallback, useEffect, useId, useReducer, useRef, useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View, useWindowDimensions } from 'react-native';
import Svg, { Circle, Line, Polygon } from 'react-native-svg';
import { useRouter } from 'expo-router';
import * as FileSystem from 'expo-file-system/legacy';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { useVoiceInputAdapter } from '../src/input/VoiceInputAdapter';
import { cancelVoiceMove, fetchAgents, pollVoiceMove, submitVoiceMove, transcribeVoiceAudio, VoiceMoveResult } from '../src/api/client';
import { ttsService } from '../src/services/TextToSpeechService';
import { transitionVoiceState, VoiceState } from '../src/services/VoiceSessionReducer';

type Target = { id: string; name: string; status?: string };

export default function VoiceScreen() {
  const router = useRouter();
  const { width } = useWindowDimensions();
  const canvasSize = Math.min(Math.max(width - 40, 220), 320);
  const [voiceState, setVoiceState] = useReducer(transitionVoiceState, 'READY' as VoiceState);
  const [rotation, setRotation] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [intermediate, setIntermediate] = useState('');
  const [response, setResponse] = useState('');
  const [error, setError] = useState('');
  const [captureNotice, setCaptureNotice] = useState('');
  const [target, setTarget] = useState('captain');
  const [targets, setTargets] = useState<Target[]>([]);
  const [targetError, setTargetError] = useState('');
  const [pendingMove, setPendingMove] = useState<VoiceMoveResult | null>(null);
  const [pendingKey, setPendingKey] = useState('');
  const sessionSeed = useId().replace(/[^a-zA-Z0-9]/g, '');
  const sessionId = useRef(`voice-${sessionSeed}-${Date.now().toString(36)}`).current;
  const moveSequence = useRef(0);
  const requestAbortRef = useRef<AbortController | null>(null);

  const fail = useCallback((cause: unknown) => {
    if (cause instanceof Error && cause.name === 'AbortError') return;
    setError(cause instanceof Error ? cause.message : 'Voice Mode encountered an unexpected error.');
    setVoiceState('ERROR');
  }, []);
  const capture = useVoiceInputAdapter(setIntermediate, setCaptureNotice);

  useEffect(() => {
    let mounted = true;
    fetchAgents().then(agents => {
      if (mounted) setTargets(agents.map(agent => ({ id: agent.id, name: agent.name, status: agent.status })));
    }).catch(cause => {
      if (mounted) setTargetError(cause instanceof Error ? cause.message : 'Live targets are unavailable.');
    });
    const interval = setInterval(() => setRotation(previous => (previous + 0.05) % (Math.PI * 2)), 30);
    return () => {
      mounted = false;
      clearInterval(interval);
      requestAbortRef.current?.abort();
      capture.cancel();
      ttsService.stop();
    };
    // Capture is deliberately not started on mount; cleanup uses the mounted adapter instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startRecording = async () => {
    if (voiceState === 'SPEAKING') {
      ttsService.stop();
      setVoiceState('READY');
    }
    setResponse(''); setError(''); setCaptureNotice(''); setTranscript(''); setIntermediate(''); setPendingMove(null); setPendingKey('');
    try {
      await capture.start();
      setVoiceState('LISTENING');
    } catch (cause) { fail(cause); }
  };

  const finishRecording = async () => {
    if (voiceState !== 'LISTENING') return;
    setVoiceState('TRANSCRIBING');
    const controller = new AbortController();
    requestAbortRef.current = controller;
    let recordingUri = '';
    try {
      const recording = await capture.stop();
      recordingUri = recording.uri;
      if (recording.durationMillis < 250) throw new Error('The recording was too short. Speak before stopping.');
      const result = await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename, sessionId, controller.signal);
      setTranscript(result.text); setIntermediate(''); setVoiceState('REVIEW');
    } catch (cause) { fail(cause); }
    finally {
      if (recordingUri) await FileSystem.deleteAsync(recordingUri, { idempotent: true }).catch(() => undefined);
      if (requestAbortRef.current === controller) requestAbortRef.current = null;
    }
  };

  const clearVoice = async () => {
    requestAbortRef.current?.abort();
    await capture.cancel();
    ttsService.stop();
    setIntermediate(''); setPendingMove(null); setPendingKey(''); setResponse(''); setError(''); setCaptureNotice('');
    if (voiceState !== 'READY') setVoiceState('READY');
  };

  const pollResult = async (move: VoiceMoveResult, signal: AbortSignal) => {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, move.poll_after_ms || 1500));
      if (signal.aborted) return;
      try {
        const latest = await pollVoiceMove(move.move_id, sessionId, signal);
        setPendingMove(latest);
        if (latest.status !== 'acknowledged') {
          if (requestAbortRef.current?.signal === signal) requestAbortRef.current = null;
          const completedText = latest.response || latest.error || 'The request completed.';
          setResponse(completedText);
          setVoiceState(latest.status === 'completed' ? 'SPEAKING' : 'ERROR');
          if (latest.status === 'completed') {
            ttsService.speakChunk(completedText.slice(0, 280), () => setVoiceState('READY'), message => {
              setError(message); setVoiceState('ERROR');
            });
          }
          return;
        }
      } catch (cause) {
        if (!signal.aborted) fail(cause);
        return;
      }
    }
    if (requestAbortRef.current?.signal === signal) requestAbortRef.current = null;
    setResponse('The request is accepted and still running. Check this result again to see the correlated agent outcome.');
    setVoiceState('READY');
  };

  const executeMove = async (move = pendingMove, key = pendingKey) => {
    if (!move) return;
    const controller = new AbortController();
    requestAbortRef.current = controller;
    let keepControllerForPolling = false;
    setVoiceState('EXECUTING'); setError('');
    try {
      const result = await submitVoiceMove(transcript.trim(), target, key,
        true, move.confirmation_token, sessionId, controller.signal);
      setPendingMove(result);
      if (result.status === 'acknowledged') {
        setResponse(result.acknowledgement || 'Request accepted. Waiting for the agent result.');
        setVoiceState('WAITING_RESULT');
        keepControllerForPolling = true;
        void pollResult(result, controller.signal);
      } else if (result.status === 'completed') {
        const text = result.response || `Request completed by ${result.target}.`;
        setResponse(text); setVoiceState('SPEAKING');
        ttsService.speakChunk(text.slice(0, 280), () => setVoiceState('READY'), message => {
          setError(message); setVoiceState('ERROR');
        });
      } else {
        fail(new Error(result.error || 'The request did not complete.'));
      }
    } catch (cause) { fail(cause); }
    finally { if (!keepControllerForPolling && requestAbortRef.current === controller) requestAbortRef.current = null; }
  };

  const resolveAndSubmit = async () => {
    const utterance = transcript.trim();
    if (!utterance) return fail(new Error('Review or enter a transcript before submitting.'));
    const key = `voice-${sessionId}-${++moveSequence.current}`;
    const controller = new AbortController();
    requestAbortRef.current = controller;
    setPendingMove(null); setPendingKey(key); setVoiceState('RESOLVING'); setError('');
    try {
      // submitVoiceMove(utterance, target, key) is the legacy call shape; Voice always supplies its authenticated session below.
      const move = await submitVoiceMove(utterance, target, key, false, undefined, sessionId, controller.signal);
      setPendingMove(move);
      if (move.status === 'confirmation_required') setVoiceState('CONFIRMING');
      else if (move.status === 'ready') await executeMove(move, key);
      else fail(new Error(move.error || 'The gateway refused this voice move.'));
    } catch (cause) { fail(cause); }
    finally { if (requestAbortRef.current === controller) requestAbortRef.current = null; }
  };

  const stopSpeaking = () => { ttsService.stop(); setVoiceState('READY'); };
  const selectedTarget = targets.find(item => item.id === target);
  const targetLabel = selectedTarget?.name || (target === 'captain' ? 'Firstmate' : target);
  const cx = canvasSize / 2, cy = canvasSize / 2, radius = 85 + capture.amplitude * 45;
  const v0 = { x: cx + radius * Math.cos(rotation), y: cy - radius * 0.8 };
  const v1 = { x: cx - radius * Math.cos(rotation + 1.05), y: cy + radius * 0.7 };
  const v2 = { x: cx + radius * Math.cos(rotation + 2.1), y: cy + radius * 0.6 };
  const v3 = { x: cx + radius * 0.3 * Math.sin(rotation * 2), y: cy + radius * 0.2 * Math.cos(rotation) };
  const status: Record<VoiceState, string> = {
    READY: 'READY · MICROPHONE OFF', LISTENING: 'LISTENING · TAP TO STOP', TRANSCRIBING: 'TRANSCRIBING AUDIO',
    REVIEW: 'REVIEW TRANSCRIPT', RESOLVING: 'CHECKING INTENT & LIVE TARGET', CONFIRMING: 'CONFIRMATION REQUIRED',
    EXECUTING: 'SENDING REQUEST', WAITING_RESULT: 'ACKNOWLEDGED · WAITING FOR RESULT', SPEAKING: 'SPEAKING SUMMARY', ERROR: 'VOICE ERROR',
  };
  const canPress = ['READY', 'LISTENING', 'SPEAKING', 'ERROR'].includes(voiceState);

  return <EnvironmentBackground>
    <View style={styles.headerRow}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Leave Voice Mode" onPress={() => router.back()}>
        <GlassSurface variant="control" style={styles.headerCircleBtn}><Text style={styles.backText}>←</Text></GlassSurface>
      </TouchableOpacity>
      <Text style={styles.headerTitle}>VOICE MODE</Text><View style={{ width: 36 }} />
    </View>
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <GlassSurface variant="card" style={styles.statusBox}><Text accessibilityLiveRegion="polite" style={styles.statusText}>{status[voiceState]}</Text></GlassSurface>
      <Text style={styles.targetLabel}>LIVE TARGET · {targetLabel}</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.targetRow}>
        <TouchableOpacity testID="target-captain" accessibilityRole="radio" accessibilityLabel="Target Firstmate" accessibilityState={{ selected: target === 'captain' }} onPress={() => setTarget('captain')} style={[styles.targetChip, target === 'captain' && styles.targetSelected]}><Text style={styles.chipText}>Firstmate</Text></TouchableOpacity>
        {targets.filter(item => item.id !== 'captain').map(item => <TouchableOpacity key={item.id} accessibilityRole="radio" accessibilityLabel={`Target ${item.name}`} accessibilityState={{ selected: target === item.id }} onPress={() => setTarget(item.id)} style={[styles.targetChip, target === item.id && styles.targetSelected]}><Text style={styles.chipText}>{item.name}</Text></TouchableOpacity>)}
      </ScrollView>
      {targetError ? <Text style={styles.errorText}>{targetError}</Text> : null}
      <GlassSurface variant="card" style={styles.privacyCard}><Text style={styles.privacyText}>Microphone is off until you press the control. The final caption comes from the configured speech provider; raw audio is sent only for this transcription request and is not retained by Voice Mode.</Text></GlassSurface>
      <TouchableOpacity testID="voice-control" accessibilityRole="button" accessibilityLabel={voiceState === 'LISTENING' ? 'Stop recording' : voiceState === 'SPEAKING' ? 'Start a new recording' : 'Start recording'} accessibilityHint="Tap to talk, then tap again to stop. Voice Mode never records on open." accessibilityState={{ busy: !['READY', 'LISTENING', 'SPEAKING', 'ERROR'].includes(voiceState) }} onPress={voiceState === 'LISTENING' ? finishRecording : startRecording} disabled={!canPress} activeOpacity={0.9} style={styles.canvasTouch}>
        <GlassSurface variant="surface" intensity={40} style={[styles.canvasSurface, { width: canvasSize, height: canvasSize }]}><Svg width={canvasSize} height={canvasSize}>
          <Polygon points={`${v0.x},${v0.y} ${v1.x},${v1.y} ${v2.x},${v2.y}`} fill="rgba(255,255,255,.08)" stroke="#FFF" strokeWidth="1.5" />
          <Polygon points={`${v0.x},${v0.y} ${v1.x},${v1.y} ${v3.x},${v3.y}`} fill="rgba(255,255,255,.04)" stroke="#FFF" />
          <Polygon points={`${v0.x},${v0.y} ${v2.x},${v2.y} ${v3.x},${v3.y}`} fill="rgba(255,255,255,.06)" stroke="#FFF" />
          <Line x1={v1.x} y1={v1.y} x2={v2.x} y2={v2.y} stroke="#FFF" /><Line x1={v1.x} y1={v1.y} x2={v3.x} y2={v3.y} stroke="#AAA" /><Line x1={v2.x} y1={v2.y} x2={v3.x} y2={v3.y} stroke="#AAA" />
          {[v0, v1, v2, v3].map((vertex, index) => <Circle key={index} cx={vertex.x} cy={vertex.y} r={3 + capture.amplitude * 6} fill="#FFF" />)}
        </Svg></GlassSurface>
      </TouchableOpacity>
      <Text style={styles.hintText}>{voiceState === 'LISTENING' ? `${(capture.durationMillis / 1000).toFixed(1)}s · TAP TO STOP` : 'TAP TO TALK · TAP AGAIN TO STOP'}</Text>
      {captureNotice ? <Text style={styles.noticeText}>{captureNotice}</Text> : null}
      {(intermediate || transcript || voiceState === 'REVIEW') && <GlassSurface variant="card" style={styles.transcriptCard}>
        <Text style={styles.cardLabel}>{intermediate ? 'LIVE CAPTION · DEVICE (INTERIM)' : 'FINAL CAPTION · EDITABLE'}</Text>
        <TextInput testID="voice-transcript" accessibilityLabel="Final voice transcript, editable" multiline editable={voiceState === 'REVIEW' || voiceState === 'ERROR'} value={intermediate || transcript} onChangeText={setTranscript} placeholder="Your final recognized request appears here" placeholderTextColor="#899" style={styles.transcriptInput} />
        <Text style={styles.captionNote}>Captions are authoritative. Review and edit before submitting.</Text>
      </GlassSurface>}
      {pendingMove?.confirmation_message && voiceState === 'CONFIRMING' && <GlassSurface variant="card" style={styles.transcriptCard}>
        <Text style={styles.cardLabel}>CONFIRM {pendingMove.impact.toUpperCase()} · {pendingMove.target}</Text><Text style={styles.bodyText}>{pendingMove.confirmation_message}</Text>
        <View style={styles.actions}><TouchableOpacity testID="confirm-move" accessibilityRole="button" accessibilityLabel="Confirm voice request" onPress={() => executeMove()} style={styles.primary}><Text style={styles.buttonText}>CONFIRM</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" onPress={clearVoice} style={styles.secondary}><Text style={styles.buttonText}>CANCEL</Text></TouchableOpacity></View>
      </GlassSurface>}
      {response ? <GlassSurface variant="card" style={styles.transcriptCard}><Text style={styles.cardLabel}>{pendingMove?.status === 'acknowledged' || voiceState === 'WAITING_RESULT' ? 'IMMEDIATE ACKNOWLEDGEMENT' : 'CORRELATED RESULT'} · {pendingMove?.move_id}</Text><Text accessibilityLiveRegion="polite" style={styles.bodyText}>{response}</Text></GlassSurface> : null}
      {error ? <Text testID="voice-error" accessibilityLiveRegion="assertive" style={styles.errorText}>{error}</Text> : null}
      {voiceState === 'REVIEW' && <View style={styles.actions}><TouchableOpacity testID="submit-voice-move" accessibilityRole="button" onPress={resolveAndSubmit} style={styles.primary}><Text style={styles.buttonText}>SUBMIT TO {targetLabel.toUpperCase()}</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" onPress={clearVoice} style={styles.secondary}><Text style={styles.buttonText}>CANCEL</Text></TouchableOpacity></View>}
      {voiceState === 'LISTENING' && <TouchableOpacity accessibilityRole="button" onPress={clearVoice} style={styles.secondary}><Text style={styles.buttonText}>CANCEL RECORDING</Text></TouchableOpacity>}
      {voiceState === 'WAITING_RESULT' && pendingMove && <View style={styles.actions}><TouchableOpacity accessibilityRole="button" onPress={() => pollResult(pendingMove, new AbortController().signal)} style={styles.secondary}><Text style={styles.buttonText}>CHECK RESULT</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" onPress={async () => { requestAbortRef.current?.abort(); try { const cancelled = await cancelVoiceMove(pendingMove.move_id, sessionId); setPendingMove(cancelled); setResponse(cancelled.response || 'Request cancelled.'); setVoiceState('READY'); } catch (cause) { fail(cause); } }} style={styles.secondary}><Text style={styles.buttonText}>CANCEL REQUEST</Text></TouchableOpacity></View>}
      {voiceState === 'SPEAKING' && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Stop speech" onPress={stopSpeaking} style={styles.secondary}><Text style={styles.buttonText}>STOP SPEAKING</Text></TouchableOpacity>}
    </ScrollView>
  </EnvironmentBackground>;
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', padding: 20, paddingBottom: 48 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12 },
  headerTitle: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' }, backText: { color: '#FFF', fontSize: 16, fontWeight: 'bold' },
  statusBox: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 20, marginVertical: 14 }, statusText: { fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', color: '#FFF', letterSpacing: 1 },
  targetLabel: { color: '#9CA3AF', fontFamily: 'monospace', fontSize: 10 }, targetRow: { gap: 8, paddingVertical: 8 }, targetChip: { borderWidth: 1, borderColor: '#64748B', borderRadius: 16, paddingHorizontal: 12, paddingVertical: 7 }, targetSelected: { backgroundColor: 'rgba(255,255,255,.18)', borderColor: '#FFF' }, chipText: { color: '#FFF', fontSize: 12 },
  privacyCard: { width: '100%', maxWidth: 600, padding: 12, borderRadius: 14, marginTop: 8 }, privacyText: { color: '#CBD5E1', fontSize: 12, lineHeight: 18 }, canvasTouch: { borderRadius: 24, overflow: 'hidden' }, canvasSurface: { borderRadius: 24, justifyContent: 'center', alignItems: 'center' }, hintText: { fontFamily: 'monospace', fontSize: 10, color: 'rgba(255,255,255,.7)', marginVertical: 14, letterSpacing: .8 }, noticeText: { color: '#FDE68A', fontSize: 12, width: '100%', marginBottom: 4 },
  transcriptCard: { width: '100%', maxWidth: 600, padding: 16, borderRadius: 18, marginTop: 10 }, cardLabel: { fontFamily: 'monospace', fontSize: 10, color: '#A5B4FC', letterSpacing: 1, marginBottom: 8 }, transcriptInput: { color: '#FFF', fontSize: 16, minHeight: 58, textAlignVertical: 'top' }, captionNote: { color: '#94A3B8', fontSize: 11, marginTop: 8 }, bodyText: { color: '#FFF', fontSize: 15, lineHeight: 21 }, errorText: { color: '#FCA5A5', fontSize: 14, width: '100%', marginTop: 12 }, actions: { flexDirection: 'row', gap: 10, width: '100%', maxWidth: 600, marginTop: 14 }, primary: { flex: 1, backgroundColor: '#FFF', borderRadius: 12, padding: 13, alignItems: 'center' }, secondary: { borderWidth: 1, borderColor: '#94A3B8', borderRadius: 12, padding: 13, alignItems: 'center', marginTop: 14 }, buttonText: { color: '#111827', fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold' },
});
