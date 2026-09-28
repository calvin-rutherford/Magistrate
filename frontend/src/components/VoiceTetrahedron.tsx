import React, { useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import Svg, { Circle, Defs, G, LinearGradient, Polygon, Stop } from 'react-native-svg';
import type { VoiceState } from '../services/VoiceSessionReducer';
import { clampAudioPeak } from '../services/VoiceVisuals';
import { projectTetrahedron } from '../services/VoiceTetrahedronGeometry';

const palette = ['#8B6CFF', '#24D8FF', '#54FF87', '#FF3FD1'];

function StaticFallback({ size }: { size: number }) {
  return <Svg testID="voice-tetrahedron-fallback" width={size} height={size} viewBox="0 0 240 240" accessibilityLabel="Magistrate voice tetrahedron, motion unavailable">
    <G fill="rgba(247,248,250,0.04)" stroke="#D7DCE4" strokeWidth="1.5" strokeLinejoin="round">
      <Polygon points="120,34 43,174 120,211" /><Polygon points="120,34 197,174 120,211" /><Polygon points="43,174 197,174 120,211" />
    </G>
  </Svg>;
}

type VoiceTetrahedronProps = { size: number; state: VoiceState; amplitude: number; reducedMotion: boolean };

function VoiceTetrahedronScene({ size, state, amplitude, reducedMotion }: VoiceTetrahedronProps) {
  const [phase, setPhase] = useState(0.66);
  const animated = !reducedMotion;
  useEffect(() => {
    if (!animated) return;
    // 20fps is intentionally capped: four projected faces need no display-rate
    // loop and this remains inexpensive on older supported iPhones.
    const timer = setInterval(() => {
      const speed = state === 'LISTENING' ? 0.34 : state === 'SPEAKING' ? 0.28 : 0.16;
      setPhase(current => current + speed * 0.05);
    }, 50);
    return () => clearInterval(timer);
  }, [animated, state]);
  const energy = state === 'LISTENING' ? clampAudioPeak(amplitude) : 0;
  const faces = useMemo(() => {
    try {
      const projected = projectTetrahedron(phase, energy, size);
      if (projected.some(face => !face.points || !Number.isFinite(face.depth))) throw new Error('invalid projection');
      return projected;
    } catch {
      return null;
    }
  }, [energy, phase, size]);
  if (!faces) return <StaticFallback size={size} />;

  const spectral = state === 'SPEAKING' || state === 'THINKING';
  const rippleCount = reducedMotion ? 1 : state === 'SPEAKING' ? 4 : state === 'THINKING' ? 2 : 0;
  const bob = reducedMotion ? 0 : Math.sin(phase * 1.25) * (state === 'LISTENING' ? 2 + energy * 5 : 3);
  return <View testID="voice-tetrahedron-renderer" style={[styles.root, { width: size, height: size, transform: [{ translateY: bob }] }]}>
    <Svg testID="voice-active-mark" width={size} height={size} viewBox={`0 0 ${size} ${size}`} accessibilityLabel={`Three dimensional Magistrate tetrahedron. ${state.toLowerCase()}.`}>
      <Defs>
        <LinearGradient id="voiceSpectralFace" x1="0%" y1="0%" x2="100%" y2="100%">
          <Stop offset="0%" stopColor="#8B6CFF" /><Stop offset="48%" stopColor="#24D8FF" /><Stop offset="100%" stopColor="#54FF87" />
        </LinearGradient>
      </Defs>
      {Array.from({ length: rippleCount }, (_, index) => {
        const progress = reducedMotion ? 0.5 : (phase * 0.32 + index / Math.max(1, rippleCount)) % 1;
        return <Circle key={index} testID={`voice-spectral-ripple-${index}`} cx={size / 2} cy={size / 2} r={size * (0.25 + progress * 0.22)} fill="none" stroke={palette[index % palette.length]} strokeWidth={0.7} opacity={(1 - progress) * (state === 'THINKING' ? 0.16 : 0.3)} />;
      })}
      {faces.map(face => <Polygon key={face.index} testID={`voice-tetrahedron-face-${face.index}`} points={face.points}
        fill={spectral ? 'url(#voiceSpectralFace)' : '#F7F8FA'} fillOpacity={spectral ? 0.025 + face.light * 0.09 : 0.018 + face.light * 0.055}
        stroke={spectral ? palette[face.index] : '#D7DCE4'} strokeOpacity={spectral ? (state === 'THINKING' ? 0.48 : 0.78) : 0.38 + face.light * 0.42}
        strokeWidth={state === 'LISTENING' ? 1.25 + energy * 1.1 : 1.25} strokeLinejoin="round" />)}
    </Svg>
  </View>;
}

class VoiceRendererBoundary extends React.Component<React.PropsWithChildren<{ size: number }>, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { /* Deliberately fall back without exposing renderer internals. */ }
  render() { return this.state.failed ? <StaticFallback size={this.props.size} /> : this.props.children; }
}

export function VoiceTetrahedron(props: VoiceTetrahedronProps) {
  return <VoiceRendererBoundary size={props.size}><VoiceTetrahedronScene {...props} /></VoiceRendererBoundary>;
}

const styles = StyleSheet.create({ root: { alignItems: 'center', justifyContent: 'center' } });
