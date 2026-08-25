import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Svg, Path, Circle } from 'react-native-svg';
import { GlassSurface } from './GlassSurface';

interface FleetMetricProps {
  runningCount: number;
  blockedCount: number;
  prsCount: number;
  needsYouCount: number;
}

export const FleetMetric: React.FC<FleetMetricProps> = ({
  runningCount,
  blockedCount,
  prsCount,
  needsYouCount
}) => {
  return (
    <View style={styles.container} pointerEvents="none">
      {/* RUNNING CARD (GREEN) */}
      <View style={styles.touchable}>
        <GlassSurface variant="card" style={styles.card}>
          <Text style={[
            styles.number,
            {
              color: '#34D399',
              textShadowColor: '#34D399',
              textShadowOffset: { width: 0, height: 0 },
              textShadowRadius: 10
            }
          ]}>{runningCount}</Text>
          <Text style={[styles.label, { color: '#34D399' }]}>RUNNING</Text>
          <View style={styles.iconBox}>
            <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="#34D399" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <Path d="M2 12h4l3-9 4 18 3-9h4" />
            </Svg>
          </View>
        </GlassSurface>
      </View>

      {/* BLOCKED CARD (ORANGE) */}
      <View style={styles.touchable}>
        <GlassSurface variant="card" style={styles.card}>
          <Text style={[
            styles.number,
            {
              color: '#F59E0B',
              textShadowColor: '#F59E0B',
              textShadowOffset: { width: 0, height: 0 },
              textShadowRadius: 10
            }
          ]}>{blockedCount}</Text>
          <Text style={[styles.label, { color: '#F59E0B' }]}>BLOCKED</Text>
          <View style={styles.iconBox}>
            <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="#F59E0B" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <Circle cx="12" cy="12" r="9" />
              <Path d="M12 8v4M12 16h.01" />
            </Svg>
          </View>
        </GlassSurface>
      </View>

      {/* PRS OPEN CARD (CYAN) */}
      <View style={styles.touchable}>
        <GlassSurface variant="card" style={styles.card}>
          <Text style={[
            styles.number,
            {
              color: '#38BDF8',
              textShadowColor: '#38BDF8',
              textShadowOffset: { width: 0, height: 0 },
              textShadowRadius: 10
            }
          ]}>{prsCount}</Text>
          <Text style={[styles.label, { color: '#38BDF8' }]}>PRS OPEN</Text>
          <View style={styles.iconBox}>
            <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="#38BDF8" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <Path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <Path d="M14 2v6h6" />
              <Path d="M16 13H8M16 17H8M10 9H8" />
            </Svg>
          </View>
        </GlassSurface>
      </View>

      {/* NEEDS YOU CARD (PURPLE) */}
      <View style={styles.touchable}>
        <GlassSurface variant="card" style={styles.card}>
          <Text style={[
            styles.number,
            {
              color: '#A855F7',
              textShadowColor: '#A855F7',
              textShadowOffset: { width: 0, height: 0 },
              textShadowRadius: 10
            }
          ]}>{needsYouCount}</Text>
          <Text style={[styles.label, { color: '#A855F7' }]}>NEEDS YOU</Text>
          <View style={styles.iconBox}>
            <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="#A855F7" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <Path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
              <Circle cx="12" cy="7" r="4" />
            </Svg>
          </View>
        </GlassSurface>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 8,
    marginVertical: 12
  },
  touchable: {
    flex: 1
  },
  card: {
    paddingVertical: 16,
    paddingHorizontal: 2,
    minHeight: 108,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 18
  },
  number: {
    fontFamily: 'monospace',
    fontSize: 26,
    fontWeight: 'bold',
    textAlign: 'center',
    alignSelf: 'center',
    marginBottom: 2
  },
  label: {
    fontFamily: 'monospace',
    fontSize: 8.5,
    fontWeight: 'bold',
    letterSpacing: 0.8,
    textAlign: 'center',
    alignSelf: 'center',
    marginBottom: 6
  },
  iconBox: {
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'center'
  }
});
