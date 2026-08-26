import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentInfo } from '../src/api/client';
import { mapAgentStatus, summarizeAgents } from '../src/services/AgentStatus';

const agent = (id: string, status?: string, name?: string): AgentInfo => ({ id, name: name || id, status });

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
