// Create, configure or clone an environment (web /containers create dialog; POST /api/containers,
// PUT /api/containers/{name}, then GET /api/containers/jobs/{id} until it is ready). Base is a
// Dockerfile (with its network policy) or a template; folders and hardware are optional. Saving an
// existing environment recreates the worker but keeps named volumes and attached sessions.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { containersApi, type ContainerConfiguration, type ContainerJob, type ContainerTemplateSummary } from '../../api/containers';
import { sessionsApi, type ContainerDevice, type ContainerHardwareCapabilities, type ContainerMount } from '../../api/sessions';
import { ContainerBuildProgress, type ContainerProgressLog } from '../../components/ContainerBuildProgress';
import { ContainerHardwareSection } from '../../components/ContainerHardwareSection';
import { spacing } from '../../theme/tokens';
import { BoardSection, Button, ChoiceRow, Field, PageHeader, Row, Rows, Segmented, TextField, Text, useToast } from '../../ui';
import { splitLines } from './envModel';

export type EnvironmentFormMode = 'create' | 'edit' | 'clone';
type Source = 'dockerfile' | 'template';

const JOB_POLL_MS = 700;

export function EnvironmentFormScreen() {
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const mode: EnvironmentFormMode = route.params?.mode || 'create';
  const sourceName: string | undefined = route.params?.name;
  const toast = useToast();
  const [templates, setTemplates] = useState<ContainerTemplateSummary[]>([]);
  const [hardware, setHardware] = useState<ContainerHardwareCapabilities | null>(null);
  const [name, setName] = useState('');
  const [source, setSource] = useState<Source>(route.params?.template ? 'template' : 'dockerfile');
  const [templateName, setTemplateName] = useState<string>(route.params?.template || '');
  const [dockerfile, setDockerfile] = useState('');
  const [allow, setAllow] = useState('');
  const [deny, setDeny] = useState('');
  const [mounts, setMounts] = useState<ContainerMount[]>([]);
  const [mountHost, setMountHost] = useState('');
  const [mountTarget, setMountTarget] = useState('/workspace/project');
  const [mountMode, setMountMode] = useState<'rw' | 'ro'>('rw');
  const [gpuRequest, setGpuRequest] = useState('');
  const [devices, setDevices] = useState<ContainerDevice[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [job, setJob] = useState<{ message: string; logs: ContainerProgressLog[]; failed: boolean; running: boolean } | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [listing, defaults] = await Promise.all([containersApi.list(), sessionsApi.getContainerDefaults().catch(() => null)]);
        if (cancelled) return;
        setTemplates(listing.templates || []);
        setHardware(defaults?.hardware || null);
        if ((mode === 'edit' || mode === 'clone') && sourceName) {
          const config: ContainerConfiguration = await containersApi.configuration(sourceName);
          if (cancelled) return;
          setName(mode === 'clone' ? `${config.container_name.replace(/^mucli-/, '')}-copy` : config.container_name);
          setSource(config.template_name ? 'template' : 'dockerfile');
          setTemplateName(config.template_name || '');
          setDockerfile(config.dockerfile || '');
          setAllow((config.egress_allow || []).join('\n'));
          setDeny((config.egress_deny || []).join('\n'));
          setMounts(config.mounts || []);
          setGpuRequest(config.gpu_request || '');
          setDevices(config.devices || []);
        } else {
          setDockerfile(defaults?.dockerfile || '');
          setAllow((defaults?.egress_allow || []).join('\n'));
          setDeny((defaults?.egress_deny || []).join('\n'));
          if (route.params?.template) setName(`${route.params.template}-env`);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [mode, route.params?.template, sourceName]);

  const addMount = () => {
    const host = mountHost.trim();
    const target = mountTarget.trim();
    if (!host || !target) return;
    if (mounts.some(m => m.host_path === host || m.container_path === target)) { setError('That host folder or container target is already mounted.'); return; }
    setMounts(m => [...m, { host_path: host, container_path: target, mode: mountMode }]);
    setMountHost('');
    setMountTarget('/workspace/project');
    setError('');
  };

  const pollJob = useCallback(async (jobId: string) => {
    let after = 0;
    for (;;) {
      const next: ContainerJob = await containersApi.job(jobId, after);
      if (!alive.current) return;
      const incoming = next.logs || [];
      for (const line of incoming) after = Math.max(after, line.seq);
      setJob(j => ({ message: next.message || next.stage, logs: [...(j?.logs || []), ...incoming], failed: false, running: true }));
      if (next.state === 'ready') return;
      if (next.state === 'error') throw new Error(next.detail || next.message || 'The build failed');
      await new Promise(resolve => setTimeout(resolve, JOB_POLL_MS));
    }
  }, []);

  const editing = mode === 'edit';
  const canSubmit = !!name.trim() && (source === 'dockerfile' ? !!dockerfile.trim() : !!templateName) && !job?.running;

  const submit = useCallback(async () => {
    if (!name.trim()) { setError('Name the environment.'); return; }
    setError('');
    setJob({ message: editing ? 'Updating environment…' : 'Creating environment…', logs: [], failed: false, running: true });
    try {
      const payload = {
        name: name.trim(),
        template_name: source === 'template' ? templateName : null,
        dockerfile: source === 'dockerfile' ? dockerfile : null,
        mounts,
        gpu_request: gpuRequest,
        devices,
        egress_allow: source === 'dockerfile' ? splitLines(allow) : null,
        egress_deny: source === 'dockerfile' ? splitLines(deny) : null,
        start: true,
      };
      const result = editing && sourceName ? await containersApi.update(sourceName, payload) : await containersApi.create(payload);
      await pollJob(result.job_id);
      if (!alive.current) return;
      toast(editing ? `${payload.name} updated` : `${payload.name} is ready`, 'ok');
      const target = { name: editing ? sourceName : payload.name };
      if (typeof navigation.replace === 'function') navigation.replace('EnvironmentDetail', target);
      else navigation.goBack();
    } catch (err) {
      if (!alive.current) return;
      setJob(j => ({ message: 'The build failed', logs: j?.logs || [], failed: true, running: false }));
      setLogsOpen(true);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [allow, deny, devices, dockerfile, editing, gpuRequest, mounts, name, navigation, pollJob, source, sourceName, templateName, toast]);

  const title = editing ? `Configure ${sourceName}` : mode === 'clone' ? `Clone ${sourceName}` : 'New environment';
  return (
    <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === 'ios' ? 'padding' : undefined} testID="environment-form">
      <PageHeader title={title} back={{ label: 'Back', onPress: () => navigation.goBack() }}
        state={[{ text: editing ? 'Saving recreates the worker; named volumes and sessions stay.' : 'A standalone container, not tied to a session.', tone: 'quiet' }]} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {loading ? <Text variant="caption" tone="ink3">Loading…</Text> : null}
        <Field label="Name">
          <TextField value={name} onChangeText={setName} editable={!editing} autoCapitalize="none" autoCorrect={false} placeholder="research-box" testID="env-name" />
        </Field>
        <Field label="Base">
          <Segmented<Source> accessibilityLabel="Base" value={source} onChange={setSource} testID="env-source"
            options={[{ value: 'dockerfile', label: 'Dockerfile', testID: 'env-source-dockerfile' }, { value: 'template', label: 'Template', testID: 'env-source-template' }]} />
        </Field>
        {source === 'template' ? (
          <Field label="Template" hint={templates.length ? undefined : 'No templates yet: create one from an environment.'}>
            <View>
              {templates.map((t, i) => (
                <ChoiceRow key={t.name} index={i} label={t.name} description={t.description || t.image} selected={templateName === t.name} onPress={() => setTemplateName(t.name)} testID={`env-template-${t.name}`} />
              ))}
            </View>
          </Field>
        ) : (
          <>
            <Field label="Dockerfile">
              <TextField value={dockerfile} onChangeText={setDockerfile} multiline mono autoCapitalize="none" autoCorrect={false} testID="env-dockerfile" style={styles.editor} />
            </Field>
            <Field label="Network allow" hint="One domain per line." optional>
              <TextField value={allow} onChangeText={setAllow} multiline mono autoCapitalize="none" autoCorrect={false} testID="env-allow" />
            </Field>
            <Field label="Network block" hint="One domain per line." optional>
              <TextField value={deny} onChangeText={setDeny} multiline mono autoCapitalize="none" autoCorrect={false} testID="env-deny" />
            </Field>
          </>
        )}

        <BoardSection title="Folders" count={mounts.length}>
          <Rows>
            {mounts.map(m => (
              <Row key={m.host_path} title={m.host_path} sub={`${m.container_path} · ${m.mode === 'ro' ? 'read only' : 'read and write'}`}
                actions={<Button label="Remove" variant="ghost" size="md" onPress={() => setMounts(list => list.filter(x => x !== m))} accessibilityLabel={`Remove ${m.host_path}`} />} />
            ))}
          </Rows>
        </BoardSection>
        <Field label="Host folder" optional>
          <TextField value={mountHost} onChangeText={setMountHost} autoCapitalize="none" autoCorrect={false} placeholder="/home/user/dev/project" mono testID="env-mount-host" />
        </Field>
        <Field label="Inside the environment">
          <TextField value={mountTarget} onChangeText={setMountTarget} autoCapitalize="none" autoCorrect={false} mono testID="env-mount-target" />
        </Field>
        <View style={styles.mountRow}>
          <View style={styles.grow}>
            <Segmented<'rw' | 'ro'> accessibilityLabel="Access" value={mountMode} onChange={setMountMode}
              options={[{ value: 'rw', label: 'Read and write' }, { value: 'ro', label: 'Read only' }]} />
          </View>
          <Button label="Add folder" variant="secondary" onPress={addMount} disabled={!mountHost.trim()} testID="env-mount-add" />
        </View>

        {hardware ? (
          <ContainerHardwareSection capabilities={hardware} gpuRequest={gpuRequest} onGpuRequestChange={setGpuRequest} devices={devices} onDevicesChange={setDevices} />
        ) : null}

        {job ? <ContainerBuildProgress message={job.message} logs={job.logs} expanded={logsOpen} onToggle={() => setLogsOpen(o => !o)} running={job.running} failed={job.failed} /> : null}
        {error ? <Text variant="caption" accessibilityRole="alert" style={styles.error} tone="danger" testID="env-error">{error}</Text> : null}
        <Button label={job?.running ? 'Building…' : editing ? 'Save and recreate' : 'Create environment'} variant="primary" onPress={() => void submit()}
          disabled={!canSubmit} loading={!!job?.running} testID="env-submit" />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['4xl'], gap: spacing.sm },
  editor: { minHeight: 200 },
  mountRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.md },
  grow: { flex: 1 },
  error: { marginVertical: spacing.sm },
});
