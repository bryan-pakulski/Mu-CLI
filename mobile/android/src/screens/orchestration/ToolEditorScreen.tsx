// A tool draft in the Studio (web studio.js renderEditor): the discovery progress, the manifest YAML
// and the files, saved with updateDraft; the server's actions (lint, test on a canary, submit for
// review, discard) save first when there are unsaved edits. A change made elsewhere (usually the AI)
// never overwrites unsaved edits: a banner offers to reload.

import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchAction, type OrchToolDetail } from '../../api/orchestration';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, Button, Dialog, ErrorState, Field, Loading, MiniStepper, PageHeader, Text, TextField, useToast } from '../../ui';
import { DiscoveryChatSheet } from './AskSheets';
import { CodeEditor } from './CodeEditor';
import { SubmitSheet, TestToolSheet } from './forms';
import { OPEN_STATUSES } from './CatalogView';
import { OrchActionBar, errorText, useOrchActions } from './parts';
import { lintWords, stageSteps, testsWords } from './StudioView';
import { langFor, versionState } from './ToolScreen';
import { useOrchData } from './useOrchData';

const EDITABLE = ['draft', 'testing'];
export const FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/** The updateDraft args for the edits made: the manifest if touched, changed and removed files. */
export function draftChanges(ref: string, manifest: string | null, files: Record<string, string>, changed: Set<string>, removed: Set<string>): Record<string, unknown> {
  const args: Record<string, unknown> = { ref };
  if (manifest != null) args.manifest = manifest;
  if (changed.size) args.files = Object.fromEntries([...changed].map(n => [n, files[n]]));
  if (removed.size) args.remove_files = [...removed];
  return args;
}

export function OrchToolEditorScreen({ route, navigation }: { route: { params: { toolId: string; version: number } }; navigation: any }) {
  const { toolId, version } = route.params;
  const toast = useToast();
  const [stale, setStale] = useState(false);
  const [manifest, setManifest] = useState<string | null>(null);
  const [files, setFiles] = useState<Record<string, string>>({});
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [dialog, setDialog] = useState<'test' | 'submit' | 'chat' | null>(null);
  const [saving, setSaving] = useState(false);
  const dirty = manifest != null || changed.size > 0 || removed.size > 0;
  const dirtyRef = React.useRef(dirty);
  dirtyRef.current = dirty;
  const { data: t, error, reload } = useOrchData(() => orchestrationApi.tool(toolId, version), c => {
    if (c.scope !== 'tool' || (c.id && c.id !== toolId)) return false;
    if (dirtyRef.current) { setStale(true); return false; }
    return true;
  }, [toolId, version]);
  const { run, element } = useOrchActions();
  useEffect(() => { if (t && !dirtyRef.current) setFiles({ ...(t.files || {}) }); }, [t]);
  const back = { label: 'Studio', onPress: () => navigation.goBack() };

  if (!t) {
    return (
      <View style={{ flex: 1 }}>
        <PageHeader title={`${toolId} v${version}`} back={back} />
        {error ? <ErrorState message={error} onRetry={() => { void reload(); }} /> : <View style={styles.pad}><Loading label="Loading draft…" /></View>}
      </View>
    );
  }
  const editable = EDITABLE.includes(t.status);
  const names = Object.keys(files).filter(n => !removed.has(n));

  const clearEdits = () => { setManifest(null); setChanged(new Set()); setRemoved(new Set()); setStale(false); };
  const save = async (): Promise<boolean> => {
    setSaving(true);
    try {
      await orchestrationApi.command('updateDraft', draftChanges(t.ref, manifest, files, changed, removed));
      clearEdits();
      toast('Draft saved', 'ok');
      await reload();
      return true;
    } catch (err) { toast(`Couldn’t save: ${errorText(err)}`, 'danger'); return false; } finally { setSaving(false); }
  };
  /** Lint, tests and review use the saved draft: save first. */
  const guard = (fn: () => void) => async () => { if (!dirty || await save()) fn(); };
  const lint = (action: OrchAction) => { void guard(() => { void run({ ...action, dialog: null } as OrchAction, { args: { ref: t.ref }, done: () => { void reload(); } }); })(); };
  const addFile = () => {
    const clean = newName.trim();
    if (!FILE_NAME.test(clean)) { toast('Use letters, digits, dots, dashes and underscores in file names.', 'danger'); return; }
    setFiles(f => ({ ...f, [clean]: f[clean] && !removed.has(clean) ? f[clean] : '' }));
    setRemoved(r => { const next = new Set(r); next.delete(clean); return next; });
    setChanged(c => new Set(c).add(clean));
    setAdding(false); setNewName('');
  };

  return (
    <View style={{ flex: 1 }} testID="orch-tool-editor">
      <PageHeader testID="orch-tool-editor-head" title={`${t.title} v${t.version}`} back={back}
        state={[...versionState(t), dirty ? { text: 'unsaved changes', tone: 'attention' } : null]} />
      <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
        <MiniStepper steps={stageSteps(t)} />
        <Text variant="caption" tone="ink3">{lintWords(t)} · {testsWords(t)}</Text>
        {stale ? (
          <Banner tone="info" glyph="●" testID="orch-tool-editor-stale" title="This draft changed elsewhere (the AI may have updated it). Your unsaved edits are kept."
            action={{ label: 'Discard my edits and reload', testID: 'orch-tool-editor-reload', onPress: () => { clearEdits(); setFiles({ ...(t.files || {}) }); void reload(); } }} />
        ) : null}
        {t.status === 'in_review' ? <Banner tone="attention" glyph="◆" title="In review: editing is locked. Request changes from the catalog page to reopen it." /> : null}
        {!OPEN_STATUSES.includes(t.status) ? <Banner tone="info" glyph="●" title={`This version is ${t.style?.label || t.status} and can’t be edited. Start a new version from the catalog page.`} /> : null}
        <View style={styles.buttons}>
          {editable ? <Button size="md" variant="primary" icon="check" label="Save draft" disabled={!dirty} loading={saving} onPress={() => { void save(); }} testID="orch-tool-editor-save" /> : null}
          <Button size="md" variant="ghost" icon="message-circle" label={t.session_name ? 'Continue chat' : 'Discovery chat'} onPress={() => setDialog('chat')} testID="orch-tool-editor-chat" />
          <Button size="md" variant="ghost" label="Catalog view" navigates onPress={() => navigation.navigate('OrchTool', { toolId: t.tool_id, version: t.version })} />
        </View>
        <OrchActionBar testID="orch-tool-editor-actions" actions={t.actions} only={['lintDraft', 'testDraft', 'submitReview', 'discardDraft']} run={run} ctx={{
          args: { ref: t.ref },
          done: (_r, action) => { if (action.id === 'discardDraft') navigation.goBack(); else void reload(); },
          dialogs: { test: () => { void guard(() => setDialog('test'))(); } },
          overrides: { submitReview: () => { void guard(() => setDialog('submit'))(); }, lintDraft: lint },
        }} />
        {t.intent ? <Text variant="body" tone="ink2">{t.intent}</Text> : null}

        <CodeEditor label="tool.yaml" language="yaml" value={manifest ?? t.manifest_yaml ?? ''} editable={editable} onChange={setManifest} testID="orch-manifest" />

        <View style={styles.filesHead}>
          <Text variant="label" accessibilityRole="header" style={{ flex: 1 }}>Files</Text>
          {editable ? <Button size="md" variant="ghost" icon="plus" label="Add a file" onPress={() => setAdding(true)} testID="orch-file-add" /> : null}
        </View>
        {names.length ? names.map(name => (
          <View key={name} style={styles.file}>
            <CodeEditor label={`${name}${changed.has(name) ? ' •' : ''}`} language={langFor(name)} value={files[name] ?? ''} editable={editable} testID={`orch-file-${name}`}
              onChange={v => { setFiles(f => ({ ...f, [name]: v })); setChanged(c => new Set(c).add(name)); }} />
            {editable ? <Button size="md" variant="ghost" icon="trash-2" label={`Remove ${name}`} testID={`orch-file-remove-${name}`}
              onPress={() => { setRemoved(r => new Set(r).add(name)); setChanged(c => { const next = new Set(c); next.delete(name); return next; }); }} /> : null}
          </View>
        )) : <Text variant="caption" tone="ink3">No files yet. The entry script named in the manifest goes here.</Text>}
      </ScrollView>
      <Dialog visible={adding} onClose={() => setAdding(false)} title="Add a file" testID="orch-file-dialog"
        footer={<><Button variant="secondary" label="Cancel" onPress={() => setAdding(false)} /><Button variant="primary" label="Add file" onPress={addFile} testID="orch-file-dialog-add" /></>}>
        <Field label="File name" hint="For example rotate.sh or check.py. Letters, digits, dots, dashes and underscores.">
          <TextField value={newName} onChangeText={setNewName} mono autoCapitalize="none" autoCorrect={false} testID="orch-file-name" />
        </Field>
      </Dialog>
      {dialog === 'test' ? <TestToolSheet tool={t as OrchToolDetail} visible onClose={() => setDialog(null)} onProposed={(runId, msg) => { setDialog(null); toast(msg, 'ok'); navigation.navigate('OrchRun', { runId }); }} /> : null}
      {dialog === 'submit' ? <SubmitSheet tool={t} visible onClose={() => setDialog(null)} onSubmitted={() => { setDialog(null); toast(`${t.ref} submitted for review`, 'ok'); void reload(); }} /> : null}
      <DiscoveryChatSheet visible={dialog === 'chat'} onClose={() => setDialog(null)} draftRef={t.ref} />
      {element}
    </View>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  filesHead: { flexDirection: 'row', alignItems: 'center' },
  file: { gap: spacing.xs },
});
