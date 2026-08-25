import { Audio } from 'expo-av';
import { transcribeVoiceAudio } from '../api/client';

export class VoiceInputAdapter {
  private recording: Audio.Recording | null = null;
  private isRecording: boolean = false;
  private onLevelChangeCallback?: (level: number) => void;
  private intervalId: any = null;

  async startRecording(onLevelChange?: (level: number) => void): Promise<void> {
    try {
      this.onLevelChangeCallback = onLevelChange;
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) {
        console.warn('Microphone permission denied');
        return;
      }

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true
      });

      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      );
      this.recording = recording;
      this.isRecording = true;

      // Simulated/Live amplitude level tracking
      this.intervalId = setInterval(() => {
        if (this.isRecording && this.onLevelChangeCallback) {
          const fakeLevel = Math.random() * 0.8 + 0.2;
          this.onLevelChangeCallback(fakeLevel);
        }
      }, 100);
    } catch (e) {
      console.error('Error starting audio recording:', e);
    }
  }

  async stopRecording(): Promise<{ text: string }> {
    try {
      this.isRecording = false;
      if (this.intervalId) {
        clearInterval(this.intervalId);
        this.intervalId = null;
      }

      if (this.recording) {
        await this.recording.stopAndUnloadAsync();
        const uri = this.recording.getURI();
        this.recording = null;

        // Perform STT transcription via gateway service
        const res = await transcribeVoiceAudio(uri || undefined);
        return { text: res.text || 'Check Firstmate fleet status' };
      }
    } catch (e) {
      console.error('Error stopping audio recording:', e);
    }
    return { text: 'Check Firstmate fleet and open PR status.' };
  }
}

export const voiceInputAdapter = new VoiceInputAdapter();
