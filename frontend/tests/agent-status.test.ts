import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentInfo } from '../src/api/client';
import { AGENT_NAME_UNAVAILABLE, agentDisplayName, mapAgentStatus, summarizeAgents } from '../src/services/AgentStatus';

const agent = (id: string, status?: string, name?: string): AgentInfo => ({ id, name: name || id, status });

test('uses Herdr names and fails closed for generic or missing labels', () => {
  assert.equal(agentDisplayName({ id: 'w1:p7', name: 'Review worker', pane_id: 'w1:p7' }), 'Review worker');
  assert.equal(agentDisplayName({ id: 'w1:p8', name: 'firstmate', pane_id: 'w1:p8' }), AGENT_NAME_UNAVAILABLE);
  assert.equal(agentDisplayName({ id: 'w1:p9', name: 'w1:p9', pane_id: 'w1:p9', tab_id: 'w1:t9' }), AGENT_NAME_UNAVAILABLE);
  assert.equal(agentDisplayName({ id: 'w1:p10', name: 'pane_id=w1:p10', pane_id: 'w1:p10' }), AGENT_NAME_UNAVAILABLE);
  assert.equal(agentDisplayName({ id: 'w1:p11', name: null }), AGENT_NAME_UNAVAILABLE);
});

test('maps only known live states and never guesses unknown values', () => {
  assert.equal(mapAgentStatus(agent('a', 'working')), 'active');
  assert.equal(mapAgentStatus(agent('b', 'running')), 'active');
  assert.equal(mapAgentStatus(agent('c', 'paused')), 'idle');
  assert.equal(mapAgentStatus(agent('d', 'done')), 'idle');
  assert.equal(mapAgentStatus(agent('e', 'blocked')), 'blocked');
  assert.equal(mapAgentStatus(agent('f', 'something-new')), 'unavailable');
  assert.equal(mapAgentStatus(agent('g')), 'unavailable');
});

test('counts and orders active, idle, blocked, and unavailable agents with stable ties', () => {
  const summary = summarizeAgents([
    agent('idle-z', 'idle', 'Zulu'),
    agent('active-b', 'working', 'Bravo'),
    agent('unknown', 'future-state', 'Mystery'),
    agent('active-a', 'running', 'Alpha'),
    agent('blocked', 'blocked', 'Blocked')
  ]);
  assert.deepEqual(summary.ordered.map(item => item.agent.id), ['active-a', 'active-b', 'idle-z', 'blocked', 'unknown']);
  assert.equal(summary.activeCount, 2);
  assert.equal(summary.idleCount, 1);
  assert.equal(summary.blockedCount, 1);
  assert.equal(summary.unavailableCount, 1);
});
