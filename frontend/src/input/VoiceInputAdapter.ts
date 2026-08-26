import { Audio } from 'expo-av';
import { transcribeVoiceAudio } from '../api/client';
import { SpeechActivityAdapter } from '../services/SpeechActivityAdapter';

export class VoiceInputAdapter {
  private recording: Audio.Recording | null = null;
  private isRecording: boolean = false;
  private onLevelChangeCallback?: (level: number) => void;
  private speechActivity = new SpeechActivityAdapter();

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

      const { recording } = await Audio.Recording.createAsync({ ...Audio.RecordingOptionsPresets.HIGH_QUALITY, isMeteringEnabled: true });
      this.recording = recording;
      this.isRecording = true;

      recording.setProgressUpdateInterval(80);
      recording.setOnRecordingStatusUpdate(status => {
        if (this.isRecording && status.isRecording) this.onLevelChangeCallback?.(this.speechActivity.update(status.metering));
      });
    } catch (e) {
      console.error('Error starting audio recording:', e);
    }
  }

  async stopRecording(): Promise<{ text: string }> {
    try {
      this.isRecording = false;
      this.onLevelChangeCallback?.(this.speechActivity.reset());

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
