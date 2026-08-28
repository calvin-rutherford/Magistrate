import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Image, NativeScrollEvent, NativeSyntheticEvent, Platform, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, useColorScheme, useWindowDimensions, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { CapabilitySelect } from '../../src/components/CapabilitySelect';
import { AgentInfo, AuthProviderInfo, ExecutionHarness, fetchAgents, fetchAuthProviders, fetchCaptainOutput, fetchExecutionCapabilities, fetchGitHubPRs, fetchUnifiedAttention, GitHubPR, sendCaptainPrompt, UnifiedAttentionRecord } from '../../src/api/client';
import { displayAgentStatus, summarizeAgents } from '../../src/services/AgentStatus';
import { openExternalUrl } from '../../src/utils/externalLinks';

const markPaper = require('../../assets/images/magistrate-mark-paper-256.png');
const markInk = require('../../assets/images/magistrate-mark-ink-256.png');

const brand = {
  obsidian: '#05070A',
  command: '#111722',
  paper: '#F7F8FA',
  ink: '#11151B',
  surfaceGray: '#EEF1F4',
  mutedDark: '#8E99AA',
  mutedLight: '#667180',
  borderDark: '#2A3542',
  borderLight: '#D5DAE2',
  cyan: '#24D8FF',
  violet: '#8B6CFF',
  success: '#43D17A',
  attention: '#FFB347',
  critical: '#FF625F'
};

type ChatMessage = { id: string; role: 'user' | 'assistant' | 'system'; text: string; meta?: string };
type DrawerSection = 'attention' | 'fleet' | 'activity' | 'connections' | 'settings' | null;

const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const isDarkTheme = (scheme: string | null | undefined) => scheme !== 'light';

function statusColor(status?: string | null) {
  const normalized = (status || '').toLowerCase();
  if (['working', 'running', 'active', 'executing'].includes(normalized)) return brand.success;
  if (['blocked', 'failed', 'error'].includes(normalized)) return brand.critical;
  if (['waiting', 'paused'].includes(normalized)) return brand.attention;
  return brand.mutedDark;
}

function BrandMark({ dark, size = 34 }: { dark: boolean; size?: number }) {
  return <Image source={dark ? markPaper : markInk} style={{ width: size, height: size }} resizeMode="contain" accessibilityIgnoresInvertColors />;
}

export function ChatCanvas({ target = 'captain', onDrawerToggle = () => {}, drawerOpen = false }: { target?: string; onDrawerToggle?: () => void; drawerOpen?: boolean }) {
  const router = useRouter();
  const colorScheme = useColorScheme();
  const dark = isDarkTheme(colorScheme);
  const text = dark ? '#F4F5F7' : brand.ink;
  const muted = dark ? brand.mutedDark : brand.mutedLight;
  const userBubble = dark ? 'rgba(17,23,34,0.86)' : brand.surfaceGray;
  const surface = dark ? 'rgba(10,14,20,0.72)' : 'rgba(255,255,255,0.82)';
  const border = dark ? brand.borderDark : brand.borderLight;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [promptText, setPromptText] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [outputPreview, setOutputPreview] = useState<string[]>([]);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const [hasNewMessages, setHasNewMessages] = useState(false);
  const [micInline, setMicInline] = useState(false);
  const [harnesses, setHarnesses] = useState<ExecutionHarness[]>([]);
  const [selectedHarness, setSelectedHarness] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [capabilityLoading, setCapabilityLoading] = useState(true);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);
  const atBottomRef = useRef(true);

  const targetLabel = target === 'captain' ? 'Firstmate' : target;

  const loadDiagnostics = useCallback(async () => {
    try {
      const data = await fetchCaptainOutput();
      const lines = (data.output || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter(Boolean);
      setOutputPreview(lines.slice(-160));
    } catch (error) {
      setOutputPreview([errorText(error, 'Diagnostics could not be loaded.')]);
    }
  }, []);

  useEffect(() => { void loadDiagnostics(); const interval = setInterval(loadDiagnostics, 5000); return () => clearInterval(interval); }, [loadDiagnostics]);

  useEffect(() => {
    let mounted = true;
    fetchExecutionCapabilities()
      .then(data => {
        if (!Array.isArray(data.harnesses)) throw new Error('Gateway returned an invalid execution inventory.');
        const verifiedHarnesses = data.harnesses.filter(harness => harness.verified);
        if (!mounted) return;
        setHarnesses(verifiedHarnesses);
        const firstHarness = verifiedHarnesses[0];
        setSelectedHarness(firstHarness?.id || '');
        setSelectedModel(firstHarness?.models[0]?.id || '');
      })
      .catch(error => { if (mounted) setCapabilityError(errorText(error, 'Execution options could not be loaded.')); })
      .finally(() => { if (mounted) setCapabilityLoading(false); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const recoverComposerFocus = (event: KeyboardEvent) => {
      const active = document.activeElement;
      if (active && active !== document.body) return;
      if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) return;
      inputRef.current?.focus();
      setPromptText(value => value + event.key);
      event.preventDefault();
    };
    window.addEventListener('keydown', recoverComposerFocus, true);
    return () => window.removeEventListener('keydown', recoverComposerFocus, true);
  }, []);

  const selectedHarnessOption = harnesses.find(harness => harness.id === selectedHarness);
  const models = selectedHarnessOption?.models || [];

  const handleScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const atBottom = contentOffset.y + layoutMeasurement.height >= contentSize.height - 48;
    atBottomRef.current = atBottom;
    setIsScrolledUp(!atBottom);
    if (atBottom) setHasNewMessages(false);
  };

  const appendMessage = (message: ChatMessage) => {
    setMessages(current => [...current, message]);
    if (!atBottomRef.current) setHasNewMessages(true);
    requestAnimationFrame?.(() => { if (atBottomRef.current) scrollRef.current?.scrollToEnd({ animated: true }); });
  };

  const handleSend = async () => {
    const trimmed = promptText.trim();
    if (!trimmed) {
      router.push('/voice' as any);
      return;
    }
    if (capabilityLoading) { setSendError('Execution options are still loading.'); return; }
    if (capabilityError) { setSendError('Execution options are unavailable.'); return; }
    if (!selectedHarness || !selectedModel) { setSendError('Select a verified harness and model before sending.'); return; }
    setPromptText('');
    setSendError(null);
    setIsThinking(true);
    appendMessage({ id: `u-${Date.now()}`, role: 'user', text: trimmed });
    try {
      const response = await sendCaptainPrompt(trimmed, 'iphone', target, selectedHarness, selectedModel);
      if (response?.status === 'error' || response?.error) throw new Error(response.error || 'The prompt was not accepted.');
      appendMessage({ id: `s-${Date.now()}`, role: 'system', meta: 'Submitted', text: 'Sent to Firstmate. Waiting for structured conversation output from the runner.' });
      void loadDiagnostics();
    } catch (error) {
      setPromptText(trimmed);
      setSendError(errorText(error, 'The prompt could not be sent.'));
      appendMessage({ id: `e-${Date.now()}`, role: 'system', meta: 'Send failed', text: errorText(error, 'The prompt could not be sent.') });
    } finally {
      setIsThinking(false);
    }
  };

  const composerIcon = promptText.trim() ? '↑' : '◉';

  return (
    <View testID="branded-chat-shell" style={styles.canvas}>
      <View style={styles.shellHeader}>
        <TouchableOpacity
          testID="brand-drawer-toggle"
          accessibilityRole="button"
          accessibilityLabel={drawerOpen ? 'Collapse Magistrate drawer' : 'Open Magistrate drawer'}
          accessibilityState={{ expanded: drawerOpen }}
          onPress={onDrawerToggle}
          style={[styles.logoButton, { backgroundColor: surface }]}
          activeOpacity={0.8}
        >
          <BrandMark dark={dark} size={32} />
        </TouchableOpacity>
        <View style={styles.headerControls}>
          <View style={styles.compactSelectWrap}>
            <CapabilitySelect
              testID="harness-select"
              label="MODEL"
              value={selectedHarness}
              options={harnesses.map(harness => ({ id: harness.id, label: harness.label }))}
              loading={capabilityLoading}
              error={capabilityError}
              emptyMessage="No verified models configured."
              disabled={isThinking}
              onChange={value => {
                const harness = harnesses.find(option => option.id === value);
                setSelectedHarness(value);
                setSelectedModel(harness?.models[0]?.id || '');
                setSendError(null);
              }}
            />
          </View>
          <View style={styles.compactSelectWrap}>
            <CapabilitySelect
              testID="model-select"
              label="VARIANT"
              value={selectedModel}
              options={models}
              loading={capabilityLoading}
              error={capabilityError || (selectedHarness && harnesses.length > 0 && models.length === 0 ? 'No variants are available for this model.' : null)}
              emptyMessage={selectedHarness ? 'No variants available.' : 'Select a model first.'}
              disabled={isThinking || !selectedHarness}
              onChange={value => { setSelectedModel(value); setSendError(null); }}
            />
          </View>
          <View style={[styles.liveChip, { borderColor: border }]}><View style={[styles.tinyDot, { backgroundColor: isThinking ? brand.violet : brand.success }]} /><Text style={[styles.liveText, { color: muted }]}>{isThinking ? 'Thinking' : 'Live'}</Text></View>
        </View>
      </View>

      <ScrollView ref={scrollRef} testID="chat-history" style={styles.chatHistory} contentContainerStyle={styles.chatHistoryContent} onScroll={handleScroll} scrollEventThrottle={16} keyboardShouldPersistTaps="handled" accessibilityLabel={`${targetLabel} conversation history`}>
        {messages.length === 0 ? (
          <View style={styles.emptyConversation}>
            <Text style={[styles.conversationEyebrow, { color: muted }]}>Firstmate / Conversation</Text>
            <Text testID="chat-target" style={[styles.conversationTitle, { color: text }]}>{targetLabel}</Text>
            <Text style={[styles.emptyCopy, { color: muted }]}>Ask Firstmate anything. Structured replies will appear here when the runner provides them; raw terminal output stays in Diagnostics.</Text>
          </View>
        ) : null}
        {messages.map(message => (
          <View key={message.id} style={[styles.messageBubble, message.role === 'user' ? styles.userMessage : styles.assistantMessage, { backgroundColor: message.role === 'user' ? userBubble : 'transparent' }]}>
            {message.role !== 'user' ? <Text style={[styles.messageSpeaker, { color: text }]}><View style={[styles.tinyDot, { backgroundColor: message.role === 'system' ? brand.cyan : brand.success }]} /> {message.meta || targetLabel}</Text> : null}
            <Text style={[styles.messageText, { color: text }]}>{message.text}</Text>
          </View>
        ))}
        {diagnosticsOpen ? (
          <View testID="terminal-scroll" style={[styles.diagnosticsBox, { borderColor: border, backgroundColor: dark ? '#05070A' : '#FFFFFF' }]}>
            <Text style={[styles.diagnosticsTitle, { color: muted }]}>Diagnostics · raw runner output</Text>
            {outputPreview.length ? outputPreview.map((line, index) => <Text key={`${index}-${line}`} style={[styles.diagnosticsLine, { color: text }]}>{line || ' '}</Text>) : <Text style={[styles.diagnosticsLine, { color: muted }]}>No retained runner output is available.</Text>}
          </View>
        ) : null}
      </ScrollView>

      {(hasNewMessages || isScrolledUp) ? (
        <TouchableOpacity testID="jump-to-latest" accessibilityRole="button" accessibilityLabel="Jump to latest message" style={styles.jumpButton} onPress={() => { atBottomRef.current = true; setHasNewMessages(false); setIsScrolledUp(false); scrollRef.current?.scrollToEnd({ animated: true }); }}>
          <Text style={styles.jumpText}>{hasNewMessages ? '↓ New message' : '↓ Latest'}</Text>
        </TouchableOpacity>
      ) : null}

      <View style={[styles.composer, { borderColor: border, backgroundColor: surface }]}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Attach file" style={styles.composerIconButton}><Text style={[styles.composerIconText, { color: muted }]}>＋</Text></TouchableOpacity>
        <TextInput
          ref={inputRef}
          testID="captain-prompt"
          style={[styles.composerInput, { color: text }]}
          placeholder={`Message ${targetLabel}…`}
          placeholderTextColor={muted}
          value={promptText}
          onChangeText={setPromptText}
          onSubmitEditing={handleSend}
          returnKeyType="send"
          editable={!isThinking}
          accessibilityLabel={`Message ${targetLabel}`}
          {...({ onPointerDown: Platform.OS === 'web' ? (event: any) => event.currentTarget.focus() : undefined } as any)}
        />
        <TouchableOpacity accessibilityRole="button" accessibilityLabel={micInline ? 'Cancel inline microphone' : 'Start inline microphone'} accessibilityState={{ selected: micInline }} style={styles.composerIconButton} onPress={() => setMicInline(value => !value)}>
          <Text style={[styles.composerIconText, { color: micInline ? brand.cyan : muted }]}>⌕</Text>
        </TouchableOpacity>
        <TouchableOpacity testID="send-captain-prompt" accessibilityRole="button" accessibilityLabel={promptText.trim() ? `Send message to ${targetLabel}` : 'Open voice mode'} accessibilityState={{ disabled: isThinking, busy: isThinking }} onPress={handleSend} disabled={isThinking} style={[styles.sendButton, isThinking ? styles.disabled : undefined]}>
          <Text style={styles.sendButtonText}>{isThinking ? '…' : composerIcon}</Text>
        </TouchableOpacity>
      </View>
      {micInline ? <View style={styles.inlineMic} accessibilityLabel="Inline microphone levels"><Text style={styles.inlineMicMuted}>×</Text>{[0.35, 0.75, 0.5, 0.95, 0.62, 0.82].map((height, index) => <View key={index} style={[styles.levelBar, { height: 24 * height, backgroundColor: index > 3 ? brand.violet : brand.cyan }]} />)}<Text style={styles.inlineMicMuted}>✓</Text></View> : null}
      <View style={styles.chatFooterRow}>
        <TouchableOpacity testID="diagnostics-toggle" accessibilityRole="button" accessibilityLabel={diagnosticsOpen ? 'Hide diagnostics' : 'Show diagnostics'} onPress={() => setDiagnosticsOpen(value => !value)} style={styles.diagnosticsToggle}><Text style={[styles.diagnosticsToggleText, { color: muted }]}>{diagnosticsOpen ? 'Hide diagnostics' : 'Diagnostics'}</Text></TouchableOpacity>
        {sendError ? <Text testID="captain-send-error" style={styles.sendError}>{sendError}</Text> : null}
      </View>
    </View>
  );
}

function DrawerPanel({ open, dark, isNarrow, onToggle, activeSection, setActiveSection, accountActive, setAccountActive, agents, attention, prs, providers, errors, loading }: {
  open: boolean;
  dark: boolean;
  isNarrow: boolean;
  onToggle: () => void;
  activeSection: DrawerSection;
  setActiveSection: (section: DrawerSection) => void;
  accountActive: boolean;
  setAccountActive: (active: boolean) => void;
  agents: AgentInfo[];
  attention: UnifiedAttentionRecord[];
  prs: GitHubPR[];
  providers: AuthProviderInfo[];
  errors: { agents?: string | null; attention?: string | null; prs?: string | null; providers?: string | null };
  loading: boolean;
}) {
  const router = useRouter();
  const text = dark ? '#F4F5F7' : brand.ink;
  const muted = dark ? brand.mutedDark : brand.mutedLight;
  const surface = dark ? 'rgba(10,14,20,0.86)' : 'rgba(255,255,255,0.9)';
  const border = dark ? brand.borderDark : brand.borderLight;
  const fleet = summarizeAgents(agents);
  const activeAttention = attention.filter(item => item.requires_action !== false);

  const toggleSection = (section: DrawerSection) => setActiveSection(activeSection === section ? null : section);
  const openAttentionItem = async (item: UnifiedAttentionRecord) => {
    if (item.url?.startsWith('/')) router.push(item.url as any);
    else {
      const result = await openExternalUrl(item.external_url || item.url);
      if (!result.ok) Alert.alert('Unable to open attention item', result.message);
    }
  };

  const rows = [
    { key: 'attention' as const, icon: '!', title: 'Attention', count: activeAttention.length },
    { key: 'fleet' as const, icon: '::', title: 'Fleet Summary', count: agents.length },
    { key: 'activity' as const, icon: '⌁', title: 'Recent Activity' },
    { key: 'connections' as const, icon: '⌘', title: 'Connections' }
  ];

  return (
    <View pointerEvents={open ? 'auto' : 'none'} testID="magistrate-drawer" style={[styles.drawer, { width: open ? (isNarrow ? '86%' : 318) : 0, opacity: open ? 1 : 0, backgroundColor: surface, borderColor: border }, isNarrow ? styles.drawerMobile : undefined]}>
      {open ? <>
        <View style={styles.drawerBrandRow}>
          <TouchableOpacity testID="drawer-brand-toggle" accessibilityRole="button" accessibilityLabel="Collapse Magistrate drawer" accessibilityState={{ expanded: open }} onPress={onToggle} style={styles.drawerMarkButton}>
            <BrandMark dark={dark} size={34} />
          </TouchableOpacity>
          <Text style={[styles.drawerWordmark, { color: text }]}>melkezic</Text>
        </View>
        <ScrollView style={styles.drawerScroll} contentContainerStyle={styles.drawerScrollContent}>
          {rows.map(row => (
            <View key={row.key}>
              <TouchableOpacity accessibilityRole="button" accessibilityLabel={`${row.title} section`} accessibilityState={{ expanded: activeSection === row.key }} onPress={() => toggleSection(row.key)} style={styles.drawerRow}>
                <Text style={[styles.drawerIcon, { color: muted }]}>{row.icon}</Text>
                <Text style={[styles.drawerRowText, { color: text }]}>{row.title}</Text>
                {typeof row.count === 'number' ? <Text style={[styles.drawerCount, { color: muted }]}>{row.count}</Text> : null}
              </TouchableOpacity>
              {activeSection === row.key ? <View style={styles.sectionPanel}>{row.key === 'attention' ? (
                loading ? <PanelText text="Loading attention…" muted={muted} /> : errors.attention ? <PanelText text={errors.attention} muted={brand.critical} /> : activeAttention.length === 0 ? <PanelText text="Nothing requires your attention." muted={muted} /> : activeAttention.slice(0, 5).map(item => <TouchableOpacity key={item.id} testID={`attention-item-${item.id}`} onPress={() => void openAttentionItem(item)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{item.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{item.provider} · {item.subtitle}</Text></TouchableOpacity>)
              ) : row.key === 'fleet' ? (
                loading ? <PanelText text="Loading fleet…" muted={muted} /> : errors.agents ? <PanelText text={errors.agents} muted={brand.critical} /> : agents.length === 0 ? <PanelText text="No live agent sessions reported by Herdr." muted={muted} /> : fleet.ordered.map(({ agent, displayStatus }) => <TouchableOpacity key={agent.id} onPress={() => router.push({ pathname: '/chat', params: { agentId: agent.id } } as any)} style={styles.fleetPanelRow}><View style={[styles.tinyDot, { backgroundColor: statusColor(agent.status) }]} /><Text style={[styles.fleetPanelName, { color: text }]}>{agent.name || agent.id}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{displayAgentStatus(displayStatus)}</Text></TouchableOpacity>)
              ) : row.key === 'activity' ? (
                errors.prs ? <PanelText text={errors.prs} muted={brand.critical} /> : prs.length === 0 ? <PanelText text="No meaningful recent activity is available from the configured providers." muted={muted} /> : prs.slice(0, 4).map(pr => <TouchableOpacity key={pr.id || pr.number} onPress={() => router.push(`/pr-detail?number=${pr.number}` as any)} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>PR #{pr.number}: {pr.title}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{pr.repository} · {pr.review_status}</Text></TouchableOpacity>)
              ) : (
                errors.providers ? <PanelText text={errors.providers} muted={brand.critical} /> : providers.length === 0 ? <PanelText text="No connected account data is available. Configure providers from Account when credentials are ready." muted={muted} /> : providers.map(provider => <View key={provider.provider} style={styles.panelItem}><Text style={[styles.panelItemTitle, { color: text }]}>{provider.provider}</Text><Text style={[styles.panelItemMeta, { color: muted }]}>{provider.status}{provider.username ? ` · ${provider.username}` : ''}</Text></View>)
              )}</View> : null}
            </View>
          ))}
        </ScrollView>
        <View style={styles.drawerBottom}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Account" accessibilityState={{ selected: accountActive }} onPress={() => setAccountActive(!accountActive)} style={styles.drawerRow}>
            <Text style={[styles.drawerIcon, { color: muted }]}>◌</Text><Text style={[styles.drawerRowText, { color: text }]}>Account</Text>
          </TouchableOpacity>
          {accountActive ? <TouchableOpacity accessibilityRole="button" accessibilityLabel="Settings" accessibilityState={{ expanded: activeSection === 'settings' }} onPress={() => toggleSection('settings')} style={styles.drawerRow}><Text style={[styles.drawerIcon, { color: muted }]}>⚙</Text><Text style={[styles.drawerRowText, { color: text }]}>Settings</Text></TouchableOpacity> : null}
          {activeSection === 'settings' ? <View style={styles.sectionPanel}><PanelText text="Settings are present but not expanded into the full MVP here. Backgrounds, voice, notifications, and diagnostics remain future slices." muted={muted} /></View> : null}
        </View>
      </> : null}
    </View>
  );
}

function PanelText({ text, muted }: { text: string; muted: string }) { return <Text style={[styles.panelText, { color: muted }]}>{text}</Text>; }

export default function ChatScreen() {
  const { agentId } = useLocalSearchParams<{ agentId?: string | string[] }>();
  const target = Array.isArray(agentId) ? agentId[0] : agentId;
  const colorScheme = useColorScheme();
  const dark = isDarkTheme(colorScheme);
  const { width } = useWindowDimensions();
  const isNarrow = width < 720;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeSection, setActiveSection] = useState<DrawerSection>(null);
  const [accountActive, setAccountActive] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [attention, setAttention] = useState<UnifiedAttentionRecord[]>([]);
  const [prs, setPrs] = useState<GitHubPR[]>([]);
  const [providers, setProviders] = useState<AuthProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<{ agents?: string | null; attention?: string | null; prs?: string | null; providers?: string | null }>({});

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    Promise.allSettled([fetchAgents(), fetchUnifiedAttention(), fetchGitHubPRs(), fetchAuthProviders()]).then(([agentResult, attentionResult, prsResult, providerResult]) => {
      if (!mounted) return;
      setErrors({
        agents: agentResult.status === 'rejected' ? errorText(agentResult.reason, 'Agent data could not be loaded.') : null,
        attention: attentionResult.status === 'rejected' ? errorText(attentionResult.reason, 'Attention data could not be loaded.') : null,
        prs: prsResult.status === 'rejected' ? errorText(prsResult.reason, 'Recent activity could not be loaded.') : null,
        providers: providerResult.status === 'rejected' ? errorText(providerResult.reason, 'Connections data could not be loaded.') : null
      });
      if (agentResult.status === 'fulfilled') setAgents(agentResult.value);
      if (attentionResult.status === 'fulfilled') setAttention(attentionResult.value);
      if (prsResult.status === 'fulfilled') setPrs(prsResult.value.items);
      if (providerResult.status === 'fulfilled') setProviders(providerResult.value);
      setLoading(false);
    });
    return () => { mounted = false; };
  }, []);

  const shellShift = useMemo(() => isNarrow && drawerOpen ? [{ translateX: Math.min(width * 0.72, 330) }] : [{ translateX: 0 }], [drawerOpen, isNarrow, width]);

  return (
    <EnvironmentBackground hideBottomControls>
      <View style={styles.page}>
        <DrawerPanel open={drawerOpen} dark={dark} isNarrow={isNarrow} onToggle={() => setDrawerOpen(false)} activeSection={activeSection} setActiveSection={setActiveSection} accountActive={accountActive} setAccountActive={setAccountActive} agents={agents} attention={attention} prs={prs} providers={providers} errors={errors} loading={loading} />
        <View style={[styles.chatStage, { transform: shellShift }]}>
          <ChatCanvas target={target || 'captain'} drawerOpen={drawerOpen} onDrawerToggle={() => setDrawerOpen(value => !value)} />
        </View>
      </View>
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, minWidth: 0, overflow: 'hidden' },
  chatStage: { flex: 1, minWidth: 0 },
  canvas: { flex: 1, minWidth: 0, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 10 },
  shellHeader: { minHeight: 50, flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, zIndex: 3 },
  logoButton: { width: 44, height: 44, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  headerControls: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'flex-end', gap: 7 },
  compactSelectWrap: { width: 132, maxWidth: '32%' },
  liveChip: { minHeight: 34, borderWidth: 1, borderRadius: 999, paddingHorizontal: 9, flexDirection: 'row', gap: 6, alignItems: 'center', marginTop: 15 },
  liveText: { fontSize: 11, fontWeight: '700', textTransform: 'uppercase' },
  tinyDot: { width: 9, height: 9, borderRadius: 5, display: 'inline-block' as any },
  chatHistory: { flex: 1, minHeight: 0 },
  chatHistoryContent: { paddingTop: 24, paddingHorizontal: 22, paddingBottom: 24, gap: 12 },
  emptyConversation: { maxWidth: 600, alignSelf: 'center', width: '100%', paddingTop: 28 },
  conversationEyebrow: { fontSize: 12, fontWeight: '800', letterSpacing: 1.4, textTransform: 'uppercase', marginBottom: 8 },
  conversationTitle: { fontSize: 42, lineHeight: 48, fontWeight: '500', marginBottom: 10 },
  emptyCopy: { maxWidth: 480, fontSize: 15, lineHeight: 22 },
  messageBubble: { maxWidth: 680, paddingVertical: 12, paddingHorizontal: 16, borderRadius: 22 },
  userMessage: { alignSelf: 'flex-end' },
  assistantMessage: { alignSelf: 'flex-start' },
  messageSpeaker: { fontSize: 14, fontWeight: '800', marginBottom: 6 },
  messageText: { fontSize: 16, lineHeight: 23 },
  diagnosticsBox: { marginTop: 8, borderWidth: 1, borderRadius: 18, padding: 14, maxHeight: 320, overflow: 'scroll' as any },
  diagnosticsTitle: { fontSize: 11, fontWeight: '800', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 },
  diagnosticsLine: { fontFamily: Platform.select({ web: 'monospace', default: undefined }), fontSize: 11, lineHeight: 16 },
  jumpButton: { position: 'absolute', alignSelf: 'center', bottom: 92, backgroundColor: brand.cyan, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8 },
  jumpText: { color: brand.obsidian, fontSize: 12, fontWeight: '800' },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 64, borderWidth: 1, borderRadius: 28, paddingHorizontal: 10, paddingVertical: 8, marginHorizontal: 8 },
  composerIconButton: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center' },
  composerIconText: { fontSize: 22, fontWeight: '500' },
  composerInput: { flex: 1, minWidth: 0, fontSize: 16, paddingVertical: 8, outlineStyle: 'none' as any },
  sendButton: { width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center', backgroundColor: brand.cyan },
  sendButtonText: { color: brand.obsidian, fontSize: 22, fontWeight: '800' },
  disabled: { opacity: 0.55 },
  inlineMic: { position: 'absolute', bottom: 78, right: 68, flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 10, paddingVertical: 7, borderRadius: 999, backgroundColor: 'rgba(17,23,34,0.72)' },
  inlineMicMuted: { color: 'rgba(255,255,255,0.45)', fontSize: 16 },
  levelBar: { width: 3, borderRadius: 2 },
  chatFooterRow: { minHeight: 25, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 18 },
  diagnosticsToggle: { paddingVertical: 4, paddingHorizontal: 6 },
  diagnosticsToggleText: { fontSize: 11, fontWeight: '700' },
  sendError: { color: '#FFB4B2', fontSize: 12, flex: 1, textAlign: 'right' },
  drawer: { position: 'absolute', left: 8, top: 8, bottom: 8, zIndex: 5, borderWidth: 1, borderRadius: 24, padding: 14, overflow: 'hidden' },
  drawerMobile: { left: '7%', right: '7%' as any, top: 10, bottom: 10 },
  drawerBrandRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 18 },
  drawerMarkButton: { width: 42, height: 42, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  drawerWordmark: { fontFamily: Platform.select({ web: 'Bodoni Moda, Times New Roman, serif', default: undefined }), fontSize: 27, lineHeight: 32, fontWeight: '500' },
  drawerScroll: { flex: 1, minHeight: 0 },
  drawerScrollContent: { paddingBottom: 16 },
  drawerRow: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, paddingHorizontal: 5 },
  drawerIcon: { width: 26, fontSize: 15, fontWeight: '800' },
  drawerRowText: { flex: 1, fontSize: 15, fontWeight: '700' },
  drawerCount: { fontSize: 12, fontWeight: '800' },
  sectionPanel: { paddingLeft: 36, paddingRight: 4, paddingBottom: 8, gap: 8 },
  panelText: { fontSize: 13, lineHeight: 19 },
  panelItem: { paddingVertical: 7 },
  panelItemTitle: { fontSize: 13, fontWeight: '800', marginBottom: 2 },
  panelItemMeta: { fontSize: 12, lineHeight: 17 },
  fleetPanelRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7 },
  fleetPanelName: { flex: 1, fontSize: 13, fontWeight: '700' },
  drawerBottom: { paddingTop: 8 }
});
