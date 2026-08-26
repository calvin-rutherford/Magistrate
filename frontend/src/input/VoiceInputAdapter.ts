import { useCallback, useRef, useState } from 'react';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { Platform } from 'react-native';
import { SpeechActivityAdapter } from '../services/SpeechActivityAdapter';

export class VoiceCaptureError extends Error {
  constructor(public code: 'permission-denied' | 'capture-failed', message: string) { super(message); }
}
export interface VoiceRecording { uri: string; mimeType: string; filename: string; durationMillis: number; }
const options = { ...RecordingPresets.HIGH_QUALITY, isMeteringEnabled: true, directory: 'cache' as const };

export function useVoiceInputAdapter(onIntermediate?: (text: string) => void, onStatus?: (message: string) => void) {
  const recorder = useAudioRecorder(options);
  const recorderState = useAudioRecorderState(recorder, 100);
  const [error, setError] = useState<VoiceCaptureError | null>(null);
  const recognitionRef = useRef<any>(null);
  const startedAtRef = useRef(0);
  const activityRef = useRef(new SpeechActivityAdapter());
  const stopBrowserRecognition = useCallback(() => {
    try { recognitionRef.current?.stop(); } catch {}
    recognitionRef.current = null;
  }, []);
  const startBrowserRecognition = useCallback(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined') return;
    const Recognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!Recognition) {
      onStatus?.('This browser does not provide live interim captions; the final cloud transcript remains authoritative.');
      return;
    }
    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event: any) => {
      let text = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) text += event.results[i][0].transcript;
      if (text.trim()) onIntermediate?.(text.trim());
    };
    recognition.onerror = (event: any) => {
      const detail = event?.error === 'not-allowed' ? 'Browser speech captions are blocked; the final cloud transcript can still be used.'
        : 'Live browser captions are unavailable; the final cloud transcript remains authoritative.';
      onStatus?.(detail);
    };
    try {
      recognition.start();
      recognitionRef.current = recognition;
    } catch {
      onStatus?.('Live browser captions are unavailable; the final cloud transcript remains authoritative.');
    }
  }, [onIntermediate, onStatus]);
  const start = useCallback(async () => {
    setError(null);
    activityRef.current.reset();
    let permission;
    try {
      const current = await AudioModule.getRecordingPermissionsAsync();
      permission = current.granted ? current : await AudioModule.requestRecordingPermissionsAsync();
    } catch {
      const unavailable = new VoiceCaptureError('permission-denied', 'Microphone permission status is unavailable. Check browser or system privacy settings and try again.');
      setError(unavailable); throw unavailable;
    }
    if (!permission.granted) {
      const denied = new VoiceCaptureError('permission-denied', 'Microphone permission was denied. Enable it in system settings and try again.');
      setError(denied); throw denied;
    }
    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      startedAtRef.current = Date.now();
      startBrowserRecognition();
    } catch {
      const failed = new VoiceCaptureError('capture-failed', 'The microphone could not start. Check whether another app is using it.');
      setError(failed); throw failed;
    }
  }, [recorder, startBrowserRecognition]);
  const stop = useCallback(async (): Promise<VoiceRecording> => {
    stopBrowserRecognition();
    try {
      await recorder.stop();
      if (!recorder.uri) throw new Error('missing recording');
      const web = Platform.OS === 'web';
      const elapsed = startedAtRef.current ? Date.now() - startedAtRef.current : 0;
      return { uri: recorder.uri, mimeType: web ? 'audio/webm' : 'audio/mp4',
        filename: web ? 'speech.webm' : 'speech.m4a', durationMillis: Math.max(recorderState.durationMillis || 0, elapsed) };
    } catch {
      const failed = new VoiceCaptureError('capture-failed', 'The recording could not be completed. No request was sent.');
      setError(failed); throw failed;
    } finally {
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => undefined);
    }
  }, [recorder, recorderState.durationMillis, stopBrowserRecognition]);
  const cancel = useCallback(async () => {
    stopBrowserRecognition();
    startedAtRef.current = 0;
    activityRef.current.reset();
    if (recorderState.isRecording) await recorder.stop().catch(() => undefined);
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => undefined);
  }, [recorder, recorderState.isRecording, stopBrowserRecognition]);
  const db = recorderState.metering;
  const amplitude = recorderState.isRecording ? activityRef.current.update(db) : activityRef.current.reset();
  return { start, stop, cancel, isRecording: recorderState.isRecording,
    durationMillis: recorderState.durationMillis, amplitude, error };
}
