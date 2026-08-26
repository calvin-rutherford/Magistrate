import React from 'react';
import { View, StyleSheet, StyleProp, ViewStyle, Platform } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { GlassTokens } from '../theme/glass';

let BlurViewComponent: any = View;
try {
  const ExpoBlur = require('expo-blur');
  if (ExpoBlur && ExpoBlur.BlurView) {
    BlurViewComponent = ExpoBlur.BlurView;
  }
} catch (e) {
  BlurViewComponent = View;
}

interface GlassSurfaceProps {
  children?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  contentStyle?: StyleProp<ViewStyle>;
  variant?: 'surface' | 'card' | 'control' | 'alert' | 'circle';
  intensity?: number;
}

export const GlassSurface: React.FC<GlassSurfaceProps> = ({
  children,
  style,
  contentStyle,
  variant = 'card',
  intensity = 15
}) => {
  const borderRadius =
    variant === 'circle' || variant === 'control'
      ? 9999
      : variant === 'surface'
      ? 24
      : 18;

  const isWeb = Platform.OS === 'web';

  return (
    <View style={[
      styles.container,
      { borderRadius },
      isWeb ? ({ backdropFilter: 'blur(' + intensity + 'px)', WebkitBackdropFilter: 'blur(' + intensity + 'px)' } as any) : null,
      style
    ]}>
      {!isWeb && (
        <BlurViewComponent intensity={intensity} tint="dark" style={[StyleSheet.absoluteFill, { borderRadius }]} />
      )}
      <LinearGradient
        colors={['rgba(255, 255, 255, 0.15)', 'rgba(255, 255, 255, 0.01)', 'rgba(255, 255, 255, 0.05)']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[StyleSheet.absoluteFill, { borderRadius, opacity: 0.85 }]}
      />
      <View style={[styles.content, contentStyle]}>{children}</View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    overflow: 'hidden',
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.12)',
    elevation: 0
  },
  content: {
    position: 'relative',
    zIndex: 1
  }
});
