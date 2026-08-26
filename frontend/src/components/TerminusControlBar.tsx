import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ScrollView } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { sendAgentKey } from '../api/client';
import { GlassTokens } from '../theme/glass';

interface TerminusControlBarProps {
  target?: string;
  onKeySent?: (key: string) => void;
}

export const TerminusControlBar: React.FC<TerminusControlBarProps> = ({
  target = 'firstmate',
  onKeySent
}) => {
  const handlePressKey = async (key: string) => {
    try {
      await sendAgentKey(target, key);
      if (onKeySent) onKeySent(key);
    } catch (e) {
      console.error('Error sending key:', key, e);
    }
  };

  const keys = [
    { label: 'ENTER', value: 'Enter' },
    { label: 'Y (YES)', value: 'Y' },
    { label: 'N (NO)', value: 'N' },
    { label: 'ESC', value: 'Escape' },
    { label: '▲', value: 'Up' },
    { label: '▼', value: 'Down' },
    { label: 'CTRL+C', value: 'C-c' },
    { label: 'TAB', value: 'Tab' },
  ];

  return (
    <View style={styles.container}>
      <GlassSurface variant="control" style={styles.unifiedPill}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
          {keys.map((k, index) => (
            <React.Fragment key={k.label}>
              <TouchableOpacity onPress={() => handlePressKey(k.value)} activeOpacity={0.5} style={styles.btnHitbox}>
                <Text style={styles.keyText}>{k.label}</Text>
              </TouchableOpacity>
              {index < keys.length - 1 && <View style={styles.divider} />}
            </React.Fragment>
          ))}
        </ScrollView>
      </GlassSurface>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    paddingVertical: 6,
    marginVertical: 4,
    alignItems: 'center'
  },
  unifiedPill: {
    borderRadius: 24,
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    borderColor: 'rgba(255, 255, 255, 0.2)',
    borderWidth: 1,
    overflow: 'hidden',
    height: 38
  },
  scrollContent: {
    alignItems: 'center',
    paddingHorizontal: 8
  },
  btnHitbox: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    justifyContent: 'center',
    alignItems: 'center'
  },
  divider: {
    width: 1,
    height: 14,
    backgroundColor: 'rgba(255, 255, 255, 0.15)'
  },
  keyText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#FFFFFF',
    letterSpacing: 0.8
  }
});
