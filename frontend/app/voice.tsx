import React, { useCallback, useEffect, useId, useReducer, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Dimensions, TextInput, ScrollView } from 'react-native';
import Svg, { Polygon, Line, Circle } from 'react-native-svg';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { useVoiceInputAdapter } from '../src/input/VoiceInputAdapter';
import { fetchAgents, submitVoiceMove, transcribeVoiceAudio, VoiceMoveResult } from '../src/api/client';
import { ttsService } from '../src/services/TextToSpeechService';
import { transitionVoiceState, VoiceState } from '../src/services/VoiceSessionReducer';
import { useRouter } from 'expo-router';

const { width } = Dimensions.get('window');
const CANVAS_SIZE = Math.min(width - 40, 320);

export default function VoiceScreen() {
  const router = useRouter();
  const [voiceState, setVoiceState] = useReducer(transitionVoiceState, 'READY' as VoiceState);
  const [rotation, setRotation] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [intermediate, setIntermediate] = useState('');
  const [response, setResponse] = useState('');
  const [error, setError] = useState('');
  const [target, setTarget] = useState('captain');
  const [targets, setTargets] = useState<{id: string; name: string}[]>([]);
  const [pendingMove, setPendingMove] = useState<VoiceMoveResult | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const sessionId = useId();
  const moveSequence = useRef(0);
  const capture = useVoiceInputAdapter(setIntermediate);

  useEffect(() => {
    fetchAgents().then(agents => setTargets(agents.map(agent => ({ id: agent.id, name: agent.name })))).catch(() => undefined);
    const interval = setInterval(() => setRotation(prev => (prev + 0.05) % (Math.PI * 2)), 30);
    return () => { clearInterval(interval); capture.cancel(); ttsService.stop(); };
    // Capture is deliberately not started on mount; cleanup uses the mounted adapter instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fail = useCallback((cause: unknown) => {
    setError(cause instanceof Error ? cause.message : 'Voice Mode encountered an unexpected error.');
    setVoiceState('ERROR');
  }, []);

  const startRecording = async () => {
    ttsService.stop(); setResponse(''); setError(''); setTranscript(''); setIntermediate(''); setPendingMove(null);
    try { await capture.start(); setVoiceState('LISTENING'); } catch (cause) { fail(cause); }
  };
  const finishRecording = async () => {
    if (voiceState !== 'LISTENING') return;
    setVoiceState('TRANSCRIBING');
    try {
      const recording = await capture.stop();
      if (recording.durationMillis < 250) throw new Error('The recording was too short. Start the microphone and speak before stopping.');
      const result = await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename);
      setTranscript(result.text); setIntermediate(''); setVoiceState('REVIEW');
    } catch (cause) { fail(cause); }
  };
  const cancel = async () => {
    await capture.cancel(); ttsService.stop(); setIntermediate(''); setPendingMove(null); setVoiceState('READY');
  };
  const resolveAndSubmit = async () => {
    const utterance = transcript.trim();
    if (!utterance) return fail(new Error('Review or enter a transcript before submitting.'));
    moveSequence.current += 1;
    const key = `voice-${sessionId}-${moveSequence.current}`;
    setIdempotencyKey(key); setVoiceState('RESOLVING'); setError('');
    try {
      const move = await submitVoiceMove(utterance, target, key);
      setPendingMove(move);
      if (move.status === 'confirmation_required') setVoiceState('CONFIRMING');
      else if (move.status === 'ready') await executeMove(move, key);
      else fail(new Error(move.error || 'The gateway refused this voice move.'));
    } catch (cause) { fail(cause); }
  };
  const executeMove = async (move = pendingMove, key = idempotencyKey) => {
    if (!move) return;
    setVoiceState('EXECUTING');
    try {
      const result = await submitVoiceMove(transcript.trim(), target, key, true, move.confirmation_token);
      if (result.status !== 'completed') throw new Error(result.error || 'The request did not complete.');
      const text = result.response || `Request completed by ${result.target}.`;
      setPendingMove(result); setResponse(text); setVoiceState('SPEAKING');
      ttsService.speakChunk(text.slice(0, 280), () => setVoiceState('READY'));
    } catch (cause) { fail(cause); }
  };

  const cx = CANVAS_SIZE / 2, cy = CANVAS_SIZE / 2, r = 85 + capture.amplitude * 45;
  const v0 = { x: cx + r * Math.cos(rotation), y: cy - r * 0.8 };
  const v1 = { x: cx - r * Math.cos(rotation + 1.05), y: cy + r * 0.7 };
  const v2 = { x: cx + r * Math.cos(rotation + 2.1), y: cy + r * 0.6 };
  const v3 = { x: cx + r * 0.3 * Math.sin(rotation * 2), y: cy + r * 0.2 * Math.cos(rotation) };
  const status = { READY: 'READY · MICROPHONE OFF', LISTENING: 'LISTENING · TAP TO STOP', TRANSCRIBING: 'TRANSCRIBING AUDIO',
    REVIEW: 'REVIEW TRANSCRIPT', RESOLVING: 'CHECKING INTENT & TARGET', CONFIRMING: 'CONFIRMATION REQUIRED', EXECUTING: 'SENDING REQUEST',
    SPEAKING: 'SPEAKING RESPONSE', ERROR: 'VOICE ERROR' }[voiceState];

  return <EnvironmentBackground>
    <View style={styles.headerRow}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Leave Voice Mode" onPress={() => router.back()}>
        <GlassSurface variant="control" style={styles.headerCircleBtn}><Text style={styles.backText}>←</Text></GlassSurface>
      </TouchableOpacity>
      <Text style={styles.headerTitle}>VOICE MODE</Text><View style={{ width: 36 }} />
    </View>
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <GlassSurface variant="card" style={styles.statusBox}><Text accessibilityLiveRegion="polite" style={styles.statusText}>{status}</Text></GlassSurface>
      <Text style={styles.targetLabel}>TARGET</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.targetRow}>
        <TouchableOpacity testID="target-captain" onPress={() => setTarget('captain')} style={[styles.targetChip, target === 'captain' && styles.targetSelected]}><Text style={styles.chipText}>Magistrate</Text></TouchableOpacity>
        {targets.filter(item => item.id !== 'captain').map(item => <TouchableOpacity key={item.id} onPress={() => setTarget(item.id)} style={[styles.targetChip, target === item.id && styles.targetSelected]}><Text style={styles.chipText}>{item.name}</Text></TouchableOpacity>)}
      </ScrollView>
      <TouchableOpacity testID="voice-control" accessibilityRole="button" accessibilityLabel={voiceState === 'LISTENING' ? 'Stop recording' : 'Start recording'}
        accessibilityState={{ busy: !['READY','REVIEW','ERROR'].includes(voiceState) }}
        onPress={voiceState === 'LISTENING' ? finishRecording : startRecording}
        disabled={!['READY','LISTENING','SPEAKING','ERROR'].includes(voiceState)} activeOpacity={0.9} style={styles.canvasTouch}>
        <GlassSurface variant="surface" intensity={40} style={styles.canvasSurface}><Svg width={CANVAS_SIZE} height={CANVAS_SIZE}>
          <Polygon points={`${v0.x},${v0.y} ${v1.x},${v1.y} ${v2.x},${v2.y}`} fill="rgba(255,255,255,.08)" stroke="#FFF" strokeWidth="1.5" />
          <Polygon points={`${v0.x},${v0.y} ${v1.x},${v1.y} ${v3.x},${v3.y}`} fill="rgba(255,255,255,.04)" stroke="#FFF" />
          <Polygon points={`${v0.x},${v0.y} ${v2.x},${v2.y} ${v3.x},${v3.y}`} fill="rgba(255,255,255,.06)" stroke="#FFF" />
          <Line x1={v1.x} y1={v1.y} x2={v2.x} y2={v2.y} stroke="#FFF" /><Line x1={v1.x} y1={v1.y} x2={v3.x} y2={v3.y} stroke="#AAA" /><Line x1={v2.x} y1={v2.y} x2={v3.x} y2={v3.y} stroke="#AAA" />
          {[v0,v1,v2,v3].map((v,i) => <Circle key={i} cx={v.x} cy={v.y} r={3 + capture.amplitude * 6} fill="#FFF" />)}
        </Svg></GlassSurface>
      </TouchableOpacity>
      <Text style={styles.hintText}>{voiceState === 'LISTENING' ? `${(capture.durationMillis / 1000).toFixed(1)}s · TAP TO STOP` : 'TAP TO TALK · TAP AGAIN TO STOP'}</Text>
      {(intermediate || transcript || voiceState === 'REVIEW') && <GlassSurface variant="card" style={styles.transcriptCard}>
        <Text style={styles.cardLabel}>{intermediate ? 'LIVE TRANSCRIPT (DEVICE)' : 'FINAL TRANSCRIPT · EDITABLE'}</Text>
        <TextInput testID="voice-transcript" accessibilityLabel="Voice transcript" multiline editable={voiceState === 'REVIEW' || voiceState === 'ERROR'} value={intermediate || transcript}
          onChangeText={setTranscript} placeholder="Your recognized request appears here" placeholderTextColor="#899" style={styles.transcriptInput} />
      </GlassSurface>}
      {pendingMove?.confirmation_message && voiceState === 'CONFIRMING' && <GlassSurface variant="card" style={styles.transcriptCard}>
        <Text style={styles.cardLabel}>IMPACT: {pendingMove.impact.toUpperCase()}</Text><Text style={styles.bodyText}>{pendingMove.confirmation_message}</Text>
        <View style={styles.actions}><TouchableOpacity testID="confirm-move" onPress={() => executeMove()} style={styles.primary}><Text style={styles.buttonText}>CONFIRM</Text></TouchableOpacity><TouchableOpacity onPress={cancel} style={styles.secondary}><Text style={styles.buttonText}>CANCEL</Text></TouchableOpacity></View>
      </GlassSurface>}
      {response ? <GlassSurface variant="card" style={styles.transcriptCard}><Text style={styles.cardLabel}>CORRELATED RESPONSE · {pendingMove?.move_id}</Text><Text accessibilityLiveRegion="polite" style={styles.bodyText}>{response}</Text></GlassSurface> : null}
      {error ? <Text testID="voice-error" accessibilityLiveRegion="assertive" style={styles.errorText}>{error}</Text> : null}
      {voiceState === 'REVIEW' && <View style={styles.actions}><TouchableOpacity testID="submit-voice-move" onPress={resolveAndSubmit} style={styles.primary}><Text style={styles.buttonText}>SUBMIT TO {target === 'captain' ? 'MAGISTRATE' : target}</Text></TouchableOpacity><TouchableOpacity onPress={cancel} style={styles.secondary}><Text style={styles.buttonText}>CANCEL</Text></TouchableOpacity></View>}
      {voiceState === 'LISTENING' && <TouchableOpacity onPress={cancel} style={styles.secondary}><Text style={styles.buttonText}>CANCEL RECORDING</Text></TouchableOpacity>}
    </ScrollView>
  </EnvironmentBackground>;
}

const styles = StyleSheet.create({
  container:{alignItems:'center',padding:20,paddingBottom:48},headerRow:{flexDirection:'row',justifyContent:'space-between',alignItems:'center',paddingHorizontal:16,paddingTop:12},headerTitle:{fontFamily:'monospace',fontSize:14,fontWeight:'bold',color:'#FFF',letterSpacing:2},headerCircleBtn:{width:36,height:36,borderRadius:18,justifyContent:'center',alignItems:'center'},backText:{color:'#FFF',fontSize:16,fontWeight:'bold'},statusBox:{paddingHorizontal:20,paddingVertical:10,borderRadius:20,marginVertical:14},statusText:{fontFamily:'monospace',fontSize:12,fontWeight:'bold',color:'#FFF',letterSpacing:1},targetLabel:{color:'#9CA3AF',fontFamily:'monospace',fontSize:10},targetRow:{gap:8,paddingVertical:8},targetChip:{borderWidth:1,borderColor:'#64748B',borderRadius:16,paddingHorizontal:12,paddingVertical:7},targetSelected:{backgroundColor:'rgba(255,255,255,.18)',borderColor:'#FFF'},chipText:{color:'#FFF',fontSize:12},canvasTouch:{borderRadius:24,overflow:'hidden'},canvasSurface:{width:CANVAS_SIZE,height:CANVAS_SIZE,borderRadius:24,justifyContent:'center',alignItems:'center'},hintText:{fontFamily:'monospace',fontSize:10,color:'rgba(255,255,255,.7)',marginVertical:14,letterSpacing:.8},transcriptCard:{width:'100%',padding:16,borderRadius:18,marginTop:10},cardLabel:{fontFamily:'monospace',fontSize:10,color:'#A5B4FC',letterSpacing:1,marginBottom:8},transcriptInput:{color:'#FFF',fontSize:16,minHeight:58,textAlignVertical:'top'},bodyText:{color:'#FFF',fontSize:15,lineHeight:21},errorText:{color:'#FCA5A5',fontSize:14,width:'100%',marginTop:12},actions:{flexDirection:'row',gap:10,width:'100%',marginTop:14},primary:{flex:1,backgroundColor:'#FFF',borderRadius:12,padding:13,alignItems:'center'},secondary:{borderWidth:1,borderColor:'#94A3B8',borderRadius:12,padding:13,alignItems:'center',marginTop:14},buttonText:{color:'#111827',fontFamily:'monospace',fontSize:11,fontWeight:'bold'},
});
