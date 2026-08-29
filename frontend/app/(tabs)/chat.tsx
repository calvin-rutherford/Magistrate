import * as Clipboard from 'expo-clipboard';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Alert, Image, KeyboardAvoidingView, NativeScrollEvent, NativeSyntheticEvent, PanResponder, Platform, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, useColorScheme, useWindowDimensions, View } from 'react-native';
import Animated, { Easing, interpolate, useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AgentInfo, AuthProviderInfo, ExecutionHarness, fetchAgents, fetchAuthProviders, fetchExecutionCapabilities, fetchGitHubPRs, fetchHealth, fetchUnifiedAttention, GitHubPR, HealthInfo, sendCaptainPrompt, UnifiedAttentionRecord } from '../../src/api/client';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { displayAgentStatus, summarizeAgents } from '../../src/services/AgentStatus';
import { openExternalUrl } from '../../src/utils/externalLinks';

const markPaper = require('../../assets/images/magistrate-mark-paper-256.png');
const markInk = require('../../assets/images/magistrate-mark-ink-256.png');
const brand = { obsidian: '#05070A', command: '#111722', paper: '#F7F8FA', ink: '#11151B', mutedDark: '#8E99AA', mutedLight: '#667180', cyan: '#24D8FF', violet: '#8B6CFF', success: '#43D17A', attention: '#FFB347', critical: '#FF625F' };

type ChatMessage = { id: string; role: 'user' | 'assistant'; text: string; sentAt: Date };
type DrawerSection = 'attention' | 'fleet' | 'activity' | 'connections' | null;
type ModelSelection = { harness: string; model: string; label: string } | null;
const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const isDarkTheme = (scheme: string | null | undefined) => scheme !== 'light';
const optionId = (harness: string, model: string) => `${harness}-${model}`.replace(/[^A-Za-z0-9_-]/g, '-');
const providerLabel = (provider: string) => provider.toLowerCase() === 'firstmate' ? 'Magistrate' : provider;

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

function ModelMenu({ dark, harnesses, loading, error, open, selection, onToggle, onSelect }: {
  dark: boolean; harnesses: ExecutionHarness[]; loading: boolean; error: string | null; open: boolean;
  selection: ModelSelection; onToggle: () => void; onSelect: (selection: ModelSelection) => void;
}) {
  const text = dark ? '#F4F5F7' : brand.ink;
  const muted = dark ? brand.mutedDark : brand.mutedLight;
  return <View style={styles.modelControl}>
    <TouchableOpacity testID="model-menu-button" accessibilityRole="button" accessibilityLabel={`Model, ${selection?.label || 'current session'}`} accessibilityState={{ expanded: open }} onPress={onToggle} style={styles.modelButton}>
      <Text style={[styles.modelButtonText, { color: selection ? brand.cyan : muted }]}>model</Text>
    </TouchableOpacity>
    {open ? <View testID="model-menu" accessibilityViewIsModal style={[styles.modelMenu, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}>
      <Text style={[styles.menuTitle, { color: text }]}>Agent and variant</Text>
      <TouchableOpacity testID="model-option-current" accessibilityRole="button" accessibilityLabel="Use the running session model" accessibilityState={{ selected: selection === null }} onPress={() => onSelect(null)} style={styles.modelOption}>
        <View style={[styles.selectionDot, { backgroundColor: selection === null ? brand.cyan : 'transparent' }]} />
        <View style={styles.modelOptionCopy}><Text style={[styles.modelOptionTitle, { color: text }]}>Current session</Text><Text style={[styles.modelOptionMeta, { color: muted }]}>Uses the model already running on the backend</Text></View>
      </TouchableOpacity>
      <ScrollView style={styles.modelOptionsScroll} keyboardShouldPersistTaps="handled">
        {harnesses.map(harness => <View key={harness.id}>
          <Text style={[styles.harnessLabel, { color: muted }]}>{harness.label}</Text>
          {harness.models.map(model => {
            const selected = selection?.harness === harness.id && selection.model === model.id;
            return <TouchableOpacity key={model.id} testID={`model-option-${optionId(harness.id, model.id)}`} accessibilityRole="button" accessibilityLabel={`${harness.label}, ${model.label}`} accessibilityState={{ selected }} onPress={() => onSelect({ harness: harness.id, model: model.id, label: model.label })} style={styles.modelOption}>
              <View style={[styles.selectionDot, { backgroundColor: selected ? brand.cyan : 'transparent' }]} /><Text style={[styles.modelOptionTitle, { color: text }]}>{model.label}</Text>
            </TouchableOpacity>;
          })}
        </View>)}
        {loading ? <Text style={[styles.modelNotice, { color: muted }]}>Loading available variants…</Text> : null}
        {!loading && error ? <Text style={styles.modelError}>{error} Current session remains available.</Text> : null}
        {!loading && !error && harnesses.length === 0 ? <Text style={[styles.modelNotice, { color: muted }]}>No overrides are configured. Current session remains available.</Text> : null}
      </ScrollView>
    </View> : null}
  </View>;
}

function UserMessage({ message, textColor, selectable, onLongPress }: { message: ChatMessage; textColor: string; selectable: boolean; onLongPress: () => void }) {
  const timestamp = message.sentAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return <TouchableOpacity testID={`user-message-${message.id}`} accessibilityRole="button" accessibilityLabel={`Your message, sent ${timestamp}. Press and hold for actions.`} delayLongPress={2000} onLongPress={onLongPress} activeOpacity={0.92} style={styles.userMessage}>
    <Text testID={`user-message-text-${message.id}`} selectable={selectable} style={[styles.messageText, { color: textColor }]}>{message.text}</Text>
    <Text style={styles.messageTimestamp}>Sent {timestamp}</Text>
  </TouchableOpacity>;
}

export function ChatCanvas({ target = 'captain', onDrawerToggle = () => {}, drawerOpen = false }: { target?: string; onDrawerToggle?: () => void; drawerOpen?: boolean }) {
  const router = useRouter();
  const dark = isDarkTheme(useColorScheme());
  const text = dark ? '#F4F5F7' : brand.ink;
  const muted = dark ? brand.mutedDark : brand.mutedLight;
  const composerSurface = dark ? 'rgba(17,23,34,0.98)' : 'rgba(255,255,255,0.98)';
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [promptText, setPromptText] = useState('');
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [messageActionsId, setMessageActionsId] = useState<string | null>(null);
  const [selectableMessageId, setSelectableMessageId] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const [hasNewMessages, setHasNewMessages] = useState(false);
  const [micInline, setMicInline] = useState(false);
  const [harnesses, setHarnesses] = useState<ExecutionHarness[]>([]);
  const [modelSelection, setModelSelection] = useState<ModelSelection>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [capabilityLoading, setCapabilityLoading] = useState(true);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);
  const atBottomRef = useRef(true);
  const targetLabel = target === 'captain' ? 'Magistrate' : target;

  useEffect(() => {
    let mounted = true;
    fetchExecutionCapabilities().then(data => {
      if (!Array.isArray(data.harnesses)) throw new Error('Gateway returned an invalid execution inventory.');
      if (mounted) setHarnesses(data.harnesses.filter(harness => harness.verified));
    }).catch(error => { if (mounted) setCapabilityError(errorText(error, 'Execution options could not be loaded.')); })
      .finally(() => { if (mounted) setCapabilityLoading(false); });
    return () => { mounted = false; };
  }, []);

  const handleScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const atBottom = contentOffset.y + layoutMeasurement.height >= contentSize.height - 48;
    atBottomRef.current = atBottom; setIsScrolledUp(!atBottom); if (atBottom) setHasNewMessages(false);
  };
  const appendMessage = (message: ChatMessage) => {
    setMessages(current => [...current, message]);
    if (!atBottomRef.current) setHasNewMessages(true);
    requestAnimationFrame?.(() => { if (atBottomRef.current) scrollRef.current?.scrollToEnd({ animated: true }); });
  };
  const handleSend = async () => {
    const trimmed = promptText.trim();
    if (!trimmed) { router.push('/voice' as any); return; }
    const now = new Date();
    if (editingMessageId) {
      setMessages(current => current.map(message => message.id === editingMessageId ? { ...message, text: trimmed, sentAt: now } : message));
      setEditingMessageId(null);
    } else appendMessage({ id: `u-${Date.now()}`, role: 'user', text: trimmed, sentAt: now });
    setPromptText(''); setSendError(null); setIsThinking(true);
    try {
      const response = await sendCaptainPrompt(trimmed, 'iphone', target, modelSelection?.harness, modelSelection?.model);
      if (response?.status === 'error' || response?.error) throw new Error(response.error || 'The message was not accepted.');
    } catch (error) { setPromptText(trimmed); setSendError(errorText(error, 'The message could not be sent.')); }
    finally { setIsThinking(false); }
  };
  const activeMessage = messages.find(message => message.id === messageActionsId);
  const editMessage = () => {
    if (!activeMessage) return;
    setPromptText(activeMessage.text); setEditingMessageId(activeMessage.id); setMessageActionsId(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  };
  const copyMessage = async () => { if (activeMessage) await Clipboard.setStringAsync(activeMessage.text); setMessageActionsId(null); };
  const selectMessage = () => { if (activeMessage) setSelectableMessageId(activeMessage.id); setMessageActionsId(null); };

  return <KeyboardAvoidingView testID="branded-chat-shell" behavior={Platform.OS === 'ios' ? 'padding' : Platform.OS === 'android' ? 'height' : undefined} style={[styles.canvas, { backgroundColor: dark ? 'rgba(5,7,10,0.94)' : 'rgba(247,248,250,0.95)' }]}>
    <View style={styles.shellHeader}><TouchableOpacity testID="brand-drawer-toggle" accessibilityRole="button" accessibilityLabel={drawerOpen ? 'Collapse Magistrate drawer' : 'Open Magistrate drawer'} accessibilityState={{ expanded: drawerOpen }} onPress={onDrawerToggle} style={styles.logoButton} activeOpacity={0.72}><BrandMark dark={dark} /></TouchableOpacity></View>
    <ScrollView ref={scrollRef} testID="chat-history" style={styles.chatHistory} contentContainerStyle={styles.chatHistoryContent} onScroll={handleScroll} scrollEventThrottle={16} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive" automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'} accessibilityLabel={`${targetLabel} conversation history`}>
      {messages.map(message => message.role === 'user' ? <UserMessage key={message.id} message={message} textColor={text} selectable={selectableMessageId === message.id} onLongPress={() => setMessageActionsId(message.id)} /> : <View key={message.id} style={styles.assistantMessage}><Text selectable style={[styles.messageText, { color: text }]}>{message.text}</Text></View>)}
    </ScrollView>
    {(hasNewMessages || isScrolledUp) ? <TouchableOpacity testID="jump-to-latest" accessibilityRole="button" accessibilityLabel="Jump to latest message" style={styles.jumpButton} onPress={() => { atBottomRef.current = true; setHasNewMessages(false); setIsScrolledUp(false); scrollRef.current?.scrollToEnd({ animated: true }); }}><Text style={styles.jumpText}>{hasNewMessages ? '↓ New message' : '↓ Latest'}</Text></TouchableOpacity> : null}
    {messageActionsId ? <View testID="message-actions" accessibilityViewIsModal style={[styles.messageActions, { backgroundColor: dark ? brand.command : '#FFFFFF' }]}>
      <TouchableOpacity accessibilityRole="button" onPress={editMessage} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Edit</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" onPress={() => void copyMessage()} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Copy</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" onPress={selectMessage} style={styles.messageAction}><Text style={[styles.messageActionText, { color: text }]}>Select text</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close message actions" onPress={() => setMessageActionsId(null)} style={styles.messageAction}><Text style={[styles.messageActionText, { color: muted }]}>×</Text></TouchableOpacity>
    </View> : null}
    {micInline ? <View style={[styles.inlineMic, { backgroundColor: composerSurface }]} accessibilityLabel="Inline microphone levels"><Text style={[styles.inlineMicMuted, { color: muted }]}>×</Text>{[0.35, 0.75, 0.5, 0.95, 0.62, 0.82].map((height, index) => <View key={index} style={[styles.levelBar, { height: 24 * height, backgroundColor: index > 3 ? brand.violet : brand.cyan }]} />)}<Text style={[styles.inlineMicMuted, { color: muted }]}>✓</Text></View> : null}
    <View style={[styles.composer, { backgroundColor: composerSurface }]}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Attach file" style={styles.composerIconButton}><Text style={[styles.composerIconText, { color: muted }]}>＋</Text></TouchableOpacity>
      <ModelMenu dark={dark} harnesses={harnesses} loading={capabilityLoading} error={capabilityError} open={modelMenuOpen} selection={modelSelection} onToggle={() => setModelMenuOpen(value => !value)} onSelect={selection => { setModelSelection(selection); setModelMenuOpen(false); setSendError(null); }} />
      <TextInput ref={inputRef} testID="captain-prompt" style={[styles.composerInput, { color: text }]} placeholder={editingMessageId ? 'Edit message…' : `Message ${targetLabel}…`} placeholderTextColor={muted} value={promptText} onChangeText={setPromptText} onSubmitEditing={() => void handleSend()} returnKeyType="send" editable={!isThinking} accessibilityLabel={`Message ${targetLabel}`} />
      <TouchableOpacity testID="inline-mic-button" accessibilityRole="button" accessibilityLabel={micInline ? 'Stop microphone' : 'Start microphone'} accessibilityState={{ selected: micInline }} style={styles.composerIconButton} onPress={() => setMicInline(value => !value)}><Text style={[styles.micIcon, { color: micInline ? brand.cyan : muted }]}>♩</Text></TouchableOpacity>
      <TouchableOpacity testID="send-captain-prompt" accessibilityRole="button" accessibilityLabel={promptText.trim() ? `Send message to ${targetLabel}` : 'Open voice mode'} accessibilityState={{ disabled: isThinking, busy: isThinking }} onPress={() => void handleSend()} disabled={isThinking} style={[styles.sendButton, isThinking ? styles.disabled : undefined]}><Text style={promptText.trim() ? styles.sendArrow : styles.voiceIcon}>{isThinking ? '…' : promptText.trim() ? '↑' : '≋'}</Text></TouchableOpacity>
    </View>
    <View style={styles.composerStatus} accessibilityLiveRegion="polite">{editingMessageId ? <Text style={styles.editingLabel}>Editing message</Text> : null}{sendError ? <Text testID="captain-send-error" style={styles.sendError}>{sendError}</Text> : null}</View>
  </KeyboardAvoidingView>;
}

function PanelText({ text, muted }: { text: string; muted: string }) { return <Text style={[styles.panelText, { color: muted }]}>{text}</Text>; }

function DrawerPanel({ open, dark, isNarrow, animatedStyle, panHandlers, activeSection, setActiveSection, onOpenSettings, agents, attention, prs, providers, errors, loading }: {
  open: boolean; dark: boolean; isNarrow: boolean; animatedStyle: object; panHandlers: object; activeSection: DrawerSection; setActiveSection: (section: DrawerSection) => void; onOpenSettings: () => void;
  agents: AgentInfo[]; attention: UnifiedAttentionRecord[]; prs: GitHubPR[]; providers: AuthProviderInfo[]; errors: { agents?: string | null; attention?: string | null; prs?: string | null; providers?: string | null }; loading: boolean;
}) {
  const router = useRouter();
  const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const fleet = summarizeAgents(agents); const activeAttention = attention.filter(item => item.requires_action !== false);
  const toggleSection = (section: DrawerSection) => setActiveSection(activeSection === section ? null : section);
  const openAttentionItem = async (item: UnifiedAttentionRecord) => {
    if (item.url?.startsWith('/')) router.push(item.url as any);
    else { const result = await openExternalUrl(item.external_url || item.url); if (!result.ok) Alert.alert('Unable to open attention item', result.message); }
  };
  const rows = [{ key: 'attention' as const, icon: '!', title: 'Attention', count: activeAttention.length }, { key: 'fleet' as const, icon: '⌘', title: 'Fleet Summary', count: agents.length }, { key: 'activity' as const, icon: '↗', title: 'Recent Activity' }, { key: 'connections' as const, icon: '⌁', title: 'Connections' }];
  return <Animated.View accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="magistrate-drawer" style={[styles.drawer, isNarrow ? styles.drawerMobile : styles.drawerDesktop, { backgroundColor: dark ? 'rgba(10,14,20,0.98)' : 'rgba(255,255,255,0.98)' }, animatedStyle]} {...panHandlers}>
    <Text style={[styles.drawerWordmark, { color: text }]}>Magistrate</Text>
    <ScrollView style={styles.drawerScroll} contentContainerStyle={styles.drawerScrollContent} keyboardShouldPersistTaps="handled">
      {rows.map(row => <View key={row.key}>
        <TouchableOpacity testID={`drawer-section-${row.key}`} accessibilityRole="button" accessibilityLabel={`${row.title} section`} accessibilityState={{ expanded: activeSection === row.key }} onPress={() => toggleSection(row.key)} style={styles.drawerRow}>
          <Text style={[styles.drawerIcon, { color: muted }]}>{row.icon}</Text><Text style={[styles.drawerRowText, { color: text }]}>{row.title}</Text>{typeof row.count === 'number' ? <Text style={[styles.drawerCount, { color: muted }]}>{row.count}</Text> : null}<Text style={[styles.chevron, { color: muted }]}>{activeSection === row.key ? '⌃' : '⌄'}</Text>
        </TouchableOpacity>
        {activeSection === row.key ? <View testID={`drawer-panel-${row.key}`} style={styles.sectionPanel}>{row.key === 'attention' ? (
          loading ? <PanelText text="Loading attention…" muted={muted} /> : errors.attention ? <PanelText text={errors.attention} muted={brand.critical} /> : activeAttention.length === 0 ? <PanelText text="Nothing requires your attention." muted={muted} /> : activeAttention.slice(0, 5).map(item => <TouchableOpacity key={item.id} testID={`attention-item-${item.id}`} onPress={() => void openAttentionItem(item)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{providerLabel(item.provider)} · {item.subtitle}</Text></TouchableOpacity>)
        ) : row.key === 'fleet' ? (
          loading ? <PanelText text="Loading fleet…" muted={muted} /> : errors.agents ? <PanelText text={errors.agents} muted={brand.critical} /> : agents.length === 0 ? <PanelText text="No live agent sessions are available." muted={muted} /> : fleet.ordered.map(({ agent, displayStatus }) => <TouchableOpacity key={agent.id} onPress={() => router.push({ pathname: '/chat', params: { agentId: agent.id } } as any)} style={styles.fleetPanelRow}><View style={[styles.tinyDot, { backgroundColor: statusColor(agent.status) }]} /><Text style={[styles.fleetPanelName, { color: text }]}>{agent.name || agent.id}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{displayAgentStatus(displayStatus)}</Text></TouchableOpacity>)
        ) : row.key === 'activity' ? (
          errors.prs ? <PanelText text={errors.prs} muted={brand.critical} /> : prs.length === 0 ? <PanelText text="No meaningful recent activity is available." muted={muted} /> : prs.slice(0, 4).map(pr => <TouchableOpacity key={pr.id || pr.number} onPress={() => router.push(`/pr-detail?number=${pr.number}` as any)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>PR #{pr.number}: {pr.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{pr.repository} · {pr.review_status}</Text></TouchableOpacity>)
        ) : errors.providers ? <PanelText text={errors.providers} muted={brand.critical} /> : providers.length === 0 ? <PanelText text="No connected account data is available." muted={muted} /> : providers.map(provider => <View key={provider.provider} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{provider.provider}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{provider.status}{provider.username ? ` · ${provider.username}` : ''}</Text></View>)}</View> : null}
      </View>)}
    </ScrollView>
    <View style={styles.drawerBottom}><TouchableOpacity testID="settings-open" accessibilityRole="button" accessibilityLabel="Open Account settings" onPress={onOpenSettings} activeOpacity={0.75} style={styles.accountRow}><Text style={[styles.accountIcon, { color: muted }]}>○</Text><Text style={[styles.drawerRowText, { color: text }]}>Account</Text><View style={styles.gearButton}><Text style={[styles.gearIcon, { color: muted }]}>⚙</Text></View></TouchableOpacity></View>
  </Animated.View>;
}

function SettingsSheet({ open, dark, animatedStyle, health, loading, error, onClose }: { open: boolean; dark: boolean; animatedStyle: object; health: HealthInfo | null; loading: boolean; error: string | null; onClose: () => void }) {
  const router = useRouter(); const text = dark ? '#F4F5F7' : brand.ink; const muted = dark ? brand.mutedDark : brand.mutedLight;
  const network = health?.status === 'healthy'; const runtime = Boolean(health?.herdr_socket_connected);
  return <Animated.View pointerEvents={open ? 'auto' : 'none'} accessibilityElementsHidden={!open} importantForAccessibility={open ? 'auto' : 'no-hide-descendants'} testID="settings-sheet" style={[styles.settingsSheet, { backgroundColor: dark ? brand.command : '#FFFFFF' }, animatedStyle]}>
    <TouchableOpacity testID="settings-close" accessibilityRole="button" accessibilityLabel="Close settings" onPress={onClose} style={styles.settingsClose}><Text style={[styles.settingsCloseText, { color: text }]}>×</Text></TouchableOpacity>
    <Text style={[styles.settingsTitle, { color: text }]}>Settings</Text>
    <View style={styles.settingsStatusGrid}><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: error ? brand.critical : loading ? brand.attention : network ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Network</Text><Text testID="settings-network-status" style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : error ? 'Unavailable' : network ? 'Connected' : 'Degraded'}</Text></View></View><View style={styles.settingsStatus}><View style={[styles.statusDot, { backgroundColor: runtime ? brand.success : brand.attention }]} /><View><Text style={[styles.settingsLabel, { color: muted }]}>Runtime</Text><Text style={[styles.settingsValue, { color: text }]}>{loading ? 'Checking…' : runtime ? 'Live' : 'Unavailable'}</Text></View></View></View>
    {error ? <Text style={styles.settingsError}>{error}</Text> : null}
    <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open diagnostics" onPress={() => { onClose(); router.push('/diagnostics' as any); }} style={styles.diagnosticsButton}><Text style={[styles.diagnosticsButtonText, { color: text }]}>Diagnostics</Text><Text style={[styles.diagnosticsArrow, { color: muted }]}>↗</Text></TouchableOpacity>
  </Animated.View>;
}

export default function ChatScreen() {
  const { agentId } = useLocalSearchParams<{ agentId?: string | string[] }>(); const target = Array.isArray(agentId) ? agentId[0] : agentId;
  const dark = isDarkTheme(useColorScheme()); const { width } = useWindowDimensions(); const isNarrow = width < 720; const drawerWidth = Math.min(isNarrow ? width * 0.82 : 310, 330);
  const [drawerOpen, setDrawerOpen] = useState(false); const [settingsOpen, setSettingsOpen] = useState(false); const [activeSection, setActiveSection] = useState<DrawerSection>(null);
  const [agents, setAgents] = useState<AgentInfo[]>([]); const [attention, setAttention] = useState<UnifiedAttentionRecord[]>([]); const [prs, setPrs] = useState<GitHubPR[]>([]); const [providers, setProviders] = useState<AuthProviderInfo[]>([]); const [health, setHealth] = useState<HealthInfo | null>(null);
  const [loading, setLoading] = useState(true); const [healthLoading, setHealthLoading] = useState(true); const [healthError, setHealthError] = useState<string | null>(null); const [reducedMotion, setReducedMotion] = useState(false);
  const [errors, setErrors] = useState<{ agents?: string | null; attention?: string | null; prs?: string | null; providers?: string | null }>({});
  const drawerProgress = useSharedValue(0); const settingsProgress = useSharedValue(0);
  useEffect(() => { AccessibilityInfo.isReduceMotionEnabled().then(setReducedMotion); const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReducedMotion); return () => sub.remove(); }, []);
  useEffect(() => { drawerProgress.value = withTiming(drawerOpen ? 1 : 0, { duration: reducedMotion ? 1 : 260, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [drawerOpen, drawerProgress, reducedMotion]);
  useEffect(() => { settingsProgress.value = withTiming(settingsOpen ? 1 : 0, { duration: reducedMotion ? 1 : 300, easing: Easing.bezier(0.2, 0.8, 0.2, 1) }); }, [settingsOpen, settingsProgress, reducedMotion]);
  useEffect(() => {
    let mounted = true;
    Promise.allSettled([fetchAgents(), fetchUnifiedAttention(), fetchGitHubPRs(), fetchAuthProviders(), fetchHealth()]).then(([agentResult, attentionResult, prsResult, providerResult, healthResult]) => {
      if (!mounted) return;
      setErrors({ agents: agentResult.status === 'rejected' ? errorText(agentResult.reason, 'Agent data could not be loaded.') : null, attention: attentionResult.status === 'rejected' ? errorText(attentionResult.reason, 'Attention data could not be loaded.') : null, prs: prsResult.status === 'rejected' ? errorText(prsResult.reason, 'Recent activity could not be loaded.') : null, providers: providerResult.status === 'rejected' ? errorText(providerResult.reason, 'Connections data could not be loaded.') : null });
      if (agentResult.status === 'fulfilled') setAgents(agentResult.value); if (attentionResult.status === 'fulfilled') setAttention(attentionResult.value); if (prsResult.status === 'fulfilled') setPrs(prsResult.value.items); if (providerResult.status === 'fulfilled') setProviders(providerResult.value);
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value); else setHealthError(errorText(healthResult.reason, 'Network status could not be loaded.'));
      setLoading(false); setHealthLoading(false);
    }); return () => { mounted = false; };
  }, []);
  const drawerAnimatedStyle = useAnimatedStyle(() => ({ opacity: drawerProgress.value, transform: [{ translateX: interpolate(drawerProgress.value, [0, 1], [-(drawerWidth + 70), 0]) }] }), [drawerWidth]);
  const chatAnimatedStyle = useAnimatedStyle(() => ({ transform: [{ translateX: isNarrow ? drawerProgress.value * drawerWidth : 0 }] }), [drawerWidth, isNarrow]);
  const settingsAnimatedStyle = useAnimatedStyle(() => ({ opacity: settingsProgress.value, transform: [{ translateY: interpolate(settingsProgress.value, [0, 1], [420, 0]) }] }));
  const swipeToClose = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => isNarrow && drawerOpen && g.dx < -8 && Math.abs(g.dx) > Math.abs(g.dy),
    onPanResponderRelease: (_, g) => { if (g.dx < -55 || g.vx < -0.35) setDrawerOpen(false); },
  }), [drawerOpen, isNarrow]);
  return <EnvironmentBackground hideBottomControls><SafeAreaView style={styles.page}>
    <DrawerPanel open={drawerOpen} dark={dark} isNarrow={isNarrow} animatedStyle={drawerAnimatedStyle} panHandlers={isNarrow ? swipeToClose.panHandlers : {}} activeSection={activeSection} setActiveSection={setActiveSection} onOpenSettings={() => setSettingsOpen(true)} agents={agents} attention={attention} prs={prs} providers={providers} errors={errors} loading={loading} />
    <Animated.View style={[styles.chatStage, chatAnimatedStyle]}><ChatCanvas target={target || 'captain'} drawerOpen={drawerOpen} onDrawerToggle={() => setDrawerOpen(value => !value)} /></Animated.View>
    <SettingsSheet open={settingsOpen} dark={dark} animatedStyle={settingsAnimatedStyle} health={health} loading={healthLoading} error={healthError} onClose={() => setSettingsOpen(false)} />
  </SafeAreaView></EnvironmentBackground>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minWidth: 0, overflow: 'hidden' }, chatStage: { flex: 1, minWidth: 0, padding: 8, zIndex: 1 }, canvas: { flex: 1, minWidth: 0, borderRadius: 26, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 8, overflow: 'hidden' },
  shellHeader: { height: 48, flexDirection: 'row', alignItems: 'center', zIndex: 3 }, logoButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' }, mark: { width: 34, height: 34 }, tinyDot: { width: 8, height: 8, borderRadius: 4 },
  chatHistory: { flex: 1, minHeight: 0 }, chatHistoryContent: { flexGrow: 1, justifyContent: 'flex-end', paddingTop: 22, paddingHorizontal: 22, paddingBottom: 20, gap: 14 }, userMessage: { maxWidth: 680, alignSelf: 'flex-end', paddingVertical: 11, paddingHorizontal: 16, borderRadius: 22, backgroundColor: 'rgba(36,216,255,0.15)' }, assistantMessage: { maxWidth: 680, alignSelf: 'flex-start', paddingVertical: 8, paddingHorizontal: 2 }, messageText: { fontSize: 16, lineHeight: 23 }, messageTimestamp: { color: 'rgba(142,153,170,0.9)', fontSize: 10, marginTop: 6, textAlign: 'right' },
  jumpButton: { position: 'absolute', alignSelf: 'center', bottom: 90, backgroundColor: brand.cyan, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8, zIndex: 5 }, jumpText: { color: brand.obsidian, fontSize: 12, fontWeight: '800' },
  messageActions: { position: 'absolute', right: 24, bottom: 82, flexDirection: 'row', borderRadius: 18, padding: 4, zIndex: 12, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 20, elevation: 8 }, messageAction: { minWidth: 52, height: 40, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8 }, messageActionText: { fontSize: 13, fontWeight: '700' },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 4, minHeight: 60, borderRadius: 30, paddingHorizontal: 9, paddingVertical: 7, marginHorizontal: 8, zIndex: 10 }, composerIconButton: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, composerIconText: { fontSize: 21, fontWeight: '500' }, composerInput: { flex: 1, minWidth: 0, fontSize: 16, paddingVertical: 8, outlineStyle: 'none' as any }, micIcon: { fontSize: 20, fontWeight: '700' }, sendButton: { width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center', backgroundColor: brand.cyan }, sendArrow: { color: brand.obsidian, fontSize: 22, fontWeight: '800' }, voiceIcon: { color: brand.violet, fontSize: 27, lineHeight: 28, fontWeight: '700' }, disabled: { opacity: 0.55 }, composerStatus: { minHeight: 22, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 18 }, editingLabel: { color: brand.cyan, fontSize: 11, fontWeight: '700' }, sendError: { color: '#FFB4B2', fontSize: 12, flex: 1, textAlign: 'right' }, inlineMic: { position: 'absolute', bottom: 78, right: 68, flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 10, paddingVertical: 7, borderRadius: 999, zIndex: 11 }, inlineMicMuted: { fontSize: 16 }, levelBar: { width: 3, borderRadius: 2 },
  modelControl: { width: 52, zIndex: 15 }, modelButton: { height: 34, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 3 }, modelButtonText: { fontSize: 12, fontWeight: '700', textTransform: 'lowercase' }, modelMenu: { position: 'absolute', left: -46, bottom: 44, width: 280, maxHeight: 350, borderRadius: 22, padding: 12, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 24, elevation: 10 }, modelOptionsScroll: { maxHeight: 235 }, menuTitle: { fontSize: 14, fontWeight: '800', marginBottom: 7, paddingHorizontal: 7 }, harnessLabel: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8, textTransform: 'uppercase', paddingHorizontal: 7, paddingTop: 8 }, modelOption: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 9, paddingHorizontal: 7, paddingVertical: 6 }, modelOptionCopy: { flex: 1 }, modelOptionTitle: { fontSize: 13, fontWeight: '700' }, modelOptionMeta: { fontSize: 11, lineHeight: 15, marginTop: 2 }, selectionDot: { width: 7, height: 7, borderRadius: 4 }, modelNotice: { fontSize: 11, lineHeight: 16, paddingHorizontal: 7, paddingTop: 8 }, modelError: { color: '#FFB4B2', fontSize: 11, lineHeight: 16, paddingHorizontal: 7, paddingTop: 8 },
  drawer: { position: 'absolute', top: 8, bottom: 8, width: 310, zIndex: 10, borderRadius: 24, padding: 14, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.28, shadowRadius: 28, elevation: 12 }, drawerDesktop: { left: 58 }, drawerMobile: { left: 8, width: '82%' }, drawerWordmark: { fontFamily: Platform.select({ web: 'Bodoni Moda, Times New Roman, serif', default: undefined }), fontSize: 25, lineHeight: 32, fontWeight: '500', marginLeft: 4, marginBottom: 13 }, drawerScroll: { flex: 1, minHeight: 0 }, drawerScrollContent: { paddingBottom: 12 }, drawerRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 4 }, drawerIcon: { width: 20, fontSize: 14, fontWeight: '800', textAlign: 'center' }, drawerRowText: { flex: 1, fontSize: 15, fontWeight: '700', textAlign: 'left' }, drawerCount: { fontSize: 11, fontWeight: '800' }, chevron: { width: 18, fontSize: 13, textAlign: 'center' }, sectionPanel: { paddingLeft: 30, paddingRight: 4, paddingBottom: 10, gap: 7 }, panelText: { fontSize: 13, lineHeight: 19 }, panelItem: { paddingVertical: 6 }, panelItemTitle: { fontSize: 13, fontWeight: '800', marginBottom: 2 }, panelItemMeta: { fontSize: 12, lineHeight: 17 }, fleetPanelRow: { flexDirection: 'row', alignItems: 'center', gap: 7, paddingVertical: 6 }, fleetPanelName: { flex: 1, fontSize: 13, fontWeight: '700' }, drawerBottom: { paddingTop: 6 }, accountRow: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: 5 }, accountIcon: { width: 20, fontSize: 19, textAlign: 'center' }, gearButton: { width: 42, height: 42, alignItems: 'center', justifyContent: 'center' }, gearIcon: { fontSize: 17 },
  settingsSheet: { position: 'absolute', left: 8, right: 8, bottom: 8, height: '34%', minHeight: 250, zIndex: 20, borderTopLeftRadius: 28, borderTopRightRadius: 28, borderBottomLeftRadius: 18, borderBottomRightRadius: 18, padding: 18, shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 30, elevation: 18 }, settingsClose: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', marginLeft: -7, marginTop: -7 }, settingsCloseText: { fontSize: 27, lineHeight: 30, fontWeight: '300' }, settingsTitle: { fontSize: 24, fontWeight: '700', marginTop: -3, marginBottom: 18 }, settingsStatusGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 24 }, settingsStatus: { minWidth: 150, flexDirection: 'row', alignItems: 'center', gap: 10 }, statusDot: { width: 9, height: 9, borderRadius: 5 }, settingsLabel: { fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: '700' }, settingsValue: { fontSize: 15, fontWeight: '700', marginTop: 2 }, settingsError: { color: '#FFB4B2', fontSize: 12, marginTop: 12 }, diagnosticsButton: { marginTop: 20, minHeight: 44, flexDirection: 'row', alignItems: 'center', maxWidth: 260 }, diagnosticsButtonText: { flex: 1, fontSize: 15, fontWeight: '700' }, diagnosticsArrow: { fontSize: 17 },
});
