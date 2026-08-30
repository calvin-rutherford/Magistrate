import { useCallback, useRef, useState } from 'react';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { Platform } from 'react-native';
import { browserSpeechRecognitionAvailable, DEFAULT_VOICE_INPUT_MODE, VoiceInputMode } from '../services/VoiceInputModes';

export class VoiceCaptureError extends Error {
  constructor(public code: 'permission-denied' | 'capture-failed' | 'unsupported', message: string) { super(message); this.name = 'VoiceCaptureError'; }
}
export interface VoiceRecording {
  uri: string;
  mimeType: string;
  filename: string;
  durationMillis: number;
  /** Browser mode can finish locally; gateway modes intentionally leave this empty. */
  transcript?: string;
}
const options = { ...RecordingPresets.HIGH_QUALITY, isMeteringEnabled: true, directory: 'cache' as const };

/**
 * The single microphone seam used by chat, Voice Mode, and future native
 * launch adapters. Capture is always local; only the selected transcription
 * path decides whether audio crosses the authenticated gateway boundary.
 */
export function useVoiceInputAdapter(onIntermediate?: (text: string) => void, mode: VoiceInputMode = DEFAULT_VOICE_INPUT_MODE) {
  const recorder = useAudioRecorder(options);
  const recorderState = useAudioRecorderState(recorder, 100);
  const [error, setError] = useState<VoiceCaptureError | null>(null);
  const recognitionRef = useRef<any>(null);
  const browserTranscriptRef = useRef('');
  const stopBrowserRecognition = useCallback(() => {
    try { recognitionRef.current?.stop(); } catch {}
    recognitionRef.current = null;
  }, []);
  const startBrowserRecognition = useCallback(() => {
    const shouldUseBrowser = mode === 'browser' || (mode === 'automatic' && Platform.OS === 'web');
    if (!shouldUseBrowser || Platform.OS !== 'web' || typeof window === 'undefined') return;
    const value = window as typeof window & { SpeechRecognition?: any; webkitSpeechRecognition?: any };
    const Recognition = value.SpeechRecognition || value.webkitSpeechRecognition;
    if (typeof Recognition !== 'function') {
      if (mode === 'browser') throw new VoiceCaptureError('unsupported', 'Browser speech recognition is unavailable. Choose Automatic or Gateway OpenAI.');
      return;
    }
    browserTranscriptRef.current = '';
    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event: any) => {
      let text = '';
      for (let i = 0; i < event.results.length; i += 1) text += event.results[i][0].transcript;
      browserTranscriptRef.current = text.trim();
      if (browserTranscriptRef.current) onIntermediate?.(browserTranscriptRef.current);
    };
    // Automatic mode keeps recording and can still use gateway STT when the
    // browser recognizer stops. Explicit browser mode reports the failure.
    recognition.onerror = (event: any) => {
      if (mode === 'browser' && event?.error !== 'aborted') {
        setError(new VoiceCaptureError('capture-failed', 'Browser speech recognition failed. Choose Automatic or Gateway OpenAI and try again.'));
      }
    };
    try { recognition.start(); } catch {
      if (mode === 'browser') throw new VoiceCaptureError('capture-failed', 'Browser speech recognition could not start.');
      return;
    }
    recognitionRef.current = recognition;
  }, [mode, onIntermediate]);
  const start = useCallback(async () => {
    setError(null);
    if (mode === 'browser' && !browserSpeechRecognitionAvailable()) {
      const unsupported = new VoiceCaptureError('unsupported', 'Browser speech recognition is unavailable. Choose Automatic or Gateway OpenAI.');
      setError(unsupported); throw unsupported;
    }
    if (mode === 'native' && Platform.OS === 'web') {
      const unsupported = new VoiceCaptureError('unsupported', 'Native device capture is unavailable in the browser. Choose Automatic or Browser speech.');
      setError(unsupported); throw unsupported;
    }
    const current = await AudioModule.getRecordingPermissionsAsync();
    const permission = current.granted ? current : await AudioModule.requestRecordingPermissionsAsync();
    if (!permission.granted) {
      const denied = new VoiceCaptureError('permission-denied', 'Microphone permission was denied. Enable it in system settings and try again.');
      setError(denied); throw denied;
    }
    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      startBrowserRecognition();
    } catch (cause) {
      if (cause instanceof VoiceCaptureError) { setError(cause); throw cause; }
      const failed = new VoiceCaptureError('capture-failed', 'The microphone could not start. Check whether another app is using it.');
      setError(failed); throw failed;
    }
  }, [mode, recorder, startBrowserRecognition]);
  const stop = useCallback(async (): Promise<VoiceRecording> => {
    const transcript = browserTranscriptRef.current;
    stopBrowserRecognition();
    try {
      await recorder.stop();
      if (!recorder.uri) throw new Error('missing recording');
      const web = Platform.OS === 'web';
      return { uri: recorder.uri, mimeType: web ? 'audio/webm' : 'audio/mp4',
        filename: web ? 'speech.webm' : 'speech.m4a', durationMillis: recorderState.durationMillis,
        transcript: mode === 'browser' ? transcript : undefined };
    } catch {
      const failed = new VoiceCaptureError('capture-failed', 'The recording could not be completed. No request was sent.');
      setError(failed); throw failed;
    } finally {
      browserTranscriptRef.current = '';
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => undefined);
    }
  }, [mode, recorder, recorderState.durationMillis, stopBrowserRecognition]);
  const cancel = useCallback(async () => {
    browserTranscriptRef.current = '';
    stopBrowserRecognition();
    if (recorderState.isRecording) await recorder.stop().catch(() => undefined);
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => undefined);
  }, [recorder, recorderState.isRecording, stopBrowserRecognition]);
  const db = recorderState.metering;
  const amplitude = recorderState.isRecording && typeof db === 'number'
    ? Math.max(0, Math.min(1, Math.pow(10, db / 20))) : 0;
  return { start, stop, cancel, isRecording: recorderState.isRecording,
    durationMillis: recorderState.durationMillis, amplitude, error };
}
