import React from 'react';
import { View, StyleSheet, TouchableOpacity, Text } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { useRouter } from 'expo-router';

export function BottomControls() {
  const router = useRouter();

  return (
    <View style={styles.fixedContainer} pointerEvents="box-none">
      <GlassSurface variant="control" intensity={75} style={styles.barSurface}>
        <View style={styles.controlsRow}>
          {/* CHAT TERMINAL BUTTON */}
          <TouchableOpacity
            style={styles.iconBtn}
            onPress={() => router.push('/chat' as any)}
            activeOpacity={0.7}
          >
            <Text style={styles.iconText}>💬</Text>
          </TouchableOpacity>

          {/* CENTRAL VOICE MODE BUTTON */}
          <TouchableOpacity
            style={styles.centerMicBtn}
            onPress={() => router.push('/voice' as any)}
            activeOpacity={0.8}
          >
            <Text style={styles.centerMicIcon}>🎙️</Text>
          </TouchableOpacity>

          {/* GESTURE / AR GLASSES BUTTON */}
          <TouchableOpacity
            style={styles.iconBtn}
            onPress={() => router.push('/gesture' as any)}
            activeOpacity={0.7}
          >
            <Text style={styles.iconText}>👓</Text>
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
  iconText: {
    fontSize: 18
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
  },
  centerMicIcon: {
    fontSize: 22
  }
});
