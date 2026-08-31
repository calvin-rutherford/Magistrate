import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Platform, Pressable, ScrollView, StyleSheet, Text, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import { useLocalSearchParams, usePathname, useRouter } from 'expo-router';
import { ChatCanvas } from '../../app/(tabs)/chat';
import { EnvironmentBackground } from './EnvironmentBackground';
import { GlassSurface } from './GlassSurface';
import { StatusRing } from './StatusRing';
import {
  AgentInfo,
  fetchAgents,
  fetchGitHubPR,
  fetchGitHubPRs,
  fetchHealth,
  fetchUnifiedAttention,
  GitHubPR,
  HealthInfo,
  interruptAgent,
  sendAgentKey,
  UnifiedAttentionRecord
} from '../api/client';
import { agentDisplayName, summarizeAgents, displayAgentStatus } from '../services/AgentStatus';
import { openExternalUrl } from '../utils/externalLinks';

// Sidebar refreshes must not replace the Chat DOM: terminal scroll position,
// composer focus, and the PR #14 send/control guards belong to this canvas.
const PersistentChatCanvas = React.memo(ChatCanvas);

export type WorkspaceSection = 'situation' | 'fleet' | 'attention' | 'pull-requests';

const sectionLabels: Record<WorkspaceSection, string> = {
  situation: 'Situation / telemetry',
  fleet: 'Agent fleet',
  attention: 'Needs attention',
  'pull-requests': 'Pull requests'
};

const navItems: Array<{ id: WorkspaceSection; icon: string; short: string }> = [
  { id: 'situation', icon: '◉', short: 'SITUATION' },
  { id: 'fleet', icon: '⌘', short: 'FLEET' },
  { id: 'attention', icon: '!', short: 'ATTENTION' },
  { id: 'pull-requests', icon: '⑂', short: 'PULL REQUESTS' }
];

const firstParam = (value: string | string[] | undefined) => Array.isArray(value) ? value[0] : value;

function sectionFrom(value: string | undefined): WorkspaceSection | null {
  if (value === 'situation' || value === 'status' || value === 'telemetry') return 'situation';
  if (value === 'fleet' || value === 'agents') return 'fleet';
  if (value === 'attention') return 'attention';
  if (value === 'pull-requests' || value === 'prs') return 'pull-requests';
  return null;
}

function legacySection(pathname: string): WorkspaceSection | null {
  if (pathname.endsWith('/agents')) return 'fleet';
  if (pathname.endsWith('/attention')) return 'attention';
  if (pathname.endsWith('/prs') || pathname.endsWith('/pr-detail')) return 'pull-requests';
  if (pathname.endsWith('/status')) return 'situation';
  return null;
}

export function WorkspaceShell() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useLocalSearchParams<{ section?: string; window?: string; agentId?: string; item?: string; number?: string }>();
  const { width } = useWindowDimensions();
  const isMobile = width < 760;
  const [railCollapsed, setRailCollapsed] = useState(true);
  const [mobileRailOpen, setMobileRailOpen] = useState(false);

  const querySection = sectionFrom(firstParam(params.section) || firstParam(params.window));
  const activeSection = querySection || legacySection(pathname);
  const selectedAgentId = firstParam(params.agentId);
  const selectedAttentionId = firstParam(params.item);
  const selectedPrNumber = Number(firstParam(params.number));
  const chatTarget = selectedAgentId && !activeSection ? selectedAgentId : 'captain';

  const closePanel = useCallback(() => {
    setMobileRailOpen(false);
    // Replace the query state so a direct deep link closes safely without
    // taking the user out of Magistrate. Browser/Android Back still traverses
    // the push that opened the window when the user chooses Back instead.
    router.replace('/' as any);
  }, [router]);

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && (activeSection || mobileRailOpen)) {
        event.preventDefault();
        if (mobileRailOpen) setMobileRailOpen(false);
        else closePanel();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeSection, closePanel, mobileRailOpen]);

  const openSection = (section: WorkspaceSection) => {
    setMobileRailOpen(false);
    router.push({ pathname: '/', params: { section } } as any);
  };

  const openAgent = (agentId: string) => {
    setMobileRailOpen(false);
    router.push({ pathname: '/', params: { agentId } } as any);
  };

  const openLegacyTarget = (query: Record<string, string>) => {
    router.push({ pathname: '/', params: query } as any);
  };

  return (
    <EnvironmentBackground hideBottomControls>
      <View style={styles.shell} testID="workspace-shell">
        <View style={styles.topbar}>
          <View style={styles.brandGroup}>
            <View style={styles.brandMark}><View style={styles.brandDot} /></View>
            <Text style={styles.brand}>MAGISTRATE</Text>
          </View>
          <Text style={styles.shellMode}>CHAT-CENTERED WORKSPACE</Text>
          {isMobile ? (
            <TouchableOpacity
              testID="mobile-rail-toggle"
              accessibilityRole="button"
              accessibilityLabel="Open workspace navigation"
              onPress={() => setMobileRailOpen(true)}
              style={styles.topbarButton}
            ><Text style={styles.topbarButtonText}>☰</Text></TouchableOpacity>
          ) : (
            <TouchableOpacity
              testID="rail-toggle"
              accessibilityRole="button"
              accessibilityLabel={railCollapsed ? 'Expand workspace navigation' : 'Collapse workspace navigation'}
              onPress={() => setRailCollapsed(value => !value)}
              style={styles.topbarButton}
            ><Text style={styles.topbarButtonText}>{railCollapsed ? '→' : '←'}</Text></TouchableOpacity>
          )}
        </View>

        <View style={styles.workspaceBody}>
          <View style={styles.primaryColumn}>
            <PersistentChatCanvas target={chatTarget} />
            <WorkspaceDataChrome
              activeSection={activeSection}
              isMobile={isMobile}
              railCollapsed={railCollapsed}
              mobileRailOpen={mobileRailOpen}
              setMobileRailOpen={setMobileRailOpen}
              onOpenSection={openSection}
              onOpenAgent={openAgent}
              onOpenAttention={id => openLegacyTarget({ section: 'attention', item: id })}
              onOpenPr={number => openLegacyTarget({ section: 'pull-requests', number: String(number) })}
              selectedAgentId={selectedAgentId}
              selectedAttentionId={selectedAttentionId}
              selectedPrNumber={selectedPrNumber}
              onClosePanel={closePanel}
            />
          </View>
        </View>
      </View>
    </EnvironmentBackground>
  );
}

function WorkspaceDataChrome({ activeSection, isMobile, railCollapsed, mobileRailOpen, setMobileRailOpen, onOpenSection, onOpenAgent, onOpenAttention, onOpenPr, selectedAgentId, selectedAttentionId, selectedPrNumber, onClosePanel }: {
  activeSection: WorkspaceSection | null;
  isMobile: boolean;
  railCollapsed: boolean;
  mobileRailOpen: boolean;
  setMobileRailOpen: (open: boolean) => void;
  onOpenSection: (section: WorkspaceSection) => void;
  onOpenAgent: (id: string) => void;
  onOpenAttention: (id: string) => void;
  onOpenPr: (number: number) => void;
  selectedAgentId?: string;
  selectedAttentionId?: string;
  selectedPrNumber: number;
  onClosePanel: () => void;
}) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [attention, setAttention] = useState<UnifiedAttentionRecord[]>([]);
  const [prs, setPrs] = useState<GitHubPR[]>([]);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [dataReady, setDataReady] = useState(false);
  const [panelDataError, setPanelDataError] = useState<string | null>(null);
  // A count is a claim about live data. When a source fails we must not reuse
  // the previous or empty value as if it were current.
  const [unavailableSources, setUnavailableSources] = useState<Record<WorkspaceSection, boolean>>({ situation: false, fleet: false, attention: false, 'pull-requests': false });
  const loadWorkspaceData = useCallback(async () => {
    const [agentResult, attentionResult, prResult, healthResult] = await Promise.allSettled([fetchAgents(), fetchUnifiedAttention(), fetchGitHubPRs(1, false), fetchHealth()]);
    const errors: string[] = [];
    const agentsOk = agentResult.status === 'fulfilled' && Array.isArray(agentResult.value);
    const attentionOk = attentionResult.status === 'fulfilled' && Array.isArray(attentionResult.value);
    const prsOk = prResult.status === 'fulfilled' && Array.isArray(prResult.value.items);
    const healthOk = healthResult.status === 'fulfilled';
    if (agentsOk) setAgents((agentResult as PromiseFulfilledResult<AgentInfo[]>).value); else errors.push('agent fleet');
    if (attentionOk) setAttention((attentionResult as PromiseFulfilledResult<UnifiedAttentionRecord[]>).value); else errors.push('attention queue');
    if (prsOk) setPrs((prResult as PromiseFulfilledResult<{ items: GitHubPR[] }>).value.items.filter(pr => pr.requires_attention)); else errors.push('pull requests');
    if (healthOk) setHealth((healthResult as PromiseFulfilledResult<HealthInfo>).value); else errors.push('telemetry');
    setUnavailableSources({ situation: !healthOk, fleet: !agentsOk, attention: !attentionOk, 'pull-requests': !prsOk });
    setPanelDataError(errors.length ? `Live ${errors.join(', ')} unavailable.` : null);
    setDataReady(true);
  }, []);
  useEffect(() => { loadWorkspaceData(); const timer = setInterval(loadWorkspaceData, 10000); return () => clearInterval(timer); }, [loadWorkspaceData]);
  const counts = useMemo(() => {
    const value = (section: WorkspaceSection, compute: () => string) => !dataReady ? '…' : unavailableSources[section] ? '—' : compute();
    return {
      situation: value('situation', () => health?.herdr_socket_connected ? '1' : '0'),
      fleet: value('fleet', () => String(agents.length)),
      attention: value('attention', () => String(attention.filter(item => item.requires_action !== false).length)),
      'pull-requests': value('pull-requests', () => String(prs.length))
    };
  }, [agents.length, attention, dataReady, health, prs.length, unavailableSources]);
  return <>
    {!isMobile ? <WorkspaceRail collapsed={railCollapsed} activeSection={activeSection} counts={counts} onOpen={onOpenSection} /> : null}
    {activeSection ? <WorkspacePanel section={activeSection} counts={counts} agents={agents} attention={attention} prs={prs} health={health} dataReady={dataReady} error={panelDataError} selectedAgentId={selectedAgentId} selectedAttentionId={selectedAttentionId} selectedPrNumber={selectedPrNumber} onClose={onClosePanel} onOpenAgent={onOpenAgent} onOpenAttention={onOpenAttention} onOpenPr={onOpenPr} /> : null}
    {isMobile && mobileRailOpen ? <View style={styles.mobileRailLayer}><Pressable accessibilityLabel="Close workspace navigation" style={styles.mobileBackdrop} onPress={() => setMobileRailOpen(false)} /><WorkspaceRail collapsed={false} activeSection={activeSection} counts={counts} onOpen={onOpenSection} mobile /></View> : null}
  </>;
}

function WorkspaceRail({ collapsed, activeSection, counts, onOpen, mobile = false }: {
  collapsed: boolean;
  activeSection: WorkspaceSection | null;
  counts: Record<WorkspaceSection, string>;
  onOpen: (section: WorkspaceSection) => void;
  mobile?: boolean;
}) {
  return (
    <View testID="workspace-rail" style={[styles.rail, collapsed && !mobile ? styles.railCollapsed : undefined, mobile ? styles.mobileRail : undefined]}>
      <Text style={styles.railHeading}>{collapsed && !mobile ? 'NAV' : 'COMMAND NAVIGATION'}</Text>
      {navItems.map(item => {
        const active = activeSection === item.id;
        return (
          <TouchableOpacity
            key={item.id}
            testID={`rail-${item.id}`}
            accessibilityRole="button"
            accessibilityLabel={`Open ${sectionLabels[item.id]}`}
            accessibilityState={{ selected: active }}
            {...({ title: sectionLabels[item.id] } as any)}
            onPress={() => onOpen(item.id)}
            style={[styles.railItem, active ? styles.railItemActive : undefined]}
          >
            <Text style={[styles.railIcon, active ? styles.railIconActive : undefined]}>{item.icon}</Text>
            {(!collapsed || mobile) ? <Text style={[styles.railLabel, active ? styles.railLabelActive : undefined]}>{item.short}</Text> : null}
            <View style={[styles.countBadge, item.id === 'attention' ? styles.attentionBadge : undefined]}>
              <Text style={styles.countText}>{counts[item.id]}</Text>
            </View>
          </TouchableOpacity>
        );
      })}
      <View style={styles.railFooter}><Text style={styles.railFooterText}>{collapsed && !mobile ? 'AI' : 'CHAT IS ALWAYS ON'}</Text></View>
    </View>
  );
}

function WorkspacePanel({ section, counts, agents, attention, prs, health, dataReady, error, selectedAgentId, selectedAttentionId, selectedPrNumber, onClose, onOpenAgent, onOpenAttention, onOpenPr }: {
  section: WorkspaceSection;
  counts: Record<WorkspaceSection, string>;
  agents: AgentInfo[];
  attention: UnifiedAttentionRecord[];
  prs: GitHubPR[];
  health: HealthInfo | null;
  dataReady: boolean;
  error: string | null;
  selectedAgentId?: string;
  selectedAttentionId?: string;
  selectedPrNumber: number;
  onClose: () => void;
  onOpenAgent: (id: string) => void;
  onOpenAttention: (id: string) => void;
  onOpenPr: (number: number) => void;
}) {
  const [selectedPr, setSelectedPr] = useState<GitHubPR | null>(null);
  const [selectedPrError, setSelectedPrError] = useState<string | null>(null);
  const [prLoading, setPrLoading] = useState(false);
  useEffect(() => {
    if (!selectedPrNumber) { setSelectedPr(null); setSelectedPrError(null); return; }
    const known = prs.find(pr => pr.number === selectedPrNumber);
    if (known) { setSelectedPr(known); setSelectedPrError(null); return; }
    setPrLoading(true); setSelectedPrError(null);
    // The gateway's own message is shown: a swallowed failure is
    // indistinguishable from a pull request that does not exist.
    fetchGitHubPR(selectedPrNumber)
      .then(pr => { setSelectedPr(pr); setSelectedPrError(null); })
      .catch(error => { setSelectedPr(null); setSelectedPrError(error instanceof Error && error.message ? error.message : `Pull request #${selectedPrNumber} could not be loaded.`); })
      .finally(() => setPrLoading(false));
  }, [prs, selectedPrNumber]);

  return (
    <View testID="workspace-panel" style={styles.panel}>
      <View style={styles.panelHeader}>
        <View style={styles.panelTitleGroup}>
          <Text style={styles.panelEyebrow}>IN-SHELL WINDOW</Text>
          <Text testID="panel-title" style={styles.panelTitle}>{sectionLabels[section]}</Text>
        </View>
        <TouchableOpacity testID="panel-close" accessibilityRole="button" accessibilityLabel="Close panel" onPress={onClose} style={styles.closeButton}>
          <Text style={styles.closeButtonText}>×</Text>
        </TouchableOpacity>
      </View>
      {error ? <Text style={styles.panelError}>{error}</Text> : null}
      {!dataReady ? <GlassSurface variant="card" style={styles.panelCard}><Text style={styles.muted}>Loading live data…</Text></GlassSurface> : null}
      {section === 'situation' ? <SituationPanel health={health} /> : null}
      {section === 'fleet' ? <FleetPanel agents={agents} selectedAgentId={selectedAgentId} onOpenAgent={onOpenAgent} /> : null}
      {section === 'attention' ? <AttentionPanel items={attention} selectedId={selectedAttentionId} onOpen={onOpenAttention} /> : null}
      {section === 'pull-requests' ? <PullRequestPanel prs={prs} selectedPr={selectedPr} loading={prLoading} onOpen={onOpenPr} /> : null}
      {section === 'pull-requests' && selectedPr ? <PrDetail pr={selectedPr} /> : null}
      {section === 'pull-requests' && selectedPrNumber && !selectedPr && !prLoading ? <Text testID="panel-pr-error" accessibilityRole="alert" style={styles.panelError}>{selectedPrError || `Pull request #${selectedPrNumber} was not returned by the live gateway.`}</Text> : null}
      <Text style={styles.panelHint}>Chat remains mounted underneath this window. Close with ×, Escape, or Back.</Text>
    </View>
  );
}

function SituationPanel({ health }: { health: HealthInfo | null }) {
  const status = !health ? 'UNAVAILABLE' : health.status === 'healthy' && health.herdr_socket_connected ? 'OPERATIONAL' : health.status === 'healthy' || health.status === 'degraded' ? 'DEGRADED' : health.status.toUpperCase();
  const color = status === 'OPERATIONAL' ? '#34D399' : status === 'DEGRADED' ? '#F59E0B' : '#FCA5A5';
  return <ScrollView contentContainerStyle={styles.panelScroll}>
    <StatusRing statusText={status} statusColor={color} subText={health?.herdr_socket_connected ? 'Gateway and Herdr connected' : 'Live telemetry is unavailable'} />
    <GlassSurface variant="card" style={styles.panelCard}>
      <Text style={styles.cardLabel}>LIVE TELEMETRY</Text>
      <Text style={styles.cardValue}>{health?.herdr_socket_connected ? 'Herdr socket connected' : 'Herdr socket unavailable'}</Text>
      <Text style={styles.muted}>{health?.service || 'Gateway service'}{health?.herdr_version ? ` · Herdr ${health.herdr_version}` : ' · Herdr version not reported'}</Text>
      {health?.degraded_sources?.length ? <Text testID="situation-degraded" style={styles.muted}>Unavailable sources: {health.degraded_sources.join(', ')}</Text> : null}
    </GlassSurface>
  </ScrollView>;
}

function FleetPanel({ agents, selectedAgentId, onOpenAgent }: { agents: AgentInfo[]; selectedAgentId?: string; onOpenAgent: (id: string) => void }) {
  const ordered = summarizeAgents(agents).ordered;
  return <ScrollView contentContainerStyle={styles.panelScroll}>
    <Text style={styles.panelCount}>LIVE AGENT SESSIONS ({agents.length})</Text>
    {ordered.length === 0 ? <GlassSurface variant="card" style={styles.panelCard}><Text style={styles.muted}>No Herdr agent sessions are active.</Text></GlassSurface> : null}
    {ordered.map(({ agent, displayStatus }) => <AgentPreview key={agent.id} agent={agent} status={displayStatus} selected={agent.id === selectedAgentId} onOpen={onOpenAgent} />)}
  </ScrollView>;
}

function AgentPreview({ agent, status, selected, onOpen }: { agent: AgentInfo; status: string; selected: boolean; onOpen: (id: string) => void }) {
  const [pending, setPending] = useState<'interrupt' | 'Enter' | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const run = async (action: 'interrupt' | 'Enter' | 'Escape') => {
    setBusy(action); setMessage(null);
    try {
      const result = action === 'interrupt' ? await interruptAgent(agent.id) : await sendAgentKey(agent.id, action);
      if (result.status === 'error' || result.error) throw new Error(result.error || `${action} was not accepted.`);
      setMessage(`${action.toUpperCase()} sent.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Agent control failed.'); }
    finally { setBusy(null); setPending(null); }
  };
  return <GlassSurface variant="card" style={[styles.panelCard, selected ? styles.selectedCard : undefined]}>
    <TouchableOpacity testID={`agent-card-${agent.id}`} accessibilityRole="button" accessibilityLabel={`Chat with ${agentDisplayName(agent)}`} onPress={() => onOpen(agent.id)} activeOpacity={0.85}>
      <View style={styles.agentHeader}><Text style={styles.agentName}>{agentDisplayName(agent)}</Text><Text style={styles.agentStatus}>{displayAgentStatus(status as any)}</Text></View>
      {agent.harness ? <Text style={styles.muted}>Harness: {agent.harness}</Text> : null}
      <Text style={styles.agentTargetHint}>OPEN CHAT TARGET →</Text>
    </TouchableOpacity>
    <Text style={styles.controlsLabel}>AGENT CONTROLS</Text>
    <View style={styles.controlsRow}>
      {(['interrupt', 'Enter', 'Escape'] as const).map(action => <TouchableOpacity key={action} testID={`agent-${agent.id}-${action.toLowerCase()}-control`} accessibilityRole="button" accessibilityLabel={`${action} ${agentDisplayName(agent)}`} disabled={busy !== null || pending !== null} onPress={() => action === 'Escape' ? void run(action) : setPending(action)} style={[styles.controlButton, action === 'interrupt' ? styles.interruptButton : undefined]}><Text style={styles.controlText}>{busy === action ? '…' : action.toUpperCase()}</Text></TouchableOpacity>)}
    </View>
    {pending ? <View style={styles.confirmRow}><Text style={styles.confirmText}>Confirm {pending}?</Text><TouchableOpacity testID="agent-control-cancel" onPress={() => setPending(null)}><Text style={styles.cancelText}>CANCEL</Text></TouchableOpacity><TouchableOpacity testID="agent-control-confirm" onPress={() => void run(pending)}><Text style={styles.confirmText}>CONFIRM</Text></TouchableOpacity></View> : null}
    {message ? <Text style={styles.muted}>{message}</Text> : null}
  </GlassSurface>;
}

function AttentionPanel({ items, selectedId, onOpen }: { items: UnifiedAttentionRecord[]; selectedId?: string; onOpen: (id: string) => void }) {
  return <ScrollView contentContainerStyle={styles.panelScroll}>
    <Text style={styles.panelCount}>NEEDS ATTENTION ({items.length})</Text>
    {items.length === 0 ? <GlassSurface variant="card" style={styles.panelCard}><Text style={styles.muted}>No live attention items reported.</Text></GlassSurface> : null}
    {items.map(item => <TouchableOpacity key={item.id} testID={`attention-item-${item.id}`} onPress={() => onOpen(item.id)} activeOpacity={0.85}><GlassSurface variant="card" style={[styles.panelCard, item.id === selectedId ? styles.selectedCard : undefined]}><View style={styles.agentHeader}><Text style={styles.cardLabel}>{item.provider.toUpperCase()}</Text><Text style={styles.agentStatus}>{item.priority || item.status || 'ACTION'}</Text></View><Text style={styles.cardValue}>{item.title}</Text><Text style={styles.muted}>{item.subtitle}</Text></GlassSurface></TouchableOpacity>)}
  </ScrollView>;
}

function PullRequestPanel({ prs, selectedPr, loading, onOpen }: { prs: GitHubPR[]; selectedPr: GitHubPR | null; loading: boolean; onOpen: (number: number) => void }) {
  return <ScrollView contentContainerStyle={styles.panelScroll}>
    <Text style={styles.panelCount}>PULL REQUESTS ({prs.length})</Text>
    {loading ? <Text style={styles.muted}>Loading pull request…</Text> : null}
    {prs.length === 0 && !loading ? <GlassSurface variant="card" style={styles.panelCard}><Text style={styles.muted}>No open pull requests need attention.</Text></GlassSurface> : null}
    {prs.map(pr => <TouchableOpacity key={pr.number} testID={`pr-card-${pr.number}`} onPress={() => onOpen(pr.number)} activeOpacity={0.85}><GlassSurface variant="card" style={[styles.panelCard, selectedPr?.number === pr.number ? styles.selectedCard : undefined]}><View style={styles.agentHeader}><Text style={styles.cardLabel}>PR #{pr.number}</Text><Text style={styles.agentStatus}>{pr.review_status}</Text></View><Text style={styles.cardValue}>{pr.title}</Text><Text style={styles.muted}>{pr.repository}{pr.branch ? ` · ${pr.branch}` : ''}</Text></GlassSurface></TouchableOpacity>)}
  </ScrollView>;
}

function PrDetail({ pr }: { pr: GitHubPR }) {
  const open = async () => {
    const result = await openExternalUrl(pr.url);
    if (!result.ok) Alert.alert('Unable to open link', result.message);
  };
  return <GlassSurface variant="card" style={styles.panelCard}><Text style={styles.cardLabel}>SELECTED PR #{pr.number}</Text><Text style={styles.cardValue}>{pr.title}</Text><Text style={styles.muted}>{pr.checks.summary}</Text><Text style={styles.muted}>{pr.body}</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Open pull request on GitHub" onPress={open} style={styles.githubButton}><Text style={styles.githubButtonText}>OPEN ON GITHUB ↗</Text></TouchableOpacity></GlassSurface>;
}

const styles = StyleSheet.create({
  shell: { flex: 1, minWidth: 0 },
  topbar: { flexDirection: 'row', alignItems: 'center', minHeight: 58, paddingHorizontal: 16, gap: 12, borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.1)' },
  brandGroup: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  brandMark: { width: 24, height: 24, borderRadius: 12, borderWidth: 1, borderColor: '#72F5B1', backgroundColor: 'rgba(114,245,177,0.16)', justifyContent: 'center', alignItems: 'center' },
  brandDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: '#72F5B1' },
  brand: { color: '#FFFFFF', fontFamily: 'monospace', fontWeight: 'bold', letterSpacing: 2, fontSize: 13 },
  shellMode: { flex: 1, color: 'rgba(255,255,255,0.48)', fontFamily: 'monospace', fontSize: 9, letterSpacing: 1.4 },
  topbarButton: { minWidth: 38, minHeight: 38, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255,255,255,0.2)', justifyContent: 'center', alignItems: 'center' },
  topbarButtonText: { color: '#FFFFFF', fontSize: 18 },
  workspaceBody: { flex: 1, flexDirection: 'row', minHeight: 0 },
  rail: { width: 218, padding: 12, borderRightWidth: 1, borderRightColor: 'rgba(255,255,255,0.1)', backgroundColor: 'rgba(4,10,20,0.2)' },
  railCollapsed: { width: 72, alignItems: 'center' },
  railHeading: { color: 'rgba(255,255,255,0.42)', fontFamily: 'monospace', fontSize: 9, letterSpacing: 1.2, marginBottom: 12 },
  railItem: { minHeight: 48, borderRadius: 13, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 },
  railItemActive: { backgroundColor: 'rgba(114,245,177,0.13)', borderWidth: 1, borderColor: 'rgba(114,245,177,0.42)' },
  railIcon: { width: 24, color: 'rgba(255,255,255,0.72)', fontSize: 18, textAlign: 'center' },
  railIconActive: { color: '#72F5B1' },
  railLabel: { flex: 1, color: 'rgba(255,255,255,0.72)', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold', letterSpacing: 0.5 },
  railLabelActive: { color: '#72F5B1' },
  countBadge: { minWidth: 22, height: 22, borderRadius: 11, paddingHorizontal: 5, backgroundColor: 'rgba(56,189,248,0.82)', justifyContent: 'center', alignItems: 'center' },
  attentionBadge: { backgroundColor: 'rgba(245,158,11,0.9)' },
  countText: { color: '#07101D', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  railFooter: { marginTop: 'auto', paddingTop: 18 },
  railFooterText: { color: 'rgba(255,255,255,0.34)', fontFamily: 'monospace', fontSize: 9, letterSpacing: 1 },
  primaryColumn: { flex: 1, minWidth: 0, minHeight: 0, position: 'relative' },
  panel: { position: 'absolute', zIndex: 20, top: 0, right: 0, bottom: 0, width: 450, maxWidth: '100%', padding: 18, backgroundColor: 'rgba(8,17,31,0.97)', borderLeftWidth: 1, borderLeftColor: 'rgba(114,245,177,0.25)', shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 20, elevation: 10 },
  panelHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 14 },
  panelTitleGroup: { flex: 1, minWidth: 0 },
  panelEyebrow: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 9, letterSpacing: 1.3 },
  panelTitle: { color: '#FFFFFF', fontSize: 23, fontWeight: '300', marginTop: 3 },
  closeButton: { width: 38, height: 38, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255,255,255,0.24)', justifyContent: 'center', alignItems: 'center' },
  closeButtonText: { color: '#FFFFFF', fontSize: 26, lineHeight: 28 },
  panelScroll: { paddingBottom: 80 },
  panelCard: { padding: 14, borderRadius: 16, marginBottom: 9 },
  selectedCard: { borderWidth: 2, borderColor: '#72F5B1' },
  panelCount: { color: 'rgba(255,255,255,0.58)', fontFamily: 'monospace', fontSize: 10, letterSpacing: 1.1, marginBottom: 10 },
  cardLabel: { color: 'rgba(255,255,255,0.58)', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', letterSpacing: 1 },
  cardValue: { color: '#FFFFFF', fontSize: 14, fontWeight: 'bold', marginTop: 6, lineHeight: 20 },
  muted: { color: 'rgba(255,255,255,0.58)', fontSize: 11, lineHeight: 17, marginTop: 5 },
  panelError: { color: '#FCA5A5', fontSize: 11, marginBottom: 10 },
  panelHint: { position: 'absolute', bottom: 14, left: 18, right: 18, color: 'rgba(255,255,255,0.4)', fontFamily: 'monospace', fontSize: 9 },
  agentHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  agentName: { flex: 1, color: '#FFFFFF', fontWeight: 'bold', fontSize: 14 },
  agentStatus: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  agentTargetHint: { color: '#72F5B1', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold', marginTop: 10 },
  controlsLabel: { color: 'rgba(255,255,255,0.65)', fontFamily: 'monospace', fontSize: 9, letterSpacing: 1, marginTop: 14, marginBottom: 7 },
  controlsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 },
  controlButton: { minHeight: 36, minWidth: 68, paddingHorizontal: 10, borderRadius: 9, borderWidth: 1, borderColor: 'rgba(255,255,255,0.22)', backgroundColor: 'rgba(255,255,255,0.08)', justifyContent: 'center', alignItems: 'center' },
  interruptButton: { borderColor: 'rgba(252,165,165,0.7)', backgroundColor: 'rgba(127,29,29,0.32)' },
  controlText: { color: '#FFFFFF', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  confirmRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 8 },
  confirmText: { color: '#FCA5A5', fontFamily: 'monospace', fontSize: 10, fontWeight: 'bold' },
  cancelText: { color: 'rgba(255,255,255,0.7)', fontFamily: 'monospace', fontSize: 10 },
  githubButton: { alignSelf: 'flex-start', marginTop: 12, borderRadius: 9, backgroundColor: '#FFFFFF', paddingHorizontal: 12, paddingVertical: 9 },
  githubButtonText: { color: '#07101D', fontFamily: 'monospace', fontSize: 9, fontWeight: 'bold' },
  mobileRailLayer: { ...StyleSheet.absoluteFill, zIndex: 50, flexDirection: 'row' },
  mobileBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.62)' },
  mobileRail: { width: 270, backgroundColor: '#0D1322', borderRightWidth: 1, borderRightColor: 'rgba(114,245,177,0.3)' }
});
