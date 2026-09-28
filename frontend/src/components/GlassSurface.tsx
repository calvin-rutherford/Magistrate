import React from 'react';
import { View, StyleSheet, StyleProp, ViewStyle } from 'react-native';
import { useChatColorScheme } from '../services/ChatPreferences';

interface GlassSurfaceProps {
  children?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  contentStyle?: StyleProp<ViewStyle>;
  variant?: 'surface' | 'card' | 'control' | 'alert' | 'circle';
  /** Retained for source compatibility; restrained surfaces do not blur. */
  intensity?: number;
}

/** Tonal native surface. The historical name is retained to avoid churn. */
export const GlassSurface: React.FC<GlassSurfaceProps> = ({
  children, style, contentStyle, variant = 'card', intensity: _intensity,
}) => {
  const dark = useChatColorScheme() === 'dark';
  const borderRadius = variant === 'circle' || variant === 'control' ? 9999 : variant === 'surface' ? 20 : 14;
  return <View style={[
    styles.container,
    {
      borderRadius,
      backgroundColor: dark ? (variant === 'surface' ? '#111214' : '#1A1B1E') : (variant === 'surface' ? '#FFFFFF' : '#F1F2F4'),
      borderColor: dark ? '#303238' : '#D9DCE1',
    },
    style,
  ]}><View style={contentStyle}>{children}</View></View>;
};

const styles = StyleSheet.create({
  container: { overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth, elevation: 0 },
});
