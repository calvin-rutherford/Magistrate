import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

interface StatusRingProps {
  statusText?: string;
  statusColor?: string;
  subText?: string;
}

export const StatusRing: React.FC<StatusRingProps> = ({
  statusText = 'OPERATIONAL',
  statusColor = '#34D399',
  subText = 'All Systems Operational'
}) => {
  return (
    <View style={styles.container}>
      <View style={[styles.outerGlowRing, { borderColor: statusColor }]}>
        <LinearGradient
          colors={['rgba(255, 255, 255, 0.35)', 'rgba(52, 211, 153, 0.4)', 'rgba(52, 211, 153, 0.1)']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={[StyleSheet.absoluteFill, { borderRadius: 110, opacity: 0.9 }]}
        />
        <View style={styles.innerGlassCircle}>
          <Text style={styles.gaugeLabel}>SYSTEM STATUS</Text>
          <Text
            style={[
              styles.gaugeStatusText,
              {
                color: statusColor,
                textShadowColor: statusColor,
                textShadowOffset: { width: 0, height: 0 },
                textShadowRadius: 12
              }
            ]}
            numberOfLines={2}
          >
            {statusText}
          </Text>
          <View style={styles.subStatusRow}>
            <View style={[styles.statusDot, { backgroundColor: statusColor, shadowColor: statusColor, shadowRadius: 6, shadowOpacity: 0.8 }]} />
            <Text style={styles.gaugeSubText}>{subText}</Text>
          </View>
        </View>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
    marginVertical: 18
  },
  outerGlowRing: {
    width: 220,
    height: 220,
    borderRadius: 110,
    borderWidth: 2,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden'
  },
  innerGlassCircle: {
    width: 194,
    height: 194,
    borderRadius: 97,
    backgroundColor: 'rgba(12, 24, 38, 0.50)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.22)',
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 12,
    zIndex: 2
  },
  gaugeLabel: {
    fontSize: 10.5,
    fontWeight: '600',
    color: 'rgba(255, 255, 255, 0.65)',
    letterSpacing: 1.4,
    marginBottom: 6,
    textAlign: 'center'
  },
  gaugeStatusText: {
    fontSize: 21,
    fontWeight: 'bold',
    letterSpacing: 1.5,
    marginBottom: 8,
    textAlign: 'center'
  },
  subStatusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center'
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 6
  },
  gaugeSubText: {
    fontSize: 11,
    color: 'rgba(255, 255, 255, 0.80)',
    textAlign: 'center'
  }
});
