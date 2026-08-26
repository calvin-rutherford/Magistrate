import React from 'react';
import { WorkspaceShell } from '../../src/components/WorkspaceShell';
// Home is the compatibility entry point for the persistent Chat workspace.
// Legacy destinations are translated into query-backed in-shell panels.
export default function HomeScreen() {
  return <WorkspaceShell />;
}
