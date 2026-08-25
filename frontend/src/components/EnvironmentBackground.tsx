import React from 'react';
import { View, StyleSheet, ImageBackground, PanResponder } from 'react-native';
import { getEnvironmentTheme } from '../services/environmentTheme';
import { BottomControls } from './BottomControls';
import { useRouter, usePathname } from 'expo-router';

interface EnvironmentBackgroundProps {
  children: React.ReactNode;
}

export function EnvironmentBackground({ children }: EnvironmentBackgroundProps) {
  const router = useRouter();
  const pathname = usePathname();
  const theme = getEnvironmentTheme();

  // Right-Swipe Back Gesture Handler (Active on non-chat screens)
  const panResponder = React.useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (evt, gestureState) => {
        return pathname !== '/chat' && gestureState.dx > 50 && Math.abs(gestureState.dy) < 40;
      },
      onPanResponderRelease: (evt, gestureState) => {
        if (gestureState.dx > 60) {
          router.back();
        }
      }
    })
  ).current;

  return (
    <View style={styles.container} {...panResponder.panHandlers}>
      <ImageBackground
        source={{ uri: theme.sceneImageUri }}
        style={styles.bgImage}
        resizeMode="cover"
      >
        <View style={styles.darkDimOverlay} />
        <View style={styles.contentArea}>
          {children}
        </View>

        {pathname !== '/chat' ? <BottomControls /> : null}
      </ImageBackground>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  bgImage: { flex: 1, width: '100%', height: '100%' },
  darkDimOverlay: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'rgba(10, 15, 26, 0.45)'
  },
  contentArea: { flex: 1 }
});
