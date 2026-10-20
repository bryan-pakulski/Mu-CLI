// The fleet chat (Orchestration → Ask) and the Studio discovery chat, as the screens use them.
// Both live in src/components/orchestration/OrchChatSheet.tsx, built from the Chat kit.

import React from 'react';
import { DiscoveryChat, FleetChat } from '../../components/orchestration/OrchChatSheet';

export type FleetChatSheetProps = { visible: boolean; onClose: () => void; /** A question put in the composer, unsent. */ prefill?: string };
export type DiscoveryChatSheetProps = { visible: boolean; onClose: () => void; /** Draft ref (tool@v or runbook@v); '' for a new tool. */ draftRef: string };

export function FleetChatSheet({ visible, onClose, prefill }: FleetChatSheetProps) {
  return <FleetChat visible={visible} onClose={onClose} prefill={prefill} />;
}

/** The question "Ask about this" puts in the fleet chat (web runs.js / catalog.js `ask`). */
export function resultsQuestion(subject: { run?: { id: string; target: string }; toolId?: string }, v: { label: string; filter: string }): string {
  const view = `${v.label} view${v.filter ? `, filtered by "${v.filter}"` : ''}`;
  return subject.run
    ? `Look at the fleet results of run ${subject.run.id} (${subject.run.target}, ${view}) with fleet_results and tell me what needs attention.`
    : `Look at the latest ${subject.toolId} results across the fleet (${view}) with fleet_results and tell me what needs attention.`;
}

/** Mounted only while open, so each draft gets its own session. */
export function DiscoveryChatSheet({ visible, onClose, draftRef }: DiscoveryChatSheetProps) {
  return visible ? <DiscoveryChat visible onClose={onClose} draftRef={draftRef} /> : null;
}
