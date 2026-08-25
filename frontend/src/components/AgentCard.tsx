import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { GlassTokens } from '../theme/glass';

interface AgentCardProps {
  agentName?: string;
  harnessStatus?: string;
  elapsedTime?: string;
  onPress?: () => void;
}

export const AgentCard: React.FC<AgentCardProps> = ({
  agentName = 'auth-service',
  harnessStatus = 'Codex • Running',
  elapsedTime = '42m',
  onPress
}) => {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.8}>
      <GlassSurface variant="card" style={styles.card}>
        <View style={styles.leftRow}>
          <View style={styles.greenDot} />
          <View style={styles.iconCircle}>
            <Text style={styles.iconText}>{'</>'}</Text>
          </View>
        </View>
        <View style={styles.textContainer}>
          <Text style={styles.title}>{agentName}</Text>
          <Text style={styles.sub}>{harnessStatus}</Text>
        </View>
        <Text style={styles.timeTag}>{elapsedTime}</Text>
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
  leftRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginRight: 12
  },
  greenDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#34D399',
    marginRight: 8
  },
  iconCircle: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: 'rgba(52, 211, 153, 0.15)',
    justifyContent: 'center',
    alignItems: 'center'
  },
  iconText: {
    color: '#34D399',
    fontWeight: 'bold',
    fontSize: 12,
    letterSpacing: -0.5
  },
  textContainer: {
    flex: 1
  },
  title: {
    color: GlassTokens.colors.textPrimary,
    fontWeight: 'bold',
    fontSize: 14
  },
  sub: {
    color: '#34D399',
    fontSize: 12,
    marginTop: 2
  },
  timeTag: {
    color: GlassTokens.colors.textSecondary,
    fontSize: 12,
    marginRight: 8
  },
  chevron: {
    color: 'rgba(255, 255, 255, 0.5)',
    fontSize: 18,
    fontWeight: 'bold'
  }
});
