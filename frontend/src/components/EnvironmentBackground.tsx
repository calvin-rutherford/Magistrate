import React from 'react';
import { AccessibilityInfo, Animated, AppState, ImageBackground, PanResponder, StyleSheet, View } from 'react-native';
import { getEnvironmentTheme, subscribeActiveBackground, WeatherKind } from '../services/environmentTheme';
import { getWeather, WEATHER_REFRESH_MS } from '../services/weather';
import { BottomControls } from './BottomControls';
import { usePathname, useRouter } from 'expo-router';

export function EnvironmentBackground({ children, hideBottomControls = false, voiceMode = false }: { children: React.ReactNode; hideBottomControls?: boolean; voiceMode?: boolean }) {
  const router = useRouter(); const pathname = usePathname();
  const [, setBackgroundRevision] = React.useState(0);
  const [weather, setWeather] = React.useState<WeatherKind>('clear');
  const [clock, setClock] = React.useState(() => new Date());
  const [reducedMotion, setReducedMotion] = React.useState(false);
  const [highContrast, setHighContrast] = React.useState(false);
  const fade = React.useRef(new Animated.Value(1)).current;
  const theme = getEnvironmentTheme(weather, clock);
  const refresh = React.useCallback(async () => { setClock(new Date()); setWeather((await getWeather()).kind); }, []);
  React.useEffect(() => subscribeActiveBackground(() => setBackgroundRevision(revision => revision + 1)), []);
  React.useEffect(() => {
    refresh(); AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion); AccessibilityInfo.isHighTextContrastEnabled?.().then(setHighContrast);
    const timer = setInterval(refresh, WEATHER_REFRESH_MS);
    const appState = AppState.addEventListener('change', state => { if (state === 'active') refresh(); });
    const motion = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion);
    const contrast = AccessibilityInfo.addEventListener('highTextContrastChanged', setHighContrast);
    return () => { clearInterval(timer); appState.remove(); motion.remove(); contrast.remove(); };
  }, [refresh]);
  React.useEffect(() => {
    if (reducedMotion) { fade.setValue(1); return; }
    fade.setValue(0.72); Animated.timing(fade, { toValue: 1, duration: 4000, useNativeDriver: true }).start();
  }, [theme.timePeriod, theme.weather, reducedMotion, fade]);
  const panResponder = React.useRef(PanResponder.create({
    onMoveShouldSetPanResponder: (_, gesture) => pathname !== '/chat' && gesture.dx > 50 && Math.abs(gesture.dy) < 40,
    onPanResponderRelease: (_, gesture) => { if (gesture.dx > 60) router.back(); },
  })).current;
  return <View style={styles.container} {...panResponder.panHandlers}>
    <Animated.View style={[styles.container, { opacity: fade }]}>
      <ImageBackground source={theme.sceneImage} style={styles.bgImage} resizeMode="cover">
        <WeatherOverlay kind={theme.weather} />
        <View style={[styles.darkDimOverlay, { opacity: highContrast ? 0.78 : voiceMode ? 0.58 : theme.dimOpacity }]} />
        {voiceMode ? <View pointerEvents="none" style={styles.voiceModeTreatment} /> : null}
        <View style={styles.contentArea}>{children}</View>
        {!hideBottomControls && pathname !== '/chat' ? <BottomControls /> : null}
      </ImageBackground>
    </Animated.View>
  </View>;
}
function WeatherOverlay({ kind }: { kind: WeatherKind }) {
  if (kind === 'clear') return null;
  if (kind === 'rain' || kind === 'storm') return <View pointerEvents="none" style={[styles.weather, styles.clouds, kind === 'storm' && styles.storm]}>{Array.from({ length: 14 }, (_, i) => <View key={i} style={[styles.rain, { left: `${(i * 17) % 100}%`, top: `${(i * 29) % 94}%` }]} />)}</View>;
  if (kind === 'snow') return <View pointerEvents="none" style={[styles.weather, styles.clouds]}>{Array.from({ length: 16 }, (_, i) => <View key={i} style={[styles.snow, { left: `${(i * 23) % 96}%`, top: `${(i * 31) % 92}%`, opacity: 0.15 + (i % 3) * 0.06 }]} />)}</View>;
  return <View pointerEvents="none" style={[styles.weather, styles.clouds]} />;
}
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0D1322' }, bgImage: { flex: 1, width: '100%', height: '100%' }, contentArea: { flex: 1 },
  darkDimOverlay: { ...StyleSheet.absoluteFill, backgroundColor: '#07101D' },
  // Voice keeps the selected environment visible underneath a near-obsidian
  // treatment, preserving the dark immersive contract without breaking custom backgrounds.
  voiceModeTreatment: { ...StyleSheet.absoluteFill, backgroundColor: '#05070A', opacity: 0.18 },
  weather: { ...StyleSheet.absoluteFill },
  clouds: { backgroundColor: 'rgba(35, 45, 58, 0.18)' }, storm: { backgroundColor: 'rgba(18, 20, 38, 0.38)' },
  rain: { position: 'absolute', width: 1, height: 34, backgroundColor: 'rgba(180, 211, 225, 0.16)', transform: [{ rotate: '12deg' }] },
  snow: { position: 'absolute', width: 5, height: 5, borderRadius: 3, backgroundColor: '#EAF4F6' },
});
