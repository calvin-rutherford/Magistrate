import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { Svg, Path } from 'react-native-svg';
import { GlassSurface } from './GlassSurface';
import { GlassTokens } from '../theme/glass';

interface AttentionCardProps {
  alertTitle?: string;
  alertSub?: string;
  onPress?: () => void;
}

export const AttentionCard: React.FC<AttentionCardProps> = ({
  alertTitle = 'payment-gateway is blocked',
  alertSub = 'Tap to review',
  onPress
}) => {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.8}>
      <GlassSurface variant="card" style={styles.card}>
        <View style={styles.iconBox}>
          <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="#F59E0B" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <Path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <Path d="M12 9v4M12 17h.01" />
          </Svg>
        </View>
        <View style={styles.textContainer}>
          <Text style={styles.title}>{alertTitle}</Text>
          <Text style={styles.sub}>{alertSub}</Text>
        </View>
        <Text style={styles.chevron}>›</Text>
      </GlassSurface>
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  card: {
    padding: 14,
    flexDirection: 'row',
    alignItems: 'center',
    marginVertical: 4,
    borderRadius: 16
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: 'rgba(245, 158, 11, 0.12)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12
  },
  textContainer: {
    flex: 1
  },
  title: {
    color: '#F59E0B',
    fontWeight: 'bold',
    fontSize: 14
  },
  sub: {
    color: GlassTokens.colors.textSecondary,
    fontSize: 12,
    marginTop: 2
  },
  chevron: {
    color: 'rgba(255, 255, 255, 0.5)',
    fontSize: 18,
    fontWeight: 'bold'
  }
});
