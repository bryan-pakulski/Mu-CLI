// Skills: every installed skill, enabled or not; enable/disable, details in a sheet, delete after
// confirming.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { skillsApi, type Skill } from '../api/skills';
import { Fact, PanelPage, PanelSection } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Button, IconButton, Pre, Row, Sheet, useServerActions, useToast } from '../ui';

export function SkillsScreen() {
  const toast = useToast();
  const actions = useServerActions();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try { setError(null); setSkills((await skillsApi.list()).skills); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const toggle = async (skill: Skill) => {
    try {
      await (skill.enabled ? skillsApi.disable(skill.name) : skillsApi.enable(skill.name));
      void load();
    } catch (e) {
      toast(`Couldn't ${skill.enabled ? 'disable' : 'enable'} ${skill.name}: ${String(e)}`, 'danger');
    }
  };

  const remove = async (name: string) => {
    if (!(await actions.ask({ id: 'delete', label: 'Delete', danger: true, confirm: `Delete "${name}"?` }))) return;
    await skillsApi.delete(name);
    void load();
  };

  const enabled = skills.filter(s => s.enabled).length;
  return (
    <PanelPage testID="skills-screen" title="Skills" state={[{ text: `${enabled} enabled` }, { text: `${skills.length - enabled} off`, tone: 'quiet' }]}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && !skills.length ? { title: 'No skills', message: 'No skills installed.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={actions.element}>
      <PanelSection title="Installed" count={skills.length}>
        {skills.map(item => (
          <Row key={item.name} testID={`skill-${item.name}`} status={item.enabled ? 'done' : 'skipped'} title={item.name} sub={item.description}
            when={item.enabled ? 'enabled' : 'off'} onPress={() => setSelected(item)}
            actions={(
              <View style={styles.actions}>
                <Button label={item.enabled ? 'Disable' : 'Enable'} variant="secondary" onPress={() => void toggle(item)} testID={`skill-${item.name}-toggle`} />
                <IconButton icon="trash-2" label={`Remove skill ${item.name}`} onPress={() => void remove(item.name)} />
              </View>
            )} />
        ))}
      </PanelSection>
      <Sheet visible={!!selected} onClose={() => setSelected(null)} title={selected?.name} testID="skill-sheet">
        {selected ? (
          <View style={styles.sheet}>
            <Fact label="Trigger" value={selected.trigger} />
            <Fact label="Source" value={selected.source} mono />
            <Pre>{selected.body.length > 500 ? `${selected.body.slice(0, 500)}…` : selected.body}</Pre>
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sheet: { gap: spacing.sm },
});
