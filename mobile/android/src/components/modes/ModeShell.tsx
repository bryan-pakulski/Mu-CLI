// The shared mode shell on Android (modes_v2.md §5.2, §5.4): the Now board (rows that wait for
// the user, each with its action visible; blockers answered in a dialog), the CheckRun row and
// its sheet (command, cwd, runner, exit code, passed, predicate, excerpt, full output, details),
// and a hook that runs a mode action and reports failures. Every mode screen composes these.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { modeStateApi, type CheckRun } from '../../api/modeState';
import { spacing } from '../../theme/tokens';
import { BoardSection, Button, Pre, Row, Sheet, Text, useServerActions, type ServerAction } from '../../ui';
import { Fact } from '../chat/PanelPage';
import { checkRunLine, checkRunMark, type NowItem } from './modeModel';

/* ------------------------------------------------------------ actions */

/**
 * Run a mode action: optional confirm / input first (useServerActions), then the call, then the
 * screen's reload. `error` holds the last failure for a Banner; `element` renders the dialog.
 */
export function useModeActions(reload: () => Promise<unknown> | void) {
  const dialogs = useServerActions();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (spec: ServerAction, call: (args: Record<string, unknown>) => Promise<unknown>) => {
    const args = await dialogs.ask(spec);
    if (!args) return false;
    setBusy(spec.id);
    setError(null);
    try {
      await call(args);
      await reload();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(null);
    }
  }, [dialogs, reload]);
  return { run, busy, error, clearError: () => setError(null), element: dialogs.element };
}

function errorText(e: unknown): string {
  // ApiError already carries the server's detail (a gate's message) as its message.
  return e instanceof Error ? e.message : String(e);
}

/** The blocker answer: the user's resolution goes to the shared endpoint. */
export function resolveBlockerAction(item: NowItem): ServerAction {
  return {
    id: `resolve-${item.ref}`, label: 'Answer',
    input: { arg: 'resolution', label: item.blocker?.questions?.length ? item.blocker.questions.join('\n') : item.title, multiline: true, hint: item.blocker?.details || undefined },
  };
}

/* ------------------------------------------------------------ Now */

/**
 * Now (§5.2): only when something waits for the user, ordered by urgency (the caller orders with
 * orderNow). Each row is pinned (needs you) and shows its actions.
 */
export function NowBoard({ items, actionsFor, testID = 'mode-now' }: {
  items: NowItem[];
  actionsFor: (item: NowItem) => React.ReactNode;
  testID?: string;
}) {
  if (!items.length) return null;
  return (
    <BoardSection title="Now" testID={testID}>
      {items.map(item => (
        <Row key={item.key} pinned status="waiting" title={item.title} sub={item.sub} actions={actionsFor(item)} testID={`now-${item.key}`} />
      ))}
    </BoardSection>
  );
}

/* ------------------------------------------------------------ check runs */

/** One CheckRun row: ✓ label · command · 2.1s. */
export function CheckRunRow({ run, onOpen }: { run: CheckRun; onOpen: (run: CheckRun) => void }) {
  const mark = checkRunMark(run);
  const line = checkRunLine(run);
  return (
    <Row status={mark.status} title={line.title} when={mark.label}
      sub={<Text variant="monoSm" tone="ink3" numberOfLines={2}>{line.sub}</Text>}
      accessibilityLabel={`${line.title}, ${mark.label}`} onPress={() => onOpen(run)} testID={`check-run-${run.id}`} />
  );
}

/** Open a CheckRun sheet from a full run or just its id (fetched). Render `element` once. */
export function useCheckRunSheet(known: CheckRun[] = []) {
  const [target, setTarget] = useState<CheckRun | string | null>(null);
  const open = useCallback((run: CheckRun | string) => {
    if (typeof run === 'string') setTarget(known.find(r => r.id === run) || run);
    else setTarget(run);
  }, [known]);
  const element = <CheckRunSheet target={target} onClose={() => setTarget(null)} />;
  return { open, element };
}

export function CheckRunSheet({ target, onClose }: { target: CheckRun | string | null; onClose: () => void }) {
  const [fetched, setFetched] = useState<CheckRun | null>(null);
  const [output, setOutput] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = typeof target === 'string' ? target : target?.id || null;

  React.useEffect(() => {
    setOutput(null);
    setDetails(false);
    setError(null);
    setFetched(null);
    if (typeof target !== 'string') return;
    let live = true;
    modeStateApi.getCheck(target).then(r => { if (live) setFetched(r); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [target]);

  const run = typeof target === 'string' ? fetched : target;
  const loadOutput = async () => {
    if (!id) return;
    try {
      const text = run?.security_output_path
        ? (await modeStateApi.getSecurityOutput(run.security_output_path)).text
        : (await modeStateApi.getCheckOutput(id)).output;
      setOutput(text || '(no output)');
    }
    catch (e) { setError(String(e)); }
  };
  const mark = run ? checkRunMark(run) : null;
  const line = run ? checkRunLine(run) : null;
  return (
    <Sheet visible={target !== null} onClose={onClose} title={line?.title || 'Check run'} testID="check-run-sheet">
      {error ? <Text variant="caption" tone="danger">{error}</Text> : null}
      {run && mark ? (
        <View style={styles.body}>
          <Row status={mark.status} title={mark.label} sub={run.detail || undefined} />
          <Text variant="label" tone="ink2">Command</Text>
          <Pre testID="check-run-command">{run.command}</Pre>
          {run.cwd ? <Fact label="Working dir" value={run.cwd} mono /> : null}
          <Fact label="Runner" value={run.runner || 'host'} />
          <Fact label="Exit code" value={run.timed_out ? 'timed out' : run.exit_code == null ? '—' : String(run.exit_code)} />
          <Fact label="Passed" value={run.passed ? 'yes' : 'no'} />
          {run.predicate_text ? <Fact label="Predicate" value={run.predicate_text} /> : null}
          {run.duration_s ? <Fact label="Duration" value={`${Number(run.duration_s).toFixed(1)}s`} /> : null}
          {run.excerpt ? (
            <>
              <Text variant="label" tone="ink2">Excerpt</Text>
              <Pre testID="check-run-excerpt">{run.excerpt}</Pre>
            </>
          ) : null}
          {output === null ? (
            <Button variant="ghost" label="Full output ▸" onPress={() => void loadOutput()} testID="check-run-output" />
          ) : <Pre testID="check-run-output-text">{output}</Pre>}
          <Button variant="ghost" label={details ? 'Details ▾' : 'Details ▸'} onPress={() => setDetails(v => !v)} testID="check-run-details" />
          {details ? (
            <View style={styles.body}>
              <Fact label="ID" value={run.id} mono />
              <Fact label="Purpose" value={run.purpose} />
              {run.source ? <Fact label="Source" value={run.source} /> : null}
              {run.ref ? <Fact label="Record" value={run.ref} mono /> : null}
              {run.result_ref ? <Fact label="Result" value={run.result_ref} mono /> : null}
              {Object.entries(run.hashes || {}).map(([path, hash]) => <Fact key={path} label={path} value={hash} mono />)}
            </View>
          ) : null}
        </View>
      ) : !error ? <Text variant="caption" tone="ink3">Loading…</Text> : null}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  body: { gap: spacing.sm },
});
