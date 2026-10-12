import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, ScrollView, StyleSheet, TextInput, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import Markdown from 'react-native-markdown-display';
import { jobCoreApi, subscribeToJobChanges, type JobAction, type JobPart, type JobVM } from '../api/jobCore';
import { jobsApi, type JobDiagnosticsSnapshot } from '../api/jobs';
import { Text } from '../components/Text';
import { ModernBottomSheet } from '../components/ModernBottomSheet';
import { JobStageModelsSheet } from '../components/JobStageModelsSheet';
import { JobPlanEditor } from '../components/JobPlanEditor';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';

export type JobDetailScreenProps = NativeStackScreenProps<RootStackParamList, 'JobDetail'>;
const TABS = ['Plan', 'Session', 'Diff', 'Review', 'Checks'] as const;

export function JobDetailScreen({ route, navigation }: JobDetailScreenProps) {
  const { jobId } = route.params;
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const [job, setJob] = useState<JobVM | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState(0);
  const [draft, setDraft] = useState('');
  const [dirty, setDirty] = useState(false);
  const [guidance, setGuidance] = useState('');
  const [findings, setFindings] = useState<string[]>([]);
  const [more, setMore] = useState(false);
  const [models, setModels] = useState(false);
  const [history, setHistory] = useState<number | undefined>();
  const [stage, setStage] = useState('');
  const [diagnostics, setDiagnostics] = useState<JobDiagnosticsSnapshot | null>(null);
  const pager = useRef<ScrollView>(null);
  const draftRef = useRef({ text:'', dirty:false, revision:0, savedRevision:-1, version:0 });
  const requestBusy = useRef(false);

  const load = useCallback(async (parts?: JobPart[]) => {
    try {
      const next = await jobCoreApi.job(jobId, parts);
      setJob(old => ({ ...old, ...next }));
      navigation.setOptions({ title:next.title });
      if (next.plan && !draftRef.current.dirty) {
        const text = next.plan.draft ?? next.plan.markdown;
        draftRef.current = { text, dirty:!!next.plan.draft, revision:0, savedRevision:-1, version:next.plan.version };
        setDraft(text); setDirty(!!next.plan.draft); setHistory(undefined);
      }
    } catch (err) { setError(String(err)); }
  }, [jobId, navigation]);

  useEffect(() => {
    void load();
    const stream = subscribeToJobChanges(change => {
      if (change.scope === 'job' && change.id === jobId) void load(change.parts);
    }, () => { void load(); });
    return () => stream.close();
  }, [jobId, load]);

  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (!draftRef.current.dirty) return;
    event.preventDefault();
    Alert.alert('Unsaved plan edits', 'Leave this plan? Your last autosaved draft is kept.', [
      { text:'Stay', style:'cancel' },
      { text:'Leave', onPress:() => { draftRef.current.dirty=false; navigation.dispatch(event.data.action); } },
    ]);
  }), [navigation]);

  const editable = !!job?.actions.find(a => a.id === 'savePlanDraft' && a.enabled) && history === undefined;
  useEffect(() => {
    const timer = setInterval(async () => {
      const current = { ...draftRef.current };
      if (!editable || !current.dirty || requestBusy.current || current.savedRevision === current.revision) return;
      requestBusy.current = true;
      try {
        await jobCoreApi.command(jobId, 'savePlanDraft', { plan:current.text, version:current.version, autosave:true });
        draftRef.current.savedRevision = current.revision;
      } catch (err) { setError(String(err)); }
      finally { requestBusy.current = false; }
    }, 5000);
    return () => clearInterval(timer);
  }, [jobId, editable]);

  const selectTab = (index: number) => { setTab(index); pager.current?.scrollTo({x:width*index, animated:true}); };
  const run = async (action: JobAction, extra: Record<string, unknown> = {}) => {
    if (!action.enabled || requestBusy.current || !job) return;
    if (action.id === 'approvePlan' && dirty) { setError('Save a plan version before approving.'); return; }
    if (action.id === 'reprompt' && !findings.length) { selectTab(3); setError('Select findings for the implementer.'); return; }
    if (action.id === 'push' && action.args?.requires_override && !guidance.trim()) { setError('Enter an override reason in the guidance field.'); return; }
    requestBusy.current = true; setBusy(true); setError('');
    const current = { ...draftRef.current };
    const args: Record<string, unknown> = action.id === 'savePlanDraft' ? {plan:current.text, version:current.version} :
      action.id === 'approvePlan' ? {version:current.version} : action.id === 'rejectPlan' ? {feedback:guidance} :
      action.id === 'reprompt' ? {finding_ids:findings, guidance} : action.id === 'message' ? {text:guidance} :
      action.id === 'push' ? {override_reason:guidance} : {guidance};
    try {
      const result = await jobCoreApi.command(jobId, action.id, {...action.args, ...args, ...extra});
      if (action.id === 'delete') { draftRef.current.dirty=false; navigation.goBack(); return; }
      if (action.id === 'savePlanDraft') {
        draftRef.current.version = result.data.version || current.version;
        draftRef.current.dirty = current.revision !== draftRef.current.revision;
        setDirty(draftRef.current.dirty);
      }
      await load(result.parts);
    } catch (err) { setError(String(err)); }
    finally { requestBusy.current=false; setBusy(false); }
  };
  const invoke = (action: JobAction) => {
    if (action.id === 'delete' || action.id === 'discard') {
      Alert.alert(action.label, 'Running work is cancelled and the worktree is removed. The Git branch is preserved.', [
        {text:'Cancel', style:'cancel'}, {text:action.label, style:'destructive', onPress:() => { void run(action); }},
      ]);
    } else void run(action);
  };
  const button = (label: string, onPress: () => void, disabled = false) => <TouchableOpacity key={label} accessibilityRole="button" accessibilityState={{disabled}} disabled={disabled} onPress={onPress} style={[styles.button,{borderColor:colors.hairline,opacity:disabled ? .45 : 1}]}><Text variant="xs" style={{color:colors.accent}}>{label}</Text></TouchableOpacity>;
  const actions = (ids: string[]) => <View style={styles.row}>{job?.actions.filter(a => ids.includes(a.id)).map(a => <View key={a.id}>{button(a.label, () => invoke(a), busy || !a.enabled || (a.id === 'approvePlan' && dirty) || (history !== undefined && ['approvePlan','savePlanDraft','rejectPlan'].includes(a.id)))}{!a.enabled && <Text variant="xs" dim style={styles.reason}>{a.reason}</Text>}</View>)}</View>;
  const input = { color:colors.text, borderColor:colors.hairline };
  if (!job) return <View style={styles.page}><ActivityIndicator /><Text>{error}</Text></View>;
  return <SafeAreaView edges={['bottom']} style={styles.root}>
    <View style={styles.header}><Text variant="lg">{job.key} {job.title}</Text><Text variant="xs" dim>{job.state.replace(/_/g,' ')} · ${job.cost_usd.toFixed(2)} · {job.branch}</Text>
      <ScrollView horizontal>{job.pipeline.map((step,index) => <TouchableOpacity key={step.id} accessibilityRole="button" onPress={() => selectTab([0,0,1,4,3,3][index])} style={styles.step}><Text>{step.glyph} {step.label}</Text><Text variant="xs" dim>{Math.floor(step.elapsed/60)}m · ${step.cost_usd.toFixed(2)} · {step.runs} runs</Text></TouchableOpacity>)}</ScrollView>
      {!!job.attention_detail && <Text variant="sm">{job.attention_detail}</Text>}
      {!!error && <Text variant="sm" style={{color:colors.error}}>{error}</Text>}
      <ScrollView horizontal>{TABS.map((title,index) => button((tab===index ? '● ' : '')+title, () => selectTab(index)))}{button('More', () => { setMore(true); void jobsApi.diagnostics(jobId).then(r=>setDiagnostics(r.diagnostics)).catch(e=>setError(String(e))); })}</ScrollView>
    </View>
    <ScrollView horizontal pagingEnabled ref={pager} onMomentumScrollEnd={e => setTab(Math.round(e.nativeEvent.contentOffset.x/width))} showsHorizontalScrollIndicator={false}>
      <ScrollView style={{width}} contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        <Text>Version {job.plan?.version || 0} · approved v{job.plan?.approved_version || 0}{dirty ? ' · Unsaved edits' : ''}</Text>
        <ScrollView horizontal>{button('Latest', () => { if (!dirty) void load(['plan']); },dirty)}{job.plan?.versions.map(version=>button(`v${version}`, () => { void jobCoreApi.job(jobId,['plan'],version).then(next=>{setJob(old=>({...old!,plan:next.plan}));setHistory(version);setDraft(next.plan?.markdown || '');}).catch(e=>setError(String(e))); },dirty))}</ScrollView>
        {editable ? <JobPlanEditor value={draft} onChange={text=>{draftRef.current.text=text;draftRef.current.dirty=true;draftRef.current.revision++;setDraft(text);setDirty(true);}} /> : <Markdown style={{body:{color:colors.text}}}>{job.plan?.markdown || 'Planning has not finished.'}</Markdown>}
        {!!job.plan?.diff_from_previous && <><Text>Changes from previous version</Text><Text variant="xs">{job.plan.diff_from_previous}</Text></>}
        {actions(['savePlanDraft','approvePlan','rejectPlan'])}
        <TextInput accessibilityLabel="Re-plan feedback" placeholder="Re-plan feedback" placeholderTextColor={colors.textDim} multiline value={guidance} onChangeText={setGuidance} style={[styles.input,input]} />
      </ScrollView>
      <ScrollView style={{width}} contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        <View style={styles.row}>{['','plan','implement','review'].map(s=>button(s || 'Live',()=>setStage(s)))}</View>
        {stage ? (job.session?.stages[stage] || []).map((m,i)=><Transcript key={i} role={m.role} text={m.parts.filter(p=>p.type==='text').map(p=>p.text).join('\n')} />) : job.session?.events.map(e=><Transcript key={e.id} role={e.event_type} text={JSON.stringify(e.payload)} />)}
        <TextInput accessibilityLabel="Stage message or resume guidance" placeholder="Message or resume guidance" placeholderTextColor={colors.textDim} multiline value={guidance} onChangeText={setGuidance} style={[styles.input,input]} />
        {job.session?.approval && <><Text>Tool approval: {job.session.approval.tool_name}</Text><Text selectable>{JSON.stringify(job.session.approval.tool_args,null,2)}</Text></>}
        {actions(['approveTool','denyTool','message','resume','restartStage','retry'])}
      </ScrollView>
      <ScrollView style={{width}} contentContainerStyle={styles.page}><Text selectable variant="xs" style={{fontFamily:'monospace'}}>{job.diff?.patch || 'No committed diff yet.'}</Text></ScrollView>
      <ScrollView style={{width}} contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        {job.review ? <><Text variant="lg">{job.review.verdict.replace(/_/g,' ')} · {job.review.merge_readiness}/100</Text><Text>{job.review.summary}</Text>
          {job.review.plan_coverage.map(c=><Text key={c.criterion_id}>{c.criterion_id} · {c.status}\n{c.evidence}</Text>)}
          {job.review.findings.map(f=><View key={f.id} style={[styles.finding,{borderColor:colors.hairline}]}>
            {button(`${findings.includes(f.id) ? '☑' : '☐'} ${f.id} · ${f.severity} · ${f.title}`,()=>setFindings(old=>old.includes(f.id)?old.filter(id=>id!==f.id):[...old,f.id]))}
            {button(`${f.file || 'No file'}:${f.line_start || ''}`,()=>selectTab(2))}<Text>{f.detail}</Text><Text variant="sm" dim>{f.suggestion}</Text><Text>Disposition: {f.disposition}</Text>
            <View style={styles.row}>{['open','accepted','dismissed','fixed'].map(disposition=>button(disposition,()=>{const action=job.actions.find(a=>a.id==='markFindings');if(action) void run(action,{ids:[f.id],disposition});},busy || !job.actions.find(a=>a.id==='markFindings')?.enabled))}</View>
          </View>)}</> : <Text>Review report pending.</Text>}
        <TextInput accessibilityLabel="Guidance or push override reason" placeholder="Guidance or push override reason" placeholderTextColor={colors.textDim} multiline value={guidance} onChangeText={setGuidance} style={[styles.input,input]} />
        {actions(['reprompt','push','recheck','markMerged'])}
      </ScrollView>
      <ScrollView style={{width}} contentContainerStyle={styles.page}>{job.checks?.live?.map(e=><Transcript key={e.id} role={`Live: ${e.payload.suite}`} text={String(e.payload.text || '')} />)}{job.checks?.runs.slice().reverse().map(run=><View key={run.run} style={styles.finding}><Text>Run {run.run} · {run.passed ? '✓ Passed':'✗ Failed'}</Text><Text variant="xs">{run.new_failures} new · {run.preexisting_failures} preexisting</Text>{run.suites.map(s=><View key={s.suite}><Text>{s.passed?'✓':'✗'} {s.suite} {s.preexisting_failures?.length?'· preexisting':''}</Text>{s.junit && <Text variant="xs">{s.junit.passed} passed / {s.junit.failed} failed / {s.junit.skipped} skipped</Text>}<Transcript role={s.command} text={(s.stdout+'\n'+s.stderr).replace(/\u001b\[[0-9;]*m/g,'')} /></View>)}</View>)}{actions(['recheck'])}</ScrollView>
    </ScrollView>
    <ModernBottomSheet visible={more} onClose={()=>setMore(false)} title="Cost & audit">
      {button('Job settings',()=>setModels(true))}{button('Job performance',()=>{setMore(false);navigation.navigate('JobAnalysis',{jobId});})}
      {Object.entries(job.costs?.by_stage || {}).map(([s,c])=><Text key={s}>{s}: ${c.toFixed(2)}</Text>)}
      {job.costs?.events.slice().reverse().map(e=><Transcript key={e.id} role={e.event_type} text={e.reason+'\n'+JSON.stringify(e.payload)} />)}
      <Text variant="lg">Job diagnostics</Text><Text selectable variant="xs">{diagnostics?.worker_log_tail || 'No worker log.'}</Text>
      {actions(['discard','delete'])}
    </ModernBottomSheet>
    <JobStageModelsSheet visible={models} onClose={()=>setModels(false)} onSaved={()=>setModels(false)} />
  </SafeAreaView>;
}

function Transcript({role,text}: {role:string; text:string}) {
  const [open,setOpen] = useState(!role.includes('tool'));
  return <View style={{marginVertical:8}}><TouchableOpacity accessibilityRole="button" accessibilityState={{expanded:open}} onPress={()=>setOpen(!open)}><Text variant="sm">{open?'▾':'▸'} {role}</Text></TouchableOpacity>{open && <Text selectable variant="xs">{text}</Text>}</View>;
}
const styles = StyleSheet.create({
  root:{flex:1}, header:{paddingHorizontal:16,paddingTop:10}, page:{padding:18,gap:12}, row:{flexDirection:'row',flexWrap:'wrap',gap:6},
  button:{borderWidth:1,borderRadius:8,padding:10,margin:3,minHeight:44,justifyContent:'center'}, reason:{maxWidth:160},
  step:{padding:9}, input:{borderWidth:1,borderRadius:8,padding:12,minHeight:70,textAlignVertical:'top'},
  editor:{borderWidth:1,borderRadius:8,padding:12,minHeight:400,textAlignVertical:'top',fontFamily:'monospace'}, finding:{padding:10,borderWidth:1,borderRadius:8,marginVertical:6,gap:6},
});
