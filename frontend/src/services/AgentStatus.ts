import { AgentInfo } from '../api/client';

export type DisplayAgentStatus = 'active' | 'idle' | 'blocked' | 'unavailable';

const ACTIVE_STATES = new Set(['active', 'busy', 'executing', 'processing', 'running', 'working']);
const IDLE_STATES = new Set(['done', 'idle', 'paused', 'queued', 'ready', 'stopped', 'waiting']);

export function mapAgentStatus(agent: AgentInfo): DisplayAgentStatus {
  const status = String(agent.status || '').trim().toLowerCase();
  if (ACTIVE_STATES.has(status)) return 'active';
  if (IDLE_STATES.has(status)) return 'idle';
  if (status === 'blocked') return 'blocked';
  return 'unavailable';
}

function sortKey(agent: AgentInfo) {
  return (agent.name || agent.id || '').trim().toLowerCase();
}

const STATUS_RANK: Record<DisplayAgentStatus, number> = { active: 0, idle: 1, blocked: 2, unavailable: 3 };

export function summarizeAgents(agents: AgentInfo[]) {
  const ordered = agents
    .map(agent => ({ agent, displayStatus: mapAgentStatus(agent) }))
    .sort((left, right) => {
      const rankDifference = STATUS_RANK[left.displayStatus] - STATUS_RANK[right.displayStatus];
      if (rankDifference !== 0) return rankDifference;
      const nameDifference = sortKey(left.agent) < sortKey(right.agent) ? -1 : sortKey(left.agent) > sortKey(right.agent) ? 1 : 0;
      if (nameDifference !== 0) return nameDifference;
      return left.agent.id < right.agent.id ? -1 : left.agent.id > right.agent.id ? 1 : 0;
    });
  return {
    ordered,
    activeCount: ordered.filter(item => item.displayStatus === 'active').length,
    idleCount: ordered.filter(item => item.displayStatus === 'idle').length,
    blockedCount: ordered.filter(item => item.displayStatus === 'blocked').length,
    unavailableCount: ordered.filter(item => item.displayStatus === 'unavailable').length,
  };
}

export function displayAgentStatus(status: DisplayAgentStatus) {
  return status.toUpperCase();
}
