import React from 'react';
import { View, TouchableOpacity, StyleSheet } from 'react-native';
import { Svg, Path } from 'react-native-svg';
import { GlassSurface } from './GlassSurface';
import { useRouter, usePathname } from 'expo-router';

export const BottomControls: React.FC = () => {
  const router = useRouter();
  const pathname = usePathname();

  return (
    <View testID="floating-bottom-controls" style={styles.fixedContainer} pointerEvents="box-none">
      <GlassSurface variant="card" style={styles.barSurface}>
        <View style={styles.controlsRow}>
          {/* CHAT WIREFRAME BUTTON */}
          <TouchableOpacity
            style={styles.iconBtn}
            onPress={() => router.push('/chat' as any)}
            activeOpacity={0.7}
          >
            <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={pathname === '/chat' ? "#72F5B1" : "#FFFFFF"} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <Path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </Svg>
          </TouchableOpacity>

          {/* CENTRAL VOICE MODE BUTTON */}
          <TouchableOpacity
            style={styles.centerMicBtn}
            onPress={() => router.push('/voice' as any)}
            activeOpacity={0.8}
          >
            <Svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke={pathname === '/voice' ? "#72F5B1" : "#FFFFFF"} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <Path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
              <Path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <Path d="M12 19v3" />
              <Path d="M8 22h8" />
            </Svg>
          </TouchableOpacity>

          {/* GESTURE / AR GLASSES BUTTON */}
          <TouchableOpacity
            style={styles.iconBtn}
            onPress={() => router.push('/gesture' as any)}
            activeOpacity={0.7}
          >
            <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={pathname === '/gesture' ? "#72F5B1" : "#FFFFFF"} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <Path d="M18 11V6a2 2 0 0 0-4 0v5" />
              <Path d="M14 10V4a2 2 0 0 0-4 0v6" />
              <Path d="M10 10.5V6a2 2 0 0 0-4 0v9" />
              <Path d="M18 11a2 2 0 0 1 4 0v3c0 5-4 9-9 9h-1c-5 0-9-4-9-9v-2a2 2 0 0 1 4 0" />
            </Svg>
          </TouchableOpacity>
        </View>
      </GlassSurface>
    </View>
  );
}

const styles = StyleSheet.create({
  fixedContainer: {
    position: 'absolute',
    bottom: 24,
    left: 0,
    right: 0,
    alignItems: 'center',
    zIndex: 999
  },
  barSurface: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 30,
    borderColor: 'rgba(255, 255, 255, 0.2)',
    borderWidth: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 8
  },
  controlsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 20
  },
  iconBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    justifyContent: 'center',
    alignItems: 'center'
  },
  centerMicBtn: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
    borderWidth: 1.5,
    borderColor: '#FFFFFF',
    justifyContent: 'center',
    alignItems: 'center'
  }
});
