import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { sendAgentKey } from '../api/client';

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
    { label: 'Enter', value: 'Enter', accessibilityLabel: 'Send Enter' },
    { label: '↑', value: 'Up', accessibilityLabel: 'Send Up arrow' },
    { label: '↓', value: 'Down', accessibilityLabel: 'Send Down arrow' },
    { label: 'Yes', value: 'y', accessibilityLabel: 'Send Yes' },
    { label: 'No', value: 'n', accessibilityLabel: 'Send No' },
  ];

  return (
    <View style={styles.container}>
      <View style={styles.controlRow}>
        {keys.map((k) => (
          <TouchableOpacity
            key={k.label}
            testID={`terminal-control-${k.value.toLowerCase()}`}
            accessibilityRole="button"
            accessibilityLabel={k.accessibilityLabel}
            onPress={() => handlePressKey(k.value)}
            activeOpacity={0.6}
            style={styles.btnHitbox}
          >
            <Text style={styles.keyText}>{k.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    paddingVertical: 2,
    marginVertical: 4,
    alignItems: 'center'
  },
  controlRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 18,
  },
  btnHitbox: {
    minWidth: 32,
    minHeight: 32,
    justifyContent: 'center',
    alignItems: 'center'
  },
  keyText: {
    fontSize: 12,
    fontWeight: '500',
    color: '#FFFFFF'
  }
});
