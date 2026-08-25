import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Animated } from 'react-native';
import { Audio } from 'expo-av';
import { Theme } from '../theme';

interface Props {
  colors: typeof Theme.light;
  onTranscription: (text: string) => void;
}

export default function OctoberAssistant({ colors, onTranscription }: Props) {
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [animation] = useState(new Animated.Value(1));

  const startAnimation = () => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(animation, { toValue: 1.2, duration: 800, useNativeDriver: true }),
        Animated.timing(animation, { toValue: 1, duration: 800, useNativeDriver: true })
      ])
    ).start();
  };

  const stopAnimation = () => {
    animation.stopAnimation();
    animation.setValue(1);
  };

  async function startRecording() {
    try {
      await Audio.requestPermissionsAsync();
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      );
      setRecording(recording);
      setIsListening(true);
      startAnimation();
    } catch (err) {
      console.error('Failed to start recording', err);
    }
  }

  async function stopRecording() {
    setIsListening(false);
    stopAnimation();
    if (!recording) return;

    await recording.stopAndUnloadAsync();
    await Audio.setAudioModeAsync({ allowsRecordingIOS: false });
    
    // In a real pipeline, we would upload the recording.getURI() to Whisper API.
    // For scaffolding, we simulate transcription after a brief delay.
    setTimeout(() => {
      onTranscription("Ahoy. Run Firstmate bootstrap diagnostics only.");
    }, 1000);
    
    setRecording(null);
  }

  return (
    <View style={styles.container}>
      <Animated.View style={[
        styles.glowRing, 
        { borderColor: colors.accent, transform: [{ scale: animation }], opacity: isListening ? 0.5 : 0 }
      ]} />
      
      <TouchableOpacity 
        style={[styles.button, { backgroundColor: isListening ? colors.danger : colors.cardBackground, borderColor: colors.accent }]}
        onPressIn={startRecording}
        onPressOut={stopRecording}
        activeOpacity={0.8}
      >
        <Text style={[styles.text, { color: isListening ? '#FFF' : colors.accent }]}>
          {isListening ? 'LISTENING...' : 'HOLD FOR OCTOBER'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 20,
    marginBottom: 20,
    position: 'relative'
  },
  glowRing: {
    position: 'absolute',
    width: 220,
    height: 60,
    borderRadius: 30,
    borderWidth: 4,
  },
  button: {
    paddingVertical: 16,
    paddingHorizontal: 32,
    borderRadius: 30,
    borderWidth: 2,
    shadowColor: '#000',
    shadowOpacity: 0.3,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
  text: {
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: 2,
  }
});
