import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ScrollView } from 'react-native';
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
    { label: 'shift tab', value: 'Shift-Tab' },
    { label: '?', value: '?' },
    { label: '/', value: '/' },
    { label: '|', value: '|' },
    { label: 'esc', value: 'Escape' },
    { label: 'tab', value: 'Tab' },
    { label: 'ctrl', value: 'Ctrl' },
    { label: 'alt', value: 'Alt' },
    { label: '^C', value: 'C-c' }
  ];

  return (
    <View style={styles.container}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
        {keys.map((k) => (
          <TouchableOpacity key={k.label} onPress={() => handlePressKey(k.value)} activeOpacity={0.5} style={styles.btnHitbox}>
            <Text style={styles.keyText}>{k.label}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    paddingVertical: 2,
    marginVertical: 4,
    alignItems: 'center'
  },
  scrollContent: {
    alignItems: 'center',
    paddingHorizontal: 8,
    gap: 16
  },
  btnHitbox: {
    paddingVertical: 4,
    justifyContent: 'center',
    alignItems: 'center'
  },
  keyText: {
    fontSize: 12,
    fontWeight: '500',
    color: '#20C20E'
  }
});
