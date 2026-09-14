import * as Clipboard from 'expo-clipboard';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Alert, Image, ImageSourcePropType, KeyboardAvoidingView, LayoutChangeEvent, NativeScrollEvent, NativeSyntheticEvent, PanResponder, Platform, RefreshControl, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import Animated, { Easing, interpolate, useAnimatedStyle, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Circle, Path, Rect } from 'react-native-svg';
import { LinearGradient } from 'expo-linear-gradient';
import {
  AgentInfo, AgentMigration, AuthProviderInfo, cancelMagiChatTurn,
  CHAT_MAX_UPLOAD_COUNT, CHAT_MAX_UPLOAD_TOTAL_BYTES, ChatUpload,
  ExecutionProfile, ExecutionSettings, fetchAgentMigration, fetchAgents,
  fetchAuthProviders, fetchCanonicalActivitySnapshot,
  fetchExecutionCapabilities, fetchExecutionSettings, fetchHealth,
  fetchMagiChatConversation, fetchRecentActivity, fetchUnifiedAttention,
  fetchUsage, fetchVoiceInputCapabilities, getGatewaySessionRevision, HealthInfo, logoutGatewaySession,
  RecentActivityItem, requestAgentMigration, saveExecutionCredential,
  sendMagiChatPrompt, transcribeVoiceAudio, UnifiedAttentionRecord,
  updateExecutionSettings, uploadChatFile, UsageProvider, validateChatAttachment,
} from '../../src/api/client';
import { CanonicalActivitySurface } from '../../src/components/CanonicalActivitySurface';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { AccountIcon, ActivityIcon, ArrowUpIcon, AttentionIcon, BellIcon, ChevronRightIcon, CloseIcon, ConnectionsIcon, FleetIcon, HomeIcon, ICON_SIZE, MenuIcon, PaletteIcon, ProjectsIcon, SearchIcon, ShieldIcon, SlidersIcon, StopIcon } from '../../src/components/MagistrateIcons';
import { SafeMarkdown } from '../../src/components/SafeMarkdown';
import { useVoiceInputAdapter } from '../../src/input/VoiceInputAdapter';
import { agentDisplayName, displayAgentStatus, summarizeAgents } from '../../src/services/AgentStatus';
import {
  deriveCanonicalWorkState, getCanonicalActivityCursor, hydrateCanonicalActivity,
  ingestCanonicalActivityPage, ingestCanonicalActivitySnapshot,
  markCanonicalActivityFresh, markCanonicalActivityInterrupted,
  useCanonicalActivity,
} from '../../src/services/CanonicalActivity';
import { hasMagiReconciliationConflict, MagiMessageRecord, normalizeMagiMessageRecords, reconcileMagiMessages, sameMagiTranscript } from '../../src/services/MagiConversation';
import {
  appendMagiMessage, getMagiConversationPrincipal, getMagiMessages,
  loadCachedMagiConversation, MagiAttachment, MagiMessage, resetMagiMessages,
  updateMagiMessage, useMagiMessages,
} from '../../src/services/MagiConversationSession';
import { ChatPreferences, ChatThemeMode, DEFAULT_CHAT_PREFERENCES, loadChatPreferences, removeCustomBackground, saveChatBackground, saveCustomBackground, saveThemeMode, saveVoiceCaptureBehavior, saveVoiceInputMode, saveVoiceTranscriptBehavior, VoiceCaptureBehavior, VoiceTranscriptBehavior, useChatColorScheme } from '../../src/services/ChatPreferences';
import { setActiveBackground, TIME_IMAGES, WeatherSceneKey } from '../../src/services/environmentTheme';
import { loadMagiGreeting, magiGreeting } from '../../src/services/Greeting';
import { notificationManager } from '../../src/services/NotificationManager';
import { capabilityFor, getLocalVoiceCapabilities, VOICE_INPUT_MODE_OPTIONS, VoiceInputCapabilities, VoiceInputMode } from '../../src/services/VoiceInputModes';
import { formatAccessibleTimestamp, formatConversationTimestamp as formatChatTimestamp } from '../../src/services/ChatFormatting';
import { RealtimeClient } from '../../src/realtime/socket';
import { openExternalUrl } from '../../src/utils/externalLinks';

const markPaper = require('../../assets/images/magistrate-mark-paper-256.png');
const markInk = require('../../assets/images/magistrate-mark-ink-256.png');
const markActive = require('../../assets/images/magistrate-mark-active-256.png');
const brand = { obsidian: '#05070A', command: '#111722', paper: '#F7F8FA', ink: '#11151B', mutedDark: '#8E99AA', mutedLight: '#667180', cyan: '#24D8FF', violet: '#8B6CFF', success: '#43D17A', attention: '#FFB347', critical: '#FF625F' };

type ComposerAttachment = { id: string; name: string; uri: string; mimeType?: string; size?: number; kind: 'image' | 'file'; status?: 'ready' | 'uploading' | 'uploaded' | 'failed'; uploaded?: ChatUpload };
type QueuedPrompt = { messageId: string; text: string; source: 'text' | 'voice'; attachments: ComposerAttachment[]; retryFailed?: boolean };
type DrawerSection = 'attention' | 'fleet' | 'activity' | 'projects' | 'connections' | null;
type ConversationSyncState = { status: 'loading' | 'fresh' | 'stale'; cachedRows: number; error?: string };
const FLOATING_CHROME_GAP = 12;
const CANONICAL_ACTIVITY_PAGE_SIZE = 100;
const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const isDarkTheme = (scheme: string | null | undefined) => scheme !== 'light';
const optionId = (harness: string, model: string) => `${harness}-${model}`.replace(/[^A-Za-z0-9_-]/g, '-');
const providerLabel = (provider: string) => provider.toLowerCase() === 'firstmate' ? 'Magistrate' : provider;
const profilesFromCapabilities = (data: { profiles?: ExecutionProfile[]; harnesses?: { id: string; label: string; verified: boolean; models: { id: string; label: string; provider?: string; variant?: string; profile_id?: string; available?: boolean; availability?: string; auth?: { required: boolean; credential_key: string; status: string } }[] }[] }): ExecutionProfile[] => {
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
function BrandMark({ dark, style }: { dark: boolean; style?: object }) { return <Image source={dark ? markPaper : markInk} style={[styles.mark, style]} resizeMode="contain" accessibilityIgnoresInvertColors />; }
const glassFill = (dark: boolean, strength: 'control' | 'surface' = 'control') => dark ? strength === 'control' ? 'rgba(12,17,26,0.52)' : 'rgba(10,14,20,0.80)' : strength === 'control' ? 'rgba(255,255,255,0.66)' : 'rgba(255,255,255,0.88)';
const glassEdge = (dark: boolean) => dark ? 'rgba(255,255,255,0.10)' : 'rgba(17,21,27,0.08)';
const blurStyle = (radius: number) => Platform.OS === 'web' ? { backdropFilter: `blur(${radius}px)`, WebkitBackdropFilter: `blur(${radius}px)` } as any : null;

function GlassCircleButton({ dark, onPress, accessibilityLabel, accessibilityHint, accessibilityState, testID, children, badge }: { dark: boolean; onPress: () => void; accessibilityLabel: string; accessibilityHint?: string; accessibilityState?: object; testID?: string; children: React.ReactNode; badge?: boolean }) {
  return <TouchableOpacity testID={testID} accessibilityRole="button" accessibilityLabel={accessibilityLabel} accessibilityHint={accessibilityHint} accessibilityState={accessibilityState} onPress={onPress} activeOpacity={0.7} style={[styles.glassCircle, { backgroundColor: glassFill(dark), borderColor: glassEdge(dark) }, blurStyle(20)]}>{children}{badge ? <View testID="unread-attention-dot" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.unreadAttentionDot} /> : null}</TouchableOpacity>;
}
function EmptyStateMagi({ dark, visible, greeting, active }: { dark: boolean; visible: boolean; greeting: string; active: boolean }) {
  const progress = useSharedValue(visible ? 1 : 0); const breath = useSharedValue(0);
  useEffect(() => { progress.value = withTiming(visible ? 1 : 0, { duration: 260, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [visible, progress]);
  useEffect(() => { breath.value = active ? withRepeat(withTiming(1, { duration: 2400 }), -1, true) : withTiming(0, { duration: 320 }); }, [active, breath]);
  const style = useAnimatedStyle(() => ({ opacity: progress.value, transform: [{ translateY: interpolate(progress.value, [0, 1], [10, 0]) }] }));
  const haloStyle = useAnimatedStyle(() => ({ opacity: interpolate(breath.value, [0, 1], [0, 0.5]), transform: [{ scale: interpolate(breath.value, [0, 1], [0.94, 1.08]) }] }));
  return <Animated.View testID="chat-empty-state" pointerEvents="none" accessibilityElementsHidden={!visible} importantForAccessibility={visible ? 'auto' : 'no-hide-descendants'} style={[styles.emptyState, style]}><View style={styles.emptyStateMarkWrap}><Animated.View testID="empty-state-halo" style={[styles.emptyStateHalo, haloStyle]} /><BrandMark dark={dark} style={styles.emptyStateMark} /></View><Text testID="chat-greeting" accessibilityRole="header" style={[styles.greeting, { color: dark ? '#F4F5F7' : brand.ink }]}>{greeting}</Text></Animated.View>;
}
function MicIcon({ color, size = 18 }: { color: string; size?: number }) { return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none"><Rect x="9" y="2.5" width="6" height="11" rx="3" stroke={color} strokeWidth={1.6} /><Path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3M9 20.5h6" stroke={color} strokeWidth={1.6} strokeLinecap="round" fill="none" /></Svg>; }
function GearIcon({ color, size = 18 }: { color: string; size?: number }) { return <Svg testID="settings-gear-icon" width={size} height={size} viewBox="0 0 24 24" fill="none"><Circle cx="12" cy="12" r="3.1" stroke={color} strokeWidth={1.6} /><Path d="M9.8 3.1h4.4l.5 2.1c.5.2.9.4 1.3.7l2-.6 2.2 3.8-1.5 1.5v2.8l1.5 1.5-2.2 3.8-2-.6c-.4.3-.8.5-1.3.7l-.5 2.1H9.8l-.5-2.1c-.5-.2-.9-.4-1.3-.7l-2 .6-2.2-3.8 1.5-1.5v-2.8L3.8 9.1 6 5.3l2 .6c.4-.3.8-.5 1.3-.7z" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" /></Svg>; }
function SoundwaveIcon({ color, size = 18 }: { color: string; size?: number }) { const bars = [0.32, 0.62, 1, 0.72, 0.42]; return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">{bars.map((ratio, index) => { const height = 16 * ratio; return <Rect key={index} x={2 + index * 4.6} y={(24 - height) / 2} width="2.4" height={height} rx="1.2" fill={color} />; })}</Svg>; }
function EllipsisIcon({ color, size = 18 }: { color: string; size?: number }) { return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none"><Circle cx="5" cy="12" r="1.5" fill={color} /><Circle cx="12" cy="12" r="1.5" fill={color} /><Circle cx="19" cy="12" r="1.5" fill={color} /></Svg>; }
function ImageIcon({ color, size = 18 }: { color: string; size?: number }) { return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none"><Rect x="3" y="4" width="18" height="16" rx="3" stroke={color} strokeWidth={1.6} /><Path d="m6.5 16 3.6-3.8 2.8 2.6 2.3-2.3 2.8 3.5M15.8 9h.01" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" /></Svg>; }
function FileIcon({ color, size = 18 }: { color: string; size?: number }) { return <Svg width={size} height={size} viewBox="0 0 24 24" fill="none"><Path d="M6 3.5h7l5 5v12H6zM13 3.5v5h5" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" /></Svg>; }
const attachmentStateLabel = (status?: MagiAttachment['status']) => status === 'uploading' ? ' · Uploading…' : status === 'stored' ? ' · Stored, not yet sent' : status === 'attached' ? ' · Attached' : status === 'failed' ? ' · Upload failed' : '';
const formatAttachmentSize = (size?: number) => !size ? '' : size < 1024 ? `${size} B` : size < 1024 * 1024 ? `${Math.round(size / 1024)} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB`;
function LiveWaveform({ samples, color }: { samples: number[]; color: string }) { return <View style={styles.liveWaveform} pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">{samples.map((amplitude, index) => <View key={index} style={[styles.liveWaveformBar, { height: Math.max(3, amplitude * 46), backgroundColor: color }]} />)}</View>; }
function WorkingState({ dark, muted, operations, phase, onPress }: { dark: boolean; muted: string; operations: number; phase: 'active' | 'awaiting-user' | 'recovering'; onPress: () => void }) {
  const prefix = phase === 'awaiting-user' ? 'Magi is awaiting you' : phase === 'recovering' ? 'Magi is recovering activity' : 'Magi is working';
  const label = `${prefix}${operations ? ` · ${operations} operation${operations === 1 ? '' : 's'}` : ''}`;
  return <TouchableOpacity testID="structured-work-state" accessibilityRole="button" accessibilityLabel={label} accessibilityHint="Opens durable Magi activity" onPress={onPress} style={styles.workingState}><BrandMark dark={dark} style={styles.workingStateMark} /><Text style={[styles.workingLabel, { color: muted }]}>{label}</Text></TouchableOpacity>;
}
function UserMessage({ message, dark, textColor, selectable, onLongPress, onRetry, onActions }: { message: MagiMessage; dark: boolean; textColor: string; selectable: boolean; onLongPress: () => void; onRetry?: () => void; onActions: () => void }) {
  const timestamp = formatChatTimestamp(message.sentAt); const accessibleTimestamp = formatAccessibleTimestamp(message.sentAt);
  return <View testID="user-message" accessibilityLabel={accessibleTimestamp ? `You, ${accessibleTimestamp}` : 'You'} style={styles.userMessageWrap}><TouchableOpacity onLongPress={onLongPress} delayLongPress={360} activeOpacity={0.85} style={[styles.userBubble, { backgroundColor: dark ? 'rgba(255,255,255,0.12)' : 'rgba(17,21,27,0.08)' }]}><Text selectable={selectable} style={[styles.messageText, { color: textColor }]}>{message.text}</Text>{message.attachments?.map((attachment, index) => <View key={`${message.id}-attachment-${index}`} style={styles.attachedFile}><FileIcon size={14} color={textColor} /><Text numberOfLines={1} style={[styles.attachedFileName, { color: textColor }]}>{attachment.name}{attachmentStateLabel(attachment.status)}</Text></View>)}{timestamp ? <Text style={styles.messageTimestamp}>{timestamp}</Text> : null}{message.delivery === 'sending' ? <Text testID={`delivery-${message.id}`} style={styles.deliverySending}>Sending…</Text> : message.delivery === 'failed' ? <View><Text testID={`delivery-${message.id}`} style={styles.deliveryFailed}>Not sent</Text>{onRetry ? <TouchableOpacity testID={`retry-${message.id}`} accessibilityRole="button" onPress={onRetry}><Text style={styles.retryText}>Retry</Text></TouchableOpacity> : null}</View> : message.delivery === 'cancelled' ? <Text style={styles.deliverySending}>Cancelled</Text> : null}</TouchableOpacity><TouchableOpacity testID={`message-actions-${message.id}`} accessibilityRole="button" accessibilityLabel="Your message actions" onPress={onActions} style={styles.inlineMessageAction}><Text style={styles.inlineMessageActionText}>•••</Text></TouchableOpacity></View>;
}
function AssistantMessage({ message, dark, text, muted, onActions }: { message: MagiMessage; dark: boolean; text: string; muted: string; onActions: () => void }) {
  const timestamp = formatChatTimestamp(message.sentAt); const accessibleTimestamp = formatAccessibleTimestamp(message.sentAt);
  return <View testID="agent-message" accessibilityLabel={accessibleTimestamp ? `Magi, ${accessibleTimestamp}` : 'Magi'} style={styles.assistantMessage}><View style={styles.assistantBody}>{message.text ? <SafeMarkdown markdown={message.text} color={text} mutedColor={muted} dark={dark} testID={`assistant-markdown-${message.id}`} /> : null}{message.progress === 'failed' ? <Text testID={`assistant-failed-${message.id}`} accessibilityRole="alert" style={styles.assistantStateFailed}>Response failed. Retry the message to try again.</Text> : message.progress === 'cancelled' ? <Text style={[styles.assistantState, { color: muted }]}>Response stopped</Text> : message.progress === 'working' || message.progress === 'queued' ? <Text testID={`assistant-working-${message.id}`} style={[styles.assistantState, { color: muted }]}>Working…</Text> : null}{timestamp && message.text ? <Text style={[styles.messageTimestamp, { color: muted }]}>{timestamp}</Text> : null}</View><TouchableOpacity testID={`message-actions-${message.id}`} accessibilityRole="button" accessibilityLabel="Assistant message actions" onPress={onActions} style={styles.inlineMessageAction}><Text style={styles.inlineMessageActionText}>•••</Text></TouchableOpacity></View>;
}

// `target` remains an accepted shell prop for compatibility but is intentionally not read.
export function ChatCanvas({ onDrawerToggle = () => {}, drawerOpen = false, voiceInputMode = 'automatic', voiceCaptureBehavior = 'tap-to-toggle', voiceTranscriptBehavior = 'insert', autoStartRecording = false, activityOpen = false, onActivityOpen = () => {}, onActivityClose = () => {} }: { target?: string; onDrawerToggle?: () => void; drawerOpen?: boolean; voiceInputMode?: VoiceInputMode; voiceCapabilities?: VoiceInputCapabilities; voiceCaptureBehavior?: VoiceCaptureBehavior; voiceTranscriptBehavior?: VoiceTranscriptBehavior; autoStartRecording?: boolean; activityOpen?: boolean; onActivityOpen?: () => void; onActivityClose?: () => void }) {
  const router = useRouter(); const dark = isDarkTheme(useChatColorScheme());
  const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight; const spectral = dark ? brand.cyan : brand.violet;
  const messages = useMagiMessages(); const canonicalActivity = useCanonicalActivity();
  const [promptText, setPromptText] = useState('');
  const [messageActionsId, setMessageActionsId] = useState<string | null>(null);
  const [selectableMessageId, setSelectableMessageId] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [conversationSync, setConversationSync] = useState<ConversationSyncState>({ status: 'loading', cachedRows: 0 });
  const [followLatest, setFollowLatest] = useState(true); const [hasNewMessages, setHasNewMessages] = useState(false);
  const [unreadAttentionCount, setUnreadAttentionCount] = useState(() => notificationManager.getUnreadEvents().length);
  const [activityBefore, setActivityBefore] = useState<number | undefined>(); const [activityHasMore, setActivityHasMore] = useState(false);
  const [activityLoadingMore, setActivityLoadingMore] = useState(false); const [activityRefreshing, setActivityRefreshing] = useState(false);
  const [isRecording, setIsRecording] = useState(false); const [isTranscribing, setIsTranscribing] = useState(false);
  const [micStatus, setMicStatus] = useState<'idle' | 'requesting' | 'listening' | 'transcribing' | 'ready' | 'error'>('idle');
  const [waveSamples, setWaveSamples] = useState<number[]>(() => new Array(48).fill(0.04));
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false); const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [headerHeight, setHeaderHeight] = useState(0); const [composerHeight, setComposerHeight] = useState(0);
  const scrollRef = useRef<ScrollView>(null); const inputRef = useRef<TextInput>(null); const conversationIdRef = useRef<string | undefined>(undefined);
  const conversationObservationRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null); const activeTokenRef = useRef(0); const pendingAttachmentsRef = useRef(new Map<string, ComposerAttachment[]>());
  const holdActiveRef = useRef(false); const capture = useVoiceInputAdapter(undefined, voiceInputMode); const captureRef = useRef(capture);
  const [greeting, setGreeting] = useState(() => magiGreeting(null));
  const canonicalWork = useMemo(() => deriveCanonicalWorkState(canonicalActivity, messages), [canonicalActivity, messages]);
  const pendingAssistant = [...messages].reverse().find(row => row.role === 'assistant' && row.progress === 'working');
  const pendingUser = pendingAssistant ? messages.find(row => row.turnId === pendingAssistant.turnId && row.role === 'user') : undefined;
  const stoppableMessageId = activeMessageId || pendingUser?.id || null; const isThinking = Boolean(stoppableMessageId);

  const applyRecords = (records: MagiMessageRecord[], authoritative = false): boolean => {
    if (!records.length && !authoritative) return true;
    const current = getMagiMessages();
    if (hasMagiReconciliationConflict(current, records)) return false;
    const next = reconcileMagiMessages(current, records, { authoritative });
    if (!sameMagiTranscript(current, next)) resetMagiMessages(next);
    conversationObservationRef.current += 1;
    return true;
  };
  const refreshConversation = async (
    authoritative = true, accept: () => boolean = () => true,
  ): Promise<boolean> => {
    const owner = getMagiConversationPrincipal();
    const revision = getGatewaySessionRevision();
    if (!owner) throw new Error('Authentication is required.');
    const observation = conversationObservationRef.current;
    const result = await fetchMagiChatConversation(conversationIdRef.current);
    if (!accept() || getMagiConversationPrincipal() !== owner
      || getGatewaySessionRevision() !== revision) return false;
    // A socket event accepted while this request was in flight is newer than
    // the HTTP snapshot's authority boundary; merge then, but never prune it.
    const mayPrune = authoritative && observation === conversationObservationRef.current;
    if (!applyRecords(result.messages, mayPrune)) {
      throw new Error('Gateway returned conflicting Magi identity.');
    }
    conversationIdRef.current = result.conversation.id;
    setConversationSync({ status: 'fresh', cachedRows: result.messages.length });
    return true;
  };
  const refreshActivity = async (accept: () => boolean = () => true) => {
    const owner = getMagiConversationPrincipal();
    const revision = getGatewaySessionRevision();
    if (!owner) return;
    const ownsRequest = () => accept() && getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === revision;
    setActivityRefreshing(true);
    try {
      const snapshot = await fetchCanonicalActivitySnapshot(undefined, CANONICAL_ACTIVITY_PAGE_SIZE);
      if (!ownsRequest()) return;
      const page = ingestCanonicalActivitySnapshot(snapshot);
      if (!page) throw new Error('Invalid structured activity snapshot.');
      setActivityBefore(page.nextBefore ?? undefined); setActivityHasMore(page.hasMore); markCanonicalActivityFresh();
    } catch {
      if (ownsRequest()) markCanonicalActivityInterrupted();
    }
    finally { if (ownsRequest()) setActivityRefreshing(false); }
  };
  const loadOlderCanonicalActivity = async (): Promise<boolean> => {
    if (!activityHasMore || activityLoadingMore || !activityBefore) return false;
    const owner = getMagiConversationPrincipal();
    const revision = getGatewaySessionRevision();
    if (!owner) return false;
    const ownsRequest = () => getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === revision;
    setActivityLoadingMore(true);
    try {
      const snapshot = await fetchCanonicalActivitySnapshot(activityBefore, CANONICAL_ACTIVITY_PAGE_SIZE);
      if (!ownsRequest()) return false;
      const page = ingestCanonicalActivitySnapshot(snapshot, true);
      if (!page) throw new Error('Invalid structured activity snapshot.');
      setActivityBefore(page.nextBefore ?? undefined); setActivityHasMore(page.hasMore); return true;
    } catch { if (ownsRequest()) markCanonicalActivityInterrupted(); return false; }
    finally { if (ownsRequest()) setActivityLoadingMore(false); }
  };

  useEffect(() => { captureRef.current = capture; });
  useEffect(() => () => { void captureRef.current.cancel(); }, []);
  useEffect(() => { let mounted = true; loadMagiGreeting().then(value => { if (mounted) setGreeting(value); }).catch(() => {}); return () => { mounted = false; }; }, []);
  useEffect(() => notificationManager.subscribeUnread(events => setUnreadAttentionCount(events.length)), []);
  useEffect(() => {
    let live = true;
    const owner = getMagiConversationPrincipal();
    const ownsLifecycle = () => live && !!owner && getMagiConversationPrincipal() === owner;
    const realtimeRef: { current: RealtimeClient | null } = { current: null };
    void (async () => {
      const cached = await loadCachedMagiConversation();
      if (!ownsLifecycle()) return;
      resetMagiMessages([...cached.authoritative, ...cached.pending]);
      // Cache hydration must win the startup race exactly once. Realtime starts
      // only afterward, so a delayed storage read cannot roll back a newer
      // canonical socket revision.
      realtimeRef.current?.connect();
      setConversationSync({ status: 'loading', cachedRows: cached.authoritative.length });
      await Promise.allSettled([hydrateCanonicalActivity(), refreshActivity(ownsLifecycle)]);
      if (!ownsLifecycle()) return;
      try { await refreshConversation(true, ownsLifecycle); }
      catch (error) { if (ownsLifecycle()) setConversationSync({ status: 'stale', cachedRows: cached.authoritative.length, error: errorText(error, 'Conversation unavailable.') }); }
      finally { if (ownsLifecycle()) setHydrated(true); }
    })();
    realtimeRef.current = new RealtimeClient({
      onMagiMessages: payload => {
        if (!ownsLifecycle()) return false;
        if ((payload as { type?: string })?.type === 'reconnect') {
          void refreshConversation(true, ownsLifecycle).catch(() => {
            if (ownsLifecycle()) setConversationSync(current => ({ ...current, status: 'stale' }));
          });
          return true;
        }
        const value = payload as Record<string, unknown>; const raw = value.messages;
        if (!Array.isArray(raw)) return false;
        const normalized = normalizeMagiMessageRecords(raw);
        if (normalized.length !== raw.length) return false;
        if (typeof value.conversation_id !== 'string'
          || normalized.some(record => record.conversation_id !== value.conversation_id)
          || (conversationIdRef.current && conversationIdRef.current !== value.conversation_id)) return false;
        if (!applyRecords(normalized)) return false;
        conversationIdRef.current = value.conversation_id;
        setConversationSync(current => ({ ...current, status: 'fresh' }));
        return true;
      },
      onActivity: payload => {
        if (!ownsLifecycle()) return false;
        const accepted = ingestCanonicalActivityPage(payload);
        if (accepted) markCanonicalActivityFresh(); else markCanonicalActivityInterrupted();
        return accepted;
      },
      onError: () => {
        if (ownsLifecycle()) setConversationSync(current => ({ ...current, status: current.cachedRows ? 'stale' : current.status }));
      },
    }, getCanonicalActivityCursor());
    return () => { live = false; activeControllerRef.current?.abort(); realtimeRef.current?.disconnect(); };
  }, []); // one authenticated native thread

  useEffect(() => {
    if (capture.isRecording) setWaveSamples(current => [...current.slice(1), Math.max(0.04, capture.amplitude)]);
  }, [capture.amplitude, capture.isRecording]);
  useEffect(() => { if (autoStartRecording && !isRecording && micStatus === 'idle') void startRecording(); }, [autoStartRecording]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (activeMessageId || !queuedPrompts.length) return;
    const [next, ...rest] = queuedPrompts; setQueuedPrompts(rest); void submitPrompt(next);
  }, [activeMessageId, queuedPrompts]); // eslint-disable-line react-hooks/exhaustive-deps

  const uploadForPrompt = async (item: QueuedPrompt): Promise<ChatUpload[]> => {
    const completed: ChatUpload[] = [];
    for (const attachment of item.attachments) {
      if (attachment.uploaded) { completed.push(attachment.uploaded); continue; }
      setAttachments(current => current.map(value => value.id === attachment.id ? { ...value, status: 'uploading' } : value));
      try {
        const uploaded = await uploadChatFile(attachment.uri, attachment.name, attachment.mimeType);
        attachment.uploaded = uploaded; attachment.status = 'uploaded'; completed.push(uploaded);
        updateMagiMessage(item.messageId, { attachments: item.attachments.map(value => ({ name: value.name, mediaType: value.mimeType || 'application/octet-stream', size: value.size, status: value.uploaded ? 'stored' : 'uploading', uploadId: value.uploaded?.upload_id })) });
      } catch (error) {
        attachment.status = 'failed';
        updateMagiMessage(item.messageId, { attachments: item.attachments.map(value => ({ name: value.name, mediaType: value.mimeType || 'application/octet-stream', size: value.size, status: value.status === 'failed' ? 'failed' : value.uploaded ? 'stored' : 'uploading', uploadId: value.uploaded?.upload_id })) });
        throw error;
      }
    }
    return completed;
  };
  async function submitPrompt(item: QueuedPrompt) {
    const owner = getMagiConversationPrincipal();
    const sessionRevision = getGatewaySessionRevision();
    if (!owner) return;
    const ownsSubmission = () => getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === sessionRevision;
    const token = ++activeTokenRef.current; const controller = new AbortController(); activeControllerRef.current = controller; setActiveMessageId(item.messageId); setSendError(null);
    try {
      const uploaded = await uploadForPrompt(item);
      if (!ownsSubmission()) return;
      const result = await sendMagiChatPrompt(item.text, item.messageId, item.source, uploaded, { conversationId: conversationIdRef.current, retryFailed: item.retryFailed, signal: controller.signal });
      if (!ownsSubmission()) return;
      const responseRecords = result.messages.length ? result.messages : [result.user_message, result.assistant_message].filter((row): row is MagiMessageRecord => Boolean(row));
      if (!applyRecords(responseRecords)) throw new Error('Gateway returned conflicting Magi identity.');
      conversationIdRef.current = result.conversation.id;
      if (result.status === 'failed') throw new Error(result.error || 'Magi could not complete this response.');
    } catch (error) {
      if (controller.signal.aborted || !ownsSubmission()) return;
      updateMagiMessage(item.messageId, { delivery: 'failed', progress: 'failed' });
      setSendError(errorText(error, 'Message failed. Retry to send the same request once.'));
    } finally {
      if (activeTokenRef.current === token) { activeControllerRef.current = null; setActiveMessageId(null); }
    }
  }
  const queuePrompt = (textValue: string, source: 'text' | 'voice', selectedAttachments: ComposerAttachment[], messageId?: string, retryFailed = false) => {
    const trimmed = textValue.trim(); if (!trimmed || !getMagiConversationPrincipal()) return;
    const busy = Boolean(activeControllerRef.current || activeMessageId);
    const id = messageId || `${source === 'voice' ? 'voice-' : ''}u-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    if (!messageId) appendMagiMessage({ id, role: 'user', text: trimmed, sentAt: Date.now(), source, attachments: selectedAttachments.map(item => ({ name: item.name, mediaType: item.mimeType || 'application/octet-stream', size: item.size, status: item.uploaded ? 'stored' : 'uploading', uploadId: item.uploaded?.upload_id })), progress: busy ? 'queued' : 'working', delivery: 'sending' });
    else updateMagiMessage(id, { delivery: 'sending', progress: busy ? 'queued' : 'working' });
    pendingAttachmentsRef.current.set(id, selectedAttachments);
    const item = { messageId: id, text: trimmed, source, attachments: selectedAttachments, retryFailed };
    if (busy) setQueuedPrompts(current => [...current, item]); else void submitPrompt(item);
    setPromptText(''); setAttachments([]); setSendError(null);
  };
  const handleSend = () => {
    const trimmed = promptText.trim();
    if (attachments.length && !trimmed) { setSendError('Add a message describing the attached file before sending.'); return; }
    if (!trimmed) { router.push('/voice' as any); return; }
    queuePrompt(trimmed, 'text', [...attachments]);
  };
  const retryMessage = (message: MagiMessage) => {
    const selected = pendingAttachmentsRef.current.get(message.id) || [];
    if (message.attachments?.length && !selected.length) { setSendError('Reattach the file before retrying this message.'); return; }
    queuePrompt(message.text, message.source, selected, message.id, true);
  };
  const stopPendingResponse = async () => {
    if (!stoppableMessageId) return;
    activeControllerRef.current?.abort(); setActiveMessageId(null);
    try { await cancelMagiChatTurn(stoppableMessageId); await refreshConversation(true); }
    catch (error) { setSendError(errorText(error, 'The response could not be stopped.')); }
  };

  const addAttachments = (items: ComposerAttachment[]) => {
    const combined = [...attachments, ...items];
    if (combined.length > CHAT_MAX_UPLOAD_COUNT) { setSendError(`Add at most ${CHAT_MAX_UPLOAD_COUNT} files.`); return; }
    if (combined.reduce((sum, item) => sum + (item.size || 0), 0) > CHAT_MAX_UPLOAD_TOTAL_BYTES) { setSendError('Attachments may total at most 50 MB.'); return; }
    const invalid = items.find(item => validateChatAttachment(item.name, item.mimeType, item.size));
    if (invalid) { setSendError(validateChatAttachment(invalid.name, invalid.mimeType, invalid.size)); return; }
    setAttachments(combined); setAttachmentMenuOpen(false); setSendError(null);
  };
  const pickImages = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) { setSendError('Photo access is required to attach an image.'); return; }
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, allowsMultipleSelection: true, quality: 0.9 });
    if (!result.canceled) addAttachments(result.assets.map((asset, index) => ({ id: `image-${Date.now()}-${index}`, name: asset.fileName || `photo-${index + 1}.jpg`, uri: asset.uri, mimeType: asset.mimeType || 'image/jpeg', size: asset.fileSize, kind: 'image' as const, status: 'ready' as const })));
  };
  const pickFiles = async () => {
    const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (!result.canceled) addAttachments(result.assets.map((asset, index) => ({ id: `file-${Date.now()}-${index}`, name: asset.name, uri: asset.uri, mimeType: asset.mimeType, size: asset.size, kind: 'file' as const, status: 'ready' as const })));
  };
  const startRecording = async () => {
    const owner = getMagiConversationPrincipal(); const revision = getGatewaySessionRevision();
    const ownsCapture = () => !!owner && getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === revision;
    setMicStatus('requesting'); setSendError(null);
    try {
      await capture.start();
      if (!ownsCapture()) { await capture.cancel(); return; }
      setIsRecording(true); setMicStatus('listening');
    } catch (error) {
      if (ownsCapture()) { setMicStatus('error'); setSendError(errorText(error, 'Microphone unavailable.')); }
    }
  };
  const finishRecording = async () => {
    const owner = getMagiConversationPrincipal(); const revision = getGatewaySessionRevision();
    const ownsCapture = () => !!owner && getMagiConversationPrincipal() === owner
      && getGatewaySessionRevision() === revision;
    setIsRecording(false); setIsTranscribing(true); setMicStatus('transcribing');
    try {
      const recording = await capture.stop();
      if (!ownsCapture()) return;
      const transcript = recording.transcript || (await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename)).text;
      if (!ownsCapture()) return;
      if (!transcript.trim()) throw new Error('No speech was detected.');
      if (voiceTranscriptBehavior === 'auto-send') queuePrompt(transcript, 'voice', []); else { setPromptText(current => current ? `${current} ${transcript}` : transcript); setMicStatus('ready'); }
    } catch (error) {
      if (ownsCapture()) { setMicStatus('error'); setSendError(errorText(error, 'The recording could not be transcribed.')); }
    } finally { if (ownsCapture()) setIsTranscribing(false); }
  };
  const handleMicPress = async () => { if (isRecording) await finishRecording(); else await startRecording(); };

  const activeMessage = messages.find(message => message.id === messageActionsId);
  const copyMessage = async () => { if (activeMessage) { await Clipboard.setStringAsync(activeMessage.text); setCopiedMessageId(activeMessage.id); setTimeout(() => setCopiedMessageId(null), 1400); } setMessageActionsId(null); };
  const onHistoryScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent; const nearEnd = contentOffset.y + layoutMeasurement.height >= contentSize.height - 40;
    setFollowLatest(nearEnd); if (nearEnd) setHasNewMessages(false);
  };
  const onContentSizeChange = () => { if (followLatest) requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: false })); else setHasNewMessages(true); };
  const handleHeaderLayout = (event: LayoutChangeEvent) => setHeaderHeight(event.nativeEvent.layout.height);
  const handleComposerLayout = (event: LayoutChangeEvent) => setComposerHeight(event.nativeEvent.layout.height);
  const showEmptyState = hydrated && messages.length === 0 && conversationSync.status !== 'stale';

  return <KeyboardAvoidingView testID="branded-chat-shell" behavior={Platform.OS === 'ios' ? 'padding' : Platform.OS === 'android' ? 'height' : undefined} style={styles.canvas}>
    <ScrollView ref={scrollRef} testID="chat-history" style={styles.chatHistory} contentContainerStyle={[styles.chatHistoryContent, { paddingTop: headerHeight + FLOATING_CHROME_GAP, paddingBottom: composerHeight + FLOATING_CHROME_GAP }]} onScroll={onHistoryScroll} onContentSizeChange={onContentSizeChange} scrollEventThrottle={16} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive" automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'} refreshControl={<RefreshControl refreshing={activityRefreshing} onRefresh={() => { void Promise.allSettled([refreshConversation(true), refreshActivity()]); }} />} accessibilityLabel="Magi conversation history" aria-busy={!hydrated}>
      {messages.map(message => message.role === 'user' ? <UserMessage key={message.id} message={message} dark={dark} textColor={text} selectable={selectableMessageId === message.id} onLongPress={() => setMessageActionsId(message.id)} onActions={() => setMessageActionsId(message.id)} onRetry={message.delivery === 'failed' ? () => retryMessage(message) : undefined} /> : <AssistantMessage key={message.id} message={message} dark={dark} text={text} muted={muted} onActions={() => setMessageActionsId(message.id)} />)}
      {canonicalWork.active ? <WorkingState dark={dark} muted={muted} operations={canonicalWork.operationCount} phase={canonicalWork.phase === 'idle' ? 'active' : canonicalWork.phase} onPress={onActivityOpen} /> : null}
    </ScrollView>
    <EmptyStateMagi dark={dark} visible={showEmptyState} greeting={greeting} active={isThinking || isRecording} />
    <View testID="chat-header" style={styles.headerDock} pointerEvents="box-none" onLayout={handleHeaderLayout}><View style={styles.topBar} pointerEvents="box-none">
      <GlassCircleButton dark={dark} testID="brand-drawer-toggle" accessibilityLabel={`${drawerOpen ? 'Collapse' : 'Open'} Magistrate drawer${unreadAttentionCount ? `, ${unreadAttentionCount} unread attention item${unreadAttentionCount === 1 ? '' : 's'}` : ''}`} accessibilityState={{ expanded: drawerOpen }} onPress={onDrawerToggle} badge={unreadAttentionCount > 0}><MenuIcon size={ICON_SIZE} color={text} /></GlassCircleButton>
      <View style={styles.identityControl} accessibilityLabel="Magi, provider-native conversation"><Text style={[styles.identityName, { color: text }]}>Magi</Text></View>
      <GlassCircleButton dark={dark} testID="chat-primary-action" accessibilityLabel={isThinking ? 'Stop Magi response' : 'Open Voice Mode'} onPress={() => isThinking ? void stopPendingResponse() : router.push('/voice' as any)}>{isThinking ? <StopIcon size={ICON_SIZE} color={spectral} /> : <SoundwaveIcon size={ICON_SIZE} color={text} />}</GlassCircleButton>
    </View>{conversationSync.status === 'stale' ? <View testID="conversation-stale-state" accessibilityRole="alert" style={[styles.staleConversation, { backgroundColor: glassFill(dark, 'surface'), borderColor: glassEdge(dark) }, blurStyle(18)]}><Text style={[styles.staleConversationText, { color: conversationSync.cachedRows ? muted : brand.attention }]}>{conversationSync.cachedRows ? 'Connection interrupted · showing saved conversation while reconnecting.' : 'Conversation unavailable · reconnecting.'}</Text></View> : null}</View>
    {(hasNewMessages || !followLatest) ? <TouchableOpacity testID="jump-to-latest" accessibilityRole="button" accessibilityLabel="Jump to latest message" style={[styles.jumpButton, { bottom: composerHeight + FLOATING_CHROME_GAP, backgroundColor: glassFill(dark), borderColor: glassEdge(dark) }]} onPress={() => { setFollowLatest(true); setHasNewMessages(false); scrollRef.current?.scrollToEnd({ animated: true }); }}><Text style={[styles.jumpText, { color: text }]}>↓</Text></TouchableOpacity> : null}
    {copiedMessageId ? <Text testID="message-copied" accessibilityLiveRegion="polite" style={[styles.copiedLabel, { bottom: composerHeight + 52 }]}>Copied</Text> : null}
    {messageActionsId ? <View testID="message-actions" accessibilityViewIsModal style={[styles.messageActions, { bottom: composerHeight + FLOATING_CHROME_GAP, backgroundColor: dark ? brand.command : '#FFFFFF' }]}><TouchableOpacity accessibilityRole="button" accessibilityLabel="Copy message" onPress={() => void copyMessage()} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Copy</Text></TouchableOpacity>{activeMessage?.role === 'user' ? <TouchableOpacity accessibilityRole="button" onPress={() => { setSelectableMessageId(activeMessage.id); setMessageActionsId(null); }} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Select text</Text></TouchableOpacity> : null}<TouchableOpacity accessibilityRole="button" accessibilityLabel="Close message actions" onPress={() => setMessageActionsId(null)} style={styles.messageAction}><Text style={[styles.messageActionText, { color: muted }]}>×</Text></TouchableOpacity></View> : null}
    <View testID="composer-dock" style={styles.composerDock} pointerEvents="box-none" onLayout={handleComposerLayout}>
      {isRecording ? <View testID="active-voice-surface" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.activeVoiceSurface}><View style={styles.activeVoiceHalo} /><Image source={markActive} style={styles.activeVoiceMark} resizeMode="contain" accessibilityIgnoresInvertColors /><LiveWaveform samples={waveSamples} color={brand.cyan} /></View> : null}
      {attachments.length ? <ScrollView testID="attachment-preview" horizontal showsHorizontalScrollIndicator={false} style={styles.attachmentPreview} contentContainerStyle={styles.attachmentPreviewContent}>{attachments.map(attachment => <View key={attachment.id} style={[styles.attachmentChip, { backgroundColor: 'transparent' }]}>{attachment.kind === 'image' ? <Image source={{ uri: attachment.uri }} style={styles.attachmentThumbnail} /> : <View style={styles.attachmentFileIcon}><FileIcon color={spectral} /></View>}<View style={styles.attachmentCopy}><Text numberOfLines={1} style={[styles.attachmentName, { color: text }]}>{attachment.name}</Text><Text style={[styles.attachmentMeta, { color: attachment.status === 'failed' ? brand.critical : muted }]}>{attachment.status === 'uploading' ? 'Uploading…' : attachment.status === 'failed' ? 'Upload failed' : formatAttachmentSize(attachment.size)}</Text></View><TouchableOpacity accessibilityRole="button" accessibilityLabel={`Remove ${attachment.name}`} onPress={() => setAttachments(current => current.filter(item => item.id !== attachment.id))} style={styles.attachmentRemove}><Text style={[styles.attachmentRemoveText, { color: muted }]}>×</Text></TouchableOpacity></View>)}</ScrollView> : null}
      <View testID="composer-surface" style={[styles.composer, { backgroundColor: 'transparent', borderColor: glassEdge(dark) }, blurStyle(24)]}><View style={styles.attachmentControl}><TouchableOpacity testID="attachment-menu-button" accessibilityRole="button" accessibilityLabel="Add attachment" accessibilityState={{ expanded: attachmentMenuOpen }} onPress={() => setAttachmentMenuOpen(value => !value)} style={styles.composerIconButton}><Text style={[styles.composerIconText, { color: attachmentMenuOpen ? spectral : muted }]}>＋</Text></TouchableOpacity>{attachmentMenuOpen ? <View testID="attachment-menu" accessibilityViewIsModal style={[styles.attachmentMenu, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}><Text style={[styles.menuTitle, { color: text }]}>Add to message</Text><TouchableOpacity testID="attachment-option-images" accessibilityRole="button" onPress={() => void pickImages()} style={styles.attachmentOption}><ImageIcon color={spectral} /><Text style={[styles.attachmentOptionTitle, { color: text }]}>Photos</Text></TouchableOpacity><TouchableOpacity testID="attachment-option-files" accessibilityRole="button" onPress={() => void pickFiles()} style={styles.attachmentOption}><FileIcon color={spectral} /><Text style={[styles.attachmentOptionTitle, { color: text }]}>Files</Text></TouchableOpacity></View> : null}</View>
      <TextInput ref={inputRef} testID="magi-prompt" style={[styles.composerInput, { color: text }]} placeholder="Message Magi" placeholderTextColor={muted} value={promptText} onChangeText={setPromptText} onSubmitEditing={handleSend} returnKeyType="send" accessibilityLabel="Message Magi" />
      <TouchableOpacity testID="inline-mic-button" accessibilityRole="button" accessibilityLabel={isRecording ? 'Stop microphone' : isTranscribing ? 'Transcribing microphone' : 'Start microphone'} accessibilityState={{ selected: isRecording, busy: isTranscribing || micStatus === 'requesting' }} style={[styles.composerIconButton, isRecording ? styles.micActiveButton : undefined]} onPress={voiceCaptureBehavior === 'tap-to-toggle' ? () => void handleMicPress() : undefined} onPressIn={voiceCaptureBehavior === 'hold-to-talk' ? () => { holdActiveRef.current = true; if (!isRecording) void handleMicPress(); } : undefined} onPressOut={voiceCaptureBehavior === 'hold-to-talk' ? () => { holdActiveRef.current = false; if (isRecording) void handleMicPress(); } : undefined} disabled={isTranscribing || micStatus === 'requesting'}><MicIcon size={24} color={isRecording ? brand.cyan : muted} /></TouchableOpacity>
      <TouchableOpacity testID={isThinking ? 'stop-magi-response' : 'send-magi-prompt'} accessibilityRole="button" accessibilityLabel={isThinking ? 'Stop Magi response' : promptText.trim() || attachments.length ? 'Send message to Magi' : 'Open voice mode'} accessibilityState={{ busy: isThinking }} onPress={() => isThinking ? void stopPendingResponse() : handleSend()} style={[styles.sendButton, isThinking ? styles.stopButton : undefined]}>{isThinking ? <StopIcon size={20} color={brand.paper} /> : promptText.trim() || attachments.length ? <ArrowUpIcon size={22} color={brand.paper} /> : <SoundwaveIcon color={brand.paper} size={20} />}</TouchableOpacity></View>
      <View testID="composer-status" style={styles.composerStatus} accessibilityLiveRegion="polite">{micStatus === 'requesting' ? <Text style={styles.micTranscribingLabel}>Requesting microphone permission…</Text> : micStatus === 'listening' ? <Text style={styles.micListeningLabel}>Listening…</Text> : micStatus === 'transcribing' ? <Text style={styles.micTranscribingLabel}>Transcribing…</Text> : micStatus === 'ready' ? <Text style={styles.micReadyLabel}>Transcript ready — review before sending</Text> : null}{queuedPrompts.length ? <Text testID="queued-message-count" style={styles.queuedLabel}>{queuedPrompts.length} queued · sends in order</Text> : null}{sendError ? <Text testID="magi-send-error" accessibilityRole="alert" style={styles.sendError}>{sendError}</Text> : null}</View>
    </View>
    <CanonicalActivitySurface visible={activityOpen} snapshot={canonicalActivity} work={canonicalWork} hasMore={activityHasMore} loadingMore={activityLoadingMore} refreshing={activityRefreshing} onClose={onActivityClose} onLoadMore={loadOlderCanonicalActivity} onRefresh={refreshActivity} onOpenDecision={itemId => { onActivityClose(); router.push({ pathname: '/attention', params: { item: itemId, source: 'activity' } } as any); }} />
  </KeyboardAvoidingView>;
}

function PanelText({ text, muted }: { text: string; muted: string }) { return <Text style={[styles.panelText, { color: muted }]}>{text}</Text>; }

function FleetAgentRow({ agent, activeStatus, dark, profiles, onOpenDetails }: { agent: AgentInfo; activeStatus: string; dark: boolean; profiles: ExecutionProfile[]; onOpenDetails: () => void }) {
  const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const [menuOpen, setMenuOpen] = useState(false); const [choosingRuntime, setChoosingRuntime] = useState(false);
  const [migrationTarget, setMigrationTarget] = useState<{ profile: ExecutionProfile; idempotencyKey: string } | null>(null);
  const [migration, setMigration] = useState<AgentMigration | null>(null); const [busy, setBusy] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const displayName = agentDisplayName(agent); const availableProfiles = profiles.filter(profile => profile.available);
  useEffect(() => {
    if (!migration || migration.status === 'running-on-new' || migration.status === 'failed') return;
    let mounted = true;
    const refresh = () => fetchAgentMigration(agent.id, migration.request_id).then(value => { if (mounted) setMigration(value); }).catch(error => { if (mounted) setMessage(errorText(error, 'Migration status could not be refreshed.')); });
    const interval = setInterval(refresh, 3000); return () => { mounted = false; clearInterval(interval); };
  }, [agent.id, migration]);
  const confirmMigration = async () => {
    if (!migrationTarget) return; setBusy(true); setMessage(null);
    try { const requested = await requestAgentMigration(agent.id, migrationTarget.profile.id, migrationTarget.idempotencyKey); setMigration(requested); setMigrationTarget(null); setChoosingRuntime(false); setMessage('Request recorded. Operator confirmation is still required.'); }
    catch (error) { setMessage(errorText(error, 'Migration request failed. Retry uses the same request key.')); }
    finally { setBusy(false); }
  };
  return <View style={[styles.fleetAgentWrap, menuOpen ? styles.fleetAgentWrapOpen : undefined]}>
    <View style={styles.fleetPanelRow}><TouchableOpacity testID={`fleet-agent-${agent.id}`} accessibilityRole="button" accessibilityLabel={`Open structured run details for ${displayName}`} onPress={onOpenDetails} activeOpacity={0.75} style={styles.fleetAgentMain}><View style={[styles.tinyDot, { backgroundColor: statusColor(agent.status) }]} /><Text style={[styles.fleetPanelName, { color: text }]}>{displayName}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{displayAgentStatus(activeStatus as any)}</Text></TouchableOpacity><TouchableOpacity testID={`fleet-agent-${agent.id}-menu`} accessibilityRole="button" accessibilityLabel={`Execution actions for ${displayName}`} accessibilityState={{ expanded: menuOpen }} onPress={() => { setMenuOpen(value => !value); setChoosingRuntime(false); setMigrationTarget(null); setMessage(null); }} style={styles.ellipsisButton}><EllipsisIcon size={21.6} color={muted} /></TouchableOpacity></View>
    {menuOpen ? <View testID={`fleet-agent-${agent.id}-popover`} accessibilityViewIsModal style={[styles.agentPopover, { backgroundColor: dark ? '#171E2A' : '#F4F6F9' }]}>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>STATUS</Text><Text style={[styles.agentMetaValue, { color: text }]}>{String(agent.status || 'unavailable').toUpperCase()}</Text></View>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>TASK</Text><Text style={[styles.agentMetaValue, { color: text }]}>{agent.task_id || agent.id}</Text></View>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>RUN</Text><Text style={[styles.agentMetaValue, { color: text }]}>{agent.run_id || 'not reported'}</Text></View>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>HARNESS</Text><Text testID={`fleet-agent-${agent.id}-harness`} style={[styles.agentMetaValue, { color: text }]}>{agent.harness || 'unknown'}</Text></View>
      <View style={styles.agentMetaRow}><Text style={[styles.agentMetaLabel, { color: muted }]}>MODEL</Text><Text testID={`fleet-agent-${agent.id}-model`} style={[styles.agentMetaValue, { color: text }]}>{agent.model || 'unknown'}</Text></View>
      {choosingRuntime && !migrationTarget ? <View testID={`fleet-agent-${agent.id}-migration-targets`} style={styles.migrationTargets}><Text style={[styles.migrationNotice, { color: muted }]}>Choose a verified runtime target. This records an operator hand-off; it does not stop the run.</Text>{availableProfiles.map(profile => <TouchableOpacity key={profile.id} testID={`fleet-agent-${agent.id}-migration-${optionId(profile.harness.id, profile.model.id)}`} accessibilityRole="button" onPress={() => setMigrationTarget({ profile, idempotencyKey: `move_${Date.now()}_${agent.id.replace(/[^A-Za-z0-9]/g, '')}` })} style={styles.migrationTarget}><Text style={[styles.migrationTargetText, { color: text }]}>{profile.harness.label} · {profile.model.label}</Text></TouchableOpacity>)}</View> : null}
      {migrationTarget ? <View testID={`fleet-agent-${agent.id}-migration-confirmation`} style={styles.migrationConfirmation}><Text style={[styles.migrationTitle, { color: text }]}>Stop + relaunch with context?</Text><Text style={[styles.migrationNotice, { color: muted }]}>Target: {migrationTarget.profile.harness.label} / {migrationTarget.profile.model.label}</Text><Text style={styles.migrationWarning}>Confirming records a request only. An operator must perform the relaunch.</Text><View style={styles.popoverActions}><TouchableOpacity accessibilityRole="button" disabled={busy} onPress={() => setMigrationTarget(null)} style={styles.popoverAction}><Text style={[styles.popoverActionText, { color: muted }]}>CANCEL</Text></TouchableOpacity><TouchableOpacity testID={`fleet-agent-${agent.id}-migration-confirm`} accessibilityRole="button" disabled={busy} onPress={() => void confirmMigration()} style={styles.popoverAction}><Text style={styles.popoverActionText}>{busy ? 'REQUESTING…' : 'CONFIRM REQUEST'}</Text></TouchableOpacity></View></View> : null}
      {!choosingRuntime && !migrationTarget ? <View style={styles.popoverActions}><TouchableOpacity testID={`fleet-agent-${agent.id}-move-runtime`} accessibilityRole="button" disabled={availableProfiles.length === 0 || !['working', 'blocked'].includes(String(agent.status || '').toLowerCase())} onPress={() => setChoosingRuntime(true)} style={[styles.popoverAction, availableProfiles.length === 0 ? styles.modelOptionDisabled : undefined]}><Text style={styles.popoverActionText}>MOVE RUNTIME</Text></TouchableOpacity></View> : null}
      {migration ? <Text testID={`fleet-agent-${agent.id}-migration-state`} accessibilityLiveRegion="polite" style={[styles.agentActionMessage, { color: migration.status === 'failed' ? brand.critical : migration.status === 'running-on-new' ? brand.success : brand.attention }]}>Migration: {migration.status}. {migration.status === 'running-on-new' ? `${migration.target.harness}/${migration.target.model} was reported running by the operator.` : migration.status === 'failed' ? `${migration.error || 'Relaunch failed.'} Retry this request.` : 'Requires operator confirmation.'}</Text> : null}
      {message ? <Text accessibilityLiveRegion="polite" style={[styles.agentActionMessage, { color: muted }]}>{message}</Text> : null}
    </View> : null}
  </View>;
}

function activityDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function DrawerPanel({ open, dark, isNarrow, animatedStyle, panHandlers, activeSection, setActiveSection, onClose, onOpenSettings, onOpenHome, onOpenActivity, agents, executionProfiles, attention, activity, providers, errors, loading }: {
  open: boolean; dark: boolean; isNarrow: boolean; animatedStyle: object; panHandlers: object; activeSection: DrawerSection; setActiveSection: (section: DrawerSection) => void; onClose: () => void; onOpenSettings: () => void;
  onOpenHome: () => void; onOpenActivity: () => void;
  agents: AgentInfo[]; executionProfiles: ExecutionProfile[]; attention: UnifiedAttentionRecord[]; activity: RecentActivityItem[]; providers: AuthProviderInfo[]; errors: { agents?: string | null; attention?: string | null; activity?: string | null; providers?: string | null }; loading: boolean;
}) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
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
  // Projects are the real project names the fleet and activity feed already
  // carry - the drawer groups them, it does not invent a hierarchy.
  const projects = useMemo(() => {
    const counts = new Map<string, number>();
    activity.forEach(item => { if (item.project) counts.set(item.project, (counts.get(item.project) || 0) + 1); });
    return Array.from(counts.entries()).sort((left, right) => right[1] - left[1]);
  }, [activity]);
  const matches = (label: string) => !searching || !query.trim() || label.toLowerCase().includes(query.trim().toLowerCase());
  const rows = [
    { key: 'fleet' as const, icon: FleetIcon, title: 'Fleet', count: agents.length },
    { key: 'attention' as const, icon: AttentionIcon, title: 'Attention', count: activeAttention.length, alert: activeAttention.length > 0 },
    { key: 'activity' as const, icon: ActivityIcon, title: 'Activity' },
    { key: 'projects' as const, icon: ProjectsIcon, title: 'Projects', count: projects.length || undefined },
    { key: 'connections' as const, icon: ConnectionsIcon, title: 'Connections' },
  ].filter(row => matches(row.title));
  // Ongoing work is structured execution state, separate from Magi Chat.
  const activeWork = fleet.ordered.filter(entry => matches(agentDisplayName(entry.agent))).slice(0, 5);
  return <Animated.View pointerEvents={open ? 'auto' : 'none'} accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="magistrate-drawer" style={[styles.drawer, isNarrow ? styles.drawerMobile : styles.drawerDesktop, { backgroundColor: glassFill(dark, 'surface') }, blurStyle(28), animatedStyle]} {...panHandlers}>
    <View testID="drawer-header" style={[styles.drawerFixedHeader, { backgroundColor: glassFill(dark), borderColor: glassEdge(dark) }, blurStyle(20)]}><View style={styles.drawerTitleRow}>
      <TouchableOpacity testID="drawer-close" accessibilityRole="button" accessibilityLabel="Close the Magistrate drawer" onPress={onClose} activeOpacity={0.7} style={[styles.drawerCloseButton, { backgroundColor: glassFill(dark), borderColor: glassEdge(dark) }]}><CloseIcon size={20} color={text} /></TouchableOpacity>
      {searching
        ? <TextInput testID="drawer-search-input" autoFocus accessibilityLabel="Search Magistrate navigation" placeholder="Search" placeholderTextColor={muted} value={query} onChangeText={setQuery} style={[styles.drawerSearchInput, { color: text, borderColor: glassEdge(dark) }]} />
        : <Text testID="drawer-wordmark" accessibilityRole="header" style={[styles.drawerWordmark, { color: text }]}>Magistrate</Text>}
      <TouchableOpacity testID="drawer-search" accessibilityRole="button" accessibilityLabel={searching ? 'Close search' : 'Search Magistrate'} accessibilityState={{ expanded: searching }} onPress={() => { setSearching(value => !value); setQuery(''); }} activeOpacity={0.7} style={[styles.drawerSearchButton, { backgroundColor: glassFill(dark), borderColor: glassEdge(dark) }]}>
        {searching ? <CloseIcon size={20} color={text} /> : <SearchIcon size={20} color={text} />}
      </TouchableOpacity>
    </View></View>
    <ScrollView testID="drawer-scroll" style={styles.drawerScroll} contentContainerStyle={styles.drawerScrollContent} keyboardShouldPersistTaps="handled">
      {matches('Magi') ? <TouchableOpacity testID="drawer-home" accessibilityRole="button" accessibilityLabel="Magi, the main conversation" onPress={onOpenHome} style={styles.drawerRow}>
        <View testID="drawer-home-icon" style={styles.drawerIcon}><HomeIcon size={ICON_SIZE} color={muted} /></View><Text style={[styles.drawerRowText, { color: text }]}>Magi</Text>
      </TouchableOpacity> : null}
      {rows.map(row => <View key={row.key}>
        <TouchableOpacity testID={`drawer-section-${row.key}`} accessibilityRole="button" accessibilityLabel={`${row.title} section`} accessibilityState={{ expanded: activeSection === row.key }} onPress={() => toggleSection(row.key)} style={styles.drawerRow}>
          <View testID={`drawer-section-${row.key}-icon`} style={styles.drawerIcon}><row.icon size={ICON_SIZE} color={muted} /></View>
          <Text style={[styles.drawerRowText, { color: text }]}>{row.title}</Text>
          {typeof row.count === 'number' && row.count > 0 ? <Text testID={`drawer-count-${row.key}`} style={[styles.drawerCount, row.alert ? styles.drawerCountAlert : undefined, { color: row.alert ? brand.attention : muted }]}>{row.count}</Text> : null}
        </TouchableOpacity>
        {activeSection === row.key ? <View testID={`drawer-panel-${row.key}`} style={styles.sectionPanel}>{row.key === 'attention' ? (
          loading ? <PanelText text="Loading attention…" muted={muted} /> : errors.attention ? <PanelText text={errors.attention} muted={brand.critical} /> : activeAttention.length === 0 ? <PanelText text="Nothing requires your attention." muted={muted} /> : activeAttention.slice(0, 5).map(item => <TouchableOpacity key={item.id} testID={`attention-item-${item.id}`} accessibilityRole="button" accessibilityLabel={`${item.title}. ${providerLabel(item.provider)}. ${item.subtitle}`} onPress={() => void openAttentionItem(item)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{providerLabel(item.provider)} · {item.subtitle}</Text></TouchableOpacity>)
        ) : row.key === 'fleet' ? (
          loading ? <PanelText text="Loading fleet…" muted={muted} /> : errors.agents ? <PanelText text={errors.agents} muted={brand.critical} /> : agents.length === 0 ? <PanelText text="No active structured worker runs." muted={muted} /> : fleet.ordered.map(({ agent, displayStatus }) => <FleetAgentRow key={agent.id} agent={agent} activeStatus={displayStatus} dark={dark} profiles={executionProfiles} onOpenDetails={() => router.push({ pathname: '/agents', params: { agentId: agent.id } } as any)} />)
        ) : row.key === 'activity' ? (
          <><TouchableOpacity testID="open-canonical-activity" accessibilityRole="button" accessibilityLabel="Open durable Magi activity" onPress={onOpenActivity} style={[styles.panelItem, { backgroundColor: glassFill(dark) }]}><Text style={[styles.panelItemTitle, { color: text }]}>Magi operations</Text><Text style={[styles.panelItemMeta, { color: muted }]}>Inspect Gateway-confirmed lifecycle and decisions</Text></TouchableOpacity>{loading ? <PanelText text="Loading recent activity…" muted={muted} /> : errors.activity ? <PanelText text={errors.activity} muted={brand.critical} /> : activity.length === 0 ? <PanelText text="No recent activity is available." muted={muted} /> : activity.slice(0, 8).map(item => <TouchableOpacity key={item.id} disabled={!item.url && !item.pull_request_number} accessibilityRole="button" accessibilityLabel={`${item.title}. ${item.description}. ${item.project}`} onPress={() => void openActivityItem(item)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{item.description} · {item.project}{activityDate(item.occurred_at) ? ` · ${activityDate(item.occurred_at)}` : ''}</Text></TouchableOpacity>)}</>
        ) : row.key === 'projects' ? (
          loading ? <PanelText text="Loading projects…" muted={muted} /> : errors.activity ? <PanelText text={errors.activity} muted={brand.critical} /> : projects.length === 0 ? <PanelText text="No project activity is available." muted={muted} /> : projects.map(([name, count]) => <View key={name} testID={`drawer-project-${name}`} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{name}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{count} recent item{count === 1 ? '' : 's'}</Text></View>)
        ) : errors.providers ? <PanelText text={errors.providers} muted={brand.critical} /> : providers.length === 0 ? <PanelText text="No connected account data is available." muted={muted} /> : providers.map(provider => <View key={provider.provider} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{provider.provider}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{provider.status}{provider.username ? ` · ${provider.username}` : ''}</Text></View>)}</View> : null}
      </View>)}
      {activeWork.length ? <View testID="drawer-active-work">
        <Text style={[styles.drawerGroupLabel, { color: muted }]}>ACTIVE WORK</Text>
        {activeWork.map(({ agent, displayStatus }) => <TouchableOpacity key={agent.id} testID={`drawer-work-${agent.id}`} accessibilityRole="button" accessibilityLabel={`${agentDisplayName(agent)}, ${displayAgentStatus(displayStatus)}`} onPress={() => router.push({ pathname: '/agents', params: { agentId: agent.id } } as any)} style={styles.drawerWorkRow}>
          <View style={[styles.workDot, { backgroundColor: statusColor(displayStatus) }]} />
          <Text numberOfLines={1} style={[styles.drawerWorkName, { color: text }]}>{agentDisplayName(agent)}</Text>
          <Text numberOfLines={1} style={[styles.drawerWorkStatus, { color: muted }]}>{displayAgentStatus(displayStatus)}</Text>
        </TouchableOpacity>)}
      </View> : null}
    </ScrollView>
    <View style={[styles.drawerBottom, { borderTopColor: glassEdge(dark) }]}>
      <TouchableOpacity testID="drawer-settings-control" accessibilityRole="button" accessibilityLabel="Open Settings" onPress={onOpenSettings} activeOpacity={0.75} style={[styles.drawerSettingsButton, { backgroundColor: glassFill(dark), borderColor: glassEdge(dark) }]}><GearIcon size={21.6} color={text} /><Text style={[styles.drawerSettingsText, { color: text }]}>Settings</Text></TouchableOpacity>
      <TouchableOpacity testID="settings-open" accessibilityRole="button" accessibilityLabel="Open Account settings" onPress={onOpenSettings} activeOpacity={0.75} style={styles.accountRow}><View testID="drawer-account-icon" style={styles.accountIcon}><AccountIcon size={ICON_SIZE} color={muted} /></View></TouchableOpacity>
    </View>
  </Animated.View>;
}

// Environment choices are shown as what they actually look like. Each key maps
// to the scene image the renderer will use (src/services/environmentTheme.ts);
// the two minimal environments are flat tones and carry a swatch instead.
const backgroundOptions: { key: WeatherSceneKey; label: string; preview?: ImageSourcePropType; swatch?: string }[] = [
  { key: 'auto', label: 'Auto', swatch: 'spectral' },
  { key: 'minimal-dark', label: 'Minimal Black', swatch: '#05070A' },
  { key: 'minimal-light', label: 'Minimal Light', swatch: '#F7F8FA' },
  { key: 'dusk-mountain', label: 'Dusk Mountain', preview: TIME_IMAGES.dusk },
  { key: 'clear-night', label: 'Clear Night', preview: TIME_IMAGES.night },
  { key: 'clear-day', label: 'Clear Day', preview: TIME_IMAGES.day },
  { key: 'sunset', label: 'Sunset', preview: TIME_IMAGES.dusk },
  { key: 'clouds', label: 'Clouds', preview: TIME_IMAGES.dawn },
  { key: 'rain', label: 'Rain', preview: TIME_IMAGES.dusk },
  { key: 'storm', label: 'Storm', preview: TIME_IMAGES.night },
];
const themeOptions: { key: ChatThemeMode; label: string }[] = [
  { key: 'system', label: 'System' }, { key: 'dark', label: 'Dark' }, { key: 'light', label: 'Light' },
];

type SettingsSectionKey = 'execution' | 'voice-input' | 'usage' | 'appearance' | 'diagnostics' | 'account';

const SETTINGS_ICONS: Record<SettingsSectionKey, React.ComponentType<{ color: string; size?: number }>> = {
  execution: SlidersIcon, 'voice-input': BellIcon, usage: ActivityIcon, appearance: PaletteIcon, diagnostics: ShieldIcon, account: AccountIcon,
};

/** One grouped settings row: large hit target, icon, title, optional value. */
function SettingsSectionControl({ id, title, expanded, onPress, summary, color, muted, first }: { id: SettingsSectionKey; title: string; expanded: boolean; onPress: () => void; summary?: string; color: string; muted: string; first?: boolean }) {
  const Icon = SETTINGS_ICONS[id];
  return <View testID={`settings-${id}-section`}>
    <TouchableOpacity testID={id === 'appearance' ? 'settings-theme' : `settings-section-${id}`} accessibilityRole="button" accessibilityLabel={`${title} settings`} accessibilityState={{ expanded }} {...({ 'aria-expanded': expanded } as any)} onPress={onPress} style={[styles.settingsRow, first ? undefined : styles.settingsRowDivided]} activeOpacity={0.75}>
      <View style={styles.settingsRowIcon}><Icon size={ICON_SIZE} color={muted} /></View>
      <View style={styles.settingsRowCopy}><Text style={[styles.settingsRowTitle, { color }]}>{title}</Text>{summary ? <Text style={[styles.settingsRowSummary, { color: muted }]}>{summary}</Text> : null}</View>
      <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={expanded ? styles.settingsChevronOpen : undefined}><ChevronRightIcon size={18} color={muted} /></View>
    </TouchableOpacity>
  </View>;
}

function SettingsSheet({ open, dark, animatedStyle, scrimStyle, health, loading, error, executionError, preferences, onPreferencesChange, executionProfiles, executionSettings, onExecutionSettingsChange, onSaveCredential, voiceCapabilities, usage, usageLoading, usageError, onClose, onLogout }: { open: boolean; dark: boolean; animatedStyle: object; scrimStyle: object; health: HealthInfo | null; loading: boolean; error: string | null; executionError?: string | null; preferences: ChatPreferences; onPreferencesChange: (preferences: ChatPreferences) => void; executionProfiles: ExecutionProfile[]; executionSettings: ExecutionSettings; onExecutionSettingsChange: (update: Partial<Pick<ExecutionSettings, 'profile_id' | 'routing_profile_id' | 'switching_behavior' | 'unavailable_behavior'>>) => void; onSaveCredential: (credentialKey: string, credential: string) => Promise<void>; voiceCapabilities: VoiceInputCapabilities; usage: UsageProvider[]; usageLoading: boolean; usageError: string | null; onClose: () => void; onLogout: () => void }) {
  const router = useRouter(); const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const [expandedSection, setExpandedSection] = useState<SettingsSectionKey | null>(null);
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
  useEffect(() => { if (!open) setExpandedSection(null); }, [open]); // eslint-disable-line react-hooks/set-state-in-effect
  const toggleSection = (section: SettingsSectionKey) => setExpandedSection(current => current === section ? null : section);
  const providers = Array.from(new Map(executionProfiles.map(profile => [profile.provider.id, profile.provider.label])).entries());
  const network = Boolean(health && health.gateway_ready !== false);
  const executionReady = health?.execution_interface?.status === 'configured';
  const runtimeStatus = health?.persisted_runtime?.status || 'unobserved';
  // Grouped rows over a tonal surface, as in a native settings sheet: the
  // grouping comes from the container, not from a border on every control.
  const groupSurface = dark ? 'rgba(30,37,48,0.92)' : 'rgba(238,241,244,0.96)';
  return <View pointerEvents={open ? 'box-none' : 'none'} accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="settings-layer" style={styles.settingsLayer}>
    {/* Closed must be visually absent, not just non-interactive: pointerEvents
        alone hides touch, not the rendered wash, so opacity follows the same
        progress value driving the sheet rather than a static alpha. */}
    <Animated.View testID="settings-scrim" pointerEvents={open ? 'auto' : 'none'} style={[styles.settingsScrim, scrimStyle]}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close settings" onPress={onClose} activeOpacity={1} style={styles.chatDimPress} />
    </Animated.View>
    <Animated.View accessibilityViewIsModal testID="settings-sheet" style={[styles.settingsSheet, { backgroundColor: dark ? brand.command : '#FFFFFF' }, animatedStyle]}>
    <View style={[styles.sheetGrabber, { backgroundColor: muted }]} />
    <View testID="settings-header" style={styles.settingsHeader}>
      <Text accessibilityRole="header" style={[styles.settingsTitle, { color: text }]}>Settings</Text>
      <TouchableOpacity testID="settings-close" accessibilityRole="button" accessibilityLabel="Close settings" onPress={onClose} style={styles.settingsClose}><Text style={[styles.settingsCloseText, { color: dark ? brand.cyan : brand.violet }]}>Done</Text></TouchableOpacity>
    </View>
    <ScrollView testID="settings-scroll" style={styles.settingsScroll} contentContainerStyle={styles.settingsScrollContent} keyboardShouldPersistTaps="handled">
    <View testID="settings-execution-section" style={[styles.settingsGroup, { backgroundColor: groupSurface }]}>
    <SettingsSectionControl first id="execution" title="Models & Execution" expanded={expandedSection === 'execution'} onPress={() => toggleSection('execution')} color={text} muted={muted} />
    {expandedSection === 'execution' ? <View testID="settings-execution-content">
    <Text style={[styles.preferenceLabel, { color: muted }]}>DEFAULT FOR NEW / RESTARTED AGENTS</Text>
    <Text style={[styles.settingsToggleDescription, { color: muted }]}>Saved to this Magistrate account and exposed to Firstmate. New and restarted agents do not consume it automatically yet; the hand-off remains operator-run.</Text>
    <View testID="routing-profile-options" style={styles.optionRow}>
      <TouchableOpacity testID="routing-profile-current" accessibilityRole="button" accessibilityState={{ selected: executionSettings.routing_profile_id === null }} onPress={() => onExecutionSettingsChange({ routing_profile_id: null })} style={[styles.optionPill, executionSettings.routing_profile_id === null ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.routing_profile_id === null ? brand.obsidian : text }]}>No default</Text></TouchableOpacity>
      {executionProfiles.filter(profile => profile.available).map(profile => <TouchableOpacity key={profile.id} testID={`routing-profile-${optionId(profile.harness.id, profile.model.id)}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.routing_profile_id === profile.id }} onPress={() => onExecutionSettingsChange({ routing_profile_id: profile.id })} style={[styles.optionPill, executionSettings.routing_profile_id === profile.id ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.routing_profile_id === profile.id ? brand.obsidian : text }]}>{profile.harness.label} · {profile.model.label}</Text></TouchableOpacity>)}
    </View>
    <Text style={[styles.preferenceLabel, { color: muted }]}>RUNTIME SWITCHING</Text>
    <Text style={[styles.settingsToggleDescription, { color: muted }]}>These options apply to structured execution runtime hand-offs, not Magi Chat.</Text>
    <View style={styles.optionRow}>{[
      { key: 'migrate' as const, label: 'Migrate session' }, { key: 'new-session' as const, label: 'New session' },
    ].map(option => <TouchableOpacity key={option.key} testID={`switching-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.switching_behavior === option.key }} onPress={() => onExecutionSettingsChange({ switching_behavior: option.key })} style={[styles.optionPill, executionSettings.switching_behavior === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.switching_behavior === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
    <View style={styles.optionRow}>{[
      { key: 'error' as const, label: 'Error if unavailable' }, { key: 'fallback' as const, label: 'Fallback to current' },
    ].map(option => <TouchableOpacity key={option.key} testID={`unavailable-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.unavailable_behavior === option.key }} onPress={() => onExecutionSettingsChange({ unavailable_behavior: option.key })} style={[styles.optionPill, executionSettings.unavailable_behavior === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.unavailable_behavior === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
    {providers.length ? <View style={styles.credentialBlock}><Text style={[styles.preferenceLabel, { color: muted }]}>HARNESS CREDENTIALS</Text><View style={styles.optionRow}>{providers.map(([key, label]) => <TouchableOpacity key={key} testID={`credential-provider-${key}`} accessibilityRole="button" accessibilityState={{ selected: credentialKey === key }} onPress={() => setCredentialKey(key)} style={[styles.optionPill, credentialKey === key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: credentialKey === key ? brand.obsidian : text }]}>{label}</Text></TouchableOpacity>)}</View>{credentialKey ? <View style={styles.credentialInputRow}><TextInput testID="execution-credential-input" accessibilityLabel={`Credential for ${credentialKey}`} secureTextEntry value={credential} onChangeText={setCredential} placeholder="Paste credential (stored encrypted)" placeholderTextColor={muted} style={[styles.credentialInput, { color: text }]} /><TouchableOpacity testID="execution-credential-save" accessibilityRole="button" disabled={!credential.trim()} onPress={() => { const value = credential.trim(); setCredential(''); void onSaveCredential(credentialKey, value); }} style={styles.credentialSave}><Text style={styles.credentialSaveText}>SAVE</Text></TouchableOpacity></View> : null}</View> : null}
    </View> : null}
    </View>
    <View testID="settings-voice-input-section" style={[styles.settingsGroup, { backgroundColor: groupSurface }]}>
      <SettingsSectionControl first id="voice-input" title="Voice" expanded={expandedSection === 'voice-input'} onPress={() => toggleSection('voice-input')} color={text} muted={muted} />
      {expandedSection === 'voice-input' ? <View testID="settings-voice-input-content">
      <Text style={[styles.settingsToggleDescription, { color: muted }]}>Choose how speech becomes a draft. Nothing is sent until you press Send; gateway credentials stay on the server.</Text>
      <View testID="settings-voice-mode-options" style={styles.optionRow}>{VOICE_INPUT_MODE_OPTIONS.map(option => { const capability = capabilityFor(voiceCapabilities, option.id); const selected = preferences.voiceInputMode === option.id; const disabled = capability.available === 'unavailable'; return <TouchableOpacity key={option.id} testID={`voice-mode-option-${option.id}`} accessibilityRole="button" accessibilityLabel={`${option.label}: ${option.description}`} accessibilityState={{ selected, disabled }} disabled={disabled} onPress={() => { const next = { ...preferences, voiceInputMode: option.id }; onPreferencesChange(next); void saveVoiceInputMode(option.id); }} style={[styles.voiceModeOption, selected ? styles.optionPillSelected : undefined, disabled ? styles.modelOptionDisabled : undefined]}><Text style={[styles.optionText, { color: selected ? brand.obsidian : text }]}>{option.label}{disabled ? ' · unavailable' : ''}</Text><Text style={[styles.voiceModeDescription, { color: selected ? brand.obsidian : muted }]}>{capability.reason || option.description}</Text></TouchableOpacity>; })}</View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>CAPTURE GESTURE</Text>
      <View testID="settings-voice-capture-options" style={styles.optionRow}>{(['tap-to-toggle', 'hold-to-talk'] as const).map(value => <TouchableOpacity key={value} testID={`voice-capture-option-${value}`} accessibilityRole="button" accessibilityState={{ selected: preferences.voiceCaptureBehavior === value }} onPress={() => { const next = { ...preferences, voiceCaptureBehavior: value }; onPreferencesChange(next); void saveVoiceCaptureBehavior(value); }} style={[styles.optionPill, preferences.voiceCaptureBehavior === value ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.voiceCaptureBehavior === value ? brand.obsidian : text }]}>{value === 'tap-to-toggle' ? 'Tap to toggle' : 'Hold to talk'}</Text></TouchableOpacity>)}</View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>FINAL TRANSCRIPT</Text>
      <View testID="settings-voice-transcript-options" style={styles.optionRow}>{(['insert', 'auto-send'] as const).map(value => <TouchableOpacity key={value} testID={`voice-transcript-option-${value}`} accessibilityRole="button" accessibilityState={{ selected: preferences.voiceTranscriptBehavior === value }} onPress={() => { const next = { ...preferences, voiceTranscriptBehavior: value }; onPreferencesChange(next); void saveVoiceTranscriptBehavior(value); }} style={[styles.optionPill, preferences.voiceTranscriptBehavior === value ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.voiceTranscriptBehavior === value ? brand.obsidian : text }]}>{value === 'insert' ? 'Insert for review' : 'Send automatically'}</Text></TouchableOpacity>)}</View>
    </View> : null}
    </View>
    <View testID="settings-usage-section" style={[styles.settingsGroup, { backgroundColor: groupSurface }]}>
      <SettingsSectionControl first id="usage" title="Usage" expanded={expandedSection === 'usage'} onPress={() => toggleSection('usage')} color={text} muted={muted} summary={usage.length ? `${usage[0].provider}${usage[0].plan ? ` · ${usage[0].plan}` : ''}` : undefined} />
      {expandedSection === 'usage' ? <View testID="settings-usage-content">
      <Text style={[styles.settingsToggleDescription, { color: muted }]}>Authenticated quota data only. Missing or unavailable amounts stay explicitly unknown.</Text>
      {usageLoading ? <PanelText text="Loading authenticated usage…" muted={muted} /> : usageError ? <PanelText text={usageError} muted={brand.critical} /> : usage.length === 0 ? <PanelText text="Usage is unknown; no authenticated quota data is available." muted={muted} /> : usage.map(item => <View key={item.provider} style={styles.settingsUsageItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.provider}{item.plan ? ` · ${item.plan}` : ''}</Text><Text style={[styles.panelItemMeta, { color: item.status === 'fresh' ? muted : brand.attention }]}>{item.status === 'fresh' && item.windows.length ? item.windows.map(window => `${window.label || window.id || 'window'}: ${typeof window.percentRemaining === 'number' ? `${window.percentRemaining}% left` : typeof window.spentUsd === 'number' && typeof window.limitUsd === 'number' ? `$${window.spentUsd} / $${window.limitUsd}` : 'amount unknown'}`).join(' · ') : item.status === 'auth_required' ? 'Authentication required' : item.error || 'Quota unknown'}</Text></View>)}
    </View> : null}
    </View>
    <View testID="settings-appearance-section" style={[styles.settingsGroup, { backgroundColor: groupSurface }]}>
      <SettingsSectionControl first id="appearance" title="Appearance" expanded={expandedSection === 'appearance'} onPress={() => toggleSection('appearance')} color={text} muted={muted} summary="Theme, background, and chat display" />
      {expandedSection === 'appearance' ? <View testID="settings-appearance-window" accessibilityViewIsModal style={[styles.appearanceWindow, { backgroundColor: dark ? '#171E2A' : '#F4F6F9' }]}>
      <View style={styles.appearanceHeader}><Text accessibilityRole="header" style={[styles.appearanceTitle, { color: text }]}>Appearance</Text><TouchableOpacity testID="settings-appearance-close" accessibilityRole="button" accessibilityLabel="Close appearance settings" onPress={() => toggleSection('appearance')} style={styles.appearanceClose}><CloseIcon size={22} color={text} /></TouchableOpacity></View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>THEME</Text>
      <View testID="settings-theme-options" style={styles.optionRow}>{themeOptions.map(option => <TouchableOpacity key={option.key} testID={`theme-option-${option.key}`} accessibilityRole="button" accessibilityLabel={`${option.label} theme`} accessibilityState={{ selected: preferences.themeMode === option.key }} onPress={() => { const next = { ...preferences, themeMode: option.key }; onPreferencesChange(next); void saveThemeMode(option.key); }} style={[styles.optionPill, preferences.themeMode === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.themeMode === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>ENVIRONMENT</Text>
      <View testID="settings-environment-grid" style={styles.environmentGrid}>{backgroundOptions.map(option => {
        const selected = preferences.background === option.key;
        return <TouchableOpacity key={option.key} testID={`background-option-${option.key}`} accessibilityRole="button" accessibilityLabel={`${option.label} environment`} accessibilityState={{ selected }} {...({ 'aria-selected': selected } as any)} onPress={() => { const next = { ...preferences, background: option.key, customBackgroundUri: undefined }; onPreferencesChange(next); void saveChatBackground(option.key); }} style={styles.environmentTile}>
          <View style={[styles.environmentThumb, selected ? { borderColor: brand.cyan, borderWidth: 2 } : { borderColor: glassEdge(dark) }]}>
            {option.preview ? <Image source={option.preview} style={styles.environmentThumbImage} resizeMode="cover" accessibilityIgnoresInvertColors />
              : option.swatch === 'spectral' ? <LinearGradient colors={[brand.cyan, brand.violet]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.environmentThumbImage} />
              : <View style={[styles.environmentThumbImage, { backgroundColor: option.swatch }]} />}
          </View>
          <Text numberOfLines={1} style={[styles.environmentLabel, { color: selected ? (dark ? brand.cyan : brand.violet) : muted }]}>{option.label}</Text>
        </TouchableOpacity>;
      })}</View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>CUSTOM BACKGROUND</Text>
      {preferences.customBackgroundUri ? <View style={styles.customBackgroundRow}>
        <Image source={{ uri: preferences.customBackgroundUri }} style={styles.customBackgroundPreview} resizeMode="cover" accessibilityLabel="Custom background preview" />
        <View style={styles.customBackgroundCopy}><Text style={[styles.settingsToggleTitle, { color: text }]}>Your photo</Text><Text style={[styles.settingsToggleDescription, { color: muted }]}>Stored on this device and used only while selected.</Text></View>
        <TouchableOpacity testID="settings-custom-background-remove" accessibilityRole="button" accessibilityLabel="Remove the custom background" onPress={() => void removeCustom()} style={styles.secondaryAction}><Text style={[styles.secondaryActionText, { color: brand.critical }]}>Remove</Text></TouchableOpacity>
      </View> : null}
      <TouchableOpacity testID="settings-custom-background-upload" accessibilityRole="button" accessibilityLabel={preferences.customBackgroundUri ? 'Replace the custom background photo' : 'Upload a custom background photo'} onPress={() => void pickCustomBackground()} style={[styles.uploadBackgroundButton, { borderColor: dark ? brand.cyan : brand.violet }]}><Text style={[styles.optionText, { color: dark ? brand.cyan : brand.violet }]}>{preferences.customBackgroundUri ? 'Replace photo' : 'Upload background'}</Text></TouchableOpacity>
    </View> : null}
    </View>
    <View style={[styles.settingsGroup, { backgroundColor: groupSurface }]}>
    <SettingsSectionControl first id="diagnostics" title="Diagnostics" expanded={expandedSection === 'diagnostics'} onPress={() => toggleSection('diagnostics')} color={text} muted={muted} summary="Gateway, event ingress and execution" />
    {expandedSection === 'diagnostics' ? <View testID="settings-diagnostics-content" style={styles.settingsSectionContent}><Text style={[styles.settingsToggleDescription, { color: muted }]}>Process-free Gateway, persisted runtime, and execution-interface details for troubleshooting.</Text><TouchableOpacity testID="settings-diagnostics-open" accessibilityRole="button" accessibilityLabel="Open diagnostics" onPress={() => { onClose(); router.push('/diagnostics' as any); }} style={styles.diagnosticsButton}><Text style={[styles.diagnosticsButtonText, { color: text }]}>Open diagnostics</Text><Text style={[styles.diagnosticsArrow, { color: muted }]}>↗</Text></TouchableOpacity></View> : null}
    <SettingsSectionControl id="account" title="Account" expanded={expandedSection === 'account'} onPress={() => toggleSection('account')} color={text} muted={muted} summary="Profile, notifications and sign-in" />
    {expandedSection === 'account' ? <View testID="settings-account-content" style={styles.settingsSectionContent}><TouchableOpacity testID="settings-account-open" accessibilityRole="button" accessibilityLabel="Open account settings" onPress={() => { onClose(); router.push('/account' as any); }} style={styles.diagnosticsButton}><Text style={[styles.diagnosticsButtonText, { color: text }]}>Account & notifications</Text><Text style={[styles.diagnosticsArrow, { color: muted }]}>↗</Text></TouchableOpacity><TouchableOpacity testID="settings-logout" accessibilityRole="button" accessibilityLabel="Sign out of Magistrate" onPress={onLogout} style={styles.logoutButton}><Text style={styles.logoutButtonText}>SIGN OUT</Text></TouchableOpacity></View> : null}
    </View>
    <View style={styles.settingsStatusGrid}><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: error ? brand.critical : loading ? brand.attention : network ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Gateway</Text><Text testID="settings-network-status" style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : error ? 'Unavailable' : network ? 'Connected' : 'Degraded'}</Text></View></View><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: executionReady ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Persisted runtime</Text><Text style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : `${runtimeStatus} · ${executionReady ? 'ready' : 'unavailable'}`}</Text></View></View></View>
    {error || executionError ? <Text style={styles.settingsError}>{error || executionError}</Text> : null}
    <Text testID="settings-about" style={[styles.settingsAbout, { color: muted }]}>Magistrate · Magi is the interface. Fleet, Attention and the environment system are behind it.</Text>
    </ScrollView>
    </Animated.View>
  </View>;
}

export default function ChatScreen() {
  const { record } = useLocalSearchParams<{ record?: string | string[] }>(); const autoStartRecording = (Array.isArray(record) ? record[0] : record) === 'true';
  const dark = isDarkTheme(useChatColorScheme()); const { width } = useWindowDimensions(); const isNarrow = width < 720; const drawerWidth = Math.min(isNarrow ? width * 0.82 : 310, 330);
  const [drawerOpen, setDrawerOpen] = useState(false); const [settingsOpen, setSettingsOpen] = useState(false); const [activityOpen, setActivityOpen] = useState(false); const [activeSection, setActiveSection] = useState<DrawerSection>(null); const [preferences, setPreferences] = useState<ChatPreferences>(DEFAULT_CHAT_PREFERENCES); const [preferencesReady, setPreferencesReady] = useState(false);
  const [executionProfiles, setExecutionProfiles] = useState<ExecutionProfile[]>([]);
  const [executionSettings, setExecutionSettings] = useState<ExecutionSettings>({ profile_id: null, routing_profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [voiceCapabilities, setVoiceCapabilities] = useState<VoiceInputCapabilities>(() => getLocalVoiceCapabilities());
  const [agents, setAgents] = useState<AgentInfo[]>([]); const [attention, setAttention] = useState<UnifiedAttentionRecord[]>([]); const [activity, setActivity] = useState<RecentActivityItem[]>([]); const [providers, setProviders] = useState<AuthProviderInfo[]>([]); const [usage, setUsage] = useState<UsageProvider[]>([]); const [usageLoading, setUsageLoading] = useState(false); const [usageError, setUsageError] = useState<string | null>(null); const [health, setHealth] = useState<HealthInfo | null>(null);
  const [loading, setLoading] = useState(true); const [healthLoading, setHealthLoading] = useState(true); const [healthError, setHealthError] = useState<string | null>(null); const [reducedMotion, setReducedMotion] = useState(false);
  const [errors, setErrors] = useState<{ agents?: string | null; attention?: string | null; activity?: string | null; providers?: string | null }>({});
  const drawerProgress = useSharedValue(0); const settingsProgress = useSharedValue(0);
  useEffect(() => { let mounted = true; loadChatPreferences().then(value => { if (mounted) setPreferences(value); }).catch(() => {}).finally(() => { if (mounted) setPreferencesReady(true); }); return () => { mounted = false; }; }, []);
  useEffect(() => {
    let mounted = true;
    Promise.allSettled([fetchExecutionCapabilities(), fetchExecutionSettings(), fetchVoiceInputCapabilities()]).then(([capabilityResult, settingsResult, voiceResult]) => {
      if (!mounted) return;
      if (capabilityResult.status === 'fulfilled') setExecutionProfiles(profilesFromCapabilities(capabilityResult.value));
      else setExecutionError(errorText(capabilityResult.reason, 'Execution capabilities could not be loaded.'));
      if (settingsResult.status === 'fulfilled') setExecutionSettings(settingsResult.value);
      else setExecutionError(errorText(settingsResult.reason, 'Execution settings could not be loaded.'));
      if (voiceResult.status === 'fulfilled') {
        const local = getLocalVoiceCapabilities(voiceResult.value.serverConfigured);
        const serverOpenai = capabilityFor(voiceResult.value, 'openai');
        setVoiceCapabilities({ ...local, serverProvider: voiceResult.value.serverProvider, serverConfigured: voiceResult.value.serverConfigured, modes: local.modes.map(item => item.id === 'openai' ? serverOpenai : item) });
      }
    });
    return () => { mounted = false; };
  }, []);
  useEffect(() => { AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion); const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion); return () => sub.remove(); }, []);
  useEffect(() => { drawerProgress.value = withTiming(drawerOpen ? 1 : 0, { duration: reducedMotion ? 1 : drawerOpen ? 260 : 340, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [drawerOpen, drawerProgress, reducedMotion]);
  useEffect(() => { settingsProgress.value = withTiming(settingsOpen ? 1 : 0, { duration: reducedMotion ? 1 : 300, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [settingsOpen, settingsProgress, reducedMotion]);
  useEffect(() => {
    let mounted = true;
    let refreshInFlight = false;
    const refresh = async () => {
      // One bounded refresh at a time prevents a slow gateway from creating a
      // polling backlog. This is the fallback for valid realtime events too:
      // the gateway has no attention push channel yet.
      if (refreshInFlight) return;
      refreshInFlight = true;
      const results = await Promise.allSettled([fetchAgents(), fetchUnifiedAttention(), fetchRecentActivity(), fetchAuthProviders(), fetchHealth()]);
      refreshInFlight = false;
      if (!mounted) return;
      const [agentResult, attentionResult, activityResult, providerResult, healthResult] = results;
      setErrors({ agents: agentResult.status === 'rejected' ? errorText(agentResult.reason, 'Agent data could not be loaded.') : null, attention: attentionResult.status === 'rejected' ? errorText(attentionResult.reason, 'Attention data could not be loaded.') : null, activity: activityResult.status === 'rejected' ? errorText(activityResult.reason, 'Recent activity could not be loaded.') : null, providers: providerResult.status === 'rejected' ? errorText(providerResult.reason, 'Connections data could not be loaded.') : null });
      if (agentResult.status === 'fulfilled') setAgents(agentResult.value); if (attentionResult.status === 'fulfilled') setAttention(attentionResult.value); if (activityResult.status === 'fulfilled') setActivity(activityResult.value.items); if (providerResult.status === 'fulfilled') setProviders(providerResult.value);
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value); else setHealthError(errorText(healthResult.reason, 'Network status could not be loaded.'));
      setLoading(false); setHealthLoading(false);
    };
    void refresh();
    const interval = setInterval(() => void refresh(), 15000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);
  useEffect(() => {
    if (!settingsOpen) return;
    setUsageLoading(true); setUsageError(null);
    fetchUsage().then(result => setUsage(result.providers)).catch(error => setUsageError(errorText(error, 'Usage data could not be loaded.'))).finally(() => setUsageLoading(false));
  }, [settingsOpen]);
  const drawerAnimatedStyle = useAnimatedStyle(() => ({ opacity: drawerProgress.value, transform: [{ translateX: interpolate(drawerProgress.value, [0, 1], [-(drawerWidth + 70), 0]) }] }), [drawerWidth]);
  // Drawer, chat, and Settings are sibling layers. The drawer translates only
  // itself; the transcript keeps the same viewport and scroll geometry while a
  // dismissing scrim makes the independent overlay explicit.
  const chatDimStyle = useAnimatedStyle(() => ({ opacity: drawerProgress.value * (isNarrow ? 0.42 : 0.18) }), [isNarrow]);
  const settingsAnimatedStyle = useAnimatedStyle(() => ({ opacity: settingsProgress.value, transform: [{ translateY: interpolate(settingsProgress.value, [0, 1], [420, 0]) }] }));
  const settingsScrimStyle = useAnimatedStyle(() => ({ opacity: settingsProgress.value }));
  const swipeToClose = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => isNarrow && drawerOpen && g.dx < -8 && Math.abs(g.dx) > Math.abs(g.dy),
    onPanResponderRelease: (_, g) => { if (g.dx < -55 || g.vx < -0.35) setDrawerOpen(false); },
  }), [drawerOpen, isNarrow]);
  return <EnvironmentBackground hideBottomControls preserveCanvas><SafeAreaView style={styles.page}>
    {!preferencesReady ? <View testID="chat-appearance-loading" style={[styles.appearanceLoading, { backgroundColor: dark ? brand.obsidian : '#F7F8FA' }]} /> : <>
      <DrawerPanel open={drawerOpen && !settingsOpen} dark={dark} isNarrow={isNarrow} animatedStyle={drawerAnimatedStyle} panHandlers={isNarrow ? swipeToClose.panHandlers : {}} activeSection={activeSection} setActiveSection={setActiveSection} onClose={() => setDrawerOpen(false)} onOpenSettings={() => { setDrawerOpen(false); setSettingsOpen(true); }} onOpenHome={() => setDrawerOpen(false)} onOpenActivity={() => { setDrawerOpen(false); setActivityOpen(true); }} agents={agents} executionProfiles={executionProfiles} attention={attention} activity={activity} providers={providers} errors={errors} loading={loading} />
      <Animated.View style={styles.chatStage}><ChatCanvas drawerOpen={drawerOpen} onDrawerToggle={() => setDrawerOpen(value => !value)} activityOpen={activityOpen} onActivityOpen={() => setActivityOpen(true)} onActivityClose={() => setActivityOpen(false)} voiceInputMode={preferences.voiceInputMode} voiceCapabilities={voiceCapabilities} voiceCaptureBehavior={preferences.voiceCaptureBehavior} voiceTranscriptBehavior={preferences.voiceTranscriptBehavior} autoStartRecording={autoStartRecording} />
        <Animated.View testID="chat-dim" pointerEvents={drawerOpen ? 'auto' : 'none'} style={[styles.chatDim, chatDimStyle]}>
          <TouchableOpacity testID="drawer-dismiss" accessibilityRole="button" accessibilityLabel="Close the Magistrate drawer" onPress={() => setDrawerOpen(false)} activeOpacity={1} style={styles.chatDimPress} />
        </Animated.View>
      </Animated.View>
      <SettingsSheet open={settingsOpen} dark={dark} animatedStyle={settingsAnimatedStyle} scrimStyle={settingsScrimStyle} health={health} loading={healthLoading} error={healthError} executionError={executionError} preferences={preferences} onPreferencesChange={setPreferences} executionProfiles={executionProfiles} voiceCapabilities={voiceCapabilities} executionSettings={executionSettings} onExecutionSettingsChange={update => { void updateExecutionSettings(update).then(saved => { setExecutionSettings(saved); setExecutionError(null); }).catch(error => setExecutionError(errorText(error, 'The execution setting could not be saved.'))); }} onSaveCredential={async (credentialKey, credential) => { try { await saveExecutionCredential(credentialKey, credential); setExecutionError(null); const capabilities = await fetchExecutionCapabilities(); setExecutionProfiles(profilesFromCapabilities(capabilities)); } catch (error) { setExecutionError(errorText(error, 'The credential could not be saved.')); } }} usage={usage} usageLoading={usageLoading} usageError={usageError} onClose={() => setSettingsOpen(false)} onLogout={() => { setSettingsOpen(false); void logoutGatewaySession(); }} />
    </>}
  </SafeAreaView></EnvironmentBackground>;
}

const styles = StyleSheet.create({
  // The environment owns the canvas: no page padding, no card, no rounded
  // window. Everything above the transcript floats (prompt sections 4 and 5).
  page: { flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden', touchAction: 'pan-y' } as any,
  chatStage: { flex: 1, minWidth: 0, minHeight: 0, zIndex: 1, overflow: 'hidden' },
  chatDim: { ...StyleSheet.absoluteFill, backgroundColor: '#05070A', zIndex: 60 }, chatDimPress: { flex: 1 },
  canvas: { flex: 1, minWidth: 0, minHeight: 0, position: 'relative', overflow: 'hidden' },

  headerDock: { position: 'absolute', top: 0, left: 0, right: 0, zIndex: 50, elevation: 20 },
  topBar: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, ...Platform.select({ web: { paddingTop: 'calc(10px + env(safe-area-inset-top, 0px))' as any }, default: { paddingTop: 10 } }) },
  staleConversation: { alignSelf: 'center', maxWidth: 560, marginTop: 8, marginHorizontal: 14, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 14, paddingVertical: 8 },
  staleConversationText: { fontSize: 12, lineHeight: 17, fontWeight: '600', textAlign: 'center' },
  glassCircle: { width: 46, height: 46, borderRadius: 23, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center', overflow: 'visible' },
  unreadAttentionDot: { position: 'absolute', top: 3, right: 3, width: 9, height: 9, borderRadius: 5, backgroundColor: '#F5C542' },
  identityControl: { flex: 1, minWidth: 0, minHeight: 46, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: 6 },
  identityName: { fontSize: 19, fontWeight: '600', letterSpacing: -0.2 }, identityVariant: { flexShrink: 1, fontSize: 19, fontWeight: '400', letterSpacing: -0.2 }, identityChevron: { transform: [{ rotate: '90deg' }] },
  mark: { width: 37, height: 37 }, tinyDot: { width: 8, height: 8, borderRadius: 4 },

  emptyState: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 34, gap: 22, zIndex: 3 },
  emptyStateMarkWrap: { width: 108, height: 108, alignItems: 'center', justifyContent: 'center' },
  emptyStateHalo: { position: 'absolute', width: 108, height: 108, borderRadius: 54, backgroundColor: 'rgba(36,216,255,0.22)' },
  emptyStateMark: { width: 54, height: 54 },
  greeting: { maxWidth: 460, fontSize: 34, lineHeight: 42, fontWeight: '400', letterSpacing: -0.6, textAlign: 'center' },

  chatHistory: { flex: 1, minHeight: 0, touchAction: 'pan-y', overscrollBehaviorY: 'contain' } as any,
  chatHistoryContent: { flexGrow: 1, justifyContent: 'flex-end', paddingHorizontal: 20, gap: 18 },
  userMessageWrap: { maxWidth: 680, alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'flex-end', gap: 5 },
  userBubble: { flex: 1, paddingVertical: 12, paddingHorizontal: 17, borderRadius: 22 },
  assistantMessage: { maxWidth: 680, alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'flex-start', gap: 5, paddingVertical: 2, paddingHorizontal: 2 },
  assistantBody: { flex: 1, minWidth: 0 },
  messageText: { fontSize: 17, lineHeight: 26 },
  workingState: { maxWidth: 680, minHeight: 44, alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 9, paddingHorizontal: 2 },
  workingStateMark: { width: 22, height: 22 },
  workingLabel: { fontSize: 13, lineHeight: 19 },
  assistantState: { fontSize: 11, marginTop: 8 }, assistantStateFailed: { color: brand.critical, fontSize: 11, lineHeight: 17, marginTop: 8 },
  messageTimestamp: { fontSize: 10, marginTop: 5, opacity: 0.62, textAlign: 'right' }, retryText: { color: brand.cyan, fontSize: 11, fontWeight: '800' },
  attachedFile: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 7 }, attachedFileName: { maxWidth: 260, fontSize: 11, opacity: 0.82 }, deliverySending: { fontSize: 10, marginTop: 4, color: brand.mutedDark }, deliveryFailed: { fontSize: 10, marginTop: 4, color: brand.critical },

  jumpButton: { position: 'absolute', alignSelf: 'center', width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderWidth: StyleSheet.hairlineWidth, borderRadius: 999, zIndex: 30, shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 10, elevation: 16 },
  jumpText: { fontSize: 20, lineHeight: 22, fontWeight: '700' },
  inlineMessageAction: { minWidth: 32, minHeight: 32, alignItems: 'center', justifyContent: 'center', borderRadius: 16 }, inlineMessageActionText: { color: brand.mutedDark, fontSize: 13, letterSpacing: 1, fontWeight: '800' },
  copiedLabel: { position: 'absolute', right: 20, color: brand.success, fontSize: 11, fontWeight: '800', zIndex: 31 },
  messageActions: { position: 'absolute', right: 24, flexDirection: 'row', borderRadius: 18, padding: 4, zIndex: 32, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 20, elevation: 8 }, messageAction: { minWidth: 52, minHeight: 40, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8 }, messageActionText: { fontSize: 13, fontWeight: '700' },

  // The composer genuinely floats on every platform: detached from the bottom
  // edge, inset horizontally, and layered above the transcript.
  composerDock: { position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 50, ...Platform.select({ web: { paddingBottom: 'calc(14px + env(safe-area-inset-bottom, 0px))' as any }, default: { paddingBottom: 14 } }) },
  composer: { flexShrink: 0, flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 60, borderRadius: 30, paddingHorizontal: 10, paddingVertical: 7, marginHorizontal: 14, maxWidth: 720, alignSelf: 'center', width: '100%', zIndex: 20, elevation: 12, borderWidth: StyleSheet.hairlineWidth, shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 22, shadowOffset: { width: 0, height: 6 } },
  composerIconButton: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' }, composerIconText: { fontSize: 23, fontWeight: '400' },
  composerInput: { flex: 1, minWidth: 0, fontSize: 16, paddingVertical: 8, paddingHorizontal: 2, outlineStyle: 'none' as any },
  sendButton: { width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center', backgroundColor: brand.violet }, stopButton: { width: 42, borderRadius: 21, backgroundColor: brand.critical }, stopButtonText: { color: brand.paper, fontSize: 12, fontWeight: '800' }, sendArrow: { color: brand.paper, fontSize: 22, fontWeight: '800' }, disabled: { opacity: 0.55 },
  composerStatus: { minHeight: 20, flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 4, paddingHorizontal: 26, paddingTop: 4 },
  editingLabel: { color: brand.cyan, fontSize: 11, fontWeight: '700' }, micListeningLabel: { color: brand.cyan, fontSize: 11, fontWeight: '700' }, micTranscribingLabel: { color: brand.violet, fontSize: 11, fontWeight: '700' }, micReadyLabel: { color: brand.success, fontSize: 11, fontWeight: '700' }, micErrorLabel: { color: brand.critical, fontSize: 11, fontWeight: '700' }, thinkingLabel: { color: brand.mutedDark, fontSize: 11, fontWeight: '700', alignItems: 'center' }, thinkingDots: { fontSize: 16, letterSpacing: 2, fontWeight: '900' }, queuedLabel: { color: brand.violet, fontSize: 11, fontWeight: '700' }, sendError: { color: '#FFB4B2', fontSize: 12, flex: 1, textAlign: 'right' },

  attachmentControl: { width: 40, zIndex: 20 }, attachmentMenu: { position: 'absolute', left: -2, bottom: 50, width: 238, borderRadius: 20, padding: 11, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 24, elevation: 14 }, attachmentOption: { minHeight: 54, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 8, paddingVertical: 7, borderRadius: 13 }, attachmentOptionTitle: { fontSize: 14, fontWeight: '700' }, attachmentOptionMeta: { fontSize: 11, marginTop: 2 },
  attachmentPreview: { flexGrow: 0, marginHorizontal: 14, marginBottom: 8, maxHeight: 60 }, attachmentPreviewContent: { gap: 8, paddingHorizontal: 3 }, attachmentChip: { width: 220, minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 9, padding: 5, paddingRight: 7, borderRadius: 15 }, attachmentThumbnail: { width: 46, height: 46, borderRadius: 11 }, attachmentFileIcon: { width: 46, height: 46, borderRadius: 11, alignItems: 'center', justifyContent: 'center' }, attachmentCopy: { flex: 1, minWidth: 0 }, attachmentName: { fontSize: 12, fontWeight: '700' }, attachmentMeta: { fontSize: 10, marginTop: 3 }, attachmentRemove: { width: 28, height: 38, alignItems: 'center', justifyContent: 'center' }, attachmentRemoveText: { fontSize: 21, lineHeight: 23 },
  activeVoiceSurface: { height: 72, marginHorizontal: 14, marginBottom: 8, borderRadius: 28, backgroundColor: 'rgba(17,23,34,0.96)', borderWidth: 1, borderColor: brand.cyan, overflow: 'hidden', zIndex: 9, alignItems: 'center', justifyContent: 'center' }, activeVoiceHalo: { position: 'absolute', width: 170, height: 170, borderRadius: 85, backgroundColor: 'rgba(36,216,255,0.12)' }, activeVoiceMark: { width: 52, height: 52, zIndex: 2 }, micActiveButton: { borderWidth: 1, borderColor: brand.cyan, borderRadius: 20, backgroundColor: 'rgba(36,216,255,0.12)' },
  liveWaveform: { position: 'absolute', left: 8, right: 8, bottom: 8, height: 52, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', paddingHorizontal: 14, zIndex: 9 }, liveWaveformBar: { width: 3, borderRadius: 2 },

  // Native-feeling sheet: grabber, large rounded top, Done, grouped rows.
  sheetLayer: { ...StyleSheet.absoluteFill, zIndex: 45, justifyContent: 'flex-end' },
  sheetScrim: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(5,7,10,0.46)' },
  sheetGrabber: { alignSelf: 'center', width: 36, height: 4, borderRadius: 2, opacity: 0.4, marginBottom: 10 },
  executionSheet: { maxHeight: '76%', borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: 10, paddingHorizontal: 16, ...Platform.select({ web: { paddingBottom: 'calc(18px + env(safe-area-inset-bottom, 0px))' as any }, default: { paddingBottom: 18 } }), shadowColor: '#000', shadowOpacity: 0.4, shadowRadius: 30, elevation: 22 },
  sheetHeader: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  sheetTitle: { fontSize: 20, fontWeight: '700', letterSpacing: -0.2 }, sheetDone: { minHeight: 44, minWidth: 56, alignItems: 'flex-end', justifyContent: 'center' }, sheetDoneText: { fontSize: 17, fontWeight: '600' },
  executionScroll: { flexGrow: 0 }, executionScrollContent: { paddingBottom: 8 },
  group: { borderRadius: 18, overflow: 'hidden' }, groupLabel: { fontSize: 11, fontWeight: '700', letterSpacing: 0.9, marginTop: 18, marginBottom: 8, paddingHorizontal: 4 },
  groupRow: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 10 },
  groupRowDivided: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(142,153,170,0.22)' },
  groupRowCopy: { flex: 1, minWidth: 0 }, groupRowTitle: { fontSize: 16, fontWeight: '500' }, groupRowMeta: { fontSize: 12, lineHeight: 17, marginTop: 2 }, groupCheck: { fontSize: 17, fontWeight: '700' }, groupNotice: { paddingHorizontal: 16, paddingVertical: 14 },

  modelOptionDisabled: { opacity: 0.5 }, menuTitle: { fontSize: 14, fontWeight: '800', marginBottom: 7, paddingHorizontal: 7 }, modelError: { color: '#FFB4B2', fontSize: 12, lineHeight: 17 },

  // Drawer: sparse rows, one icon system, pinned footer, no cards.
  drawer: { position: 'absolute', top: 0, bottom: 0, width: 310, zIndex: 10, paddingHorizontal: 14, ...Platform.select({ web: { paddingTop: 'calc(14px + env(safe-area-inset-top, 0px))' as any }, default: { paddingTop: 14 } }), overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.34, shadowRadius: 32, elevation: 14 },
  drawerDesktop: { left: 0, borderTopRightRadius: 26, borderBottomRightRadius: 26 }, drawerMobile: { left: 0, width: '84%', maxWidth: 360 },
  drawerFixedHeader: { flexShrink: 0, paddingHorizontal: 8, paddingVertical: 4, marginBottom: 10, borderRadius: 26, borderWidth: StyleSheet.hairlineWidth },
  drawerTitleRow: { minHeight: 46, flexDirection: 'row', alignItems: 'center', gap: 10 },
  drawerCloseButton: { width: 42, height: 42, borderRadius: 21, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  drawerWordmark: { flex: 1, fontFamily: Platform.select({ web: 'Bodoni Moda, Times New Roman, serif', default: undefined }), fontSize: 26, lineHeight: 34, fontWeight: '500' },
  drawerSearchInput: { flex: 1, minWidth: 0, height: 42, borderRadius: 21, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 14, fontSize: 16, outlineStyle: 'none' as any },
  drawerSearchButton: { width: 42, height: 42, borderRadius: 21, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  drawerScroll: { flex: 1, minHeight: 0, touchAction: 'pan-y', overscrollBehaviorY: 'contain' } as any, drawerScrollContent: { paddingTop: 4, paddingBottom: 18 },
  drawerRow: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 8, paddingHorizontal: 6, borderRadius: 14 },
  drawerIcon: { width: ICON_SIZE, height: ICON_SIZE, alignItems: 'center', justifyContent: 'center' },
  drawerRowText: { flex: 1, fontSize: 16, fontWeight: '400', textAlign: 'left' },
  drawerCount: { fontSize: 13, fontWeight: '600' }, drawerCountAlert: { fontWeight: '800' },
  drawerGroupLabel: { fontSize: 11, fontWeight: '700', letterSpacing: 0.9, marginTop: 22, marginBottom: 6, paddingHorizontal: 6 },
  drawerWorkRow: { minHeight: 46, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6, paddingHorizontal: 6, borderRadius: 14 },
  workDot: { width: 7, height: 7, borderRadius: 4 }, drawerWorkName: { flexShrink: 1, fontSize: 14, fontWeight: '500' }, drawerWorkStatus: { flexShrink: 1, fontSize: 12 },
  drawerBottom: { flexShrink: 0, flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 12, borderTopWidth: StyleSheet.hairlineWidth, ...Platform.select({ web: { paddingBottom: 'calc(12px + env(safe-area-inset-bottom, 0px))' as any }, default: { paddingBottom: 12 } }) },
  drawerSettingsButton: { flex: 1, minHeight: 48, paddingHorizontal: 16, flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 24, borderWidth: StyleSheet.hairlineWidth },
  drawerSettingsText: { fontSize: 15, fontWeight: '600' },
  accountRow: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center', borderRadius: 24 },
  accountIcon: { width: ICON_SIZE, height: ICON_SIZE, alignItems: 'center', justifyContent: 'center' },
  gearIconContainer: { width: 20, alignItems: 'center', justifyContent: 'center' }, chevron: { width: 18, fontSize: 13, textAlign: 'center' },
  sectionPanel: { paddingLeft: 44, paddingRight: 4, paddingBottom: 12, gap: 8 }, panelText: { fontSize: 13, lineHeight: 19 }, panelItem: { minHeight: 40, justifyContent: 'center', paddingVertical: 6 }, panelItemTitle: { fontSize: 13, fontWeight: '700', marginBottom: 2 }, panelItemMeta: { fontSize: 12, lineHeight: 17 },
  fleetAgentWrap: { borderRadius: 14 }, fleetAgentWrapOpen: { zIndex: 4 }, fleetPanelRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 3 }, fleetAgentMain: { flex: 1, minWidth: 0, minHeight: 40, flexDirection: 'row', alignItems: 'center', gap: 8 }, fleetPanelName: { flex: 1, fontSize: 13, fontWeight: '700' }, ellipsisButton: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', borderRadius: 18 }, agentPopover: { borderRadius: 15, padding: 12, marginBottom: 6, gap: 8, shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 16, elevation: 7 }, agentMetaRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10 }, agentMetaLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 0.7 }, agentMetaValue: { fontSize: 11, fontWeight: '800' }, popoverActions: { flexDirection: 'row', gap: 8, marginTop: 2 }, popoverAction: { minHeight: 34, justifyContent: 'center', paddingHorizontal: 10, borderWidth: 1, borderColor: 'rgba(142,153,170,0.3)', borderRadius: 10 }, popoverActionText: { color: '#24D8FF', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 }, agentActionMessage: { fontSize: 10, lineHeight: 14 }, migrationTargets: { marginTop: 4, gap: 6 }, migrationNotice: { fontSize: 10, lineHeight: 14 }, migrationTarget: { minHeight: 34, borderWidth: 1, borderColor: 'rgba(142,153,170,0.35)', borderRadius: 9, paddingHorizontal: 9, justifyContent: 'center' }, migrationTargetText: { fontSize: 10, fontWeight: '800' }, migrationConfirmation: { gap: 6 }, migrationTitle: { fontSize: 12, fontWeight: '800' }, migrationWarning: { color: brand.attention, fontSize: 10, lineHeight: 14 },

  settingsLayer: { ...StyleSheet.absoluteFill, zIndex: 20, justifyContent: 'flex-end' },
  settingsScrim: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(5,7,10,0.46)' },
  settingsSheet: { position: 'absolute', left: 0, right: 0, bottom: 0, height: '90%', maxHeight: '92%', borderTopLeftRadius: 30, borderTopRightRadius: 30, paddingTop: 12, paddingHorizontal: 16, shadowColor: '#000', shadowOpacity: 0.4, shadowRadius: 34, elevation: 20, overflow: 'hidden' },
  appearanceLoading: { flex: 1 },
  settingsHeader: { minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 },
  settingsClose: { minHeight: 44, minWidth: 60, alignItems: 'flex-end', justifyContent: 'center' }, settingsCloseText: { fontSize: 17, fontWeight: '600' },
  settingsTitle: { fontSize: 26, fontWeight: '700', letterSpacing: -0.4 },
  settingsScroll: { flex: 1, minHeight: 0, touchAction: 'pan-y', overscrollBehaviorY: 'contain' } as any, settingsScrollContent: { paddingBottom: 40 },
  settingsGroup: { borderRadius: 18, overflow: 'hidden', marginTop: 14 },
  settingsRow: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 14, paddingHorizontal: 16, paddingVertical: 10 },
  settingsRowDivided: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(142,153,170,0.22)' },
  settingsRowIcon: { width: ICON_SIZE, height: ICON_SIZE, alignItems: 'center', justifyContent: 'center' },
  settingsRowCopy: { flex: 1, minWidth: 0 }, settingsRowTitle: { fontSize: 16, fontWeight: '500' }, settingsRowSummary: { fontSize: 12, lineHeight: 17, marginTop: 2 },
  settingsChevronOpen: { transform: [{ rotate: '90deg' }] },
  settingsSectionContent: { paddingHorizontal: 16, paddingBottom: 16 },
  settingsStatusGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 24, marginTop: 26, paddingHorizontal: 4 }, settingsStatus: { minWidth: 150, flexDirection: 'row', alignItems: 'center', gap: 10 }, statusDot: { width: 9, height: 9, borderRadius: 5 }, settingsLabel: { fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: '700' }, settingsValue: { fontSize: 15, fontWeight: '700', marginTop: 2 }, settingsError: { color: '#FFB4B2', fontSize: 12, marginTop: 12, paddingHorizontal: 4 },
  settingsAbout: { fontSize: 12, lineHeight: 18, marginTop: 22, paddingHorizontal: 4 },
  settingsToggleRow: { maxWidth: 460, minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 16, marginTop: 8 }, settingsToggleCopy: { flex: 1 }, settingsToggleTitle: { fontSize: 15, fontWeight: '600' }, settingsToggleDescription: { fontSize: 12, lineHeight: 17, marginTop: 2 },
  settingsUsageItem: { paddingVertical: 8 }, credentialBlock: { marginTop: 8, maxWidth: 520 }, credentialInputRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }, credentialInput: { flex: 1, minWidth: 0, minHeight: 40, borderWidth: 1, borderColor: 'rgba(142,153,170,0.38)', borderRadius: 12, paddingHorizontal: 12, fontSize: 16, outlineStyle: 'none' as any }, credentialSave: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 12, backgroundColor: brand.cyan }, credentialSaveText: { color: brand.obsidian, fontSize: 11, fontWeight: '800' },
  diagnosticsButton: { marginTop: 10, minHeight: 44, flexDirection: 'row', alignItems: 'center', maxWidth: 300 }, diagnosticsButtonText: { flex: 1, fontSize: 15, fontWeight: '600' }, diagnosticsArrow: { fontSize: 17 },

  appearanceWindow: { borderRadius: 20, padding: 16, zIndex: 3 },
  appearanceHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }, appearanceTitle: { fontSize: 20, fontWeight: '700' }, appearanceClose: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  environmentGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  environmentTile: { width: 92, gap: 6 },
  environmentThumb: { width: 92, height: 64, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  environmentThumbImage: { width: '100%', height: '100%' },
  environmentLabel: { fontSize: 11, fontWeight: '600', textAlign: 'center' },

  preferenceLabel: { fontSize: 11, fontWeight: '700', letterSpacing: 0.9, marginTop: 18, marginBottom: 10 },
  optionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  optionPill: { minHeight: 40, justifyContent: 'center', borderRadius: 20, borderWidth: 1, borderColor: 'rgba(142,153,170,0.34)', paddingHorizontal: 16 },
  voiceModeOption: { minWidth: 150, maxWidth: 300, minHeight: 58, justifyContent: 'center', borderRadius: 16, borderWidth: 1, borderColor: 'rgba(142,153,170,0.34)', paddingHorizontal: 14, paddingVertical: 8 }, voiceModeDescription: { fontSize: 11, lineHeight: 15, marginTop: 2 },
  customBackgroundRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginTop: 4 }, customBackgroundPreview: { width: 92, height: 64, borderRadius: 14 }, customBackgroundCopy: { flex: 1, minWidth: 150 }, customBackgroundTitle: { fontSize: 12, fontWeight: '800' }, customBackgroundDescription: { fontSize: 10, lineHeight: 14, marginTop: 2 },
  secondaryAction: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255,98,95,0.36)' }, secondaryActionText: { fontSize: 12, fontWeight: '700' },
  uploadBackgroundButton: { minHeight: 48, justifyContent: 'center', alignItems: 'center', borderRadius: 24, marginTop: 12, borderWidth: 1 },
  optionPillSelected: { backgroundColor: brand.cyan, borderColor: brand.cyan }, optionText: { fontSize: 13, fontWeight: '700' },
  logoutButton: { minHeight: 44, marginTop: 22, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255,98,95,0.55)', justifyContent: 'center', alignItems: 'center', maxWidth: 280 }, logoutButtonText: { color: brand.critical, fontSize: 11, fontWeight: '800', letterSpacing: 0.8 },
});
