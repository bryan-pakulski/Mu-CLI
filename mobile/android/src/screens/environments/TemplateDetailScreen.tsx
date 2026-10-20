// A template's page (web /containers templateMain + aside): its source and image, the job fields
// the server lets you edit (description, job setup commands, network allowlist; PATCH
// /api/containers/templates/{name}, jobs already started keep their snapshot), "Use as a base"
// and delete.

import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { containersApi, type ContainerTemplateSummary } from '../../api/containers';
import { spacing } from '../../theme/tokens';
import {
  ActionBar, BoardSection, Button, EmptyState, ErrorState, Field, KV, PageHeader, Pre, TextField, useServerActions, useToast, type ServerAction,
} from '../../ui';
import { splitLines } from './envModel';

const MAX_SETUP = 20;

export function TemplateDetailScreen() {
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const name: string = route.params?.name;
  const focused = useIsFocused();
  const toast = useToast();
  const { ask, element } = useServerActions();
  const [item, setItem] = useState<ContainerTemplateSummary | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState('');
  const [description, setDescription] = useState('');
  const [setup, setSetup] = useState('');
  const [allow, setAllow] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);

  const apply = (t: ContainerTemplateSummary) => {
    setItem(t);
    setDescription(t.description || '');
    setSetup((t.job_setup || []).join('\n'));
    setAllow((t.egress_allow || []).join('\n'));
  };

  const load = useCallback(async () => {
    try {
      const listing = await containersApi.list();
      const found = listing.templates.find(t => t.name === name) || null;
      if (found) apply(found);
      setMissing(!found);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [name]);
  useEffect(() => { if (focused) void load(); }, [focused, load]);

  const dirty = !!item && (description !== (item.description || '') || setup !== (item.job_setup || []).join('\n') || allow !== (item.egress_allow || []).join('\n'));
  const commands = splitLines(setup);

  const save = useCallback(async () => {
    if (!item) return;
    if (commands.length > MAX_SETUP) { setSaveError(`At most ${MAX_SETUP} setup commands.`); return; }
    setSaving(true);
    setSaveError('');
    try {
      const res = await containersApi.patchTemplate(item.name, { description: description.trim(), job_setup: commands, egress_allow: splitLines(allow) });
      if (res?.template) apply(res.template); else await load();
      toast('Template saved', 'ok');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [allow, commands, description, item, load, toast]);

  const actions: ServerAction[] = item ? [
    { id: 'use', label: 'Use as a base', primary: true },
    { id: 'remove', label: 'Delete template', danger: true, confirm: `Delete the template ${item.name}? Environments made from it keep running.` },
  ] : [];

  const onAction = async (action: ServerAction) => {
    if (!item) return;
    if (action.id === 'use') { navigation.navigate('EnvironmentForm', { mode: 'create', template: item.name }); return; }
    if (!(await ask(action))) return;
    try { await containersApi.removeTemplate(item.name); toast(`${item.name} deleted`, 'ok'); navigation.goBack(); }
    catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
  };

  const back = { label: 'Environments', onPress: () => navigation.goBack() };
  if (error && !item) return <View style={styles.page}><PageHeader title={name} back={back} /><ErrorState message={error} onRetry={() => void load()} /></View>;
  if (missing) return <View style={styles.page}><PageHeader title={name} back={back} /><EmptyState title="This template is gone" message="It was deleted on the computer." icon="layers" pigment="meadow" /></View>;

  const used = Number(item?.jobs_using || 0);
  return (
    <View style={styles.page} testID="template-detail">
      <PageHeader title={name} back={back} state={[{ text: 'Template' }, item?.description ? { text: item.description, tone: 'quiet' } : null, used ? { text: `used by ${used} job${used === 1 ? '' : 's'}` } : null]} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <ActionBar testID="template-actions" actions={actions} onAction={a => void onAction(a)} />
        {item ? (
          <BoardSection title="Template">
            <View>
              <KV label="Source">{item.source_container || '—'}</KV>
              <KV label="Image"><Pre>{item.image || '—'}</Pre></KV>
              <KV label="Jobs">{used ? `used by ${used} job${used === 1 ? '' : 's'}` : 'not used by a job'}</KV>
            </View>
          </BoardSection>
        ) : null}
        {item ? (
          <BoardSection title="For jobs">
            <View>
              <Field label="Description" optional>
                <TextField value={description} onChangeText={setDescription} testID="template-description" />
              </Field>
              <Field label="Job setup" hint={`One command per line, run in the job's sandbox before work starts (at most ${MAX_SETUP}). Jobs already started keep the snapshot they took.`} optional>
                <TextField value={setup} onChangeText={setSetup} multiline mono autoCapitalize="none" autoCorrect={false} testID="template-setup" />
              </Field>
              <Field label="Network allowlist" hint="One domain per line; jobs from this template may reach them." optional error={saveError || undefined}>
                <TextField value={allow} onChangeText={setAllow} multiline mono autoCapitalize="none" autoCorrect={false} testID="template-allow" />
              </Field>
              <Button label={saving ? 'Saving…' : 'Save'} variant="primary" disabled={!dirty || saving} loading={saving} onPress={() => void save()} testID="template-save" />
            </View>
          </BoardSection>
        ) : null}
      </ScrollView>
      {element}
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['4xl'], gap: spacing.md },
});
