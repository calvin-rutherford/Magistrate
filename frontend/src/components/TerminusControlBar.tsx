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
  target = 'captain',
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
    { label: 'ENTER ↵', value: 'Enter', primary: true },
    { label: 'Y (YES)', value: 'Y', primary: false },
    { label: 'N (NO)', value: 'N', primary: false },
    { label: 'ESC', value: 'Escape', primary: false },
    { label: '▲ UP', value: 'Up', primary: false },
    { label: '▼ DOWN', value: 'Down', primary: false },
    { label: 'CTRL+C', value: 'C-c', danger: true },
    { label: 'TAB', value: 'Tab', primary: false },
  ];

  return (
    <View style={styles.container}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
        {keys.map((k) => (
          <TouchableOpacity key={k.label} onPress={() => handlePressKey(k.value)} activeOpacity={0.75}>
            <GlassSurface
              variant="control"
              style={[
                styles.keyPill,
                k.primary ? styles.keyPrimary : null,
                k.danger ? styles.keyDanger : null
              ]}
            >
              <Text style={[
                styles.keyText,
                k.primary ? styles.keyTextPrimary : null,
                k.danger ? styles.keyTextDanger : null
              ]}>
                {k.label}
              </Text>
            </GlassSurface>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    paddingVertical: 6,
    marginVertical: 4
  },
  scrollContent: {
    gap: 8,
    paddingHorizontal: 2
  },
  keyPill: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 14,
    minWidth: 54,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.12)'
  },
  keyPrimary: {
    backgroundColor: 'rgba(114, 245, 177, 0.25)',
    borderColor: '#72F5B1'
  },
  keyDanger: {
    backgroundColor: 'rgba(239, 68, 68, 0.25)',
    borderColor: '#EF4444'
  },
  keyText: {
    fontSize: 11,
    fontWeight: 'bold',
    color: GlassTokens.colors.textPrimary,
    letterSpacing: 0.8
  },
  keyTextPrimary: {
    color: '#72F5B1'
  },
  keyTextDanger: {
    color: '#EF4444'
  }
});
