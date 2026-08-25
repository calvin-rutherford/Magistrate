import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { useRouter } from 'expo-router';

interface FleetMetricProps {
  runningCount?: number;
  blockedCount?: number;
  prsOpenCount?: number;
  needsYouCount?: number;
}

export function FleetMetric({
  runningCount = 1,
  blockedCount = 0,
  prsOpenCount = 2,
  needsYouCount = 0
}: FleetMetricProps) {
  const router = useRouter();

  return (
    <View style={styles.grid}>
      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={50} style={styles.card}>
          <Text style={styles.valueText}>{runningCount}</Text>
          <Text style={styles.labelText}>RUNNING</Text>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={50} style={styles.card}>
          <Text style={styles.valueText}>{blockedCount}</Text>
          <Text style={styles.labelText}>BLOCKED</Text>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={50} style={styles.card}>
          <Text style={styles.valueText}>{prsOpenCount}</Text>
          <Text style={styles.labelText}>PRS OPEN</Text>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={50} style={styles.card}>
          <Text style={styles.valueText}>{needsYouCount}</Text>
          <Text style={styles.labelText}>NEEDS YOU</Text>
        </GlassSurface>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    justifyContent: 'space-between'
  },
  cardTouch: {
    width: '48%',
    marginBottom: 8
  },
  card: {
    paddingVertical: 18,
    paddingHorizontal: 12,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    borderColor: 'rgba(255, 255, 255, 0.15)',
    borderWidth: 1
  },
  valueText: {
    fontFamily: 'monospace',
    fontSize: 26,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 4
  },
  labelText: {
    fontFamily: 'monospace',
    fontSize: 10,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.65)',
    letterSpacing: 1.2
  }
});
