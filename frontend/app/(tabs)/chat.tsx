import * as Clipboard from 'expo-clipboard';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Alert, Image, KeyboardAvoidingView, NativeScrollEvent, NativeSyntheticEvent, PanResponder, Platform, ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import Animated, { Easing, interpolate, useAnimatedStyle, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Circle, Path, Rect } from 'react-native-svg';
import { AgentInfo, AuthProviderInfo, CHAT_HISTORY_LINES, ExecutionProfile, fetchAgentHistory, fetchAgents, fetchAuthProviders, fetchExecutionCapabilities, fetchExecutionSettings, fetchHealth, fetchRecentActivity, fetchUnifiedAttention, fetchUsage, HealthInfo, interruptAgent, logoutGatewaySession, RecentActivityItem, renameAgent, sendCaptainPrompt, transcribeVoiceAudio, UnifiedAttentionRecord, updateExecutionSettings, saveExecutionCredential, ExecutionSettings, UsageProvider } from '../../src/api/client';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { useVoiceInputAdapter } from '../../src/input/VoiceInputAdapter';
import { displayAgentStatus, summarizeAgents } from '../../src/services/AgentStatus';
import { filterAgentHistory } from '../../src/services/ChatHistory';
import { appendConversationMessage, ConversationMessage, getConversationMessages, hydrateConversationMessages, prependConversationMessages, updateConversationMessage, useConversationMessages } from '../../src/services/ConversationSession';
import { ChatPreferences, ChatThemeMode, DEFAULT_CHAT_PREFERENCES, loadChatPreferences, removeCustomBackground, saveChatBackground, saveCustomBackground, saveThemeMode, saveToolCallVisibility, useChatColorScheme } from '../../src/services/ChatPreferences';
import { setActiveBackground, WeatherSceneKey } from '../../src/services/environmentTheme';
import { openExternalUrl } from '../../src/utils/externalLinks';
import { RealtimeClient } from '../../src/realtime/socket';

const markPaper = require('../../assets/images/magistrate-mark-paper-256.png');
const markInk = require('../../assets/images/magistrate-mark-ink-256.png');
const brand = { obsidian: '#05070A', command: '#111722', paper: '#F7F8FA', ink: '#11151B', mutedDark: '#8E99AA', mutedLight: '#667180', cyan: '#24D8FF', violet: '#8B6CFF', success: '#43D17A', attention: '#FFB347', critical: '#FF625F' };

type ComposerAttachment = { id: string; name: string; uri: string; mimeType?: string; size?: number; kind: 'image' | 'file' };
type DrawerSection = 'attention' | 'fleet' | 'activity' | 'connections' | null;
type ModelSelection = { profileId: string; harness: string; provider: string; model: string; variant: string; label: string; available: boolean; availabilityReason?: string | null } | null;
const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const isDarkTheme = (scheme: string | null | undefined) => scheme !== 'light';
const optionId = (harness: string, model: string) => `${harness}-${model}`.replace(/[^A-Za-z0-9_-]/g, '-');
const providerLabel = (provider: string) => provider.toLowerCase() === 'firstmate' ? 'Magistrate' : provider;
const profilesFromCapabilities = (data: { profiles?: ExecutionProfile[]; harnesses?: Array<{ id: string; label: string; verified: boolean; models: Array<{ id: string; label: string; provider?: string; variant?: string; profile_id?: string; available?: boolean; availability?: string; auth?: { required: boolean; credential_key: string; status: string } }> }> }): ExecutionProfile[] => {
  if (Array.isArray(data.profiles)) return data.profiles;
  return (data.harnesses || []).filter(harness => harness.verified).flatMap(harness => harness.models.map(model => ({
    id: model.profile_id || `${harness.id}:${model.variant || model.id}`, variant: model.variant || model.id, label: model.label,
    harness: { id: harness.id, label: harness.label }, provider: { id: model.provider || 'unknown', label: model.provider || 'unknown' },
    model: { id: model.id, label: model.label }, verified: true, available: model.available !== false,
    availability: model.availability || (model.available === false ? 'unavailable' : 'available'), availability_reason: null,
    auth: model.auth || { required: false, credential_key: model.provider || 'unknown', status: 'not-required' },
  })));
};

function statusColor(status?: string | null) {
  const normalized = (status || '').toLowerCase();
  if (['working', 'running', 'active', 'executing'].includes(normalized)) return brand.success;
  if (['blocked', 'failed', 'error'].includes(normalized)) return brand.critical;
  if (['waiting', 'paused'].includes(normalized)) return brand.attention;
  return brand.mutedDark;
}

function BrandMark({ dark }: { dark: boolean }) {
  return <Image source={dark ? markPaper : markInk} style={styles.mark} resizeMode="contain" accessibilityIgnoresInvertColors />;
}

function WrenchIcon({ color, size = 18 }: { color: string; size?: number }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Path d="M20.1 8.5a4.6 4.6 0 0 1-5.94 5.94l-6.4 6.4a1.8 1.8 0 0 1-2.55-2.55l6.4-6.4A4.6 4.6 0 0 1 17.5 5.85l-2.83 2.83a1 1 0 0 0 0 1.41l1.24 1.24a1 1 0 0 0 1.41 0z" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
  </Svg>;
}

function MicIcon({ color, size = 18 }: { color: string; size?: number }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Rect x="9" y="2.5" width="6" height="11" rx="3" stroke={color} strokeWidth={1.6} />
    <Path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3M9 20.5h6" stroke={color} strokeWidth={1.6} strokeLinecap="round" fill="none" />
  </Svg>;
}

function GearIcon({ color, size = 18 }: { color: string; size?: number }) {
  return <Svg testID="settings-gear-icon" width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Circle cx="12" cy="12" r="3.1" stroke={color} strokeWidth={1.6} />
    <Path d="M9.8 3.1h4.4l.5 2.1c.5.2.9.4 1.3.7l2-.6 2.2 3.8-1.5 1.5v2.8l1.5 1.5-2.2 3.8-2-.6c-.4.3-.8.5-1.3.7l-.5 2.1H9.8l-.5-2.1c-.5-.2-.9-.4-1.3-.7l-2 .6-2.2-3.8 1.5-1.5v-2.8L3.8 9.1 6 5.3l2 .6c.4-.3.8-.5 1.3-.7z" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
  </Svg>;
}

function SoundwaveIcon({ color, size = 18 }: { color: string; size?: number }) {
  const bars = [0.32, 0.62, 1, 0.72, 0.42];
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    {bars.map((ratio, index) => {
      const barHeight = 16 * ratio;
      const x = 2 + index * 4.6;
      return <Rect key={index} x={x} y={(24 - barHeight) / 2} width="2.4" height={barHeight} rx="1.2" fill={color} />;
    })}
  </Svg>;
}

function EllipsisIcon({ color, size = 18 }: { color: string; size?: number }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Circle cx="5" cy="12" r="1.5" fill={color} /><Circle cx="12" cy="12" r="1.5" fill={color} /><Circle cx="19" cy="12" r="1.5" fill={color} />
  </Svg>;
}

function ImageIcon({ color, size = 18 }: { color: string; size?: number }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Rect x="3" y="4" width="18" height="16" rx="3" stroke={color} strokeWidth={1.6} />
    <Path d="m6.5 16 3.6-3.8 2.8 2.6 2.3-2.3 2.8 3.5M15.8 9h.01" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
  </Svg>;
}

function FileIcon({ color, size = 18 }: { color: string; size?: number }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Path d="M6 3.5h7l5 5v12H6zM13 3.5v5h5" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
  </Svg>;
}

const formatAttachmentSize = (size?: number) => {
  if (!size) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};

function LiveWaveform({ samples, color }: { samples: number[]; color: string }) {
  return <View style={styles.liveWaveform} pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
    {samples.map((amplitude, index) => <View key={index} style={[styles.liveWaveformBar, { height: Math.max(3, amplitude * 46), backgroundColor: color }]} />)}
  </View>;
}

function ThinkingIndicator({ dark }: { dark: boolean }) {
  const pulse = useSharedValue(0);
  useEffect(() => { pulse.value = withRepeat(withTiming(1, { duration: 700 }), -1, true); }, [pulse]);
  const style = useAnimatedStyle(() => ({ opacity: interpolate(pulse.value, [0, 0.5, 1], [0.45, 1, 0.45]), transform: [{ translateY: interpolate(pulse.value, [0, 0.5, 1], [1, -2, 1]) }] }));
  return <Animated.Text testID="thinking-dots" style={[styles.thinkingDots, { color: dark ? brand.cyan : brand.violet }, style]}>•••</Animated.Text>;
}

function ModelMenu({ dark, profiles, loading, error, open, selection, onToggle, onSelect }: {
  dark: boolean; profiles: ExecutionProfile[]; loading: boolean; error: string | null; open: boolean;
  selection: ModelSelection; onToggle: () => void; onSelect: (selection: ModelSelection) => void;
}) {
  const text = dark ? '#F4F5F7' : brand.ink;
  const muted = dark ? brand.mutedDark : brand.mutedLight;
  return <View style={styles.modelControl}>
    <TouchableOpacity testID="model-menu-button" accessibilityRole="button" accessibilityLabel={`Model, ${selection?.label || 'current session'}`} accessibilityState={{ expanded: open }} onPress={onToggle} style={styles.modelButton}>
      <WrenchIcon size={24} color={selection ? brand.cyan : muted} />
    </TouchableOpacity>
    {open ? <View testID="model-menu" accessibilityViewIsModal style={[styles.modelMenu, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}>
      <Text style={[styles.menuTitle, { color: text }]}>Agent and variant</Text>
      <TouchableOpacity testID="model-option-current" accessibilityRole="button" accessibilityLabel="Use the running session model" accessibilityState={{ selected: selection === null }} onPress={() => onSelect(null)} style={styles.modelOption}>
        <View style={[styles.selectionDot, { backgroundColor: selection === null ? brand.cyan : 'transparent' }]} />
        <View style={styles.modelOptionCopy}><Text style={[styles.modelOptionTitle, { color: text }]}>Current session</Text><Text style={[styles.modelOptionMeta, { color: muted }]}>Uses the model already running on the backend</Text></View>
      </TouchableOpacity>
      <ScrollView style={styles.modelOptionsScroll} keyboardShouldPersistTaps="handled">
        {profiles.map(profile => {
          const selected = selection?.profileId === profile.id;
          const disabled = !profile.available;
          return <TouchableOpacity key={profile.id} disabled={disabled} testID={`model-option-${optionId(profile.harness.id, profile.model.id)}`} accessibilityRole="button" accessibilityLabel={`${profile.harness.label}, ${profile.provider.label}, ${profile.model.label}`} accessibilityState={{ selected, disabled }} onPress={() => onSelect({ profileId: profile.id, harness: profile.harness.id, provider: profile.provider.id, model: profile.model.id, variant: profile.variant, label: profile.label, available: profile.available, availabilityReason: profile.availability_reason })} style={[styles.modelOption, disabled ? styles.modelOptionDisabled : undefined]}>
            <View style={[styles.selectionDot, { backgroundColor: selected ? brand.cyan : 'transparent' }]} /><View style={styles.modelOptionCopy}><Text style={[styles.modelOptionTitle, { color: text }]}>{profile.label}</Text><Text style={[styles.modelOptionMeta, { color: muted }]}>{profile.harness.label} · {profile.provider.label} · {profile.model.label}{disabled ? ` · ${profile.availability_reason || 'Unavailable'}` : ''}</Text></View>
          </TouchableOpacity>;
        })}
        {loading ? <Text style={[styles.modelNotice, { color: muted }]}>Loading available variants…</Text> : null}
        {!loading && error ? <Text style={styles.modelError}>{error} Current session remains available.</Text> : null}
        {!loading && !error && profiles.length === 0 ? <Text style={[styles.modelNotice, { color: muted }]}>No compatible variants are configured. Current session remains available.</Text> : null}
      </ScrollView>
    </View> : null}
  </View>;
}

function UserMessage({ message, textColor, selectable, onLongPress }: { message: ConversationMessage; textColor: string; selectable: boolean; onLongPress: () => void }) {
  const timestamp = message.sentAt === undefined ? undefined : new Date(message.sentAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return <TouchableOpacity testID={`user-message-${message.id}`} accessibilityRole="button" accessibilityLabel={timestamp ? `Your message, sent ${timestamp}. Press and hold for actions.` : 'Your message'} delayLongPress={2000} onLongPress={onLongPress} activeOpacity={0.92} style={styles.userMessage}>
    <Text testID={`user-message-text-${message.id}`} selectable={selectable} style={[styles.messageText, { color: textColor }]}>{message.text}</Text>
    {timestamp ? <Text style={styles.messageTimestamp}>Sent {timestamp}</Text> : null}
  </TouchableOpacity>;
}

const historyKey = (message: { role: string; kind?: string; text: string }) => `${message.role}|${message.kind || 'conversation'}|${message.text}`;
function conversationalPromptResponse(response: unknown): string | null {
  if (typeof response !== 'string' || !response.trim()) return null;
  try {
    const envelope = JSON.parse(response);
    if (!envelope || typeof envelope !== 'object') return null;
    // A few older gateways wrapped a real synchronous reply in JSON-RPC. Keep
    // only an explicit response/text field; never display arbitrary transport
    // or tool payloads as conversation.
    const source = 'result' in envelope && envelope.result && typeof envelope.result === 'object'
      ? envelope.result as Record<string, unknown>
      : envelope as Record<string, unknown>;
    if ('jsonrpc' in envelope && !('result' in envelope)) return null;
    for (const key of ['response', 'text']) {
      const value = source[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return null;
  } catch { /* A plain string is a legacy synchronous conversational response. */ }
  return response.trim();
}

export function ChatCanvas({ target = 'captain', showToolCalls = false, onDrawerToggle = () => {}, drawerOpen = false, profiles = [], capabilityLoading = false, capabilityError = null, selectedProfileId = null, routingReady = true, onProfileChange = () => {} }: { target?: string; showToolCalls?: boolean; onDrawerToggle?: () => void; drawerOpen?: boolean; profiles?: ExecutionProfile[]; capabilityLoading?: boolean; capabilityError?: string | null; selectedProfileId?: string | null; routingReady?: boolean; onProfileChange?: (profileId: string | null) => void }) {
  const router = useRouter();
  const dark = isDarkTheme(useChatColorScheme());
  const text = dark ? '#F4F5F7' : brand.ink;
  const muted = dark ? brand.mutedDark : brand.mutedLight;
  const composerSurface = dark ? 'rgba(17,23,34,0.98)' : 'rgba(255,255,255,0.98)';
  const messages = useConversationMessages(target);
  const [promptText, setPromptText] = useState('');
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [messageActionsId, setMessageActionsId] = useState<string | null>(null);
  const [selectableMessageId, setSelectableMessageId] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [queuedPrompts, setQueuedPrompts] = useState<Array<{ id: string; text: string }>>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const [hasNewMessages, setHasNewMessages] = useState(false);
  const [historyBefore, setHistoryBefore] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [waveSamples, setWaveSamples] = useState<number[]>(() => new Array(48).fill(0.04));
  const [modelSelection, setModelSelection] = useState<ModelSelection>(() => {
    const profile = profiles.find(item => item.id === selectedProfileId);
    return profile ? { profileId: profile.id, harness: profile.harness.id, provider: profile.provider.id, model: profile.model.id, variant: profile.variant, label: profile.label, available: profile.available, availabilityReason: profile.availability_reason } : selectedProfileId ? { profileId: selectedProfileId, harness: '', provider: '', model: '', variant: '', label: 'Saved profile unavailable', available: false, availabilityReason: 'The saved execution profile is no longer available.' } : null;
  });
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [webViewportHeight, setWebViewportHeight] = useState<number | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);
  const atBottomRef = useRef(true);
  const historyRequestRef = useRef(0);
  // A prompt must not race the initial scrollback seed. If the seed resolves
  // after a new reply is already present, it would mark that reply as known
  // without rendering it and the live poll would skip it forever.
  const historyReadyRef = useRef<Promise<void>>(Promise.resolve());
  // Keys of messages already known to the live poll (typed locally, or seen in
  // a prior Herdr history read), so syncFromHistory only appends genuinely new
  // ones - see the mount effect below, which seeds this without rendering.
  const knownKeysRef = useRef<Set<string>>(new Set());
  const targetLabel = target === 'captain' ? 'Magistrate' : target;
  const capture = useVoiceInputAdapter();
  useEffect(() => {
    const profile = profiles.find(item => item.id === selectedProfileId);
    setModelSelection(profile ? { profileId: profile.id, harness: profile.harness.id, provider: profile.provider.id, model: profile.model.id, variant: profile.variant, label: profile.label, available: profile.available, availabilityReason: profile.availability_reason } : selectedProfileId ? { profileId: selectedProfileId, harness: '', provider: '', model: '', variant: '', label: 'Saved profile unavailable', available: false, availabilityReason: 'The saved execution profile is no longer available.' } : null);
  }, [profiles, selectedProfileId]);

  useEffect(() => {
    const request = ++historyRequestRef.current;
    let resolveHistoryReady!: () => void;
    let historyReady = false;
    const markHistoryReady = () => { if (!historyReady) { historyReady = true; resolveHistoryReady(); } };
    historyReadyRef.current = new Promise<void>(resolve => { resolveHistoryReady = resolve; });
    setSendError(null); setIsThinking(false);
    // The captain thread is shared with Voice Mode (see ConversationSession),
    // so switching back to it keeps whatever it already holds in memory for
    // this session; other targets start each visit with a clean thread.
    knownKeysRef.current = new Set(getConversationMessages(target).map(historyKey));
    void hydrateConversationMessages(target).then(hydrated => hydrated.forEach(message => knownKeysRef.current.add(historyKey(message))));
    // Seed known-message keys from recent Herdr scrollback so the live poll
    // below doesn't treat pre-existing history as new and replay it into the
    // thread - chat only ever shows what happens while it's open.
    const loadHistory = (attempt: number) => {
      fetchAgentHistory(target).then(result => {
        if (request !== historyRequestRef.current) return;
        // A working agent can briefly leave an empty terminal snapshot (redraw
        // or alternate screen); retry a few times before accepting it as empty.
        if (result.messages.length === 0 && attempt < 5) { setTimeout(() => loadHistory(attempt + 1), 2000); return; }
        result.messages.forEach(message => knownKeysRef.current.add(historyKey(message)));
        setHistoryBefore(result.next_before || null);
        markHistoryReady();
      }).catch(error => {
        if (request !== historyRequestRef.current) return;
        setSendError(errorText(error, 'Agent history could not be loaded.'));
        // A history outage must not permanently prevent sending. The active
        // poll remains the recovery path and will discover the eventual reply.
        markHistoryReady();
      });
    };
    loadHistory(0);
    return () => { historyRequestRef.current += 1; markHistoryReady(); };
  }, [target]);

  // Mobile web keyboards resize the visual viewport, not the layout viewport.
  // Track it so only this chat canvas shrinks to stay above the keyboard while
  // the drawer/header keep their normal layout instead of the whole page
  // reflowing and pushing everything upward. Only pin an explicit height once
  // the visual viewport is actually smaller than the window (keyboard open) -
  // pinning it unconditionally on every mount froze a snapshot that could
  // differ from window.innerHeight right after navigating in (the mobile
  // browser's address bar is still animating), producing a visible viewport
  // jump before the keyboard ever appeared.
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined' || !window.visualViewport) return;
    const viewport = window.visualViewport;
    // Do not sample visualViewport during the route transition. Mobile browsers
    // can report a transient address-bar shortfall before the first settled
    // paint; treating that as a keyboard would pin the canvas to a stale height.
    // Subsequent resize events are the reliable keyboard signal.
    const update = () => {
      // Ignore transient browser/Expo values that are not a usable viewport;
      // accepting one can collapse the entire composer during route auth.
      const height = viewport.height;
      setWebViewportHeight(height > 100 && height < window.innerHeight - 1 ? height : null);
    };
    viewport.addEventListener('resize', update);
    return () => viewport.removeEventListener('resize', update);
  }, []);

  useEffect(() => {
    if (!isRecording) return;
    setWaveSamples(previous => [...previous.slice(1), Math.max(0.04, capture.amplitude)]);
  }, [capture.amplitude, isRecording]);

  useEffect(() => () => { capture.cancel(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleMicPress = async () => {
    if (isRecording) {
      setIsRecording(false);
      setIsTranscribing(true);
      try {
        const recording = await capture.stop();
        if (recording.durationMillis < 250) throw new Error('The recording was too short. Hold the mic and speak before stopping.');
        const result = await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename);
        if (result.text?.trim()) setPromptText(previous => previous.trim() ? `${previous.trim()} ${result.text.trim()}` : result.text.trim());
      } catch (error) { setSendError(errorText(error, 'The microphone recording could not be transcribed.')); }
      finally { setIsTranscribing(false); setWaveSamples(new Array(48).fill(0.04)); }
      return;
    }
    setSendError(null);
    try { await capture.start(); setIsRecording(true); }
    catch (error) { setSendError(errorText(error, 'The microphone could not start.')); }
  };

  const loadOlderHistory = async () => {
    if (!historyBefore || historyLoading) return;
    setHistoryLoading(true);
    try {
      const result = await fetchAgentHistory(target, CHAT_HISTORY_LINES, { before: historyBefore });
      prependConversationMessages(target, result.messages.map(message => ({ id: message.id || `history-${Date.now()}-${Math.random()}`, role: message.role, kind: message.kind, text: message.text, source: 'text' as const })));
      result.messages.forEach(message => knownKeysRef.current.add(historyKey(message)));
      setHistoryBefore(result.next_before || null);
    } catch (error) { setSendError(errorText(error, 'Older chat history could not be loaded.')); }
    finally { setHistoryLoading(false); }
  };
  const handleScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const atBottom = contentOffset.y + layoutMeasurement.height >= contentSize.height - 48;
    atBottomRef.current = atBottom; setIsScrolledUp(!atBottom); if (atBottom) setHasNewMessages(false);
    if (contentOffset.y < 36) void loadOlderHistory();
  };
  const addAttachments = (selected: ComposerAttachment[]) => {
    setAttachments(current => [...current, ...selected]);
    setSendError(null);
  };
  const pickImages = async () => {
    setAttachmentMenuOpen(false);
    try {
      const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: true, quality: 1 });
      if (!result.canceled) addAttachments(result.assets.map((asset, index) => ({
        id: `image-${Date.now()}-${index}`,
        name: asset.fileName || `Image ${attachments.length + index + 1}`,
        uri: asset.uri,
        mimeType: asset.mimeType,
        size: asset.fileSize,
        kind: 'image',
      })));
    } catch (error) { setSendError(errorText(error, 'The photo library could not be opened.')); }
  };
  const pickFiles = async () => {
    setAttachmentMenuOpen(false);
    try {
      const result = await DocumentPicker.getDocumentAsync({ type: '*/*', multiple: true, copyToCacheDirectory: true });
      if (!result.canceled) addAttachments(result.assets.map((asset, index) => ({
        id: `file-${Date.now()}-${index}`,
        name: asset.name,
        uri: asset.uri,
        mimeType: asset.mimeType,
        size: asset.size,
        kind: asset.mimeType?.startsWith('image/') ? 'image' : 'file',
      })));
    } catch (error) { setSendError(errorText(error, 'The file picker could not be opened.')); }
  };
  const appendMessage = (message: ConversationMessage) => {
    knownKeysRef.current.add(historyKey(message));
    appendConversationMessage(target, message);
    if (!atBottomRef.current) setHasNewMessages(true);
    requestAnimationFrame?.(() => { if (atBottomRef.current) scrollRef.current?.scrollToEnd({ animated: true }); });
  };
  // Merges normalized Herdr events and HTTP history into the visible thread.
  // The same deduplication path is used by WebSocket delivery and polling.
  const appendHistoryMessages = (incoming: Array<{ role: 'user' | 'assistant'; kind: 'conversation' | 'tool'; text: string }>): boolean => {
    let appendedReply = false;
    incoming.forEach(message => {
      const key = historyKey(message);
      const alreadyVisible = getConversationMessages(target).some(existing => existing.text === message.text);
      if (knownKeysRef.current.has(key) || alreadyVisible) return;
      knownKeysRef.current.add(key);
      appendMessage({ id: `live-${Date.now()}-${Math.random().toString(36).slice(2)}`, role: message.role, kind: message.kind, text: message.text, sentAt: undefined, source: 'text' });
      if (message.role === 'assistant' && message.kind === 'conversation') appendedReply = true;
    });
    return appendedReply;
  };
  const syncFromHistory = async (): Promise<boolean> => {
    const result = await fetchAgentHistory(target);
    return appendHistoryMessages(result.messages);
  };

  // Herdr has no native push channel, so the gateway's event stream is an
  // acceleration path only. HTTP polling below remains the recovery path when
  // the socket is unavailable or a snapshot is transiently empty.
  useEffect(() => {
    const realtime = new RealtimeClient(target);
    const unsubscribe = realtime.subscribe(event => {
      if (event?.type !== 'agent_history' || !Array.isArray(event.messages)) return;
      if (appendHistoryMessages(event.messages)) setIsThinking(false);
    });
    realtime.connect();
    return () => { unsubscribe(); realtime.disconnect(); };
  }, [target]);
  // Live auto-refresh: whichever agent is behind `target` may produce new
  // terminal output without this device having sent the prompt (Herdr has no
  // push channel, see AGENTS.md), so poll on an interval independent of the
  // faster post-send poll below.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try { if (await syncFromHistory() && !cancelled) setIsThinking(false); }
      catch { /* Transient network/Herdr hiccup: retry on the next tick. */ }
    };
    const interval = setInterval(() => void poll(), 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [target]);
  const submitPrompt = async (trimmed: string, editId: string | null = null) => {
    const now = new Date();
    if (editId) { updateConversationMessage(target, editId, trimmed, now.getTime()); setEditingMessageId(null); }
    else appendMessage({ id: `u-${Date.now()}`, role: 'user', text: trimmed, sentAt: now.getTime(), source: 'text' });
    setPromptText(''); setSendError(null); setIsThinking(true);
    try {
      // Do not let the initial history seed race this submission (see
      // historyReadyRef). This preserves the distinction between pre-existing
      // scrollback and the reply generated by this prompt.
      await historyReadyRef.current;
      if (modelSelection && !modelSelection.available) throw new Error(modelSelection.availabilityReason || 'The selected execution profile is unavailable.');
      const response = await sendCaptainPrompt(trimmed, 'iphone', target, modelSelection?.harness, modelSelection?.model, modelSelection?.profileId ?? null);
      if (response?.status === 'error' || response?.error) throw new Error(response.error || 'The message was not accepted.');
      const reply = conversationalPromptResponse(response?.response);
      if (reply) { appendMessage({ id: `a-${Date.now()}`, role: 'assistant', kind: 'conversation', text: reply, sentAt: Date.now(), source: 'text' }); setIsThinking(false); return; }
      // Herdr acknowledges first and answers in its terminal. The WebSocket
      // normally clears this wait; HTTP polling below is the recovery path.
      const request = ++historyRequestRef.current;
      const pollForReply = async () => {
        if (request !== historyRequestRef.current) return;
        try { if (await syncFromHistory()) { setIsThinking(false); return; } }
        catch { /* Retry after transient gateway/snapshot failures. */ }
        setTimeout(() => void pollForReply(), 1000);
      };
      void pollForReply();
    } catch (error) { setPromptText(trimmed); setSendError(errorText(error, 'The message could not be sent.')); setIsThinking(false); }
  };
  useEffect(() => {
    if (isThinking || queuedPrompts.length === 0) return;
    const next = queuedPrompts[0];
    setQueuedPrompts(queue => queue.slice(1));
    void submitPrompt(next.text);
  }, [isThinking, queuedPrompts]); // eslint-disable-line react-hooks/exhaustive-deps
  const handleSend = async () => {
    const trimmed = promptText.trim();
    if (attachments.length) { setSendError('Attachments are ready, but the gateway cannot accept uploads yet. Remove them to send text only.'); return; }
    // Current-session prompts remain usable while optional routing metadata
    // loads; only an explicit profile choice needs the inventory to be ready.
    if (!routingReady && modelSelection) { setSendError('Execution settings are still unavailable; your message was not sent.'); return; }
    if (!trimmed) { if (!isThinking) router.push('/voice' as any); return; }
    if (isThinking) {
      setQueuedPrompts(queue => [...queue, { id: `q-${Date.now()}`, text: trimmed }]);
      setPromptText(''); setSendError(null); return;
    }
    await submitPrompt(trimmed, editingMessageId);
  };
  const activeMessage = messages.find(message => message.id === messageActionsId);
  const editMessage = () => {
    if (!activeMessage) return;
    setPromptText(activeMessage.text); setEditingMessageId(activeMessage.id); setMessageActionsId(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  };
  const copyMessage = async () => { if (activeMessage) await Clipboard.setStringAsync(activeMessage.text); setMessageActionsId(null); };
  const selectMessage = () => { if (activeMessage) setSelectableMessageId(activeMessage.id); setMessageActionsId(null); };

  return <KeyboardAvoidingView testID="branded-chat-shell" behavior={Platform.OS === 'ios' ? 'padding' : Platform.OS === 'android' ? 'height' : undefined} style={[styles.canvas, { backgroundColor: dark ? 'rgba(10,14,20,0.784)' : 'rgba(255,255,255,0.8)' }, webViewportHeight ? { height: webViewportHeight } : null]}>
    <View style={styles.shellHeader}>
      <TouchableOpacity testID="brand-drawer-toggle" accessibilityRole="button" accessibilityLabel={drawerOpen ? 'Collapse Magistrate drawer' : 'Open Magistrate drawer'} accessibilityState={{ expanded: drawerOpen }} onPress={onDrawerToggle} style={styles.logoButton} activeOpacity={0.72}><BrandMark dark={dark} /></TouchableOpacity>
    </View>
    <ScrollView ref={scrollRef} testID="chat-history" style={styles.chatHistory} contentContainerStyle={styles.chatHistoryContent} onScroll={handleScroll} scrollEventThrottle={16} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive" automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'} accessibilityLabel={`${targetLabel} conversation history`}>
      {filterAgentHistory(messages.map(message => ({ ...message, kind: message.kind || 'conversation' })), showToolCalls).map(message => message.role === 'user' ? <UserMessage key={message.id} message={message} textColor={text} selectable={selectableMessageId === message.id} onLongPress={() => setMessageActionsId(message.id)} /> : <View key={message.id} testID={message.kind === 'tool' ? 'tool-history-message' : 'agent-message'} style={message.kind === 'tool' ? styles.toolMessage : styles.assistantMessage}><Text selectable style={[message.kind === 'tool' ? styles.toolMessageText : styles.messageText, { color: message.kind === 'tool' ? muted : text }]}>{message.text}</Text></View>)}
    </ScrollView>
    {(hasNewMessages || isScrolledUp) ? <TouchableOpacity testID="jump-to-latest" accessibilityRole="button" accessibilityLabel="Jump to latest message" style={styles.jumpButton} onPress={() => { atBottomRef.current = true; setHasNewMessages(false); setIsScrolledUp(false); scrollRef.current?.scrollToEnd({ animated: true }); }}><Text style={styles.jumpText}>{hasNewMessages ? '↓ New message' : '↓ Latest'}</Text></TouchableOpacity> : null}
    {messageActionsId ? <View testID="message-actions" accessibilityViewIsModal style={[styles.messageActions, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}>
      <TouchableOpacity accessibilityRole="button" onPress={editMessage} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Edit</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" onPress={() => void copyMessage()} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Copy</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" onPress={selectMessage} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Select text</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close message actions" onPress={() => setMessageActionsId(null)} style={styles.messageAction}><Text style={[styles.messageActionText, { color: muted }]}>×</Text></TouchableOpacity>
    </View> : null}
    {isRecording ? <LiveWaveform samples={waveSamples} color={dark ? brand.cyan : brand.violet} /> : null}
    {attachments.length ? <ScrollView testID="attachment-preview" horizontal showsHorizontalScrollIndicator={false} style={styles.attachmentPreview} contentContainerStyle={styles.attachmentPreviewContent} keyboardShouldPersistTaps="handled">
      {attachments.map(attachment => <View key={attachment.id} testID={`attachment-${attachment.id}`} style={[styles.attachmentChip, { backgroundColor: composerSurface }]}>
        {attachment.kind === 'image' ? <Image source={{ uri: attachment.uri }} style={styles.attachmentThumbnail} resizeMode="cover" /> : <View style={[styles.attachmentFileIcon, { backgroundColor: dark ? 'rgba(36,216,255,0.12)' : 'rgba(139,108,255,0.10)' }]}><FileIcon color={dark ? brand.cyan : brand.violet} /></View>}
        <View style={styles.attachmentCopy}><Text numberOfLines={1} style={[styles.attachmentName, { color: text }]}>{attachment.name}</Text><Text style={[styles.attachmentMeta, { color: muted }]}>{attachment.kind === 'image' ? 'Image' : 'File'}{formatAttachmentSize(attachment.size) ? ` · ${formatAttachmentSize(attachment.size)}` : ''}</Text></View>
        <TouchableOpacity testID={`remove-${attachment.id}`} accessibilityRole="button" accessibilityLabel={`Remove ${attachment.name}`} onPress={() => { setAttachments(current => current.filter(item => item.id !== attachment.id)); setSendError(null); }} style={styles.attachmentRemove}><Text style={[styles.attachmentRemoveText, { color: muted }]}>×</Text></TouchableOpacity>
      </View>)}
    </ScrollView> : null}
    <View style={[styles.composer, { backgroundColor: composerSurface }]}>
      <View style={styles.attachmentControl}>
        <TouchableOpacity testID="attachment-menu-button" accessibilityRole="button" accessibilityLabel={attachmentMenuOpen ? 'Close attachment menu' : 'Add attachment'} accessibilityState={{ expanded: attachmentMenuOpen }} onPress={() => { setAttachmentMenuOpen(value => !value); setModelMenuOpen(false); }} style={styles.composerIconButton}><Text style={[styles.composerIconText, { color: attachmentMenuOpen ? brand.cyan : muted }]}>＋</Text></TouchableOpacity>
        {attachmentMenuOpen ? <View testID="attachment-menu" accessibilityViewIsModal style={[styles.attachmentMenu, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}>
          <Text style={[styles.menuTitle, { color: text }]}>Add to message</Text>
          <TouchableOpacity testID="attachment-option-images" accessibilityRole="button" accessibilityLabel="Choose photos" onPress={() => void pickImages()} style={styles.attachmentOption}><ImageIcon color={dark ? brand.cyan : brand.violet} /><View><Text style={[styles.attachmentOptionTitle, { color: text }]}>Photos</Text><Text style={[styles.attachmentOptionMeta, { color: muted }]}>Choose from your library</Text></View></TouchableOpacity>
          <TouchableOpacity testID="attachment-option-files" accessibilityRole="button" accessibilityLabel="Choose files" onPress={() => void pickFiles()} style={styles.attachmentOption}><FileIcon color={dark ? brand.cyan : brand.violet} /><View><Text style={[styles.attachmentOptionTitle, { color: text }]}>Files</Text><Text style={[styles.attachmentOptionMeta, { color: muted }]}>Browse this device</Text></View></TouchableOpacity>
        </View> : null}
      </View>
      <TextInput ref={inputRef} testID="captain-prompt" style={[styles.composerInput, { color: text }]} placeholder="Message Magi" placeholderTextColor={muted} value={promptText} onChangeText={setPromptText} onSubmitEditing={() => void handleSend()} returnKeyType="send" editable accessibilityLabel={`Message ${targetLabel}`} />
      <ModelMenu dark={dark} profiles={profiles} loading={capabilityLoading} error={capabilityError} open={modelMenuOpen} selection={modelSelection} onToggle={() => setModelMenuOpen(value => !value)} onSelect={selection => { setModelSelection(selection); onProfileChange(selection?.profileId || null); setModelMenuOpen(false); setSendError(null); }} />
      <TouchableOpacity testID="inline-mic-button" accessibilityRole="button" accessibilityLabel={isRecording ? 'Stop microphone' : 'Start microphone'} accessibilityState={{ selected: isRecording, busy: isTranscribing }} style={styles.composerIconButton} onPress={() => void handleMicPress()} disabled={isTranscribing}><MicIcon size={24} color={isRecording ? brand.cyan : muted} /></TouchableOpacity>
      <TouchableOpacity testID="send-captain-prompt" accessibilityRole="button" accessibilityLabel={promptText.trim() || attachments.length ? (isThinking ? `Queue message for ${targetLabel}` : `Send message to ${targetLabel}`) : 'Open voice mode'} accessibilityState={{ busy: isThinking }} onPress={() => void handleSend()} style={[styles.sendButton, isThinking ? styles.thinkingButton : undefined]}>{isThinking ? <Text style={styles.sendArrow}>＋</Text> : promptText.trim() || attachments.length ? <Text style={styles.sendArrow}>↑</Text> : <SoundwaveIcon color={brand.obsidian} />}</TouchableOpacity>
    </View>
    <View style={styles.composerStatus} accessibilityLiveRegion="polite">{editingMessageId ? <Text style={styles.editingLabel}>Editing message</Text> : isThinking ? <Text testID="thinking-indicator" style={styles.thinkingLabel}><ThinkingIndicator dark={dark} /> Thinking</Text> : null}{queuedPrompts.length ? <Text testID="queued-message-count" style={styles.queuedLabel}>{queuedPrompts.length} queued · sends in order</Text> : null}{sendError ? <Text testID="captain-send-error" style={styles.sendError}>{sendError}</Text> : null}</View>
  </KeyboardAvoidingView>;
}

function PanelText({ text, muted }: { text: string; muted: string }) { return <Text style={[styles.panelText, { color: muted }]}>{text}</Text>; }

function FleetAgentRow({ agent, activeStatus, dark, onOpenChat }: { agent: AgentInfo; activeStatus: string; dark: boolean; onOpenChat: () => void }) {
  const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const [menuOpen, setMenuOpen] = useState(false); const [confirmInterrupt, setConfirmInterrupt] = useState(false); const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(agent.name || ''); const [displayName, setDisplayName] = useState(agent.name || agent.id); const [busy, setBusy] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const interrupt = async () => {
    setBusy(true); setMessage(null);
    try {
      const result = await interruptAgent(agent.id);
      if (result.status === 'error' || result.error) throw new Error(result.error || 'Interrupt was not accepted.');
      setMessage('Interrupt sent.'); setConfirmInterrupt(false);
    } catch (error) { setMessage(errorText(error, 'Interrupt failed.')); }
    finally { setBusy(false); }
  };
  const rename = async () => {
    const trimmed = name.trim();
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(trimmed)) { setMessage('Use 1–32 lowercase letters, numbers, _ or -, starting with a letter.'); return; }
    setBusy(true); setMessage(null);
    try {
      const result = await renameAgent(agent.id, trimmed);
      if (result.status === 'error' || result.error) throw new Error(result.error || 'Rename was not accepted.');
      setDisplayName(trimmed); setRenaming(false); setMessage(`Renamed to ${trimmed}.`);
    } catch (error) { setMessage(errorText(error, 'Rename failed.')); }
    finally { setBusy(false); }
  };
  return <View style={[styles.fleetAgentWrap, menuOpen ? styles.fleetAgentWrapOpen : undefined]}>
    <View style={styles.fleetPanelRow}>
      <TouchableOpacity testID={`fleet-agent-${agent.id}`} accessibilityRole="button" accessibilityLabel={`Open chat with ${displayName}`} onPress={onOpenChat} activeOpacity={0.75} style={styles.fleetAgentMain}>
        <View style={[styles.tinyDot, { backgroundColor: statusColor(agent.status) }]} /><Text style={[styles.fleetPanelName, { color: text }]}>{displayName}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{displayAgentStatus(activeStatus as any)}</Text>
      </TouchableOpacity>
      <TouchableOpacity testID={`fleet-agent-${agent.id}-menu`} accessibilityRole="button" accessibilityLabel={`Agent actions for ${displayName}`} accessibilityState={{ expanded: menuOpen }} onPress={() => { setMenuOpen(value => !value); setConfirmInterrupt(false); setRenaming(false); setMessage(null); }} style={styles.ellipsisButton}><EllipsisIcon size={21.6} color={muted} /></TouchableOpacity>
    </View>
    {menuOpen ? <View testID={`fleet-agent-${agent.id}-popover`} accessibilityViewIsModal style={[styles.agentPopover, { backgroundColor: dark ? '#171E2A' : '#F4F6F9' }]}>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>STATUS</Text><Text style={[styles.agentMetaValue, { color: text }]}>{String(agent.status || 'unavailable').toUpperCase()}</Text></View>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>ACTIVE STATUS</Text><Text style={[styles.agentMetaValue, { color: activeStatus === 'active' ? brand.success : text }]}>{activeStatus === 'active' ? 'ACTIVE' : 'INACTIVE'}</Text></View>
      {renaming ? <View style={styles.renameRow}><TextInput testID={`fleet-agent-${agent.id}-rename-input`} accessibilityLabel={`New name for ${displayName}`} autoCapitalize="none" autoCorrect={false} value={name} onChangeText={setName} editable={!busy} style={[styles.renameInput, { color: text }]} /><TouchableOpacity testID={`fleet-agent-${agent.id}-rename-save`} accessibilityRole="button" onPress={() => void rename()} disabled={busy} style={styles.popoverAction}><Text style={styles.popoverActionText}>{busy ? '…' : 'SAVE'}</Text></TouchableOpacity></View> : null}
      {confirmInterrupt ? <View style={styles.confirmInterruptRow}><Text style={[styles.confirmInterruptText, { color: muted }]}>Interrupt current work?</Text><TouchableOpacity accessibilityRole="button" onPress={() => setConfirmInterrupt(false)}><Text style={[styles.popoverLink, { color: muted }]}>CANCEL</Text></TouchableOpacity><TouchableOpacity testID={`fleet-agent-${agent.id}-interrupt-confirm`} accessibilityRole="button" onPress={() => void interrupt()} disabled={busy}><Text style={[styles.popoverLink, { color: brand.critical }]}>{busy ? '…' : 'CONFIRM'}</Text></TouchableOpacity></View> : null}
      {!renaming && !confirmInterrupt ? <View style={styles.popoverActions}><TouchableOpacity testID={`fleet-agent-${agent.id}-interrupt`} accessibilityRole="button" onPress={() => setConfirmInterrupt(true)} style={styles.popoverAction}><Text style={[styles.popoverActionText, { color: brand.critical }]}>INTERRUPT</Text></TouchableOpacity><TouchableOpacity testID={`fleet-agent-${agent.id}-rename`} accessibilityRole="button" onPress={() => setRenaming(true)} style={styles.popoverAction}><Text style={styles.popoverActionText}>RENAME</Text></TouchableOpacity></View> : null}
      {message ? <Text accessibilityLiveRegion="polite" style={[styles.agentActionMessage, { color: muted }]}>{message}</Text> : null}
    </View> : null}
  </View>;
}

function activityDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function DrawerPanel({ open, dark, isNarrow, animatedStyle, panHandlers, activeSection, setActiveSection, onOpenSettings, onOpenAgent, agents, attention, activity, providers, errors, loading }: {
  open: boolean; dark: boolean; isNarrow: boolean; animatedStyle: object; panHandlers: object; activeSection: DrawerSection; setActiveSection: (section: DrawerSection) => void; onOpenSettings: () => void;
  onOpenAgent: (agentId: string) => void;
  agents: AgentInfo[]; attention: UnifiedAttentionRecord[]; activity: RecentActivityItem[]; providers: AuthProviderInfo[]; errors: { agents?: string | null; attention?: string | null; activity?: string | null; providers?: string | null }; loading: boolean;
}) {
  const router = useRouter();
  const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const fleet = summarizeAgents(agents); const activeAttention = attention.filter(item => item.requires_action !== false);
  const toggleSection = (section: DrawerSection) => setActiveSection(activeSection === section ? null : section);
  const openAttentionItem = async (item: UnifiedAttentionRecord) => {
    if (item.url?.startsWith('/')) router.push(item.url as any);
    else { const result = await openExternalUrl(item.external_url || item.url); if (!result.ok) Alert.alert('Unable to open attention item', result.message); }
  };
  const openActivityItem = async (item: RecentActivityItem) => {
    if (item.pull_request_number) router.push(`/pr-detail?number=${item.pull_request_number}` as any);
    else if (item.url) { const result = await openExternalUrl(item.url); if (!result.ok) Alert.alert('Unable to open activity', result.message); }
  };
  const rows = [{ key: 'attention' as const, icon: '!', title: 'Attention', count: activeAttention.length }, { key: 'fleet' as const, icon: '⌘', title: 'Fleet Summary', count: agents.length }, { key: 'activity' as const, icon: '↗', title: 'Recent Activity' }, { key: 'connections' as const, icon: '⌁', title: 'Connections' }];
  return <Animated.View pointerEvents={open ? 'auto' : 'none'} accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="magistrate-drawer" style={[styles.drawer, isNarrow ? styles.drawerMobile : styles.drawerDesktop, { backgroundColor: dark ? 'rgba(10,14,20,0.98)' : 'rgba(255,255,255,0.98)' }, animatedStyle]} {...panHandlers}>
    <Text style={[styles.drawerWordmark, { color: text }]}>Magistrate</Text>
    <ScrollView style={styles.drawerScroll} contentContainerStyle={styles.drawerScrollContent} keyboardShouldPersistTaps="handled">
      {rows.map(row => <View key={row.key}>
        <TouchableOpacity testID={`drawer-section-${row.key}`} accessibilityRole="button" accessibilityLabel={`${row.title} section`} accessibilityState={{ expanded: activeSection === row.key }} onPress={() => toggleSection(row.key)} style={styles.drawerRow}>
          <Text testID={`drawer-section-${row.key}-icon`} style={[styles.drawerIcon, { color: muted }]}>{row.icon}</Text><Text style={[styles.drawerRowText, { color: text }]}>{row.title}</Text>{typeof row.count === 'number' ? <Text style={[styles.drawerCount, { color: muted }]}>{row.count}</Text> : null}<View style={styles.chevron} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" />
        </TouchableOpacity>
        {activeSection === row.key ? <View testID={`drawer-panel-${row.key}`} style={styles.sectionPanel}>{row.key === 'attention' ? (
          loading ? <PanelText text="Loading attention…" muted={muted} /> : errors.attention ? <PanelText text={errors.attention} muted={brand.critical} /> : activeAttention.length === 0 ? <PanelText text="Nothing requires your attention." muted={muted} /> : activeAttention.slice(0, 5).map(item => <TouchableOpacity key={item.id} testID={`attention-item-${item.id}`} onPress={() => void openAttentionItem(item)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{providerLabel(item.provider)} · {item.subtitle}</Text></TouchableOpacity>)
        ) : row.key === 'fleet' ? (
          loading ? <PanelText text="Loading fleet…" muted={muted} /> : errors.agents ? <PanelText text={errors.agents} muted={brand.critical} /> : agents.length === 0 ? <PanelText text="No live agent sessions are available." muted={muted} /> : fleet.ordered.map(({ agent, displayStatus }) => <FleetAgentRow key={agent.id} agent={agent} activeStatus={displayStatus} dark={dark} onOpenChat={() => onOpenAgent(agent.id)} />)
        ) : row.key === 'activity' ? (
          loading ? <PanelText text="Loading recent activity…" muted={muted} /> : errors.activity ? <PanelText text={errors.activity} muted={brand.critical} /> : activity.length === 0 ? <PanelText text="No recent activity is available." muted={muted} /> : activity.slice(0, 8).map(item => <TouchableOpacity key={item.id} disabled={!item.url && !item.pull_request_number} onPress={() => void openActivityItem(item)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{item.description} · {item.project}{activityDate(item.occurred_at) ? ` · ${activityDate(item.occurred_at)}` : ''}</Text></TouchableOpacity>)
        ) : errors.providers ? <PanelText text={errors.providers} muted={brand.critical} /> : providers.length === 0 ? <PanelText text="No connected account data is available." muted={muted} /> : providers.map(provider => <View key={provider.provider} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{provider.provider}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{provider.status}{provider.username ? ` · ${provider.username}` : ''}</Text></View>)}</View> : null}
      </View>)}
    </ScrollView>
    <View style={styles.drawerBottom}><TouchableOpacity testID="settings-open" accessibilityRole="button" accessibilityLabel="Open Account settings" onPress={onOpenSettings} activeOpacity={0.75} style={styles.accountRow}><Text testID="drawer-account-icon" style={[styles.accountIcon, { color: muted }]}>○</Text><Text style={[styles.drawerRowText, { color: text }]}>Account</Text><View style={styles.gearIconContainer}><GearIcon size={21.6} color={muted} /></View></TouchableOpacity></View>
  </Animated.View>;
}

const backgroundOptions: Array<{ key: WeatherSceneKey; label: string }> = [
  { key: 'auto', label: 'Automatic' }, { key: 'dusk-mountain', label: 'Dusk' }, { key: 'clear-day', label: 'Day' }, { key: 'clear-night', label: 'Night' }, { key: 'clouds', label: 'Clouds' }, { key: 'rain', label: 'Rain' }, { key: 'storm', label: 'Storm' }, { key: 'sunset', label: 'Sunset' }, { key: 'minimal-dark', label: 'Minimal' },
];
const themeOptions: Array<{ key: ChatThemeMode; label: string }> = [
  { key: 'system', label: 'System' }, { key: 'dark', label: 'Dark' }, { key: 'light', label: 'Light' },
];

function SettingsSheet({ open, dark, animatedStyle, health, loading, error, executionError, preferences, onPreferencesChange, executionProfiles, executionSettings, onExecutionSettingsChange, onSaveCredential, usage, usageLoading, usageError, onClose, onLogout }: { open: boolean; dark: boolean; animatedStyle: object; health: HealthInfo | null; loading: boolean; error: string | null; executionError?: string | null; preferences: ChatPreferences; onPreferencesChange: (preferences: ChatPreferences) => void; executionProfiles: ExecutionProfile[]; executionSettings: ExecutionSettings; onExecutionSettingsChange: (update: Partial<Pick<ExecutionSettings, 'profile_id' | 'switching_behavior' | 'unavailable_behavior'>>) => void; onSaveCredential: (credentialKey: string, credential: string) => Promise<void>; usage: UsageProvider[]; usageLoading: boolean; usageError: string | null; onClose: () => void; onLogout: () => void }) {
  const router = useRouter(); const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [credentialKey, setCredentialKey] = useState('');
  const [credential, setCredential] = useState('');
  const pickCustomBackground = async () => {
    try {
      const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!permission.granted) { Alert.alert('Permission required', 'Media library access is needed to choose a background.'); return; }
      const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, allowsEditing: true, quality: 0.85 });
      if (result.canceled || !result.assets?.length) return;
      const asset = result.assets[0];
      if (asset.fileSize && asset.fileSize > 10 * 1024 * 1024) { Alert.alert('Photo too large', 'Choose an image smaller than 10 MB.'); return; }
      if (asset.mimeType && !asset.mimeType.startsWith('image/')) { Alert.alert('Unsupported file', 'Choose a supported image file.'); return; }
      const next = { ...preferences, background: 'custom' as WeatherSceneKey, customBackgroundUri: asset.uri };
      onPreferencesChange(next);
      try { await saveCustomBackground(asset.uri); }
      catch { setActiveBackground(preferences.background, preferences.customBackgroundUri); onPreferencesChange(preferences); Alert.alert('Background unavailable', 'The custom background could not be saved.'); }
    } catch { Alert.alert('Background unavailable', 'The custom background could not be selected.'); }
  };
  const removeCustom = async () => {
    const next = { ...preferences, background: 'auto' as WeatherSceneKey, customBackgroundUri: undefined };
    onPreferencesChange(next);
    try { await removeCustomBackground(); }
    catch { setActiveBackground(preferences.background, preferences.customBackgroundUri); onPreferencesChange(preferences); Alert.alert('Background unavailable', 'The custom background could not be removed.'); }
  };
  useEffect(() => { if (!open) setAppearanceOpen(false); }, [open]);
  const providers = Array.from(new Map(executionProfiles.map(profile => [profile.provider.id, profile.provider.label])).entries());
  const network = health?.status === 'healthy'; const runtime = Boolean(health?.herdr_socket_connected);
  return <Animated.View pointerEvents={open ? 'auto' : 'none'} accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="settings-sheet" style={[styles.settingsSheet, { backgroundColor: dark ? brand.command : '#FFFFFF' }, animatedStyle]}>
    <TouchableOpacity testID="settings-close" accessibilityRole="button" accessibilityLabel="Close settings" onPress={onClose} style={styles.settingsClose}><Text style={[styles.settingsCloseText, { color: text }]}>×</Text></TouchableOpacity>
    <Text style={[styles.settingsTitle, { color: text }]}>Settings</Text>
    <ScrollView testID="settings-scroll" style={styles.settingsScroll} contentContainerStyle={styles.settingsScrollContent} keyboardShouldPersistTaps="handled">
    <View style={styles.settingsStatusGrid}><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: error ? brand.critical : loading ? brand.attention : network ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Network</Text><Text testID="settings-network-status" style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : error ? 'Unavailable' : network ? 'Connected' : 'Degraded'}</Text></View></View><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: runtime ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Runtime</Text><Text style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : runtime ? 'Live' : 'Unavailable'}</Text></View></View></View>
    {error || executionError ? <Text style={styles.settingsError}>{error || executionError}</Text> : null}
    <View testID="settings-execution-section" style={styles.settingsSection}>
    <Text style={[styles.settingsSectionTitle, { color: text }]}>Execution</Text>
    <Text style={[styles.preferenceLabel, { color: muted }]}>ROUTING PREFERENCE</Text>
    <Text style={[styles.settingsToggleDescription, { color: muted }]}>Selection is saved to this Magistrate account. Runtime migration is not available yet; selected profiles are sent as explicit prompt context.</Text>
    <View style={styles.optionRow}>{[
      { key: 'migrate' as const, label: 'Migrate session' }, { key: 'new-session' as const, label: 'New session' },
    ].map(option => <TouchableOpacity key={option.key} testID={`switching-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.switching_behavior === option.key }} onPress={() => onExecutionSettingsChange({ switching_behavior: option.key })} style={[styles.optionPill, executionSettings.switching_behavior === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.switching_behavior === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
    <View style={styles.optionRow}>{[
      { key: 'error' as const, label: 'Error if unavailable' }, { key: 'fallback' as const, label: 'Fallback to current' },
    ].map(option => <TouchableOpacity key={option.key} testID={`unavailable-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.unavailable_behavior === option.key }} onPress={() => onExecutionSettingsChange({ unavailable_behavior: option.key })} style={[styles.optionPill, executionSettings.unavailable_behavior === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.unavailable_behavior === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
    {providers.length ? <View style={styles.credentialBlock}><Text style={[styles.preferenceLabel, { color: muted }]}>HARNESS CREDENTIALS</Text><View style={styles.optionRow}>{providers.map(([key, label]) => <TouchableOpacity key={key} testID={`credential-provider-${key}`} accessibilityRole="button" accessibilityState={{ selected: credentialKey === key }} onPress={() => setCredentialKey(key)} style={[styles.optionPill, credentialKey === key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: credentialKey === key ? brand.obsidian : text }]}>{label}</Text></TouchableOpacity>)}</View>{credentialKey ? <View style={styles.credentialInputRow}><TextInput testID="execution-credential-input" accessibilityLabel={`Credential for ${credentialKey}`} secureTextEntry value={credential} onChangeText={setCredential} placeholder="Paste credential (stored encrypted)" placeholderTextColor={muted} style={[styles.credentialInput, { color: text }]} /><TouchableOpacity testID="execution-credential-save" accessibilityRole="button" disabled={!credential.trim()} onPress={() => { const value = credential.trim(); setCredential(''); void onSaveCredential(credentialKey, value); }} style={styles.credentialSave}><Text style={styles.credentialSaveText}>SAVE</Text></TouchableOpacity></View> : null}</View> : null}
    </View>
    <View testID="settings-usage-section" style={styles.settingsSection}>
      <Text style={[styles.settingsSectionTitle, { color: text }]}>Usage</Text>
      <Text style={[styles.settingsToggleDescription, { color: muted }]}>Authenticated quota data only. Missing or unavailable amounts stay explicitly unknown.</Text>
      {usageLoading ? <PanelText text="Loading authenticated usage…" muted={muted} /> : usageError ? <PanelText text={usageError} muted={brand.critical} /> : usage.length === 0 ? <PanelText text="Usage is unknown; no authenticated quota data is available." muted={muted} /> : usage.map(item => <View key={item.provider} style={styles.settingsUsageItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.provider}{item.plan ? ` · ${item.plan}` : ''}</Text><Text style={[styles.panelItemMeta, { color: item.status === 'fresh' ? muted : brand.attention }]}>{item.status === 'fresh' && item.windows.length ? item.windows.map(window => `${window.label || window.id || 'window'}: ${typeof window.percentRemaining === 'number' ? `${window.percentRemaining}% left` : typeof window.spentUsd === 'number' && typeof window.limitUsd === 'number' ? `$${window.spentUsd} / $${window.limitUsd}` : 'amount unknown'}`).join(' · ') : item.status === 'auth_required' ? 'Authentication required' : item.error || 'Quota unknown'}</Text></View>)}
    </View>
    <TouchableOpacity testID="settings-theme" accessibilityRole="button" accessibilityLabel="Open theme settings" accessibilityState={{ expanded: appearanceOpen }} onPress={() => setAppearanceOpen(true)} style={styles.diagnosticsButton}><View><Text style={[styles.diagnosticsButtonText, { color: text }]}>Appearance</Text><Text style={[styles.settingsToggleDescription, { color: muted }]}>Theme, background, and chat display</Text></View><Text style={[styles.diagnosticsArrow, { color: muted }]}>›</Text></TouchableOpacity>
    <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open diagnostics" onPress={() => { onClose(); router.push('/diagnostics' as any); }} style={styles.diagnosticsButton}><Text style={[styles.diagnosticsButtonText, { color: text }]}>Diagnostics</Text><Text style={[styles.diagnosticsArrow, { color: muted }]}>↗</Text></TouchableOpacity>
    <TouchableOpacity testID="settings-logout" accessibilityRole="button" accessibilityLabel="Sign out of Magistrate" onPress={onLogout} style={styles.logoutButton}><Text style={styles.logoutButtonText}>SIGN OUT</Text></TouchableOpacity>
    {appearanceOpen ? <View testID="settings-appearance-window" accessibilityViewIsModal style={[styles.appearanceWindow, { backgroundColor: dark ? '#171E2A' : '#F4F6F9' }]}>
      <View style={styles.appearanceHeader}><Text style={[styles.appearanceTitle, { color: text }]}>Appearance</Text><TouchableOpacity testID="settings-appearance-close" accessibilityRole="button" accessibilityLabel="Close appearance settings" onPress={() => setAppearanceOpen(false)} style={styles.appearanceClose}><Text style={[styles.settingsCloseText, { color: text }]}>×</Text></TouchableOpacity></View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>BACKGROUND</Text>
      <View style={styles.optionRow}>{backgroundOptions.map(option => <TouchableOpacity key={option.key} testID={`background-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: preferences.background === option.key }} onPress={() => { const next = { ...preferences, background: option.key, customBackgroundUri: undefined }; onPreferencesChange(next); void saveChatBackground(option.key); }} style={[styles.optionPill, preferences.background === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.background === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
      {preferences.customBackgroundUri ? <View style={styles.customBackgroundRow}><Image source={{ uri: preferences.customBackgroundUri }} style={styles.customBackgroundPreview} resizeMode="cover" accessibilityLabel="Custom background preview" /><View style={styles.customBackgroundCopy}><Text style={[styles.settingsToggleTitle, { color: text }]}>Custom background</Text><Text style={[styles.settingsToggleDescription, { color: muted }]}>Stored on this device and used only when selected.</Text></View><TouchableOpacity testID="settings-custom-background-remove" accessibilityRole="button" onPress={() => void removeCustom()} style={styles.secondaryAction}><Text style={[styles.secondaryActionText, { color: brand.critical }]}>Remove</Text></TouchableOpacity></View> : null}
      <TouchableOpacity testID="settings-custom-background-upload" accessibilityRole="button" onPress={() => void pickCustomBackground()} style={styles.uploadBackgroundButton}><Text style={[styles.optionText, { color: text }]}>{preferences.customBackgroundUri ? 'Replace custom photo' : 'Upload custom photo'}</Text></TouchableOpacity>
      <Text style={[styles.preferenceLabel, { color: muted }]}>MODE</Text>
      <View testID="settings-theme-options" style={styles.optionRow}>{themeOptions.map(option => <TouchableOpacity key={option.key} testID={`theme-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: preferences.themeMode === option.key }} onPress={() => { const next = { ...preferences, themeMode: option.key }; onPreferencesChange(next); void saveThemeMode(option.key); }} style={[styles.optionPill, preferences.themeMode === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.themeMode === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
      <View style={styles.settingsToggleRow}><View style={styles.settingsToggleCopy}><Text style={[styles.settingsToggleTitle, { color: text }]}>Show tool calls</Text><Text style={[styles.settingsToggleDescription, { color: muted }]}>Include tool activity in agent conversations.</Text></View><Switch testID="settings-tool-calls-toggle" accessibilityLabel="Show tool calls in chat history" value={preferences.showToolCalls} onValueChange={value => { const next = { ...preferences, showToolCalls: value }; onPreferencesChange(next); void saveToolCallVisibility(value); }} trackColor={{ false: '#424B59', true: brand.cyan }} thumbColor={preferences.showToolCalls ? brand.obsidian : '#F4F5F7'} /></View>
    </View> : null}
    </ScrollView>
  </Animated.View>;
}

export default function ChatScreen() {
  const { agentId } = useLocalSearchParams<{ agentId?: string | string[] }>(); const target = Array.isArray(agentId) ? agentId[0] : agentId;
  const router = useRouter();
  const dark = isDarkTheme(useChatColorScheme()); const { width, height } = useWindowDimensions(); const isNarrow = width < 720; const drawerWidth = Math.min(isNarrow ? width * 0.82 : 310, 330);
  const [drawerOpen, setDrawerOpen] = useState(false); const [settingsOpen, setSettingsOpen] = useState(false); const [activeSection, setActiveSection] = useState<DrawerSection>(null); const [preferences, setPreferences] = useState<ChatPreferences>(DEFAULT_CHAT_PREFERENCES); const [preferencesReady, setPreferencesReady] = useState(false);
  const [executionProfiles, setExecutionProfiles] = useState<ExecutionProfile[]>([]);
  const [executionSettings, setExecutionSettings] = useState<ExecutionSettings>({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
  const [executionLoading, setExecutionLoading] = useState(true);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [executionReady, setExecutionReady] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]); const [attention, setAttention] = useState<UnifiedAttentionRecord[]>([]); const [activity, setActivity] = useState<RecentActivityItem[]>([]); const [providers, setProviders] = useState<AuthProviderInfo[]>([]); const [usage, setUsage] = useState<UsageProvider[]>([]); const [usageLoading, setUsageLoading] = useState(false); const [usageError, setUsageError] = useState<string | null>(null); const [health, setHealth] = useState<HealthInfo | null>(null);
  const [loading, setLoading] = useState(true); const [healthLoading, setHealthLoading] = useState(true); const [healthError, setHealthError] = useState<string | null>(null); const [reducedMotion, setReducedMotion] = useState(false);
  const [errors, setErrors] = useState<{ agents?: string | null; attention?: string | null; activity?: string | null; providers?: string | null }>({});
  const drawerProgress = useSharedValue(0); const settingsProgress = useSharedValue(0);
  useEffect(() => { let mounted = true; loadChatPreferences().then(value => { if (mounted) setPreferences(value); }).catch(() => {}).finally(() => { if (mounted) setPreferencesReady(true); }); return () => { mounted = false; }; }, []);
  useEffect(() => {
    let mounted = true;
    Promise.allSettled([fetchExecutionCapabilities(), fetchExecutionSettings()]).then(([capabilityResult, settingsResult]) => {
      if (!mounted) return;
      if (capabilityResult.status === 'fulfilled') setExecutionProfiles(profilesFromCapabilities(capabilityResult.value));
      else setExecutionError(errorText(capabilityResult.reason, 'Execution capabilities could not be loaded.'));
      if (settingsResult.status === 'fulfilled') { setExecutionSettings(settingsResult.value); setExecutionReady(true); }
      else setExecutionError(errorText(settingsResult.reason, 'Execution settings could not be loaded.'));
    }).finally(() => { if (mounted) setExecutionLoading(false); });
    return () => { mounted = false; };
  }, []);
  useEffect(() => { AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion); const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion); return () => sub.remove(); }, []);
  useEffect(() => { drawerProgress.value = withTiming(drawerOpen ? 1 : 0, { duration: reducedMotion ? 1 : drawerOpen ? 260 : 340, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [drawerOpen, drawerProgress, reducedMotion]);
  useEffect(() => { settingsProgress.value = withTiming(settingsOpen ? 1 : 0, { duration: reducedMotion ? 1 : 300, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [settingsOpen, settingsProgress, reducedMotion]);
  useEffect(() => {
    let mounted = true;
    Promise.allSettled([fetchAgents(), fetchUnifiedAttention(), fetchRecentActivity(), fetchAuthProviders(), fetchHealth()]).then(([agentResult, attentionResult, activityResult, providerResult, healthResult]) => {
      if (!mounted) return;
      setErrors({ agents: agentResult.status === 'rejected' ? errorText(agentResult.reason, 'Agent data could not be loaded.') : null, attention: attentionResult.status === 'rejected' ? errorText(attentionResult.reason, 'Attention data could not be loaded.') : null, activity: activityResult.status === 'rejected' ? errorText(activityResult.reason, 'Recent activity could not be loaded.') : null, providers: providerResult.status === 'rejected' ? errorText(providerResult.reason, 'Connections data could not be loaded.') : null });
      if (agentResult.status === 'fulfilled') setAgents(agentResult.value); if (attentionResult.status === 'fulfilled') setAttention(attentionResult.value); if (activityResult.status === 'fulfilled') setActivity(activityResult.value.items); if (providerResult.status === 'fulfilled') setProviders(providerResult.value);
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value); else setHealthError(errorText(healthResult.reason, 'Network status could not be loaded.'));
      setLoading(false); setHealthLoading(false);
    }); return () => { mounted = false; };
  }, []);
  useEffect(() => {
    if (!settingsOpen) return;
    setUsageLoading(true); setUsageError(null);
    fetchUsage().then(result => setUsage(result.providers)).catch(error => setUsageError(errorText(error, 'Usage data could not be loaded.'))).finally(() => setUsageLoading(false));
  }, [settingsOpen]);
  const drawerAnimatedStyle = useAnimatedStyle(() => ({ opacity: drawerProgress.value, transform: [{ translateX: interpolate(drawerProgress.value, [0, 1], [-(drawerWidth + 70), 0]) }] }), [drawerWidth]);
  const chatAnimatedStyle = useAnimatedStyle(() => ({ transform: [{ translateX: isNarrow ? drawerProgress.value * drawerWidth : 0 }] }), [drawerWidth, isNarrow]);
  const settingsAnimatedStyle = useAnimatedStyle(() => ({ opacity: settingsProgress.value, transform: [{ translateY: interpolate(settingsProgress.value, [0, 1], [420, 0]) }] }));
  const swipeToClose = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => isNarrow && drawerOpen && g.dx < -8 && Math.abs(g.dx) > Math.abs(g.dy),
    onPanResponderRelease: (_, g) => { if (g.dx < -55 || g.vx < -0.35) setDrawerOpen(false); },
  }), [drawerOpen, isNarrow]);
  // Composer sits in the bottom ~140px; excluding that band keeps this from hijacking text-selection drags there.
  const swipeToOpen = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => isNarrow && !drawerOpen && g.y0 < height - 140 && g.dx > 8 && Math.abs(g.dx) > Math.abs(g.dy),
    onPanResponderRelease: (_, g) => { if (g.dx > 55 || g.vx > 0.35) setDrawerOpen(true); },
  }), [drawerOpen, isNarrow, height]);
  return <EnvironmentBackground hideBottomControls><SafeAreaView style={styles.page} {...(isNarrow ? swipeToOpen.panHandlers : {})}>
    {!preferencesReady ? <View testID="chat-appearance-loading" style={[styles.appearanceLoading, { backgroundColor: dark ? brand.obsidian : '#F7F8FA' }]} /> : <>
      <DrawerPanel open={drawerOpen} dark={dark} isNarrow={isNarrow} animatedStyle={drawerAnimatedStyle} panHandlers={isNarrow ? swipeToClose.panHandlers : {}} activeSection={activeSection} setActiveSection={setActiveSection} onOpenSettings={() => setSettingsOpen(true)} onOpenAgent={selectedAgentId => { setDrawerOpen(false); router.push({ pathname: '/chat', params: { agentId: selectedAgentId } } as any); }} agents={agents} attention={attention} activity={activity} providers={providers} errors={errors} loading={loading} />
      <Animated.View style={[styles.chatStage, chatAnimatedStyle]}><ChatCanvas target={target || 'captain'} showToolCalls={preferences.showToolCalls} drawerOpen={drawerOpen} onDrawerToggle={() => setDrawerOpen(value => !value)} profiles={executionProfiles} capabilityLoading={executionLoading} capabilityError={executionError} selectedProfileId={executionSettings.profile_id} routingReady={executionReady} onProfileChange={profileId => { setExecutionSettings(current => ({ ...current, profile_id: profileId })); void updateExecutionSettings({ profile_id: profileId }).catch(error => setExecutionError(errorText(error, 'The routing preference could not be saved.'))); }} /></Animated.View>
      <SettingsSheet open={settingsOpen} dark={dark} animatedStyle={settingsAnimatedStyle} health={health} loading={healthLoading} error={healthError} executionError={executionError} preferences={preferences} onPreferencesChange={setPreferences} executionProfiles={executionProfiles} executionSettings={executionSettings} onExecutionSettingsChange={update => { setExecutionSettings(current => ({ ...current, ...update })); void updateExecutionSettings(update).catch(error => setExecutionError(errorText(error, 'The execution setting could not be saved.'))); }} onSaveCredential={async (credentialKey, credential) => { try { await saveExecutionCredential(credentialKey, credential); setExecutionError(null); const capabilities = await fetchExecutionCapabilities(); setExecutionProfiles(profilesFromCapabilities(capabilities)); } catch (error) { setExecutionError(errorText(error, 'The credential could not be saved.')); } }} usage={usage} usageLoading={usageLoading} usageError={usageError} onClose={() => setSettingsOpen(false)} onLogout={() => { setSettingsOpen(false); void logoutGatewaySession(); }} />
    </>}
  </SafeAreaView></EnvironmentBackground>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minWidth: 0, overflow: 'hidden', touchAction: 'pan-y' } as any, chatStage: { flex: 1, minWidth: 0, padding: 8, zIndex: 1 }, canvas: { flex: 1, minWidth: 0, borderRadius: 26, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 8, overflow: 'hidden' },
  shellHeader: { height: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', zIndex: 3 }, logoButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' }, mark: { width: 37, height: 37 }, tinyDot: { width: 8, height: 8, borderRadius: 4 },
  chatHistory: { flex: 1, minHeight: 0 }, chatHistoryContent: { flexGrow: 1, justifyContent: 'flex-end', paddingTop: 22, paddingHorizontal: 22, paddingBottom: 20, gap: 14 }, userMessage: { maxWidth: 680, alignSelf: 'flex-end', paddingVertical: 11, paddingHorizontal: 16, borderRadius: 22, backgroundColor: 'rgba(36,216,255,0.15)' }, assistantMessage: { maxWidth: 680, alignSelf: 'flex-start', paddingVertical: 8, paddingHorizontal: 2 }, toolMessage: { maxWidth: 680, alignSelf: 'flex-start', paddingVertical: 7, paddingHorizontal: 12, borderLeftWidth: 2, borderLeftColor: 'rgba(142,153,170,0.45)' }, toolMessageText: { fontFamily: 'monospace', fontSize: 12, lineHeight: 18 }, messageText: { fontSize: 16, lineHeight: 23 }, messageTimestamp: { color: 'rgba(142,153,170,0.9)', fontSize: 10, marginTop: 6, textAlign: 'right' },
  jumpButton: { position: 'absolute', alignSelf: 'center', bottom: 90, backgroundColor: brand.cyan, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8, zIndex: 5 }, jumpText: { color: brand.obsidian, fontSize: 12, fontWeight: '800' },
  messageActions: { position: 'absolute', right: 24, bottom: 82, flexDirection: 'row', borderRadius: 18, padding: 4, zIndex: 12, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 20, elevation: 8 }, messageAction: { minWidth: 52, height: 40, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8 }, messageActionText: { fontSize: 13, fontWeight: '700' },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 4, minHeight: 60, borderRadius: 30, paddingHorizontal: 9, paddingVertical: 7, marginHorizontal: 8, zIndex: 10 }, composerIconButton: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, composerIconText: { fontSize: 21, fontWeight: '500' }, composerInput: { flex: 1, minWidth: 0, fontSize: 16, paddingVertical: 8, outlineStyle: 'none' as any }, sendButton: { width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center', backgroundColor: brand.violet }, thinkingButton: { backgroundColor: brand.cyan }, sendArrow: { color: brand.paper, fontSize: 22, fontWeight: '800' }, disabled: { opacity: 0.55 }, composerStatus: { minHeight: 22, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 18 }, editingLabel: { color: brand.cyan, fontSize: 11, fontWeight: '700' }, thinkingLabel: { color: brand.mutedDark, fontSize: 11, fontWeight: '700', alignItems: 'center' }, thinkingDots: { fontSize: 16, letterSpacing: 2, fontWeight: '900' }, queuedLabel: { color: brand.violet, fontSize: 11, fontWeight: '700' }, sendError: { color: '#FFB4B2', fontSize: 12, flex: 1, textAlign: 'right' },
  attachmentControl: { width: 36, zIndex: 20 }, attachmentMenu: { position: 'absolute', left: -2, bottom: 46, width: 238, borderRadius: 20, padding: 11, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 24, elevation: 14 }, attachmentOption: { minHeight: 54, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 8, paddingVertical: 7, borderRadius: 13 }, attachmentOptionTitle: { fontSize: 14, fontWeight: '700' }, attachmentOptionMeta: { fontSize: 11, marginTop: 2 },
  attachmentPreview: { flexGrow: 0, marginHorizontal: 8, marginBottom: 7, maxHeight: 60 }, attachmentPreviewContent: { gap: 8, paddingHorizontal: 3 }, attachmentChip: { width: 220, minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 9, padding: 5, paddingRight: 7, borderRadius: 15 }, attachmentThumbnail: { width: 46, height: 46, borderRadius: 11 }, attachmentFileIcon: { width: 46, height: 46, borderRadius: 11, alignItems: 'center', justifyContent: 'center' }, attachmentCopy: { flex: 1, minWidth: 0 }, attachmentName: { fontSize: 12, fontWeight: '700' }, attachmentMeta: { fontSize: 10, marginTop: 3 }, attachmentRemove: { width: 28, height: 38, alignItems: 'center', justifyContent: 'center' }, attachmentRemoveText: { fontSize: 21, lineHeight: 23 },
  liveWaveform: { position: 'absolute', left: 8, right: 8, bottom: 72, height: 52, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', paddingHorizontal: 14, zIndex: 9 }, liveWaveformBar: { width: 3, borderRadius: 2 },
  modelControl: { width: 40, zIndex: 15 }, modelButton: { height: 34, width: 34, alignItems: 'center', justifyContent: 'center' }, modelMenu: { position: 'absolute', right: -8, bottom: 44, width: 280, maxHeight: 350, borderRadius: 22, padding: 12, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 24, elevation: 10 }, modelOptionsScroll: { maxHeight: 235 }, menuTitle: { fontSize: 14, fontWeight: '800', marginBottom: 7, paddingHorizontal: 7 }, harnessLabel: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8, textTransform: 'uppercase', paddingHorizontal: 7, paddingTop: 8 }, modelOption: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 9, paddingHorizontal: 7, paddingVertical: 6 }, modelOptionDisabled: { opacity: 0.5 }, modelOptionCopy: { flex: 1 }, modelOptionTitle: { fontSize: 13, fontWeight: '700' }, modelOptionMeta: { fontSize: 11, lineHeight: 15, marginTop: 2 }, selectionDot: { width: 7, height: 7, borderRadius: 4 }, modelNotice: { fontSize: 11, lineHeight: 16, paddingHorizontal: 7, paddingTop: 8 }, modelError: { color: '#FFB4B2', fontSize: 11, lineHeight: 16, paddingHorizontal: 7, paddingTop: 8 },
  drawer: { position: 'absolute', top: 8, bottom: 8, width: 310, zIndex: 10, borderRadius: 24, padding: 14, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.28, shadowRadius: 28, elevation: 12 }, drawerDesktop: { left: 58 }, drawerMobile: { left: 8, width: '82%' }, drawerWordmark: { fontFamily: Platform.select({ web: 'Bodoni Moda, Times New Roman, serif', default: undefined }), fontSize: 25, lineHeight: 32, fontWeight: '500', marginLeft: 4, marginBottom: 13 }, drawerScroll: { flex: 1, minHeight: 0 }, drawerScrollContent: { paddingBottom: 12 }, drawerRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 4 }, drawerIcon: { width: 20, fontSize: 16.8, fontWeight: '800', textAlign: 'center' }, gearIconContainer: { width: 20, alignItems: 'center', justifyContent: 'center' }, drawerRowText: { flex: 1, fontSize: 15, fontWeight: '400', textAlign: 'left' }, drawerCount: { fontSize: 11, fontWeight: '800' }, chevron: { width: 18, fontSize: 13, textAlign: 'center' }, sectionPanel: { paddingLeft: 30, paddingRight: 4, paddingBottom: 10, gap: 7 }, panelText: { fontSize: 13, lineHeight: 19 }, panelItem: { paddingVertical: 6 }, panelItemTitle: { fontSize: 13, fontWeight: '800', marginBottom: 2 }, panelItemMeta: { fontSize: 12, lineHeight: 17 }, fleetAgentWrap: { borderRadius: 14 }, fleetAgentWrapOpen: { zIndex: 4 }, fleetPanelRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 3 }, fleetAgentMain: { flex: 1, minWidth: 0, minHeight: 38, flexDirection: 'row', alignItems: 'center', gap: 7 }, fleetPanelName: { flex: 1, fontSize: 13, fontWeight: '700' }, ellipsisButton: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', borderRadius: 18 }, agentPopover: { borderRadius: 15, padding: 12, marginBottom: 6, gap: 8, shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 16, elevation: 7 }, agentMetaRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10 }, agentMetaLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 0.7 }, agentMetaValue: { fontSize: 11, fontWeight: '800' }, popoverActions: { flexDirection: 'row', gap: 8, marginTop: 2 }, popoverAction: { minHeight: 34, justifyContent: 'center', paddingHorizontal: 10, borderWidth: 1, borderColor: 'rgba(142,153,170,0.3)', borderRadius: 10 }, popoverActionText: { color: '#24D8FF', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 }, renameRow: { flexDirection: 'row', alignItems: 'center', gap: 7 }, renameInput: { flex: 1, minWidth: 0, height: 40, borderWidth: 1, borderColor: 'rgba(142,153,170,0.4)', borderRadius: 10, paddingHorizontal: 10, fontSize: 16, outlineStyle: 'none' as any }, confirmInterruptRow: { flexDirection: 'row', alignItems: 'center', gap: 9 }, confirmInterruptText: { flex: 1, fontSize: 11 }, popoverLink: { fontSize: 10, fontWeight: '800' }, agentActionMessage: { fontSize: 10, lineHeight: 14 }, drawerBottom: { paddingTop: 6 }, accountRow: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: 5 }, accountIcon: { width: 20, fontSize: 22.8, textAlign: 'center' },
  settingsSheet: { position: 'absolute', left: 8, right: 8, bottom: 8, height: '78%', minHeight: 420, zIndex: 20, borderTopLeftRadius: 28, borderTopRightRadius: 28, borderBottomLeftRadius: 18, borderBottomRightRadius: 18, padding: 18, shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 30, elevation: 18 }, appearanceLoading: { flex: 1, borderRadius: 26 }, settingsClose: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', marginLeft: -7, marginTop: -7 }, settingsCloseText: { fontSize: 27, lineHeight: 30, fontWeight: '300' }, settingsTitle: { fontSize: 24, fontWeight: '700', marginTop: -3, marginBottom: 12 }, settingsScroll: { flex: 1 }, settingsScrollContent: { paddingBottom: 28 }, settingsSection: { marginTop: 22, paddingTop: 18, borderTopWidth: 1, borderTopColor: 'rgba(142,153,170,0.18)' }, settingsSectionTitle: { fontSize: 17, fontWeight: '800', marginBottom: 8 }, settingsStatusGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 24 }, settingsStatus: { minWidth: 150, flexDirection: 'row', alignItems: 'center', gap: 10 }, statusDot: { width: 9, height: 9, borderRadius: 5 }, settingsLabel: { fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: '700' }, settingsValue: { fontSize: 15, fontWeight: '700', marginTop: 2 }, settingsError: { color: '#FFB4B2', fontSize: 12, marginTop: 12 }, settingsToggleRow: { maxWidth: 420, minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 16, marginTop: 16 }, settingsToggleCopy: { flex: 1 }, settingsToggleTitle: { fontSize: 15, fontWeight: '700' }, settingsToggleDescription: { fontSize: 11, lineHeight: 16, marginTop: 2 }, settingsUsageItem: { paddingVertical: 8 }, credentialBlock: { marginTop: 8, maxWidth: 520 }, credentialInputRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }, credentialInput: { flex: 1, minWidth: 0, minHeight: 38, borderWidth: 1, borderColor: 'rgba(142,153,170,0.38)', borderRadius: 10, paddingHorizontal: 10, fontSize: 16, outlineStyle: 'none' as any }, credentialSave: { minHeight: 38, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 10, backgroundColor: brand.cyan }, credentialSaveText: { color: brand.obsidian, fontSize: 10, fontWeight: '800' }, diagnosticsButton: { marginTop: 10, minHeight: 38, flexDirection: 'row', alignItems: 'center', maxWidth: 260 }, diagnosticsButtonText: { flex: 1, fontSize: 15, fontWeight: '700' }, diagnosticsArrow: { fontSize: 17 }, appearanceWindow: { ...StyleSheet.absoluteFill, borderRadius: 24, padding: 18, zIndex: 3, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 22, elevation: 20 }, appearanceHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }, appearanceTitle: { fontSize: 22, fontWeight: '800' }, appearanceClose: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' }, preferenceLabel: { fontSize: 10, fontWeight: '800', letterSpacing: 0.9, marginTop: 8, marginBottom: 8 }, optionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, optionPill: { minHeight: 36, justifyContent: 'center', borderRadius: 18, borderWidth: 1, borderColor: 'rgba(142,153,170,0.38)', paddingHorizontal: 13 }, customBackgroundRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 12 }, customBackgroundPreview: { width: 56, height: 40, borderRadius: 8 }, customBackgroundCopy: { flex: 1, minWidth: 150 }, customBackgroundTitle: { fontSize: 12, fontWeight: '800' }, customBackgroundDescription: { fontSize: 10, lineHeight: 14, marginTop: 2 }, secondaryAction: { minHeight: 34, justifyContent: 'center', paddingHorizontal: 10, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,98,95,0.36)' }, secondaryActionText: { fontSize: 11, fontWeight: '800' }, uploadBackgroundButton: { minHeight: 40, justifyContent: 'center', alignItems: 'center', borderRadius: 12, marginTop: 12, borderWidth: 1, borderColor: 'rgba(142,153,170,0.36)' }, optionPillSelected: { backgroundColor: brand.cyan, borderColor: brand.cyan }, optionText: { fontSize: 12, fontWeight: '800' }, logoutButton: { minHeight: 40, marginTop: 22, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,98,95,0.55)', justifyContent: 'center', alignItems: 'center', maxWidth: 260 }, logoutButtonText: { color: brand.critical, fontFamily: 'monospace', fontSize: 10, fontWeight: '800', letterSpacing: 0.8 }
});
