import React from 'react';
import { PanResponder, StyleSheet, View } from 'react-native';
import { usePathname, useRouter } from 'expo-router';
import { useChatColorScheme } from '../services/ChatPreferences';
import { BottomControls } from './BottomControls';

/**
 * Product canvas shared by customer routes.
 *
 * Magistrate is intentionally monochrome at rest. Historical weather scenes
 * remain readable as legacy preferences, but customer UI never turns them into
 * wallpaper or starts cosmetic network work. Spectral colour belongs to live
 * activity inside the child surfaces.
 */
export function EnvironmentBackground({ children, hideBottomControls = false, voiceMode = false, preserveCanvas = false }: {
  children: React.ReactNode;
  hideBottomControls?: boolean;
  voiceMode?: boolean;
  preserveCanvas?: boolean;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const dark = useChatColorScheme() === 'dark';
  const [panResponder] = React.useState(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, gesture) => pathname !== '/chat' && gesture.dx > 50 && Math.abs(gesture.dy) < 40,
    onPanResponderRelease: (_, gesture) => { if (gesture.dx > 60) router.back(); },
  }));

  const showRestingTreatment = !preserveCanvas && !voiceMode;

  return <View
    testID="environment-background"
    style={[styles.container, { backgroundColor: voiceMode ? '#000000' : dark ? '#0B0C0E' : '#F7F7F8' }]}
    {...panResponder.panHandlers}
  >
    {showRestingTreatment ? <View testID="environment-flat-canvas" pointerEvents="none" style={styles.restingTreatment} /> : null}
    {voiceMode ? <View testID="voice-mode-canvas" pointerEvents="none" style={styles.voiceModeTreatment} /> : null}
    <View style={styles.contentArea}>{children}</View>
    {!hideBottomControls && pathname !== '/chat' ? <BottomControls /> : null}
  </View>;
}

const styles = StyleSheet.create({
  container: { flex: 1, minHeight: 0 },
  contentArea: { flex: 1, minHeight: 0 },
  restingTreatment: { ...StyleSheet.absoluteFill, backgroundColor: 'transparent' },
  voiceModeTreatment: { ...StyleSheet.absoluteFill, backgroundColor: '#000000' },
});
