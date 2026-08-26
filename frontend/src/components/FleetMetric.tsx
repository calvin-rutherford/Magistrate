import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { useRouter } from 'expo-router';

interface FleetMetricProps {
  runningCount?: number;
  idleCount?: number;
  blockedCount?: number;
  prsOpenCount?: number;
  needsYouCount?: number;
}

export function FleetMetric({
  runningCount,
  idleCount,
  blockedCount,
  prsOpenCount,
  needsYouCount
}: FleetMetricProps) {
  const router = useRouter();

  return (
    <View style={styles.grid}>
      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={10} style={styles.card}>
          <View style={styles.cardInner}>
            <Text style={styles.valueText}>{runningCount ?? '—'}</Text>
            <Text style={styles.labelText}>RUNNING</Text>
          </View>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={10} style={styles.card}>
          <View style={styles.cardInner}>
            <Text style={styles.valueText}>{idleCount ?? '—'}</Text>
            <Text style={styles.labelText}>IDLE</Text>
          </View>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={10} style={styles.card}>
          <View style={styles.cardInner}>
            <Text style={styles.valueText}>{blockedCount ?? '—'}</Text>
            <Text style={styles.labelText}>BLOCKED</Text>
          </View>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={10} style={styles.card}>
          <View style={styles.cardInner}>
            <Text style={styles.valueText}>{prsOpenCount ?? '—'}</Text>
            <Text style={styles.labelText}>PRS OPEN</Text>
          </View>
        </GlassSurface>
      </TouchableOpacity>

      <TouchableOpacity style={styles.cardTouch} onPress={() => router.push('/agents' as any)}>
        <GlassSurface variant="card" intensity={10} style={styles.card}>
          <View style={styles.cardInner}>
            <Text style={styles.valueText}>{needsYouCount ?? '—'}</Text>
            <Text style={styles.labelText}>NEEDS YOU</Text>
          </View>
        </GlassSurface>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: 'column',
    gap: 10
  },
  cardTouch: {
    width: '100%',
    marginBottom: 8
  },
  cardInner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16
  },
  card: {
    flexDirection: 'row',
    paddingVertical: 16,
    paddingHorizontal: 24,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'space-between',
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    width: '100%',
    backgroundColor: 'rgba(255, 255, 255, 0.03)'
  },
  valueText: {
    fontFamily: 'monospace',
    fontSize: 26,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 0
  },
  labelText: {
    fontFamily: 'monospace',
    fontSize: 10,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.65)',
    letterSpacing: 1.2,
    textAlign: 'center'
  }
});
