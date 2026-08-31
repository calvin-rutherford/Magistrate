import * as Clipboard from 'expo-clipboard';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Alert, Image, KeyboardAvoidingView, NativeScrollEvent, NativeSyntheticEvent, PanResponder, Platform, ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import Animated, { Easing, interpolate, useAnimatedStyle, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Circle, Path, Rect } from 'react-native-svg';
import { AgentHistoryMessage, AgentInfo, AuthProviderInfo, CHAT_HISTORY_LINES, CHAT_MAX_UPLOAD_COUNT, CHAT_MAX_UPLOAD_TOTAL_BYTES, ExecutionProfile, fetchAgentHistory, fetchAgents, fetchAuthProviders, fetchExecutionCapabilities, fetchExecutionSettings, fetchHealth, fetchRecentActivity, fetchUnifiedAttention, fetchUsage, fetchVoiceInputCapabilities, HealthInfo, interruptAgent, logoutGatewaySession, RecentActivityItem, renameAgent, sendCaptainPrompt, transcribeVoiceAudio, UnifiedAttentionRecord, updateExecutionSettings, saveExecutionCredential, ExecutionSettings, UsageProvider, uploadChatFile, ChatUpload, validateChatAttachment } from '../../src/api/client';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { SafeMarkdown } from '../../src/components/SafeMarkdown';
import { useVoiceInputAdapter } from '../../src/input/VoiceInputAdapter';
import { capabilityFor, getLocalVoiceCapabilities, VOICE_INPUT_MODE_OPTIONS, VoiceInputCapabilities, VoiceInputMode } from '../../src/services/VoiceInputModes';
import { agentDisplayName, displayAgentStatus, summarizeAgents } from '../../src/services/AgentStatus';
import { filterAgentHistory, isHarnessArtifact, sanitizeTerminalHistory, toolCallPreview } from '../../src/services/ChatHistory';
import { messageContentKey, messageIdentity, fallbackMessageId, revisionTargetId, terminalRevisionCandidate } from '../../src/services/ChatIdentity';
import { appendConversationMessage, ConversationAttachment, ConversationMessage, getConversationMessages, hydrateConversationMessages, insertConversationMessageAfter, prependConversationMessages, updateConversationMessageState, useConversationMessages } from '../../src/services/ConversationSession';
import { ChatPreferences, ChatThemeMode, DEFAULT_CHAT_PREFERENCES, loadChatPreferences, removeCustomBackground, saveChatBackground, saveCustomBackground, saveThemeMode, saveToolCallVisibility, saveVoiceInputMode, saveVoiceCaptureBehavior, saveVoiceTranscriptBehavior, VoiceCaptureBehavior, VoiceTranscriptBehavior, useChatColorScheme } from '../../src/services/ChatPreferences';
import { setActiveBackground, WeatherSceneKey } from '../../src/services/environmentTheme';
import { openExternalUrl, validatedWebUrl } from '../../src/utils/externalLinks';
import { formatConversationTimestamp as formatChatTimestamp, formatAccessibleTimestamp, safeThinkingSummary } from '../../src/services/ChatFormatting';
import { RealtimeClient } from '../../src/realtime/socket';
import { notificationManager } from '../../src/services/NotificationManager';

const markPaper = require('../../assets/images/magistrate-mark-paper-256.png');
const markInk = require('../../assets/images/magistrate-mark-ink-256.png');
const markActive = require('../../assets/images/magistrate-mark-active-256.png');
const brand = { obsidian: '#05070A', command: '#111722', paper: '#F7F8FA', ink: '#11151B', mutedDark: '#8E99AA', mutedLight: '#667180', cyan: '#24D8FF', violet: '#8B6CFF', success: '#43D17A', attention: '#FFB347', critical: '#FF625F' };

type ComposerAttachment = { id: string; name: string; uri: string; mimeType?: string; size?: number; kind: 'image' | 'file'; status?: 'ready' | 'uploading' | 'uploaded' | 'failed'; uploaded?: ChatUpload };
type QueuedPrompt = { id: string; messageId: string; text: string; attachments: ComposerAttachment[]; editId: string | null };
type ActivePrompt = { token: number; messageId: string; text: string; observed: boolean; toolResults: string[]; controller: AbortController };
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

/** Never says an attachment is done unless the gateway confirmed that state. */
const attachmentStateLabel = (status?: ConversationAttachment['status']) => {
  if (status === 'uploading') return ' · Uploading…';
  if (status === 'stored') return ' · Stored, not yet sent';
  if (status === 'attached') return ' · Attached';
  if (status === 'failed') return ' · Upload failed';
  return '';
};

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

export function formatConversationTimestamp(sentAt?: number): string | null { return formatChatTimestamp(sentAt); }

function UserMessage({ message, textColor, selectable, onLongPress, onRetry, onActions }: { message: ConversationMessage; textColor: string; selectable: boolean; onLongPress: () => void; onRetry?: () => void; onActions: () => void }) {
  // Keep transport metadata out of the transcript, but retain a compact
  // filename/type/size summary so a reload remains useful to the captain.
  const timestamp = formatConversationTimestamp(message.sentAt);
  const accessibleTimestamp = formatAccessibleTimestamp(message.sentAt);
  return <View style={styles.userMessageWrap}><TouchableOpacity testID={`user-message-${message.id}`} accessibilityRole="text" accessibilityLabel={`Your message${timestamp ? `, sent ${timestamp}` : ''}${accessibleTimestamp ? `, ${accessibleTimestamp}` : ''}. Press and hold for actions.`} delayLongPress={2000} onLongPress={onLongPress} activeOpacity={0.92} style={styles.userMessage}>
    <Text testID={`user-message-text-${message.id}`} selectable={selectable} style={[styles.messageText, { color: textColor }]}>{message.text}</Text>
    {message.attachments?.map(attachment => <Text key={`${message.id}-${attachment.name}`} testID={`message-attachment-${message.id}`} style={[styles.messageAttachment, { color: textColor }]} numberOfLines={1}>↳ {attachment.name} · {attachment.mediaType}{formatAttachmentSize(attachment.size) ? ` · ${formatAttachmentSize(attachment.size)}` : ''}{attachmentStateLabel(attachment.status)}</Text>)}
    {timestamp ? <Text testID={`message-timestamp-${message.id}`} style={[styles.messageTimestamp, { color: textColor }]}>{timestamp}</Text> : null}
    {message.delivery === 'sending' ? <Text testID={`message-sending-${message.id}`} style={styles.messageDelivery}>Sending…</Text> : null}
    {message.delivery === 'cancelled' ? <Text testID={`message-cancelled-${message.id}`} style={styles.messageDelivery}>Response stopped</Text> : null}
    {message.delivery === 'failed' ? <View style={styles.messageFailure}><Text testID={`message-failed-${message.id}`} style={styles.messageFailed}>Not sent. Check the attachment and retry.</Text>{onRetry ? <TouchableOpacity testID={`retry-message-${message.id}`} accessibilityRole="button" accessibilityLabel="Retry sending message" onPress={onRetry}><Text style={styles.retryText}>Retry</Text></TouchableOpacity> : null}</View> : null}
  </TouchableOpacity><TouchableOpacity testID={`message-actions-${message.id}`} accessibilityRole="button" accessibilityLabel="Message actions" onPress={onActions} style={styles.inlineMessageAction}><Text style={styles.inlineMessageActionText}>•••</Text></TouchableOpacity></View>;
}

function SourceList({ sources, dark, text, muted }: { sources: NonNullable<ConversationMessage['sources']>; dark: boolean; text: string; muted: string }) {
  const safeSources = sources.filter(source => validatedWebUrl(source.url));
  if (!safeSources.length) return null;
  return <View testID="message-sources" accessibilityLabel="Sources" style={styles.sources}><Text style={[styles.sourcesTitle, { color: muted }]}>Sources</Text>{safeSources.map((source, index) => <TouchableOpacity key={source.id} testID={`message-source-${source.id}`} accessibilityRole="link" accessibilityLabel={`Source ${index + 1}: ${source.title}`} onPress={() => void openExternalUrl(source.url)} style={styles.sourceRow}><Text style={[styles.sourceMarker, { color: dark ? brand.cyan : brand.violet }]}>{index + 1}</Text><View style={styles.sourceCopy}><Text numberOfLines={2} style={[styles.sourceTitle, { color: text }]}>{source.title}</Text><Text numberOfLines={1} style={[styles.sourceMeta, { color: muted }]}>{source.publisher || new URL(source.url).hostname}{source.page ? ` · p. ${source.page}` : ''}</Text>{source.quote ? <Text numberOfLines={2} style={[styles.sourceQuote, { color: muted }]}>“{source.quote}”</Text> : null}</View></TouchableOpacity>)}</View>;
}

function AssistantMessage({ message, dark, text, muted, showToolCalls, onActions }: { message: ConversationMessage; dark: boolean; text: string; muted: string; showToolCalls: boolean; onActions: () => void }) {
  const summary = safeThinkingSummary(message.thinkingSummary);
  return <View testID="agent-message" style={styles.assistantMessage}><View style={styles.assistantBody}><SafeMarkdown markdown={message.text} color={text} mutedColor={muted} dark={dark} testID={`assistant-markdown-${message.id}`} />{showToolCalls && message.toolResults?.map((result, index) => <View key={`${message.id}-tool-${index}`} testID="tool-history-message" style={styles.attachedToolResult}><Text numberOfLines={1} style={[styles.toolMessageText, { color: muted }]}>{result}</Text></View>)}{summary ? <View testID="safe-thinking-summary" style={styles.thinkingSummary}><Text style={[styles.thinkingSummaryLabel, { color: muted }]}>{summary.provider} summary</Text><Text style={[styles.thinkingSummaryText, { color: muted }]}>{summary.text}</Text></View> : null}<SourceList sources={message.sources || []} dark={dark} text={text} muted={muted} />{message.progress === 'failed' ? <Text testID={`assistant-failed-${message.id}`} accessibilityRole="alert" style={styles.assistantStateFailed}>Response stopped before completion. Retry is available only when this run is safe to repeat.</Text> : message.progress === 'cancelled' ? <Text testID={`assistant-cancelled-${message.id}`} style={[styles.assistantState, { color: muted }]}>Response stopped</Text> : message.progress === 'streaming' ? <Text testID={`assistant-streaming-${message.id}`} style={[styles.assistantState, { color: muted }]}>Updating response…</Text> : message.progress === 'working' || message.progress === 'queued' ? <Text testID={`assistant-working-${message.id}`} style={[styles.assistantState, { color: muted }]}>Working…</Text> : null}</View><TouchableOpacity testID={`message-actions-${message.id}`} accessibilityRole="button" accessibilityLabel="Assistant message actions" onPress={onActions} style={styles.inlineMessageAction}><Text style={styles.inlineMessageActionText}>•••</Text></TouchableOpacity></View>;
}

// Identity and reconciliation live in one place for every delivery path; see
// src/services/ChatIdentity.ts.
const historyKey = messageContentKey;
const stableHistoryId = fallbackMessageId;
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
      if (typeof value === 'string' && value.trim() && !isHarnessArtifact(value)) return value.trim();
    }
    return null;
  } catch { /* A plain string is a legacy synchronous conversational response. */ }
  return isHarnessArtifact(response) ? null : response.trim();
}

export function ChatCanvas({ target = 'captain', showToolCalls = false, onDrawerToggle = () => {}, drawerOpen = false, profiles = [], capabilityLoading = false, capabilityError = null, selectedProfileId = null, routingReady = true, onProfileChange = () => {}, voiceInputMode = 'automatic', voiceCapabilities, voiceCaptureBehavior = 'tap-to-toggle', voiceTranscriptBehavior = 'insert', autoStartRecording = false, onRegenerate }: { target?: string; showToolCalls?: boolean; onDrawerToggle?: () => void; drawerOpen?: boolean; profiles?: ExecutionProfile[]; capabilityLoading?: boolean; capabilityError?: string | null; selectedProfileId?: string | null; routingReady?: boolean; onProfileChange?: (profileId: string | null) => void; voiceInputMode?: VoiceInputMode; voiceCapabilities?: VoiceInputCapabilities; voiceCaptureBehavior?: VoiceCaptureBehavior; voiceTranscriptBehavior?: VoiceTranscriptBehavior; autoStartRecording?: boolean; onRegenerate?: (message: ConversationMessage) => Promise<void> }) {
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
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const [hasNewMessages, setHasNewMessages] = useState(false);
  const [unreadAttentionCount, setUnreadAttentionCount] = useState(() => notificationManager.getUnreadEvents().length);
  const [historyBefore, setHistoryBefore] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [micStatus, setMicStatus] = useState<'idle' | 'requesting' | 'listening' | 'transcribing' | 'ready' | 'error'>('idle');
  const [waveSamples, setWaveSamples] = useState<number[]>(() => new Array(48).fill(0.04));
  const [modelSelection, setModelSelection] = useState<ModelSelection>(() => {
    const profile = profiles.find(item => item.id === selectedProfileId);
    return profile ? { profileId: profile.id, harness: profile.harness.id, provider: profile.provider.id, model: profile.model.id, variant: profile.variant, label: profile.label, available: profile.available, availabilityReason: profile.availability_reason } : selectedProfileId ? { profileId: selectedProfileId, harness: '', provider: '', model: '', variant: '', label: 'Saved profile unavailable', available: false, availabilityReason: 'The saved execution profile is no longer available.' } : null;
  });
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const pendingAttachmentsByMessageRef = useRef(new Map<string, ComposerAttachment[]>());
  const [webViewportHeight, setWebViewportHeight] = useState<number | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);
  const holdActiveRef = useRef(false);
  const atBottomRef = useRef(true);
  const initialHistoryLoadedRef = useRef(false);
  const initialScrollCancelledRef = useRef(false);
  const historyViewportMeasuredRef = useRef(false);
  const historyContentMeasuredRef = useRef(false);
  const pendingLatestScrollRef = useRef(false);
  const latestScrollFrameRef = useRef<number | null>(null);
  const finalScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const historyRequestRef = useRef(0);
  const promptTokenRef = useRef(0);
  const activePromptRef = useRef<ActivePrompt | null>(null);
  const postPromptPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A prompt must not race the initial scrollback seed. If the seed resolves
  // after a new reply is already present, it would mark that reply as known
  // without rendering it and the live poll would skip it forever.
  const historyReadyRef = useRef<Promise<void>>(Promise.resolve());
  // Keys of messages already known to the live poll (typed locally, or seen in
  // a prior Herdr history read), so syncFromHistory only appends genuinely new
  // ones - see the mount effect below, which seeds this without rendering.
  const knownKeysRef = useRef<Set<string>>(new Set());
  const optimisticCountsRef = useRef(new Map<string, number>());
  const targetLabel = target === 'captain' ? 'Magistrate' : target;
  const capture = useVoiceInputAdapter(undefined, voiceInputMode);

  // ScrollView's native content dimensions are only trustworthy after both the
  // viewport and its rendered content have measured. Keep the request pending
  // until those callbacks fire rather than guessing a height or using a timer.
  const requestLatestScroll = (force = false) => {
    if (force) {
      atBottomRef.current = true;
      initialScrollCancelledRef.current = false;
      // A forced jump supersedes a stale frame queued by content growth; it
      // must not clear the pill's request before the final row is measured.
      if (latestScrollFrameRef.current !== null) cancelAnimationFrame(latestScrollFrameRef.current);
      latestScrollFrameRef.current = null;
    } else if (!initialHistoryLoadedRef.current) {
      if (atBottomRef.current && !initialScrollCancelledRef.current) pendingLatestScrollRef.current = true;
      return;
    } else if (!atBottomRef.current && !pendingLatestScrollRef.current) return;
    pendingLatestScrollRef.current = true;
    if (latestScrollFrameRef.current !== null) return;
    latestScrollFrameRef.current = requestAnimationFrame(() => {
      latestScrollFrameRef.current = null;
      if (!pendingLatestScrollRef.current || !historyViewportMeasuredRef.current || !historyContentMeasuredRef.current) return;
      // Content growth can make the previous offset look temporarily above
      // the end before onContentSizeChange runs. The pending request records
      // that the captain was at the end when the message arrived.
      pendingLatestScrollRef.current = false;
      scrollRef.current?.scrollToEnd({ animated: false });
      if (force) {
        // React Native/Web can measure the final child one paint after the
        // press. Repeat once against the settled content size.
        if (finalScrollTimerRef.current) clearTimeout(finalScrollTimerRef.current);
        finalScrollTimerRef.current = setTimeout(() => {
          finalScrollTimerRef.current = null;
          scrollRef.current?.scrollToEnd({ animated: false });
        }, 50);
      }
    });
  };
  const jumpToLatest = () => {
    setHasNewMessages(false);
    setIsScrolledUp(false);
    requestLatestScroll(true);
  };
  useEffect(() => notificationManager.subscribeUnread(events => setUnreadAttentionCount(events.length)), []);

  useEffect(() => {
    const profile = profiles.find(item => item.id === selectedProfileId);
    setModelSelection(profile ? { profileId: profile.id, harness: profile.harness.id, provider: profile.provider.id, model: profile.model.id, variant: profile.variant, label: profile.label, available: profile.available, availabilityReason: profile.availability_reason } : selectedProfileId ? { profileId: selectedProfileId, harness: '', provider: '', model: '', variant: '', label: 'Saved profile unavailable', available: false, availabilityReason: 'The saved execution profile is no longer available.' } : null);
  }, [profiles, selectedProfileId]);

  useEffect(() => {
    const request = ++historyRequestRef.current;
    initialHistoryLoadedRef.current = false;
    initialScrollCancelledRef.current = false;
    historyViewportMeasuredRef.current = false;
    historyContentMeasuredRef.current = false;
    pendingLatestScrollRef.current = true;
    let resolveHistoryReady!: () => void;
    let historyReady = false;
    const markHistoryReady = (scrollToLatest = true) => {
      if (historyReady) return;
      historyReady = true;
      initialHistoryLoadedRef.current = true;
      resolveHistoryReady();
      if (scrollToLatest) requestLatestScroll();
    };
    historyReadyRef.current = new Promise<void>(resolve => { resolveHistoryReady = resolve; });
    activePromptRef.current?.controller.abort();
    activePromptRef.current = null;
    if (postPromptPollTimerRef.current) clearTimeout(postPromptPollTimerRef.current);
    postPromptPollTimerRef.current = null;
    setSendError(null); setIsThinking(false);
    // The captain thread is shared with Voice Mode (see ConversationSession),
    // so switching back to it keeps whatever it already holds in memory for
    // this session; other targets start each visit with a clean thread.
    knownKeysRef.current = new Set();
    optimisticCountsRef.current = new Map();
    const rememberOptimistic = (message: ConversationMessage) => {
      const key = historyKey(message);
      knownKeysRef.current.add(key);
      optimisticCountsRef.current.set(key, (optimisticCountsRef.current.get(key) || 0) + 1);
    };
    const existingIds = new Set(getConversationMessages(target).map(message => message.id));
    getConversationMessages(target).forEach(rememberOptimistic);
    const hydration = hydrateConversationMessages(target).then(hydrated => hydrated.forEach(message => {
      if (!existingIds.has(message.id)) rememberOptimistic(message);
    }));
    // Seed known-message keys from recent Herdr scrollback so the live poll
    // below doesn't treat pre-existing history as new and replay it into the
    // thread - chat only ever shows what happens while it's open.
    const loadHistory = async (attempt: number): Promise<void> => {
      try {
        // Hydrate optimistic messages before seeding server identities so a
        // delayed reload cannot leave an unreconciled duplicate count.
        await hydration;
        const result = await fetchAgentHistory(target);
        if (request !== historyRequestRef.current) return;
        reconcileCaptainHistory(result.messages);
        // A working agent can briefly leave an empty terminal snapshot (redraw
        // or alternate screen); retry a few times before accepting it as empty.
        if (result.messages.length === 0 && attempt < 5) {
          await new Promise<void>(resolve => setTimeout(resolve, 2000));
          return loadHistory(attempt + 1);
        }
        // A reply stored before a reload can have grown or reflowed in the
        // snapshot since. Revise that row rather than letting the seed record
        // the new content hash as a row this canvas has already shown.
        if (target !== 'captain') {
          const seedRevisable = terminalRevisionCandidate(sanitizeTerminalHistory(result.messages));
          if (seedRevisable) reviseRenderedReply(seedRevisable);
        }
        result.messages.forEach(message => {
          // While a captain request is active, do not consume assistant/tool
          // identities from the seed before the prompt boundary is observed.
          // A bounded snapshot can omit the prompt row; the normal sync path
          // must get another chance to associate the eventual reply.
          if (target === 'captain' && activePromptRef.current && message.role === 'assistant') return;
          const key = historyKey(message);
          const optimisticCount = optimisticCountsRef.current.get(key) || 0;
          if (message.id && optimisticCount > 0) optimisticCountsRef.current.set(key, optimisticCount - 1);
          knownKeysRef.current.add(messageIdentity(message));
        });
        // Captain history is the normalized local conversation store. Raw
        // terminal scrollback is identity-only and is never pageable prose.
        setHistoryBefore(target === 'captain' ? null : result.next_before || null);
      } catch (error) {
        if (request !== historyRequestRef.current) return;
        setSendError(errorText(error, 'Agent history could not be loaded.'));
        // A history outage must not permanently prevent sending. The active
        // poll remains the recovery path and will discover the eventual reply.
      }
    };
    void Promise.allSettled([hydration, loadHistory(0)]).then(() => {
      if (request === historyRequestRef.current) markHistoryReady();
    });
    return () => {
      historyRequestRef.current += 1;
      if (latestScrollFrameRef.current !== null) {
        cancelAnimationFrame(latestScrollFrameRef.current);
        latestScrollFrameRef.current = null;
      }
      if (finalScrollTimerRef.current) clearTimeout(finalScrollTimerRef.current);
      if (postPromptPollTimerRef.current) clearTimeout(postPromptPollTimerRef.current);
      activePromptRef.current?.controller.abort();
      activePromptRef.current = null;
      markHistoryReady(false);
    };
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
      setMicStatus('transcribing');
      setIsTranscribing(true);
      try {
        const recording = await capture.stop();
        if (recording.durationMillis < 250) throw new Error('The recording was too short. Hold the mic and speak before stopping.');
        const localTranscript = voiceInputMode === 'browser' ? recording.transcript?.trim() : undefined;
        const transcript = localTranscript || (voiceInputMode === 'browser' ? '' : (await transcribeVoiceAudio(recording.uri, recording.mimeType, recording.filename)).text?.trim());
        if (!transcript) throw new Error(voiceInputMode === 'browser' ? 'No speech was recognized. Try again or choose Automatic.' : 'No speech was recognized. Try again closer to the microphone.');
        const combined = promptText.trim() ? `${promptText.trim()} ${transcript}` : transcript;
        setPromptText(combined);
        setMicStatus('ready');
        setSendError(null);
        if (voiceTranscriptBehavior === 'auto-send') void submitPrompt(combined, editingMessageId, attachments);
      } catch (error) {
        setMicStatus('error');
        setSendError(errorText(error, 'The microphone recording could not be transcribed.'));
      } finally { setIsTranscribing(false); setWaveSamples(new Array(48).fill(0.04)); }
      return;
    }
    setSendError(null);
    const capability = voiceCapabilities && capabilityFor(voiceCapabilities, voiceInputMode);
    if (capability && capability.available === 'unavailable') {
      setMicStatus('error'); setSendError(capability.reason || `${capability.label} is unavailable.`); return;
    }
    setMicStatus('requesting');
    try {
      await capture.start();
      // A quick hold can release while permission is being requested. Do not
      // leave a truthful-looking recording running after that release.
      if (voiceCaptureBehavior === 'hold-to-talk' && !holdActiveRef.current && !autoStartRecording) { await capture.cancel(); setMicStatus('idle'); return; }
      setIsRecording(true); setMicStatus('listening');
    } catch (error) { setMicStatus('error'); setSendError(errorText(error, 'The microphone could not start.')); }
  };

  useEffect(() => {
    if (!autoStartRecording) return;
    const timer = setTimeout(() => { void handleMicPress(); }, 650);
    return () => clearTimeout(timer);
    // A native Action Button/Siri deep link only requests the foreground chat
    // capture seam; background capture is intentionally not implemented here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStartRecording]);

  const loadOlderHistory = async () => {
    if (target === 'captain' || !historyBefore || historyLoading) return;
    setHistoryLoading(true);
    try {
      const result = await fetchAgentHistory(target, CHAT_HISTORY_LINES, { before: historyBefore });
      // An older page is terminal-derived like every other read, so it goes
      // through the same exclusion: internally addressed records are never
      // restored as messages of either role. Server-known rows must also keep
      // the gateway's stable id - minting a local one would break cursor/dedup
      // identity and present a row we cannot re-address, so a row without one
      // is dropped instead.
      prependConversationMessages(target, sanitizeTerminalHistory(result.messages).reduce<ConversationMessage[]>((rows, message) => {
        if (message.kind === 'control' || typeof message.id !== 'string' || !message.id) return rows;
        rows.push({ id: message.id, role: message.role, kind: message.kind, text: message.text, source: 'text', sources: message.sources, thinkingSummary: message.thinkingSummary, runId: message.runId, regenerateSafe: message.regenerateSafe, progress: message.progress || (message.role === 'assistant' ? 'complete' : undefined) });
        return rows;
      }, []));
      result.messages.forEach(message => {
        const key = historyKey(message);
        const optimisticCount = optimisticCountsRef.current.get(key) || 0;
        if (message.id && optimisticCount > 0) optimisticCountsRef.current.set(key, optimisticCount - 1);
        knownKeysRef.current.add(messageIdentity(message));
      });
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
  const handleHistoryLayout = () => {
    historyViewportMeasuredRef.current = true;
    requestLatestScroll();
  };
  const handleHistoryContentSizeChange = () => {
    historyContentMeasuredRef.current = true;
    requestLatestScroll();
  };
  const handleHistoryScrollBeginDrag = () => {
    // A captain who starts reading older content has expressed an intentional
    // position; do not override it when an asynchronous render catches up.
    pendingLatestScrollRef.current = false;
    if (!initialHistoryLoadedRef.current) initialScrollCancelledRef.current = true;
  };
  const addAttachments = (selected: ComposerAttachment[]) => {
    const normalized = selected.map(item => ({ ...item, status: 'ready' as const }));
    const currentCount = attachments.length;
    if (currentCount + normalized.length > CHAT_MAX_UPLOAD_COUNT) { setSendError('A message may include at most 10 attachments.'); return; }
    const total = attachments.reduce((sum, item) => sum + (item.size || 0), 0) + normalized.reduce((sum, item) => sum + (item.size || 0), 0);
    if (total > CHAT_MAX_UPLOAD_TOTAL_BYTES) { setSendError('The attachments in one message are too large.'); return; }
    const invalid = normalized.find(item => validateChatAttachment(item.name, item.mimeType, item.size));
    if (invalid) { setSendError(validateChatAttachment(invalid.name, invalid.mimeType, invalid.size) || 'This file type is not supported.'); return; }
    setAttachments(current => [...current, ...normalized]);
    setSendError(null);
  };
  const pickImages = async () => {
    setAttachmentMenuOpen(false);
    try {
      const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: true, quality: 1 });
      if (!result.canceled) addAttachments(result.assets.map((asset, index) => ({
        id: `image-${Date.now()}-${index}`,
        name: asset.fileName || `Image-${attachments.length + index + 1}.jpg`,
        uri: asset.uri,
        mimeType: asset.mimeType || 'image/jpeg',
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
  const appendMessage = (message: ConversationMessage, optimistic = true) => {
    const wasAtBottom = atBottomRef.current;
    const key = historyKey(message);
    if (optimistic) {
      knownKeysRef.current.add(key);
      optimisticCountsRef.current.set(key, (optimisticCountsRef.current.get(key) || 0) + 1);
    } else {
      knownKeysRef.current.add(messageIdentity(message));
    }
    appendConversationMessage(target, message);
    if (!wasAtBottom) setHasNewMessages(true);
    if (wasAtBottom) requestLatestScroll();
  };
  // Terminal snapshots are discovery transport, never conversation content.
  // For the captain thread, an exact locally-submitted prompt opens the only
  // rendering boundary. Unknown user rows (worker prompts/replies), idle agent
  // prose, and standalone tools are remembered for dedupe but never appended.
  //
  // A seed can finish after a prompt was submitted (or after a reload). If it
  // sees the prompt and its reply together, blindly recording both identities
  // as "known" drops the reply without ever rendering it. Recover only a
  // response segment whose user row exactly matches an unresolved captain
  // message already in the normalized store; this repairs that race without
  // replaying the old terminal backlog.
  function reconcileCaptainHistory(incoming: AgentHistoryMessage[]) {
    if (target !== 'captain') return;
    const history = sanitizeTerminalHistory(incoming);
    const local = getConversationMessages(target);
    // Every captain turn, with the primary reply already rendered for it (if
    // any). An answered turn is still listed: a reply that grew or reflowed in
    // the snapshot since it was stored must revise that row, never add a second.
    const turns = local.reduce((result, message, index) => {
      if (message.role !== 'user' || message.audience !== 'captain' || message.delivery === 'failed' || message.delivery === 'cancelled') return result;
      const nextUser = local.slice(index + 1).findIndex(item => item.role === 'user');
      const end = nextUser < 0 ? local.length : index + 1 + nextUser;
      const reply = local.slice(index + 1, end).find(item => item.role === 'assistant' && item.kind !== 'tool') || null;
      result.push({ message, index, reply });
      return result;
    }, [] as { message: ConversationMessage; index: number; reply: ConversationMessage | null }[]);
    if (!turns.length) return;

    type CaptainHistoryEntry = AgentHistoryMessage;
    type CaptainHistorySegment = { prompt: CaptainHistoryEntry; reply: CaptainHistoryEntry | null; toolResults: string[] };
    const segments: CaptainHistorySegment[] = [];
    let segment: CaptainHistorySegment | null = null;
    history.forEach(message => {
      if (message.kind === 'control') {
        // A user-role control record is an internal turn addressed to someone
        // else, so it closes the captain's segment. Agent-side control rows are
        // only harness noise interleaved with a reply and change no boundary.
        if (message.role === 'user') segment = null;
      } else if (message.role === 'user') {
        segment = { prompt: message, reply: null, toolResults: [] };
        segments.push(segment);
      } else if (segment && message.kind === 'tool') {
        const preview = toolCallPreview(message.text).slice(0, 48);
        if (!segment.toolResults.includes(preview) && segment.toolResults.length < 6) segment.toolResults.push(preview);
      } else if (segment && message.kind === 'conversation' && !segment.reply) {
        segment.reply = message;
      }
    });

    const matches: Array<{ local: typeof turns[number]; segment: typeof segments[number] }> = [];
    const available = [...turns];
    // Match from the latest occurrence so an old repeated phrase cannot steal
    // the response belonging to the current locally persisted turn.
    [...segments].reverse().forEach(candidate => {
      if (!candidate.reply) return;
      const matchAt = available.findLastIndex(item => item.message.text.trim() === candidate.prompt.text.trim());
      if (matchAt < 0) return;
      const [localMessage] = available.splice(matchAt, 1);
      matches.push({ local: localMessage, segment: candidate });
    });
    matches.sort((left, right) => left.local.index - right.local.index).forEach(({ local: localMessage, segment: candidate }) => {
      const reply = candidate.reply;
      if (!reply) return;
      const id = reply.id || stableHistoryId(reply);
      knownKeysRef.current.add(messageIdentity(reply));
      const rendered = localMessage.reply;
      if (rendered) {
        // The segment match is positional proof that this is the same turn's
        // reply, re-read after the snapshot re-rendered it. That is stronger
        // evidence than text similarity, so no containment check is needed
        // here - only the live path, which has no segment, falls back to one.
        if (rendered.text !== reply.text) {
          updateConversationMessageState(target, rendered.id, {
            text: reply.text,
            ...(reply.sources ? { sources: reply.sources } : {}),
            ...(reply.thinkingSummary ? { thinkingSummary: reply.thinkingSummary } : {}),
            ...(reply.progress ? { progress: reply.progress } : {}),
          });
        }
        return;
      }
      insertConversationMessageAfter(target, localMessage.message.id, {
        id, role: 'assistant', kind: 'conversation', text: reply.text, sentAt: undefined, source: 'text', audience: 'primary', progress: reply.progress || 'complete', sources: reply.sources, thinkingSummary: reply.thinkingSummary, runId: reply.runId, regenerateSafe: reply.regenerateSafe,
        toolResults: candidate.toolResults.length ? candidate.toolResults : undefined,
      });
      if (activePromptRef.current?.messageId === localMessage.message.id) {
        activePromptRef.current = null;
        setIsThinking(false);
      }
    });
  }
  // Herdr ids hash terminal content (see gateway/app/herdr_client.py), and that
  // content mutates while a reply renders, reflows, or scrolls its head out of
  // the snapshot. Update the row it already produced instead of adding a second
  // near-identical one; a genuinely new reply has no containment relation and
  // still appends. Returns true when the incoming row was absorbed.
  function reviseRenderedReply(message: AgentHistoryMessage): boolean {
    if (message.kind !== 'conversation') return false;
    const local = getConversationMessages(target);
    const targetId = revisionTargetId(local, message);
    if (!targetId) return false;
    // Idempotent: an unchanged snapshot must not re-emit or re-persist a row.
    if (local.find(item => item.id === targetId)?.text === message.text) return true;
    updateConversationMessageState(target, targetId, {
      text: message.text,
      ...(message.sources ? { sources: message.sources } : {}),
      ...(message.thinkingSummary ? { thinkingSummary: message.thinkingSummary } : {}),
      ...(message.progress ? { progress: message.progress } : {}),
    });
    return true;
  }
  const appendHistoryMessages = (incoming: AgentHistoryMessage[]): boolean => {
    let appendedReply = false;
    const history = sanitizeTerminalHistory(incoming);
    const revisable = terminalRevisionCandidate(history);
    history.forEach(message => {
      const key = historyKey(message);
      const identity = messageIdentity(message);
      const active = activePromptRef.current;

      if (message.kind === 'control') {
        // Never a message of either role. A user-role control record is an
        // internal turn addressed to someone else, so it closes the response
        // segment the captain's prompt opened; agent-side control rows are
        // harness noise interleaved with the reply and close nothing.
        if (active && message.role === 'user') active.observed = false;
        return;
      }

      if (target === 'captain') {
        if (message.role === 'user') {
          if (active && message.kind === 'conversation') {
            // Only the exact captain prompt opens the response segment. Any
            // subsequent user-role row is an internal worker/system audience
            // boundary and fails closed until a new captain submission.
            active.observed = message.text.trim() === active.text;
          }
          const optimisticCount = optimisticCountsRef.current.get(key) || 0;
          if (message.id && optimisticCount > 0) optimisticCountsRef.current.set(key, optimisticCount - 1);
          knownKeysRef.current.add(identity);
          return;
        }
        // The revision must be tried before the identity short-circuit: a
        // gateway that keeps one id for a row still rendering would otherwise
        // pin the first partial read forever.
        if (message === revisable && reviseRenderedReply(message)) {
          knownKeysRef.current.add(identity);
          return;
        }
        if (knownKeysRef.current.has(identity)) return;
        // If a socket delivers a response before its prompt row, leave it
        // unconsumed so the authoritative full-history poll can retry safely.
        if (!active) {
          knownKeysRef.current.add(identity);
          return;
        }
        if (!active.observed) return;
        knownKeysRef.current.add(identity);
        if (message.kind === 'tool') {
          const preview = toolCallPreview(message.text).slice(0, 48);
          if (!active.toolResults.includes(preview) && active.toolResults.length < 6) active.toolResults.push(preview);
          return;
        }
        appendMessage({ id: message.id || stableHistoryId(message), role: 'assistant', kind: 'conversation', text: message.text, sentAt: undefined, source: 'text', audience: 'primary', progress: message.progress || 'complete', sources: message.sources, thinkingSummary: message.thinkingSummary, runId: message.runId, regenerateSafe: message.regenerateSafe, toolResults: active.toolResults }, false);
        activePromptRef.current = null;
        setIsThinking(false);
        appendedReply = true;
        return;
      }

      // Explicit worker conversations retain their existing navigation path,
      // while receiving the same typed artifact filtering and stable dedupe.
      if (message === revisable && reviseRenderedReply(message)) {
        knownKeysRef.current.add(identity);
        return;
      }
      if (knownKeysRef.current.has(identity)) return;
      const optimisticCount = optimisticCountsRef.current.get(key) || 0;
      if (message.id && optimisticCount > 0) {
        optimisticCountsRef.current.set(key, optimisticCount - 1);
        knownKeysRef.current.add(identity);
        return;
      }
      knownKeysRef.current.add(identity);
      appendMessage({ id: message.id || stableHistoryId(message), role: message.role, kind: message.kind, text: message.text, sentAt: undefined, source: 'text', sources: message.sources, thinkingSummary: message.thinkingSummary, runId: message.runId, regenerateSafe: message.regenerateSafe, progress: message.progress || (message.role === 'assistant' ? 'complete' : undefined) }, false);
      if (message.role === 'assistant' && message.kind === 'conversation') appendedReply = true;
    });
    return appendedReply;
  };
  const syncFromHistory = async (): Promise<boolean> => {
    const result = await fetchAgentHistory(target);
    reconcileCaptainHistory(result.messages);
    return appendHistoryMessages(result.messages);
  };

  // Herdr has no native push channel, so the gateway's event stream is an
  // acceleration path only. HTTP polling below remains the recovery path when
  // the socket is unavailable or a snapshot is transiently empty.
  useEffect(() => {
    let active = true;
    const realtime = new RealtimeClient(target);
    const unsubscribe = realtime.subscribe(event => {
      if (event?.type !== 'agent_history' || !Array.isArray(event.messages)) return;
      // In development React may mount, clean up, and mount effects again. Do
      // not let an early socket replay server history before the authoritative
      // initial seed has established identities for this mounted canvas.
      void historyReadyRef.current.then(() => {
        if (!active) return;
        reconcileCaptainHistory(event.messages);
        if (appendHistoryMessages(event.messages)) setIsThinking(false);
      });
    });
    void realtime.connect();
    return () => { active = false; unsubscribe(); realtime.disconnect(); };
  }, [target]);
  // Live auto-refresh: whichever agent is behind `target` may produce new
  // terminal output without this device having sent the prompt (Herdr has no
  // push channel, see AGENTS.md), so poll on an interval independent of the
  // faster post-send poll below.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        await historyReadyRef.current;
        if (!cancelled && await syncFromHistory()) setIsThinking(false);
      } catch { /* Transient network/Herdr hiccup: retry on the next tick. */ }
    };
    const interval = setInterval(() => void poll(), 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [target]);
  const submitPrompt = async (trimmed: string, editId: string | null = null, pendingAttachments: ComposerAttachment[] = [], queuedMessageId?: string) => {
    const messageId = editId || queuedMessageId || `u-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const token = ++promptTokenRef.current;
    const controller = new AbortController();
    // The bubble shows the file as uploading until the gateway confirms it. A
    // local pick is never rendered as a completed attachment.
    const attachmentSummaries: ConversationAttachment[] = pendingAttachments.map(attachment => ({ name: attachment.name, mediaType: attachment.mimeType || 'application/octet-stream', size: attachment.size, status: attachment.uploaded ? 'stored' : 'uploading', uploadId: attachment.uploaded?.upload_id }));
    pendingAttachmentsByMessageRef.current.set(messageId, pendingAttachments);
    if (editId || queuedMessageId) {
      updateConversationMessageState(target, messageId, { text: trimmed, attachments: attachmentSummaries, audience: 'captain', delivery: 'sending', progress: 'working' });
      if (editId) setEditingMessageId(null);
    } else appendMessage({ id: messageId, role: 'user', text: trimmed, sentAt: Date.now(), source: 'text', attachments: attachmentSummaries, audience: 'captain', delivery: 'sending', progress: 'working' });
    activePromptRef.current = { token, messageId, text: trimmed, observed: false, toolResults: [], controller };
    const isCurrent = () => activePromptRef.current?.token === token;
    setPromptText(''); setSendError(null); setIsThinking(true);
    try {
      await historyReadyRef.current;
      if (!isCurrent()) return;
      if (modelSelection && !modelSelection.available) throw new Error(modelSelection.availabilityReason || 'The selected execution profile is unavailable.');
      const uploaded: ChatUpload[] = [];
      for (const attachment of pendingAttachments) {
        if (!isCurrent()) return;
        setAttachments(current => current.map(item => item.id === attachment.id ? { ...item, status: 'uploading' } : item));
        const result = attachment.uploaded || await uploadChatFile(attachment.uri, attachment.name, attachment.mimeType, messageId);
        if (!isCurrent()) return;
        uploaded.push(result);
        setAttachments(current => current.map(item => item.id === attachment.id ? { ...item, status: 'uploaded', uploaded: result } : item));
        // Reflect each confirmed upload as it lands rather than after the whole batch.
        updateConversationMessageState(target, messageId, { attachments: attachmentSummaries.map(summary => summary.name === result.filename && !summary.uploadId ? { ...summary, status: 'stored', uploadId: result.upload_id } : summary) });
      }
      const response = await sendCaptainPrompt(trimmed, 'iphone', target, modelSelection?.harness, modelSelection?.model, modelSelection?.profileId ?? null, uploaded, messageId, controller.signal);
      if (!isCurrent()) return;
      if (response?.status === 'error' || response?.error) throw new Error(response.error || 'The message was not accepted.');
      // Only the server-confirmed records are kept, and 'attached' is claimed
      // solely because the gateway accepted the prompt carrying this manifest.
      updateConversationMessageState(target, messageId, { delivery: 'sent', progress: 'complete', attachments: uploaded.map(item => ({ name: item.filename, mediaType: item.media_type, size: item.size, status: 'attached' as const, uploadId: item.upload_id })) });
      const pendingIds = new Set(pendingAttachments.map(attachment => attachment.id));
      setAttachments(current => current.filter(item => !pendingIds.has(item.id)));
      const reply = conversationalPromptResponse(response?.response);
      if (reply) {
        // A server-issued run id is the stable identity for a server-known
        // response; the local fallback applies only when the gateway sends none.
        appendMessage({ id: response.runId ? `run-${response.runId}` : `a-${Date.now()}`, role: 'assistant', kind: 'conversation', text: reply, sentAt: Date.now(), source: 'text', audience: 'primary', progress: response.progress || 'complete', sources: response.sources, thinkingSummary: response.thinkingSummary, runId: response.runId, regenerateSafe: response.regenerateSafe });
        activePromptRef.current = null; setIsThinking(false); return;
      }
      const pollForReply = async () => {
        if (!isCurrent()) return;
        try { if (await syncFromHistory()) return; }
        catch { /* Retry after transient gateway/snapshot failures. */ }
        if (isCurrent()) postPromptPollTimerRef.current = setTimeout(() => void pollForReply(), 1000);
      };
      void pollForReply();
    } catch (error) {
      if (!isCurrent()) return;
      activePromptRef.current = null;
      updateConversationMessageState(target, messageId, { delivery: 'failed', progress: 'failed', attachments: attachmentSummaries.map(summary => summary.status === 'stored' ? summary : { ...summary, status: 'failed' as const }) });
      setAttachments(current => current.map(item => pendingAttachments.some(pending => pending.id === item.id) ? { ...item, status: 'failed' } : item));
      setPromptText(trimmed); setSendError(errorText(error, 'The message could not be sent.')); setIsThinking(false);
    }
  };
  const stopPendingResponse = async () => {
    const active = activePromptRef.current;
    if (!active) return;
    activePromptRef.current = null;
    promptTokenRef.current += 1;
    active.controller.abort();
    if (postPromptPollTimerRef.current) clearTimeout(postPromptPollTimerRef.current);
    postPromptPollTimerRef.current = null;
    updateConversationMessageState(target, active.messageId, { delivery: 'cancelled', progress: 'cancelled' });
    queuedPrompts.forEach(prompt => updateConversationMessageState(target, prompt.messageId, { delivery: 'cancelled', progress: 'cancelled' }));
    setQueuedPrompts([]);
    setIsThinking(false);
    try {
      const result = await interruptAgent(target);
      if (result.status === 'error' || result.error) throw new Error(result.error || 'Interruption was not accepted.');
      setSendError('Response stopped. Gateway interruption sent.');
    } catch {
      // The local request/subscription is definitely cancelled. Be explicit
      // that backend work can continue if its interruption endpoint failed.
      setSendError('Response stopped locally; underlying work may continue.');
    }
  };
  useEffect(() => {
    if (Platform.OS !== 'web' || !isThinking || typeof window === 'undefined') return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      void stopPendingResponse();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isThinking, target]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (isThinking || queuedPrompts.length === 0) return;
    const next = queuedPrompts[0];
    setQueuedPrompts(queue => queue.slice(1));
    void submitPrompt(next.text, next.editId, next.attachments, next.messageId);
  }, [isThinking, queuedPrompts]); // eslint-disable-line react-hooks/exhaustive-deps
  const handleSend = async () => {
    const trimmed = promptText.trim();
    if (attachments.length && !trimmed) { setSendError('Add a message describing the attached file before sending.'); return; }
    // Current-session prompts remain usable while optional routing metadata
    // loads; only an explicit profile choice needs the inventory to be ready.
    if (!routingReady && modelSelection) { setSendError('Execution settings are still unavailable; your message was not sent.'); return; }
    if (!trimmed) { if (!isThinking) router.push('/voice' as any); return; }
    if (isThinking) {
      const sentAt = Date.now();
      const messageId = editingMessageId || `u-${sentAt}-${Math.random().toString(36).slice(2, 10)}`;
      const attachmentSummaries: ConversationAttachment[] = attachments.map(attachment => ({ name: attachment.name, mediaType: attachment.mimeType || 'application/octet-stream', size: attachment.size, status: attachment.uploaded ? 'stored' : 'uploading', uploadId: attachment.uploaded?.upload_id }));
      if (editingMessageId) updateConversationMessageState(target, messageId, { text: trimmed, attachments: attachmentSummaries, audience: 'captain', delivery: 'sending', progress: 'queued' });
      else appendMessage({ id: messageId, role: 'user', text: trimmed, sentAt, source: 'text', attachments: attachmentSummaries, audience: 'captain', delivery: 'sending', progress: 'queued' });
      setQueuedPrompts(queue => [...queue, { id: `q-${sentAt}`, messageId, text: trimmed, attachments: [...attachments], editId: editingMessageId }]);
      setEditingMessageId(null); setAttachments([]); setPromptText(''); setSendError(null); return;
    }
    await submitPrompt(trimmed, editingMessageId, attachments);
  };
  const retryMessage = (message: ConversationMessage) => {
    const pending = pendingAttachmentsByMessageRef.current.get(message.id);
    if (!pending) { setSendError('Reattach the file to retry this message; the local file is no longer available.'); return; }
    void submitPrompt(message.text, message.id, pending);
  };
  const activeMessage = messages.find(message => message.id === messageActionsId);
  const editMessage = () => {
    if (!activeMessage) return;
    setPromptText(activeMessage.text); setEditingMessageId(activeMessage.id); setMessageActionsId(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  };
  const copyMessage = async () => { if (activeMessage) { await Clipboard.setStringAsync(activeMessage.text); setCopiedMessageId(activeMessage.id); setTimeout(() => setCopiedMessageId(current => current === activeMessage.id ? null : current), 1400); } setMessageActionsId(null); };
  const selectMessage = () => { if (activeMessage) setSelectableMessageId(activeMessage.id); setMessageActionsId(null); };
  const regenerateMessage = async () => { if (!activeMessage || !onRegenerate || !activeMessage.runId || activeMessage.regenerateSafe !== true) return; setMessageActionsId(null); try { await onRegenerate(activeMessage); } catch (error) { setSendError(errorText(error, 'This response could not be regenerated safely.')); } };

  return <KeyboardAvoidingView testID="branded-chat-shell" behavior={Platform.OS === 'ios' ? 'padding' : Platform.OS === 'android' ? 'height' : undefined} style={[styles.canvas, { backgroundColor: dark ? 'rgba(10,14,20,0.784)' : 'rgba(255,255,255,0.8)' }, webViewportHeight ? { height: webViewportHeight } : null]}>
    <View style={styles.shellHeader}>
      <TouchableOpacity testID="brand-drawer-toggle" accessibilityRole="button" accessibilityLabel={`${drawerOpen ? 'Collapse' : 'Open'} Magistrate drawer${unreadAttentionCount ? `, ${unreadAttentionCount} unread captain attention item${unreadAttentionCount === 1 ? '' : 's'}` : ''}`} accessibilityHint="Opens navigation and attention details" accessibilityState={{ expanded: drawerOpen }} onPress={onDrawerToggle} style={styles.logoButton} activeOpacity={0.72}>
        <View style={styles.logoWithUnread}><BrandMark dark={dark} />{unreadAttentionCount > 0 ? <View testID="unread-attention-dot" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.unreadAttentionDot} /> : null}</View>
      </TouchableOpacity>
    </View>
    <ScrollView ref={scrollRef} testID="chat-history" style={styles.chatHistory} contentContainerStyle={styles.chatHistoryContent} onLayout={handleHistoryLayout} onContentSizeChange={handleHistoryContentSizeChange} onScroll={handleScroll} onScrollBeginDrag={handleHistoryScrollBeginDrag} scrollEventThrottle={16} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive" automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'} accessibilityLabel={`${targetLabel} conversation history`}>
      {filterAgentHistory(messages.map(message => ({ ...message, kind: message.kind || 'conversation' })), showToolCalls).map(message => message.role === 'user' ? <UserMessage key={message.id} message={message} textColor={brand.obsidian} selectable={selectableMessageId === message.id} onLongPress={() => setMessageActionsId(message.id)} onActions={() => setMessageActionsId(message.id)} onRetry={message.delivery === 'failed' ? () => retryMessage(message) : undefined} /> : message.kind === 'tool' ? <View key={message.id} testID="tool-history-message" style={styles.toolMessage}><Text numberOfLines={1} style={[styles.toolMessageText, { color: muted }]}>{toolCallPreview(message.text)}</Text></View> : <AssistantMessage key={message.id} message={message} dark={dark} text={text} muted={muted} showToolCalls={showToolCalls} onActions={() => setMessageActionsId(message.id)} />)}
      {isThinking ? <View testID="agent-thinking-message" accessibilityRole="text" accessibilityLabel="Magistrate is working" style={styles.assistantMessage}><Text style={[styles.progressLabel, { color: muted }]}>Working…</Text><ThinkingIndicator dark={dark} /></View> : null}
    </ScrollView>
    {(hasNewMessages || isScrolledUp) ? <TouchableOpacity testID="jump-to-latest" accessibilityRole="button" accessibilityLabel="Jump to latest message" style={styles.jumpButton} onPress={jumpToLatest}><Text style={styles.jumpText}>↓</Text></TouchableOpacity> : null}
    {copiedMessageId ? <Text testID="message-copied" accessibilityLiveRegion="polite" style={styles.copiedLabel}>Copied</Text> : null}
    {messageActionsId ? <View testID="message-actions" accessibilityViewIsModal style={[styles.messageActions, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}>
      {activeMessage?.role === 'user' ? <TouchableOpacity accessibilityRole="button" accessibilityLabel="Edit your message" onPress={editMessage} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Edit</Text></TouchableOpacity> : null}
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Copy ${activeMessage?.role === 'assistant' ? 'assistant response' : 'your message'}`} onPress={() => void copyMessage()} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Copy</Text></TouchableOpacity>
      {activeMessage?.role === 'user' ? <TouchableOpacity accessibilityRole="button" onPress={selectMessage} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Select text</Text></TouchableOpacity> : null}
      {activeMessage?.role === 'assistant' && activeMessage.runId && activeMessage.regenerateSafe === true ? <TouchableOpacity accessibilityRole="button" accessibilityLabel={activeMessage.progress === 'failed' ? 'Retry response' : 'Regenerate response'} onPress={() => void regenerateMessage()} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>{activeMessage.progress === 'failed' ? 'Retry' : 'Regenerate'}</Text></TouchableOpacity> : null}
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close message actions" onPress={() => setMessageActionsId(null)} style={styles.messageAction}><Text style={[styles.messageActionText, { color: muted }]}>×</Text></TouchableOpacity>
    </View> : null}
    {isRecording ? <View testID="active-voice-surface" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.activeVoiceSurface}><View style={styles.activeVoiceHalo} /><Image source={markActive} style={styles.activeVoiceMark} resizeMode="contain" accessibilityIgnoresInvertColors /><LiveWaveform samples={waveSamples} color={brand.cyan} /></View> : null}
    {attachments.length ? <ScrollView testID="attachment-preview" horizontal showsHorizontalScrollIndicator={false} style={styles.attachmentPreview} contentContainerStyle={styles.attachmentPreviewContent} keyboardShouldPersistTaps="handled">
      {attachments.map(attachment => <View key={attachment.id} testID={`attachment-${attachment.id}`} style={[styles.attachmentChip, { backgroundColor: composerSurface }]}>
        {attachment.kind === 'image' ? <Image source={{ uri: attachment.uri }} style={styles.attachmentThumbnail} resizeMode="cover" /> : <View style={[styles.attachmentFileIcon, { backgroundColor: dark ? 'rgba(36,216,255,0.12)' : 'rgba(139,108,255,0.10)' }]}><FileIcon color={dark ? brand.cyan : brand.violet} /></View>}
        <View style={styles.attachmentCopy}><Text numberOfLines={1} style={[styles.attachmentName, { color: text }]}>{attachment.name}</Text><Text style={[styles.attachmentMeta, { color: attachment.status === 'failed' ? brand.critical : muted }]}>{attachment.status === 'uploading' ? 'Uploading…' : attachment.status === 'failed' ? 'Upload failed · retry' : attachment.kind === 'image' ? 'Image' : 'File'}{attachment.status !== 'uploading' && formatAttachmentSize(attachment.size) ? ` · ${formatAttachmentSize(attachment.size)}` : ''}</Text></View>
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
      <TextInput ref={inputRef} testID="captain-prompt" style={[styles.composerInput, { color: text }]} placeholder="Message Magi" placeholderTextColor={muted} value={promptText} onChangeText={setPromptText} onKeyPress={event => { if (isThinking && event.nativeEvent.key === 'Escape') void stopPendingResponse(); }} onSubmitEditing={() => void handleSend()} returnKeyType="send" editable accessibilityLabel={`Message ${targetLabel}`} />
      <ModelMenu dark={dark} profiles={profiles} loading={capabilityLoading} error={capabilityError} open={modelMenuOpen} selection={modelSelection} onToggle={() => setModelMenuOpen(value => !value)} onSelect={selection => { setModelSelection(selection); onProfileChange(selection?.profileId || null); setModelMenuOpen(false); setSendError(null); }} />
      <TouchableOpacity testID="inline-mic-button" accessibilityRole="button" accessibilityLabel={isRecording ? 'Stop microphone' : micStatus === 'requesting' ? 'Requesting microphone permission' : isTranscribing ? 'Transcribing microphone' : 'Start microphone'} accessibilityState={{ selected: isRecording, busy: isTranscribing || micStatus === 'requesting' }} style={[styles.composerIconButton, isRecording ? styles.micActiveButton : undefined]} onPress={voiceCaptureBehavior === 'tap-to-toggle' ? () => void handleMicPress() : undefined} onPressIn={voiceCaptureBehavior === 'hold-to-talk' ? () => { holdActiveRef.current = true; if (!isRecording) void handleMicPress(); } : undefined} onPressOut={voiceCaptureBehavior === 'hold-to-talk' ? () => { holdActiveRef.current = false; if (isRecording) void handleMicPress(); } : undefined} disabled={isTranscribing || micStatus === 'requesting'}><MicIcon size={24} color={isRecording ? brand.cyan : muted} /></TouchableOpacity>
      <TouchableOpacity testID={isThinking ? 'stop-captain-response' : 'send-captain-prompt'} accessibilityRole="button" accessibilityLabel={isThinking ? `Stop response from ${targetLabel}` : promptText.trim() || attachments.length ? `Send message to ${targetLabel}` : 'Open voice mode'} accessibilityState={{ busy: isThinking }} onPress={() => isThinking ? void stopPendingResponse() : void handleSend()} style={[styles.sendButton, isThinking ? styles.stopButton : undefined]}>{isThinking ? <Text style={styles.stopButtonText}>Stop</Text> : promptText.trim() || attachments.length ? <Text style={styles.sendArrow}>↑</Text> : <SoundwaveIcon color={brand.obsidian} size={20} />}</TouchableOpacity>
    </View>
    <View testID="composer-status" style={styles.composerStatus} accessibilityLiveRegion="polite">{editingMessageId ? <Text style={styles.editingLabel}>Editing message</Text> : !isThinking && (micStatus === 'requesting' ? <Text testID="mic-status" style={styles.micTranscribingLabel}>Requesting microphone permission…</Text> : micStatus === 'listening' ? <Text testID="mic-status" style={styles.micListeningLabel}>Listening… {voiceCaptureBehavior === 'hold-to-talk' ? 'release mic to finish' : 'tap mic to finish'}</Text> : micStatus === 'transcribing' ? <Text testID="mic-status" style={styles.micTranscribingLabel}>Transcribing…</Text> : micStatus === 'ready' ? <Text testID="mic-status" style={styles.micReadyLabel}>Transcript ready — review before sending</Text> : micStatus === 'error' ? <Text testID="mic-status" accessibilityRole="alert" style={styles.micErrorLabel}>Microphone unavailable. {sendError || 'Try again.'}</Text> : null)}{queuedPrompts.length ? <Text testID="queued-message-count" style={styles.queuedLabel}>{queuedPrompts.length} queued · sends in order</Text> : null}{sendError ? <Text testID="captain-send-error" style={styles.sendError}>{sendError}</Text> : null}</View>
  </KeyboardAvoidingView>;
}

function PanelText({ text, muted }: { text: string; muted: string }) { return <Text style={[styles.panelText, { color: muted }]}>{text}</Text>; }

function FleetAgentRow({ agent, activeStatus, dark, onOpenChat }: { agent: AgentInfo; activeStatus: string; dark: boolean; onOpenChat: () => void }) {
  const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const [menuOpen, setMenuOpen] = useState(false); const [confirmInterrupt, setConfirmInterrupt] = useState(false); const [renaming, setRenaming] = useState(false);
  const herdrDisplayName = agentDisplayName(agent);
  const [name, setName] = useState(agent.name || ''); const [displayName, setDisplayName] = useState(herdrDisplayName); const [busy, setBusy] = useState(false); const [message, setMessage] = useState<string | null>(null);
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
    <View style={styles.drawerFixedHeader}><View style={styles.drawerTitleRow}><Text testID="drawer-wordmark" accessibilityRole="header" style={[styles.drawerWordmark, { color: text }]}>Magistrate</Text><TouchableOpacity testID="drawer-settings-control" accessibilityRole="button" accessibilityLabel="Open Settings" onPress={onOpenSettings} activeOpacity={0.75} style={styles.drawerSettingsButton}><GearIcon size={21.6} color={muted} /><Text style={[styles.drawerSettingsText, { color: muted }]}>Settings</Text></TouchableOpacity></View></View>
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

type SettingsSectionKey = 'execution' | 'voice-input' | 'usage' | 'appearance' | 'diagnostics' | 'account';

function SettingsSectionControl({ id, title, expanded, onPress, summary, color, muted }: { id: SettingsSectionKey; title: string; expanded: boolean; onPress: () => void; summary?: string; color: string; muted: string }) {
  return <View testID={`settings-${id}-section`} style={styles.settingsSection}>
    <TouchableOpacity testID={id === 'appearance' ? 'settings-theme' : `settings-section-${id}`} accessibilityRole="button" accessibilityLabel={`${title} settings`} accessibilityState={{ expanded }} {...({ 'aria-expanded': expanded } as any)} onPress={onPress} style={styles.settingsSectionHeader} activeOpacity={0.75}>
      <View style={styles.settingsSectionHeaderCopy}><Text style={[styles.settingsSectionTitle, { color }]}>{title}</Text>{summary ? <Text style={[styles.settingsSectionSummary, { color: muted }]}>{summary}</Text> : null}</View>
      <Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.settingsSectionChevron}>{expanded ? '⌄' : '›'}</Text>
    </TouchableOpacity>
  </View>;
}

function SettingsSheet({ open, dark, animatedStyle, health, loading, error, executionError, preferences, onPreferencesChange, executionProfiles, executionSettings, onExecutionSettingsChange, onSaveCredential, voiceCapabilities, usage, usageLoading, usageError, onClose, onLogout }: { open: boolean; dark: boolean; animatedStyle: object; health: HealthInfo | null; loading: boolean; error: string | null; executionError?: string | null; preferences: ChatPreferences; onPreferencesChange: (preferences: ChatPreferences) => void; executionProfiles: ExecutionProfile[]; executionSettings: ExecutionSettings; onExecutionSettingsChange: (update: Partial<Pick<ExecutionSettings, 'profile_id' | 'switching_behavior' | 'unavailable_behavior'>>) => void; onSaveCredential: (credentialKey: string, credential: string) => Promise<void>; voiceCapabilities: VoiceInputCapabilities; usage: UsageProvider[]; usageLoading: boolean; usageError: string | null; onClose: () => void; onLogout: () => void }) {
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
  const network = health?.status === 'healthy'; const runtime = Boolean(health?.herdr_socket_connected);
  return <Animated.View pointerEvents={open ? 'auto' : 'none'} accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="settings-sheet" style={[styles.settingsSheet, { backgroundColor: dark ? brand.command : '#FFFFFF' }, animatedStyle]}>
    <TouchableOpacity testID="settings-close" accessibilityRole="button" accessibilityLabel="Close settings" onPress={onClose} style={styles.settingsClose}><Text style={[styles.settingsCloseText, { color: text }]}>×</Text></TouchableOpacity>
    <Text style={[styles.settingsTitle, { color: text }]}>Settings</Text>
    <ScrollView testID="settings-scroll" style={styles.settingsScroll} contentContainerStyle={styles.settingsScrollContent} keyboardShouldPersistTaps="handled">
    <View style={styles.settingsStatusGrid}><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: error ? brand.critical : loading ? brand.attention : network ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Network</Text><Text testID="settings-network-status" style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : error ? 'Unavailable' : network ? 'Connected' : 'Degraded'}</Text></View></View><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: runtime ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Runtime</Text><Text style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : runtime ? 'Live' : 'Unavailable'}</Text></View></View></View>
    {error || executionError ? <Text style={styles.settingsError}>{error || executionError}</Text> : null}
    <View testID="settings-execution-section" style={styles.settingsSection}>
    <SettingsSectionControl id="execution" title="Execution" expanded={expandedSection === 'execution'} onPress={() => toggleSection('execution')} color={text} muted={muted} />
    {expandedSection === 'execution' ? <View testID="settings-execution-content">
    <Text style={[styles.preferenceLabel, { color: muted }]}>ROUTING PREFERENCE</Text>
    <Text style={[styles.settingsToggleDescription, { color: muted }]}>Selection is saved to this Magistrate account. Runtime migration is not available yet; selected profiles are sent as explicit prompt context.</Text>
    <View style={styles.optionRow}>{[
      { key: 'migrate' as const, label: 'Migrate session' }, { key: 'new-session' as const, label: 'New session' },
    ].map(option => <TouchableOpacity key={option.key} testID={`switching-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.switching_behavior === option.key }} onPress={() => onExecutionSettingsChange({ switching_behavior: option.key })} style={[styles.optionPill, executionSettings.switching_behavior === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.switching_behavior === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
    <View style={styles.optionRow}>{[
      { key: 'error' as const, label: 'Error if unavailable' }, { key: 'fallback' as const, label: 'Fallback to current' },
    ].map(option => <TouchableOpacity key={option.key} testID={`unavailable-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: executionSettings.unavailable_behavior === option.key }} onPress={() => onExecutionSettingsChange({ unavailable_behavior: option.key })} style={[styles.optionPill, executionSettings.unavailable_behavior === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: executionSettings.unavailable_behavior === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
    {providers.length ? <View style={styles.credentialBlock}><Text style={[styles.preferenceLabel, { color: muted }]}>HARNESS CREDENTIALS</Text><View style={styles.optionRow}>{providers.map(([key, label]) => <TouchableOpacity key={key} testID={`credential-provider-${key}`} accessibilityRole="button" accessibilityState={{ selected: credentialKey === key }} onPress={() => setCredentialKey(key)} style={[styles.optionPill, credentialKey === key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: credentialKey === key ? brand.obsidian : text }]}>{label}</Text></TouchableOpacity>)}</View>{credentialKey ? <View style={styles.credentialInputRow}><TextInput testID="execution-credential-input" accessibilityLabel={`Credential for ${credentialKey}`} secureTextEntry value={credential} onChangeText={setCredential} placeholder="Paste credential (stored encrypted)" placeholderTextColor={muted} style={[styles.credentialInput, { color: text }]} /><TouchableOpacity testID="execution-credential-save" accessibilityRole="button" disabled={!credential.trim()} onPress={() => { const value = credential.trim(); setCredential(''); void onSaveCredential(credentialKey, value); }} style={styles.credentialSave}><Text style={styles.credentialSaveText}>SAVE</Text></TouchableOpacity></View> : null}</View> : null}
    </View> : null}
    </View>
    <View testID="settings-voice-input-section" style={styles.settingsSection}>
      <SettingsSectionControl id="voice-input" title="Voice input" expanded={expandedSection === 'voice-input'} onPress={() => toggleSection('voice-input')} color={text} muted={muted} />
      {expandedSection === 'voice-input' ? <View testID="settings-voice-input-content">
      <Text style={[styles.settingsToggleDescription, { color: muted }]}>Choose how speech becomes a draft. Nothing is sent until you press Send; gateway credentials stay on the server.</Text>
      <View testID="settings-voice-mode-options" style={styles.optionRow}>{VOICE_INPUT_MODE_OPTIONS.map(option => { const capability = capabilityFor(voiceCapabilities, option.id); const selected = preferences.voiceInputMode === option.id; const disabled = capability.available === 'unavailable'; return <TouchableOpacity key={option.id} testID={`voice-mode-option-${option.id}`} accessibilityRole="button" accessibilityLabel={`${option.label}: ${option.description}`} accessibilityState={{ selected, disabled }} disabled={disabled} onPress={() => { const next = { ...preferences, voiceInputMode: option.id }; onPreferencesChange(next); void saveVoiceInputMode(option.id); }} style={[styles.voiceModeOption, selected ? styles.optionPillSelected : undefined, disabled ? styles.modelOptionDisabled : undefined]}><Text style={[styles.optionText, { color: selected ? brand.obsidian : text }]}>{option.label}{disabled ? ' · unavailable' : ''}</Text><Text style={[styles.voiceModeDescription, { color: selected ? brand.obsidian : muted }]}>{capability.reason || option.description}</Text></TouchableOpacity>; })}</View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>CAPTURE GESTURE</Text>
      <View testID="settings-voice-capture-options" style={styles.optionRow}>{(['tap-to-toggle', 'hold-to-talk'] as const).map(value => <TouchableOpacity key={value} testID={`voice-capture-option-${value}`} accessibilityRole="button" accessibilityState={{ selected: preferences.voiceCaptureBehavior === value }} onPress={() => { const next = { ...preferences, voiceCaptureBehavior: value }; onPreferencesChange(next); void saveVoiceCaptureBehavior(value); }} style={[styles.optionPill, preferences.voiceCaptureBehavior === value ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.voiceCaptureBehavior === value ? brand.obsidian : text }]}>{value === 'tap-to-toggle' ? 'Tap to toggle' : 'Hold to talk'}</Text></TouchableOpacity>)}</View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>FINAL TRANSCRIPT</Text>
      <View testID="settings-voice-transcript-options" style={styles.optionRow}>{(['insert', 'auto-send'] as const).map(value => <TouchableOpacity key={value} testID={`voice-transcript-option-${value}`} accessibilityRole="button" accessibilityState={{ selected: preferences.voiceTranscriptBehavior === value }} onPress={() => { const next = { ...preferences, voiceTranscriptBehavior: value }; onPreferencesChange(next); void saveVoiceTranscriptBehavior(value); }} style={[styles.optionPill, preferences.voiceTranscriptBehavior === value ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.voiceTranscriptBehavior === value ? brand.obsidian : text }]}>{value === 'insert' ? 'Insert for review' : 'Send automatically'}</Text></TouchableOpacity>)}</View>
    </View> : null}
    </View>
    <View testID="settings-usage-section" style={styles.settingsSection}>
      <SettingsSectionControl id="usage" title="Usage" expanded={expandedSection === 'usage'} onPress={() => toggleSection('usage')} color={text} muted={muted} summary={usage.length ? `${usage[0].provider}${usage[0].plan ? ` · ${usage[0].plan}` : ''}` : undefined} />
      {expandedSection === 'usage' ? <View testID="settings-usage-content">
      <Text style={[styles.settingsToggleDescription, { color: muted }]}>Authenticated quota data only. Missing or unavailable amounts stay explicitly unknown.</Text>
      {usageLoading ? <PanelText text="Loading authenticated usage…" muted={muted} /> : usageError ? <PanelText text={usageError} muted={brand.critical} /> : usage.length === 0 ? <PanelText text="Usage is unknown; no authenticated quota data is available." muted={muted} /> : usage.map(item => <View key={item.provider} style={styles.settingsUsageItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.provider}{item.plan ? ` · ${item.plan}` : ''}</Text><Text style={[styles.panelItemMeta, { color: item.status === 'fresh' ? muted : brand.attention }]}>{item.status === 'fresh' && item.windows.length ? item.windows.map(window => `${window.label || window.id || 'window'}: ${typeof window.percentRemaining === 'number' ? `${window.percentRemaining}% left` : typeof window.spentUsd === 'number' && typeof window.limitUsd === 'number' ? `$${window.spentUsd} / $${window.limitUsd}` : 'amount unknown'}`).join(' · ') : item.status === 'auth_required' ? 'Authentication required' : item.error || 'Quota unknown'}</Text></View>)}
    </View> : null}
    </View>
    <View testID="settings-appearance-section">
      <SettingsSectionControl id="appearance" title="Appearance" expanded={expandedSection === 'appearance'} onPress={() => toggleSection('appearance')} color={text} muted={muted} summary="Theme, background, and chat display" />
      {expandedSection === 'appearance' ? <View testID="settings-appearance-window" accessibilityViewIsModal style={[styles.appearanceWindow, { backgroundColor: dark ? '#171E2A' : '#F4F6F9' }]}>
      <View style={styles.appearanceHeader}><Text style={[styles.appearanceTitle, { color: text }]}>Appearance</Text><TouchableOpacity testID="settings-appearance-close" accessibilityRole="button" accessibilityLabel="Close appearance settings" onPress={() => toggleSection('appearance')} style={styles.appearanceClose}><Text style={[styles.settingsCloseText, { color: text }]}>×</Text></TouchableOpacity></View>
      <Text style={[styles.preferenceLabel, { color: muted }]}>BACKGROUND</Text>
      <View style={styles.optionRow}>{backgroundOptions.map(option => <TouchableOpacity key={option.key} testID={`background-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: preferences.background === option.key }} onPress={() => { const next = { ...preferences, background: option.key, customBackgroundUri: undefined }; onPreferencesChange(next); void saveChatBackground(option.key); }} style={[styles.optionPill, preferences.background === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.background === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
      {preferences.customBackgroundUri ? <View style={styles.customBackgroundRow}><Image source={{ uri: preferences.customBackgroundUri }} style={styles.customBackgroundPreview} resizeMode="cover" accessibilityLabel="Custom background preview" /><View style={styles.customBackgroundCopy}><Text style={[styles.settingsToggleTitle, { color: text }]}>Custom background</Text><Text style={[styles.settingsToggleDescription, { color: muted }]}>Stored on this device and used only when selected.</Text></View><TouchableOpacity testID="settings-custom-background-remove" accessibilityRole="button" onPress={() => void removeCustom()} style={styles.secondaryAction}><Text style={[styles.secondaryActionText, { color: brand.critical }]}>Remove</Text></TouchableOpacity></View> : null}
      <TouchableOpacity testID="settings-custom-background-upload" accessibilityRole="button" onPress={() => void pickCustomBackground()} style={styles.uploadBackgroundButton}><Text style={[styles.optionText, { color: text }]}>{preferences.customBackgroundUri ? 'Replace custom photo' : 'Upload custom photo'}</Text></TouchableOpacity>
      <Text style={[styles.preferenceLabel, { color: muted }]}>MODE</Text>
      <View testID="settings-theme-options" style={styles.optionRow}>{themeOptions.map(option => <TouchableOpacity key={option.key} testID={`theme-option-${option.key}`} accessibilityRole="button" accessibilityState={{ selected: preferences.themeMode === option.key }} onPress={() => { const next = { ...preferences, themeMode: option.key }; onPreferencesChange(next); void saveThemeMode(option.key); }} style={[styles.optionPill, preferences.themeMode === option.key ? styles.optionPillSelected : undefined]}><Text style={[styles.optionText, { color: preferences.themeMode === option.key ? brand.obsidian : text }]}>{option.label}</Text></TouchableOpacity>)}</View>
      <View style={styles.settingsToggleRow}><View style={styles.settingsToggleCopy}><Text style={[styles.settingsToggleTitle, { color: text }]}>Show tool calls</Text><Text style={[styles.settingsToggleDescription, { color: muted }]}>Include tool activity in agent conversations.</Text></View><Switch testID="settings-tool-calls-toggle" accessibilityLabel="Show tool calls in chat history" value={preferences.showToolCalls} onValueChange={value => { const next = { ...preferences, showToolCalls: value }; onPreferencesChange(next); void saveToolCallVisibility(value); }} trackColor={{ false: '#424B59', true: brand.cyan }} thumbColor={preferences.showToolCalls ? brand.obsidian : '#F4F5F7'} /></View>
    </View> : null}
    </View>
    <SettingsSectionControl id="diagnostics" title="Diagnostics" expanded={expandedSection === 'diagnostics'} onPress={() => toggleSection('diagnostics')} color={text} muted={muted} />
    {expandedSection === 'diagnostics' ? <View testID="settings-diagnostics-content" style={styles.settingsSectionContent}><Text style={[styles.settingsToggleDescription, { color: muted }]}>Gateway, Herdr, and transport details for troubleshooting.</Text><TouchableOpacity testID="settings-diagnostics-open" accessibilityRole="button" accessibilityLabel="Open diagnostics" onPress={() => { onClose(); router.push('/diagnostics' as any); }} style={styles.diagnosticsButton}><Text style={[styles.diagnosticsButtonText, { color: text }]}>Open diagnostics</Text><Text style={[styles.diagnosticsArrow, { color: muted }]}>↗</Text></TouchableOpacity></View> : null}
    <SettingsSectionControl id="account" title="Account" expanded={expandedSection === 'account'} onPress={() => toggleSection('account')} color={text} muted={muted} summary="Profile and sign-in" />
    {expandedSection === 'account' ? <View testID="settings-account-content" style={styles.settingsSectionContent}><TouchableOpacity testID="settings-account-open" accessibilityRole="button" accessibilityLabel="Open account settings" onPress={() => { onClose(); router.push('/account' as any); }} style={styles.diagnosticsButton}><Text style={[styles.diagnosticsButtonText, { color: text }]}>Account & notifications</Text><Text style={[styles.diagnosticsArrow, { color: muted }]}>↗</Text></TouchableOpacity><TouchableOpacity testID="settings-logout" accessibilityRole="button" accessibilityLabel="Sign out of Magistrate" onPress={onLogout} style={styles.logoutButton}><Text style={styles.logoutButtonText}>SIGN OUT</Text></TouchableOpacity></View> : null}
    </ScrollView>
  </Animated.View>;
}

export default function ChatScreen() {
  const { agentId, record } = useLocalSearchParams<{ agentId?: string | string[]; record?: string | string[] }>(); const target = Array.isArray(agentId) ? agentId[0] : agentId; const autoStartRecording = (Array.isArray(record) ? record[0] : record) === 'true';
  const router = useRouter();
  const dark = isDarkTheme(useChatColorScheme()); const { width } = useWindowDimensions(); const isNarrow = width < 720; const drawerWidth = Math.min(isNarrow ? width * 0.82 : 310, 330);
  const [drawerOpen, setDrawerOpen] = useState(false); const [settingsOpen, setSettingsOpen] = useState(false); const [activeSection, setActiveSection] = useState<DrawerSection>(null); const [preferences, setPreferences] = useState<ChatPreferences>(DEFAULT_CHAT_PREFERENCES); const [preferencesReady, setPreferencesReady] = useState(false);
  const [executionProfiles, setExecutionProfiles] = useState<ExecutionProfile[]>([]);
  const [executionSettings, setExecutionSettings] = useState<ExecutionSettings>({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
  const [executionLoading, setExecutionLoading] = useState(true);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [executionReady, setExecutionReady] = useState(false);
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
      if (settingsResult.status === 'fulfilled') { setExecutionSettings(settingsResult.value); setExecutionReady(true); }
      else setExecutionError(errorText(settingsResult.reason, 'Execution settings could not be loaded.'));
      if (voiceResult.status === 'fulfilled') {
        const local = getLocalVoiceCapabilities(voiceResult.value.serverConfigured);
        const serverOpenai = capabilityFor(voiceResult.value, 'openai');
        setVoiceCapabilities({ ...local, serverProvider: voiceResult.value.serverProvider, serverConfigured: voiceResult.value.serverConfigured, modes: local.modes.map(item => item.id === 'openai' ? serverOpenai : item) });
      }
    }).finally(() => { if (mounted) setExecutionLoading(false); });
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
  const chatAnimatedStyle = useAnimatedStyle(() => ({ transform: [{ translateX: isNarrow ? drawerProgress.value * drawerWidth : 0 }] }), [drawerWidth, isNarrow]);
  const settingsAnimatedStyle = useAnimatedStyle(() => ({ opacity: settingsProgress.value, transform: [{ translateY: interpolate(settingsProgress.value, [0, 1], [420, 0]) }] }));
  const swipeToClose = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => isNarrow && drawerOpen && g.dx < -8 && Math.abs(g.dx) > Math.abs(g.dy),
    onPanResponderRelease: (_, g) => { if (g.dx < -55 || g.vx < -0.35) setDrawerOpen(false); },
  }), [drawerOpen, isNarrow]);
  // Open from a deliberate horizontal swipe anywhere in the chat, including
  // the detached composer. Vertical movement remains with text selection and
  // input scrolling; app/_layout.tsx disables browser back-navigation hijack.
  const swipeToOpen = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => isNarrow && !drawerOpen && g.dx > 8 && Math.abs(g.dx) > Math.abs(g.dy) * 1.2,
    onPanResponderRelease: (_, g) => { if (g.dx > 55 || g.vx > 0.35) setDrawerOpen(true); },
  }), [drawerOpen, isNarrow]);
  return <EnvironmentBackground hideBottomControls><SafeAreaView style={styles.page} {...(isNarrow ? swipeToOpen.panHandlers : {})}>
    {!preferencesReady ? <View testID="chat-appearance-loading" style={[styles.appearanceLoading, { backgroundColor: dark ? brand.obsidian : '#F7F8FA' }]} /> : <>
      <DrawerPanel open={drawerOpen && !settingsOpen} dark={dark} isNarrow={isNarrow} animatedStyle={drawerAnimatedStyle} panHandlers={isNarrow ? swipeToClose.panHandlers : {}} activeSection={activeSection} setActiveSection={setActiveSection} onOpenSettings={() => { setDrawerOpen(false); setSettingsOpen(true); }} onOpenAgent={selectedAgentId => { setDrawerOpen(false); router.push({ pathname: '/chat', params: { agentId: selectedAgentId } } as any); }} agents={agents} attention={attention} activity={activity} providers={providers} errors={errors} loading={loading} />
      <Animated.View style={[styles.chatStage, chatAnimatedStyle]}><ChatCanvas target={target || 'captain'} showToolCalls={preferences.showToolCalls} drawerOpen={drawerOpen} onDrawerToggle={() => setDrawerOpen(value => !value)} profiles={executionProfiles} capabilityLoading={executionLoading} capabilityError={executionError} selectedProfileId={executionSettings.profile_id} routingReady={executionReady} voiceInputMode={preferences.voiceInputMode} voiceCapabilities={voiceCapabilities} voiceCaptureBehavior={preferences.voiceCaptureBehavior} voiceTranscriptBehavior={preferences.voiceTranscriptBehavior} autoStartRecording={autoStartRecording} onProfileChange={profileId => { setExecutionSettings(current => ({ ...current, profile_id: profileId })); void updateExecutionSettings({ profile_id: profileId }).catch(error => setExecutionError(errorText(error, 'The routing preference could not be saved.'))); }} /></Animated.View>
      <SettingsSheet open={settingsOpen} dark={dark} animatedStyle={settingsAnimatedStyle} health={health} loading={healthLoading} error={healthError} executionError={executionError} preferences={preferences} onPreferencesChange={setPreferences} executionProfiles={executionProfiles} voiceCapabilities={voiceCapabilities} executionSettings={executionSettings} onExecutionSettingsChange={update => { setExecutionSettings(current => ({ ...current, ...update })); void updateExecutionSettings(update).catch(error => setExecutionError(errorText(error, 'The execution setting could not be saved.'))); }} onSaveCredential={async (credentialKey, credential) => { try { await saveExecutionCredential(credentialKey, credential); setExecutionError(null); const capabilities = await fetchExecutionCapabilities(); setExecutionProfiles(profilesFromCapabilities(capabilities)); } catch (error) { setExecutionError(errorText(error, 'The credential could not be saved.')); } }} usage={usage} usageLoading={usageLoading} usageError={usageError} onClose={() => setSettingsOpen(false)} onLogout={() => { setSettingsOpen(false); void logoutGatewaySession(); }} />
    </>}
  </SafeAreaView></EnvironmentBackground>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minWidth: 0, overflow: 'hidden', touchAction: 'pan-y' } as any, chatStage: { flex: 1, minWidth: 0, padding: 8, zIndex: 1 }, canvas: { flex: 1, minWidth: 0, borderRadius: 26, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 8, overflow: 'hidden' },
  shellHeader: { height: 48, flexShrink: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', zIndex: 12, elevation: 12 }, logoButton: { width: 52, height: 44, alignItems: 'center', justifyContent: 'center' }, logoWithUnread: { position: 'relative', width: 44, height: 44, alignItems: 'center', justifyContent: 'center' }, unreadAttentionDot: { position: 'absolute', top: 1, right: 0, width: 8, height: 8, borderRadius: 4, backgroundColor: '#F5C542', borderWidth: 1, borderColor: '#111722' }, mark: { width: 37, height: 37 }, tinyDot: { width: 8, height: 8, borderRadius: 4 },
  chatHistory: { flex: 1, minHeight: 0 }, chatHistoryContent: { flexGrow: 1, justifyContent: 'flex-end', paddingTop: 24, paddingHorizontal: 22, paddingBottom: 22, gap: 16 }, userMessageWrap: { maxWidth: 680, alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'flex-end', gap: 5 }, userMessage: { flex: 1, paddingVertical: 12, paddingHorizontal: 16, borderRadius: 22, backgroundColor: brand.cyan, borderWidth: 1, borderColor: brand.cyan }, assistantMessage: { maxWidth: 680, alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'flex-start', gap: 5, paddingVertical: 9, paddingHorizontal: 2 }, assistantBody: { flex: 1, minWidth: 0 }, toolMessage: { maxWidth: 680, alignSelf: 'flex-start', paddingVertical: 7, paddingHorizontal: 12, borderLeftWidth: 2, borderLeftColor: 'rgba(142,153,170,0.45)' }, attachedToolResult: { maxWidth: 260, marginTop: 7, paddingVertical: 5, paddingHorizontal: 9, borderRadius: 8, backgroundColor: 'rgba(142,153,170,0.10)', overflow: 'hidden' }, toolMessageText: { fontFamily: 'monospace', fontSize: 12, lineHeight: 18 }, messageText: { fontSize: 17, lineHeight: 26 }, progressLabel: { fontSize: 13, fontWeight: '700', marginBottom: 3 }, assistantState: { fontSize: 11, marginTop: 8 }, assistantStateFailed: { color: brand.critical, fontSize: 11, lineHeight: 17, marginTop: 8 }, thinkingSummary: { marginTop: 8, paddingLeft: 9, borderLeftWidth: 2, borderLeftColor: 'rgba(139,108,255,0.5)' }, thinkingSummaryLabel: { fontSize: 10, fontWeight: '800' }, thinkingSummaryText: { fontSize: 12, lineHeight: 17, marginTop: 2 }, sources: { marginTop: 10, gap: 4 }, sourcesTitle: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8, textTransform: 'uppercase' }, sourceRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, minHeight: 38, paddingVertical: 4 }, sourceMarker: { width: 20, fontSize: 12, lineHeight: 18, fontWeight: '800', textAlign: 'center' }, sourceCopy: { flex: 1, minWidth: 0 }, sourceTitle: { fontSize: 12, lineHeight: 17, fontWeight: '700' }, sourceMeta: { fontSize: 10, lineHeight: 14, marginTop: 1 }, sourceQuote: { fontSize: 11, lineHeight: 15, marginTop: 2 }, messageAttachment: { fontSize: 11, marginTop: 6, opacity: 0.82 }, messageTimestamp: { fontSize: 10, marginTop: 5, opacity: 0.72, textAlign: 'right' }, messageDelivery: { fontSize: 10, marginTop: 3, color: brand.mutedDark }, messageFailure: { flexDirection: 'row', alignItems: 'center', gap: 9, marginTop: 6 }, messageFailed: { color: brand.critical, fontSize: 10 }, retryText: { color: brand.cyan, fontSize: 11, fontWeight: '800' },
  jumpButton: { position: 'absolute', alignSelf: 'center', bottom: 90, width: 38, height: 38, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(17,23,34,0.62)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.22)', borderRadius: 999, zIndex: 30, shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 10, elevation: 16 }, jumpText: { color: brand.paper, fontSize: 20, lineHeight: 22, fontWeight: '700' }, inlineMessageAction: { minWidth: 32, minHeight: 32, alignItems: 'center', justifyContent: 'center', borderRadius: 16 }, inlineMessageActionText: { color: brand.mutedDark, fontSize: 13, letterSpacing: 1, fontWeight: '800' }, copiedLabel: { position: 'absolute', right: 20, bottom: 128, color: brand.success, fontSize: 11, fontWeight: '800', zIndex: 31 },
  messageActions: { position: 'absolute', right: 24, bottom: 82, flexDirection: 'row', borderRadius: 18, padding: 4, zIndex: 32, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 20, elevation: 8 }, messageAction: { minWidth: 52, minHeight: 40, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8 }, messageActionText: { fontSize: 13, fontWeight: '700' },
  composer: { flexShrink: 0, flexDirection: 'row', alignItems: 'center', gap: 4, minHeight: 60, borderRadius: 30, paddingHorizontal: 9, paddingVertical: 7, marginHorizontal: 8, zIndex: 20, elevation: 12, borderWidth: 1, borderColor: 'rgba(142,153,170,0.22)' }, composerIconButton: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, composerIconText: { fontSize: 21, fontWeight: '500' }, composerInput: { flex: 1, minWidth: 0, fontSize: 16, paddingVertical: 8, outlineStyle: 'none' as any }, sendButton: { width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center', backgroundColor: brand.violet }, stopButton: { width: 58, borderRadius: 21, backgroundColor: brand.critical }, stopButtonText: { color: brand.paper, fontSize: 12, fontWeight: '800' }, sendArrow: { color: brand.paper, fontSize: 22, fontWeight: '800' }, disabled: { opacity: 0.55 }, composerStatus: { minHeight: 22, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 18 }, editingLabel: { color: brand.cyan, fontSize: 11, fontWeight: '700' }, micListeningLabel: { color: brand.cyan, fontSize: 11, fontWeight: '700' }, micTranscribingLabel: { color: brand.violet, fontSize: 11, fontWeight: '700' }, micReadyLabel: { color: brand.success, fontSize: 11, fontWeight: '700' }, micErrorLabel: { color: brand.critical, fontSize: 11, fontWeight: '700' }, thinkingLabel: { color: brand.mutedDark, fontSize: 11, fontWeight: '700', alignItems: 'center' }, thinkingDots: { fontSize: 16, letterSpacing: 2, fontWeight: '900' }, queuedLabel: { color: brand.violet, fontSize: 11, fontWeight: '700' }, sendError: { color: '#FFB4B2', fontSize: 12, flex: 1, textAlign: 'right' },
  attachmentControl: { width: 36, zIndex: 20 }, attachmentMenu: { position: 'absolute', left: -2, bottom: 46, width: 238, borderRadius: 20, padding: 11, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 24, elevation: 14 }, attachmentOption: { minHeight: 54, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 8, paddingVertical: 7, borderRadius: 13 }, attachmentOptionTitle: { fontSize: 14, fontWeight: '700' }, attachmentOptionMeta: { fontSize: 11, marginTop: 2 },
  attachmentPreview: { flexGrow: 0, marginHorizontal: 8, marginBottom: 7, maxHeight: 60 }, attachmentPreviewContent: { gap: 8, paddingHorizontal: 3 }, attachmentChip: { width: 220, minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 9, padding: 5, paddingRight: 7, borderRadius: 15 }, attachmentThumbnail: { width: 46, height: 46, borderRadius: 11 }, attachmentFileIcon: { width: 46, height: 46, borderRadius: 11, alignItems: 'center', justifyContent: 'center' }, attachmentCopy: { flex: 1, minWidth: 0 }, attachmentName: { fontSize: 12, fontWeight: '700' }, attachmentMeta: { fontSize: 10, marginTop: 3 }, attachmentRemove: { width: 28, height: 38, alignItems: 'center', justifyContent: 'center' }, attachmentRemoveText: { fontSize: 21, lineHeight: 23 },
  activeVoiceSurface: { position: 'absolute', left: 8, right: 8, bottom: 68, height: 72, borderRadius: 28, backgroundColor: 'rgba(17,23,34,0.96)', borderWidth: 1, borderColor: brand.cyan, overflow: 'hidden', zIndex: 9, alignItems: 'center', justifyContent: 'center' }, activeVoiceHalo: { position: 'absolute', width: 170, height: 170, borderRadius: 85, backgroundColor: 'rgba(36,216,255,0.12)' }, activeVoiceMark: { width: 52, height: 52, zIndex: 2 }, micActiveButton: { borderWidth: 1, borderColor: brand.cyan, borderRadius: 18, backgroundColor: 'rgba(36,216,255,0.12)' },
  liveWaveform: { position: 'absolute', left: 8, right: 8, bottom: 8, height: 52, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', paddingHorizontal: 14, zIndex: 9 }, liveWaveformBar: { width: 3, borderRadius: 2 },
  modelControl: { width: 40, zIndex: 15 }, modelButton: { height: 34, width: 34, alignItems: 'center', justifyContent: 'center' }, modelMenu: { position: 'absolute', right: -8, bottom: 44, width: 280, maxHeight: 350, borderRadius: 22, padding: 12, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 24, elevation: 10 }, modelOptionsScroll: { maxHeight: 235 }, menuTitle: { fontSize: 14, fontWeight: '800', marginBottom: 7, paddingHorizontal: 7 }, harnessLabel: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8, textTransform: 'uppercase', paddingHorizontal: 7, paddingTop: 8 }, modelOption: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 9, paddingHorizontal: 7, paddingVertical: 6 }, modelOptionDisabled: { opacity: 0.5 }, modelOptionCopy: { flex: 1 }, modelOptionTitle: { fontSize: 13, fontWeight: '700' }, modelOptionMeta: { fontSize: 11, lineHeight: 15, marginTop: 2 }, selectionDot: { width: 7, height: 7, borderRadius: 4 }, modelNotice: { fontSize: 11, lineHeight: 16, paddingHorizontal: 7, paddingTop: 8 }, modelError: { color: '#FFB4B2', fontSize: 11, lineHeight: 16, paddingHorizontal: 7, paddingTop: 8 },
  drawer: { position: 'absolute', top: 8, bottom: 8, width: 310, zIndex: 10, borderRadius: 24, padding: 14, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.28, shadowRadius: 28, elevation: 12 }, drawerDesktop: { left: 58 }, drawerMobile: { left: 8, width: '82%' }, drawerFixedHeader: { flexShrink: 0, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'rgba(142,153,170,0.24)', paddingBottom: 9, marginBottom: 5 }, drawerTitleRow: { minHeight: 38, flexDirection: 'row', alignItems: 'center', gap: 8 }, drawerWordmark: { flex: 1, fontFamily: Platform.select({ web: 'Bodoni Moda, Times New Roman, serif', default: undefined }), fontSize: 25, lineHeight: 32, fontWeight: '500', marginLeft: 4 }, drawerSettingsButton: { minHeight: 36, paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', gap: 5, borderRadius: 10 }, drawerSettingsText: { fontSize: 12, fontWeight: '700' }, drawerScroll: { flex: 1, minHeight: 0 }, drawerScrollContent: { paddingTop: 4, paddingBottom: 12 }, drawerBottom: { flexShrink: 0, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(142,153,170,0.24)' }, drawerRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 4 }, drawerIcon: { width: 20, fontSize: 16.8, fontWeight: '800', textAlign: 'center' }, gearIconContainer: { width: 20, alignItems: 'center', justifyContent: 'center' }, drawerRowText: { flex: 1, fontSize: 15, fontWeight: '400', textAlign: 'left' }, drawerCount: { fontSize: 11, fontWeight: '800' }, chevron: { width: 18, fontSize: 13, textAlign: 'center' }, sectionPanel: { paddingLeft: 30, paddingRight: 4, paddingBottom: 10, gap: 7 }, panelText: { fontSize: 13, lineHeight: 19 }, panelItem: { paddingVertical: 6 }, panelItemTitle: { fontSize: 13, fontWeight: '800', marginBottom: 2 }, panelItemMeta: { fontSize: 12, lineHeight: 17 }, fleetAgentWrap: { borderRadius: 14 }, fleetAgentWrapOpen: { zIndex: 4 }, fleetPanelRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 3 }, fleetAgentMain: { flex: 1, minWidth: 0, minHeight: 38, flexDirection: 'row', alignItems: 'center', gap: 7 }, fleetPanelName: { flex: 1, fontSize: 13, fontWeight: '700' }, ellipsisButton: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', borderRadius: 18 }, agentPopover: { borderRadius: 15, padding: 12, marginBottom: 6, gap: 8, shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 16, elevation: 7 }, agentMetaRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10 }, agentMetaLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 0.7 }, agentMetaValue: { fontSize: 11, fontWeight: '800' }, popoverActions: { flexDirection: 'row', gap: 8, marginTop: 2 }, popoverAction: { minHeight: 34, justifyContent: 'center', paddingHorizontal: 10, borderWidth: 1, borderColor: 'rgba(142,153,170,0.3)', borderRadius: 10 }, popoverActionText: { color: '#24D8FF', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 }, renameRow: { flexDirection: 'row', alignItems: 'center', gap: 7 }, renameInput: { flex: 1, minWidth: 0, height: 40, borderWidth: 1, borderColor: 'rgba(142,153,170,0.4)', borderRadius: 10, paddingHorizontal: 10, fontSize: 16, outlineStyle: 'none' as any }, confirmInterruptRow: { flexDirection: 'row', alignItems: 'center', gap: 9 }, confirmInterruptText: { flex: 1, fontSize: 11 }, popoverLink: { fontSize: 10, fontWeight: '800' }, agentActionMessage: { fontSize: 10, lineHeight: 14 }, accountRow: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: 5 }, accountIcon: { width: 20, fontSize: 22.8, textAlign: 'center' },
  settingsSheet: { position: 'absolute', left: 8, right: 8, bottom: 8, height: '90%', maxHeight: '92%', zIndex: 20, borderTopLeftRadius: 28, borderTopRightRadius: 28, borderBottomLeftRadius: 18, borderBottomRightRadius: 18, padding: 21, shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 30, elevation: 18 }, appearanceLoading: { flex: 1, borderRadius: 26 }, settingsClose: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', marginLeft: -7, marginTop: -7 }, settingsCloseText: { fontSize: 27, lineHeight: 30, fontWeight: '300' }, settingsTitle: { fontSize: 24, fontWeight: '700', marginTop: -3, marginBottom: 12 }, settingsScroll: { flex: 1 }, settingsScrollContent: { paddingBottom: 28 }, settingsSection: { marginTop: 22, paddingTop: 18, borderTopWidth: 1, borderTopColor: 'rgba(142,153,170,0.18)' }, settingsSectionHeader: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingVertical: 5 }, settingsSectionHeaderCopy: { flex: 1, minWidth: 0 }, settingsSectionTitle: { fontSize: 17, fontWeight: '800' }, settingsSectionSummary: { fontSize: 11, lineHeight: 16, marginTop: 2 }, settingsSectionChevron: { width: 30, textAlign: 'center', fontSize: 22, lineHeight: 24, fontWeight: '400' }, settingsSectionContent: { paddingTop: 6 }, settingsStatusGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 24 }, settingsStatus: { minWidth: 150, flexDirection: 'row', alignItems: 'center', gap: 10 }, statusDot: { width: 9, height: 9, borderRadius: 5 }, settingsLabel: { fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: '700' }, settingsValue: { fontSize: 15, fontWeight: '700', marginTop: 2 }, settingsError: { color: '#FFB4B2', fontSize: 12, marginTop: 12 }, settingsToggleRow: { maxWidth: 420, minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 16, marginTop: 16 }, settingsToggleCopy: { flex: 1 }, settingsToggleTitle: { fontSize: 15, fontWeight: '700' }, settingsToggleDescription: { fontSize: 11, lineHeight: 16, marginTop: 2 }, settingsUsageItem: { paddingVertical: 8 }, credentialBlock: { marginTop: 8, maxWidth: 520 }, credentialInputRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }, credentialInput: { flex: 1, minWidth: 0, minHeight: 38, borderWidth: 1, borderColor: 'rgba(142,153,170,0.38)', borderRadius: 10, paddingHorizontal: 10, fontSize: 16, outlineStyle: 'none' as any }, credentialSave: { minHeight: 38, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 10, backgroundColor: brand.cyan }, credentialSaveText: { color: brand.obsidian, fontSize: 10, fontWeight: '800' }, diagnosticsButton: { marginTop: 10, minHeight: 38, flexDirection: 'row', alignItems: 'center', maxWidth: 260 }, diagnosticsButtonText: { flex: 1, fontSize: 15, fontWeight: '700' }, diagnosticsArrow: { fontSize: 17 }, appearanceWindow: { borderRadius: 24, padding: 18, zIndex: 3, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 22, elevation: 20 }, appearanceHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }, appearanceTitle: { fontSize: 22, fontWeight: '800' }, appearanceClose: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' }, preferenceLabel: { fontSize: 10, fontWeight: '800', letterSpacing: 0.9, marginTop: 8, marginBottom: 8 }, optionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, optionPill: { minHeight: 36, justifyContent: 'center', borderRadius: 18, borderWidth: 1, borderColor: 'rgba(142,153,170,0.38)', paddingHorizontal: 13 }, voiceModeOption: { minWidth: 145, maxWidth: 290, minHeight: 54, justifyContent: 'center', borderRadius: 14, borderWidth: 1, borderColor: 'rgba(142,153,170,0.38)', paddingHorizontal: 12, paddingVertical: 7 }, voiceModeDescription: { fontSize: 10, lineHeight: 14, marginTop: 2 }, customBackgroundRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 12 }, customBackgroundPreview: { width: 56, height: 40, borderRadius: 8 }, customBackgroundCopy: { flex: 1, minWidth: 150 }, customBackgroundTitle: { fontSize: 12, fontWeight: '800' }, customBackgroundDescription: { fontSize: 10, lineHeight: 14, marginTop: 2 }, secondaryAction: { minHeight: 34, justifyContent: 'center', paddingHorizontal: 10, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,98,95,0.36)' }, secondaryActionText: { fontSize: 11, fontWeight: '800' }, uploadBackgroundButton: { minHeight: 40, justifyContent: 'center', alignItems: 'center', borderRadius: 12, marginTop: 12, borderWidth: 1, borderColor: 'rgba(142,153,170,0.36)' }, optionPillSelected: { backgroundColor: brand.cyan, borderColor: brand.cyan }, optionText: { fontSize: 12, fontWeight: '800' }, logoutButton: { minHeight: 40, marginTop: 22, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,98,95,0.55)', justifyContent: 'center', alignItems: 'center', maxWidth: 260 }, logoutButtonText: { color: brand.critical, fontFamily: 'monospace', fontSize: 10, fontWeight: '800', letterSpacing: 0.8 }
});
