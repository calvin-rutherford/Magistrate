import React, { useEffect, useState } from 'react';
import { AccessibilityInfo, View, Text, StyleSheet, TouchableOpacity, Dimensions } from 'react-native';
import Svg, { Polygon, Line, Circle } from 'react-native-svg';
import { EnvironmentBackground } from '../src/components/EnvironmentBackground';
import { GlassSurface } from '../src/components/GlassSurface';
import { voiceInputAdapter } from '../src/input/VoiceInputAdapter';
import { ttsService } from '../src/services/TextToSpeechService';
import { voiceStateMachine, VoiceState } from '../src/services/VoiceSessionStateMachine';
import { useRouter } from 'expo-router';

const { width } = Dimensions.get('window');
const CANVAS_SIZE = Math.min(width - 40, 320);

export default function VoiceScreen() {
  const router = useRouter();
  const [voiceState, setVoiceState] = useState<VoiceState>('IDLE');
  const [micLevel, setMicLevel] = useState<number>(0);
  const [rotation, setRotation] = useState<number>(0);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const unsub = voiceStateMachine.subscribe((st) => setVoiceState(st));
    AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion);
    const motion = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion);
    startRecordingSession();

    return () => {
      unsub();
      motion.remove();
      voiceInputAdapter.stopRecording();
    };
  }, []);

  useEffect(() => {
    if (reducedMotion) { setRotation(0); return; }
    const interval = setInterval(() => setRotation(prev => (prev + 0.05) % (Math.PI * 2)), 30);
    return () => clearInterval(interval);
  }, [reducedMotion]);

  const startRecordingSession = async () => {
    if (ttsService.isSpeaking()) {
      ttsService.stop();
    }
    voiceStateMachine.setState('LISTENING');
    await voiceInputAdapter.startRecording((lvl) => {
      setMicLevel(lvl);
    });
  };

  const toggleRecording = async () => {
    if (ttsService.isSpeaking()) {
      ttsService.stop();
      startRecordingSession();
    } else if (voiceState === 'LISTENING') {
      voiceStateMachine.setState('TRANSCRIBING');
      const payload = await voiceInputAdapter.stopRecording();
      if (payload.text) {
        voiceStateMachine.setState('THINKING');
        router.push('/chat' as any);
      } else {
        voiceStateMachine.setState('IDLE');
      }
    } else {
      startRecordingSession();
    }
  };

  // 3D Tetrahedron projection geometry with audio wave displacement
  const cx = CANVAS_SIZE / 2;
  const cy = CANVAS_SIZE / 2;
  const ripple = reducedMotion ? 0 : micLevel;
  const r = 85 + ripple * 28;

  // 4 Vertices of Tetrahedron projected into 2D canvas space
  const v0 = { x: cx + r * Math.cos(rotation), y: cy - r * 0.8 };
  const v1 = { x: cx - r * Math.cos(rotation + 1.05), y: cy + r * 0.7 };
  const v2 = { x: cx + r * Math.cos(rotation + 2.1), y: cy + r * 0.6 };
  const v3 = { x: cx + (r * 0.3) * Math.sin(rotation * 2), y: cy + (r * 0.2) * Math.cos(rotation) };

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>VOICE MODE</Text>

        <View style={{ width: 36 }} />
      </View>

      <View style={styles.container}>
        <GlassSurface variant="card" style={styles.statusBox}>
          <Text style={styles.statusText}>
            {voiceState === 'LISTENING' ? 'LISTENING 🎙️' : voiceState === 'SPEAKING' ? 'SPEAKING ALOUD 🔊' : voiceState === 'THINKING' ? 'THINKING...' : 'TRANSCRIBING...'}
          </Text>
        </GlassSurface>

        {/* 3D TETRAHEDRON WAVING ANIMATION CANVAS */}
        <TouchableOpacity onPress={toggleRecording} activeOpacity={0.9} style={styles.canvasTouch} accessibilityRole="button" accessibilityLabel={`Voice mode: ${voiceState.toLowerCase()}`} accessibilityHint="Starts or stops voice transmission">
          <GlassSurface variant="surface" intensity={40} style={styles.canvasSurface}>
            <Svg width={CANVAS_SIZE} height={CANVAS_SIZE}>
              {ripple > 0.02 && [0, 1, 2].map(index => <Circle key={index} cx={cx} cy={cy} r={92 + index * 18 + ripple * 18} fill="none" stroke="#72F5B1" strokeWidth={1 + ripple * 2} opacity={Math.max(0, ripple * (0.46 - index * 0.1))} />)}
              {/* Triangular Faces */}
              <Polygon
                points={`${v0.x},${v0.y} ${v1.x},${v1.y} ${v2.x},${v2.y}`}
                fill="rgba(255, 255, 255, 0.08)"
                stroke="#FFFFFF"
                strokeWidth="1.5"
              />
              <Polygon
                points={`${v0.x},${v0.y} ${v1.x},${v1.y} ${v3.x},${v3.y}`}
                fill="rgba(255, 255, 255, 0.04)"
                stroke="#FFFFFF"
                strokeWidth="1.2"
              />
              <Polygon
                points={`${v0.x},${v0.y} ${v2.x},${v2.y} ${v3.x},${v3.y}`}
                fill="rgba(255, 255, 255, 0.06)"
                stroke="#FFFFFF"
                strokeWidth="1.2"
              />

              {/* Connecting Edges */}
              <Line x1={v1.x} y1={v1.y} x2={v2.x} y2={v2.y} stroke="#FFFFFF" strokeWidth="1.5" />
              <Line x1={v1.x} y1={v1.y} x2={v3.x} y2={v3.y} stroke="rgba(255, 255, 255, 0.5)" strokeDasharray="4,4" />
              <Line x1={v2.x} y1={v2.y} x2={v3.x} y2={v3.y} stroke="rgba(255, 255, 255, 0.5)" strokeDasharray="4,4" />

              {/* Audio Reactive Vertices */}
              <Circle cx={v0.x} cy={v0.y} r={4 + ripple * 6} fill={reducedMotion && voiceState === 'LISTENING' ? '#72F5B1' : '#FFFFFF'} />
              <Circle cx={v1.x} cy={v1.y} r={3 + ripple * 5} fill="#FFFFFF" />
              <Circle cx={v2.x} cy={v2.y} r={3 + ripple * 5} fill="#FFFFFF" />
              <Circle cx={v3.x} cy={v3.y} r={3 + ripple * 4} fill="rgba(255, 255, 255, 0.7)" />
            </Svg>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.hintText}>
          {voiceState === 'SPEAKING' ? 'TAP TO BARGE IN' : 'TAP TETRAHEDRON TO TRANSMIT'}
        </Text>
      </View>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 20 },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 12,
    marginBottom: 6
  },
  headerTitle: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  statusBox: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 20, marginBottom: 24 },
  statusText: { fontFamily: 'monospace', fontSize: 13, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.2 },
  canvasTouch: { borderRadius: 24, overflow: 'hidden' },
  canvasSurface: { width: CANVAS_SIZE, height: CANVAS_SIZE, borderRadius: 24, justifyContent: 'center', alignItems: 'center' },
  hintText: { fontFamily: 'monospace', fontSize: 11, color: 'rgba(255, 255, 255, 0.6)', marginTop: 24, letterSpacing: 1 }
});
