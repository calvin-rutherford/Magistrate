import React from 'react';
import { AccessibilityInfo, Animated, AppState, ImageBackground, PanResponder, StyleSheet, View } from 'react-native';
import { getEnvironmentTheme, subscribeActiveBackground, WeatherKind } from '../services/environmentTheme';
import { useChatColorScheme } from '../services/ChatPreferences';
import { getWeather, WEATHER_REFRESH_MS } from '../services/weather';
import { BottomControls } from './BottomControls';
import { usePathname, useRouter } from 'expo-router';

/**
 * The two minimal environments are a deliberate flat canvas, not a dimmed
 * scene, so their scrim colour is fixed by the choice rather than by the
 * current theme - selecting Minimal Light must not stay black in dark mode.
 */
function minimalScrim(sceneKey: string): string | null {
  if (sceneKey === 'minimal-dark') return '#05070A';
  if (sceneKey === 'minimal-light') return '#F7F8FA';
  return null;
}

export function EnvironmentBackground({ children, hideBottomControls = false, voiceMode = false, preserveCanvas = false }: { children: React.ReactNode; hideBottomControls?: boolean; voiceMode?: boolean; preserveCanvas?: boolean }) {
  const router = useRouter(); const pathname = usePathname();
  const [, setBackgroundRevision] = React.useState(0);
  const [weather, setWeather] = React.useState<WeatherKind>('clear');
  const [clock, setClock] = React.useState(() => new Date());
  const [reducedMotion, setReducedMotion] = React.useState(false);
  const [highContrast, setHighContrast] = React.useState(false);
  // Held in state rather than a ref so the value is created exactly once and is
  // never read from a ref during render (react-hooks/refs).
  const [fade] = React.useState(() => new Animated.Value(1));
  // Keep the environment on the same palette as the chat surfaces. In
  // particular, system mode must react to an OS scheme change instead of
  // retaining the palette from the previous render.
  const dark = useChatColorScheme() === 'dark';
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
  }, [theme.timePeriod, theme.weather, dark, reducedMotion, fade]);
  // Created once on mount, as before, so the back-swipe keeps the same responder
  // identity for the lifetime of the screen.
  const [panResponder] = React.useState(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, gesture) => pathname !== '/chat' && gesture.dx > 50 && Math.abs(gesture.dy) < 40,
    onPanResponderRelease: (_, gesture) => { if (gesture.dx > 60) router.back(); },
  }));
  return <View testID="environment-background" style={styles.container} {...panResponder.panHandlers}>
    <Animated.View style={[styles.container, { opacity: fade }]}>
      <ImageBackground source={theme.sceneImage} style={styles.bgImage} resizeMode="cover">
        {!preserveCanvas && !voiceMode ? <WeatherOverlay kind={theme.weather} dark={dark} /> : null}
        {!preserveCanvas && !voiceMode ? <View testID="environment-dim-overlay" style={[styles.darkDimOverlay, { backgroundColor: minimalScrim(theme.sceneKey) || (dark ? '#07101D' : '#FFFFFF'), opacity: minimalScrim(theme.sceneKey) ? theme.dimOpacity : highContrast ? (dark ? 0.78 : 0.28) : dark ? theme.dimOpacity : Math.min(theme.dimOpacity, 0.16) }]} /> : null}
        {/* Voice is a dedicated ceremonial canvas, not the selected chat scene
            dimmed: a fully opaque near-black layer replaces the scene/weather/dim
            stack instead of stacking on top of it. */}
        {voiceMode ? <View testID="voice-mode-canvas" pointerEvents="none" style={styles.voiceModeTreatment} /> : null}
        <View style={styles.contentArea}>{children}</View>
        {!hideBottomControls && pathname !== '/chat' ? <BottomControls /> : null}
      </ImageBackground>
    </Animated.View>
  </View>;
}
function WeatherOverlay({ kind, dark }: { kind: WeatherKind; dark: boolean }) {
  if (kind === 'clear') return null;
  if (kind === 'rain' || kind === 'storm') return <View pointerEvents="none" style={[styles.weather, styles.clouds, { opacity: dark ? 1 : 0.55 }, kind === 'storm' && styles.storm]}>{Array.from({ length: 14 }, (_, i) => <View key={i} style={[styles.rain, { left: `${(i * 17) % 100}%`, top: `${(i * 29) % 94}%` }]} />)}</View>;
  if (kind === 'snow') return <View pointerEvents="none" style={[styles.weather, styles.clouds]}>{Array.from({ length: 16 }, (_, i) => <View key={i} style={[styles.snow, { left: `${(i * 23) % 96}%`, top: `${(i * 31) % 92}%`, opacity: 0.15 + (i % 3) * 0.06 }]} />)}</View>;
  return <View pointerEvents="none" style={[styles.weather, styles.clouds]} />;
}
const styles = StyleSheet.create({
  container: { flex: 1, minHeight: 0, backgroundColor: '#0D1322' }, bgImage: { flex: 1, minHeight: 0 }, contentArea: { flex: 1, minHeight: 0 },
  darkDimOverlay: { ...StyleSheet.absoluteFill, backgroundColor: '#07101D' },
  // Voice Mode is a dedicated ceremonial canvas: pure black regardless of the
  // captain's selected chat scene, matching the approved brand reference
  // rather than a dimmed version of whatever background chat is using.
  voiceModeTreatment: { ...StyleSheet.absoluteFill, backgroundColor: '#000000' },
  weather: { ...StyleSheet.absoluteFill },
  clouds: { backgroundColor: 'rgba(35, 45, 58, 0.18)' }, storm: { backgroundColor: 'rgba(18, 20, 38, 0.38)' },
  rain: { position: 'absolute', width: 1, height: 34, backgroundColor: 'rgba(180, 211, 225, 0.16)', transform: [{ rotate: '12deg' }] },
  snow: { position: 'absolute', width: 5, height: 5, borderRadius: 3, backgroundColor: '#EAF4F6' },
});
