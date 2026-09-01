import React, { useState } from 'react';
import { Modal, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { ExecutionModel } from '../api/client';

interface CapabilitySelectProps {
  testID: string;
  label: string;
  value: string;
  options: ExecutionModel[];
  loading: boolean;
  error: string | null;
  emptyMessage: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}

export function CapabilitySelect({ testID, label, value, options, loading, error, emptyMessage, disabled = false, onChange }: CapabilitySelectProps) {
  const [open, setOpen] = useState(false);
  const selected = options.find(option => option.id === value);
  const unavailableMessage = error || (loading ? 'Loading options…' : options.length === 0 ? emptyMessage : 'Select an option.');

  const choose = (option: ExecutionModel) => {
    onChange(option.id);
    setOpen(false);
  };

  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>
      <TouchableOpacity
        testID={`${testID}-field`}
        accessibilityRole="button"
        accessibilityLabel={`${label} selection`}
        accessibilityState={{ disabled: disabled || loading, expanded: open }}
        disabled={disabled || loading || options.length === 0}
        onPress={() => setOpen(true)}
        style={[styles.field, disabled || loading || options.length === 0 ? styles.disabledField : undefined]}
      >
        <Text style={selected ? styles.value : styles.placeholder} numberOfLines={1}>{selected?.label || unavailableMessage}</Text>
        <Text style={styles.chevron}>⌄</Text>
      </TouchableOpacity>

      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <TouchableOpacity style={styles.overlay} activeOpacity={1} onPress={() => setOpen(false)}>
          <TouchableOpacity activeOpacity={1} style={styles.menuContainer}>
            <GlassSurface variant="surface" intensity={70} style={styles.menuSurface}>
              <Text style={styles.menuTitle}>{label}</Text>
              <ScrollView>
                {options.map(option => (
                  <TouchableOpacity
                    key={option.id}
                    testID={`${testID}-option-${option.id}`}
                    accessibilityRole="button"
                    accessibilityLabel={`Select ${option.label}`}
                    accessibilityState={{ selected: option.id === value }}
                    onPress={() => choose(option)}
                    style={[styles.option, option.id === value ? styles.selectedOption : undefined]}
                  >
                    <Text style={styles.optionText}>{option.label}</Text>
                    {option.id === value ? <Text style={styles.selectedMark}>✓</Text> : null}
                  </TouchableOpacity>
                ))}
              </ScrollView>
            </GlassSurface>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, minWidth: 0 },
  label: { color: 'rgba(255, 255, 255, 0.6)', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', letterSpacing: 1, marginBottom: 5 },
  field: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.25)', backgroundColor: 'rgba(255, 255, 255, 0.1)' },
  disabledField: { opacity: 0.6 },
  value: { flex: 1, color: '#FFFFFF', fontSize: 12, marginRight: 8 },
  placeholder: { flex: 1, color: 'rgba(255, 255, 255, 0.5)', fontSize: 11, marginRight: 8 },
  chevron: { color: '#72F5B1', fontSize: 18, lineHeight: 18 },
  overlay: { flex: 1, justifyContent: 'center', padding: 24, backgroundColor: 'rgba(0, 0, 0, 0.7)' },
  menuContainer: { maxHeight: '80%', width: '100%', maxWidth: 520, alignSelf: 'center' },
  menuSurface: { padding: 16, borderRadius: 18 },
  menuTitle: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 12, fontWeight: 'bold', letterSpacing: 1, marginBottom: 8 },
  option: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 10, borderBottomWidth: 1, borderBottomColor: 'rgba(255, 255, 255, 0.1)' },
  selectedOption: { backgroundColor: 'rgba(114, 245, 177, 0.16)' },
  optionText: { color: '#FFFFFF', fontSize: 13 },
  selectedMark: { color: '#72F5B1', fontWeight: 'bold' }
});
