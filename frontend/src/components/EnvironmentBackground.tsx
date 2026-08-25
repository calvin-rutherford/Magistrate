import React, { useEffect, useState } from 'react';
import { View, StyleSheet, Animated, ImageBackground, Platform } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { getEnvironmentTheme, EnvironmentTheme, getCurrentTimePeriod } from '../services/environmentTheme';

interface EnvironmentBackgroundProps {
  children: React.ReactNode;
}

export const EnvironmentBackground: React.FC<EnvironmentBackgroundProps> = ({ children }) => {
  const [theme, setTheme] = useState<EnvironmentTheme>(getEnvironmentTheme());
  const [imgError, setImgError] = useState<boolean>(false);
  const fadeAnim = useState(() => new Animated.Value(1))[0];

  const bgImageUri = theme.sceneImageUri || 'https://images.unsplash.com/photo-1519681393784-d120267933ba?q=80&w=1600&auto=format&fit=crop';

  useEffect(() => {
    const updateTheme = () => {
      const newTheme = getEnvironmentTheme(getCurrentTimePeriod());
      Animated.sequence([
        Animated.timing(fadeAnim, { toValue: 0.7, duration: 1000, useNativeDriver: true }),
        Animated.timing(fadeAnim, { toValue: 1.0, duration: 1000, useNativeDriver: true })
      ]).start();
      setTheme(newTheme);
    };

    const interval = setInterval(updateTheme, 60000);
    return () => clearInterval(interval);
  }, []);

  const isWeb = Platform.OS === 'web';

  return (
    <View style={styles.container}>
      <Animated.View style={[
        StyleSheet.absoluteFill,
        { opacity: fadeAnim },
        isWeb ? ({ filter: 'blur(1px)', WebkitFilter: 'blur(1px)', transform: [{ scale: 1.01 }] } as any) : null
      ]}>
        {!imgError ? (
          <ImageBackground
            source={{ uri: bgImageUri }}
            style={StyleSheet.absoluteFill}
            resizeMode="cover"
            onError={() => setImgError(true)}
          >
            <LinearGradient
              colors={theme.gradientColors}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={[StyleSheet.absoluteFill, { opacity: 0.15 }]}
            />
            <View style={[styles.overlay, { backgroundColor: 'rgba(10, 16, 26, 0.12)' }]} />
          </ImageBackground>
        ) : (
          <LinearGradient
            colors={theme.gradientColors}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={StyleSheet.absoluteFill}
          />
        )}
      </Animated.View>
      <View style={styles.content}>{children}</View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0D1322'
  },
  overlay: {
    ...StyleSheet.absoluteFill
  },
  content: {
    flex: 1,
    zIndex: 2
  }
});
