// Check run artifacts on a phone (mirrors mu/gui/static/js/work/artifacts.js; plan §5 ArtifactViewer):
//
//   ArtifactBrowser   the files of one checks run as rows, grouped (suite output, then what the
//                     suites produced), with a filter; tapping one opens the viewer full-screen
//   ArtifactViewer    one file through the view suited to its kind: log/text (find, errors only,
//                     first error), JUnit (passed/failed/skipped filter, case details), XML/JSON
//                     text, CSV table, image, packet capture (packets with protocol filter, and
//                     conversations), archive contents, hex
//   EvidenceList      what the agents captured while testing (images inline, text opens)
//
// Everything comes from the server preview and renders as text: HTML reports show their source
// (never executed); only images load the raw file. Text views can be shared.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Image, ScrollView, Share, StyleSheet, View } from 'react-native';
import type { EvidenceItem } from '../api/jobCore';
import {
  artifactImageSource, checkArtifactsApi, evidenceImageSource, evidenceText, filterPackets, firstArtifact, groupArtifacts, junitCases, kindLabel, sizeLabel, textLines, viewModes,
  type ArtifactFile, type ArtifactListing, type ArtifactPreview, type JUnitFilter, type ViewMode,
} from '../api/checkArtifacts';
import { saveCheckArtifact, saveEvidenceFile, type SaveOutcome } from '../api/artifactDownload';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing } from '../theme/tokens';
import { Button } from './Button';
import { Loading } from './Card';
import { Banner, useToast } from './Feedback';
import { CheckRow, TextField } from './Form';
import { BoardSection, Row } from './Mu';
import { Pressable } from './Pressable';
import { Sheet } from './Sheet';
import { Segmented } from './Tabs';
import { Text } from './Text';

/** "Save to phone": downloads with the device token and stores it in a folder the user picks. */
function SaveButton({ save, testID }: { save: () => Promise<SaveOutcome>; testID: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const out = await save();
      if (out.saved) toast(`Saved ${out.name}`, 'ok');
      else if (out.reason === 'too-large') toast('Too large for the phone. Download it from the web GUI on the computer.', 'attention');
    } catch (e) {
      toast(`Couldn't save it: ${e instanceof Error ? e.message : String(e)}`, 'danger');
    } finally {
      setBusy(false);
    }
  };
  return <Button small variant="ghost" label={busy ? 'Saving…' : 'Save to phone'} icon="download" loading={busy} disabled={busy} onPress={() => void run()} testID={testID} />;
}

/** The files of one checks run; opens a file in the full-screen viewer. */
export function ArtifactBrowser({ jobId, run, testID = 'artifacts' }: { jobId: string; run: number; testID?: string }) {
  const [listing, setListing] = useState<ArtifactListing | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('');
  const [open, setOpen] = useState<ArtifactFile | null>(null);
  useEffect(() => {
    let alive = true;
    setListing(null); setError('');
    checkArtifactsApi.listing(jobId, run)
      .then(l => { if (alive) setListing(l); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [jobId, run]);
  if (error) return <Text variant="caption" tone="ink3" testID={`${testID}-none`}>No artifacts for run {run} ({error}).</Text>;
  if (!listing) return <Loading label="Loading artifacts…" />;
  const groups = groupArtifacts(listing.files, filter);
  const first = firstArtifact(listing.files);
  return (
    <View testID={testID} style={styles.col}>
      <Text variant="caption" tone="ink3">
        {listing.files.length} file{listing.files.length === 1 ? '' : 's'} · {listing.collected} produced · {sizeLabel(listing.bytes || 0)}
      </Text>
      {listing.files.length > 6 ? (
        <TextField accessibilityLabel="Filter artifacts" placeholder="Filter files…" value={filter} onChangeText={setFilter} autoCapitalize="none" autoCorrect={false} testID={`${testID}-filter`} />
      ) : null}
      {groups.length ? groups.map(g => (
        <BoardSection key={g.title} title={g.title} count={g.files.length}>
          {g.files.map(f => (
            <Row key={f.path} title={f.name} sub={`${kindLabel(f.kind)} · ${sizeLabel(f.bytes || 0)}`} selected={f.path === first?.path && !open}
              onPress={() => setOpen(f)} accessibilityLabel={`${f.name}, ${kindLabel(f.kind)}, ${sizeLabel(f.bytes || 0)}`} testID={`${testID}-file-${f.path}`} />
          ))}
        </BoardSection>
      )) : <Text variant="caption" tone="ink3">No file matches.</Text>}
      {listing.skipped.length ? (
        <Text variant="caption" tone="ink3">{listing.skipped.length} not kept: {listing.skipped.slice(0, 5).map(s => `${s.name} (${s.reason})`).join(', ')}</Text>
      ) : null}
      <Sheet visible={!!open} onClose={() => setOpen(null)} title={open?.name || ''} full scroll={false} testID={`${testID}-viewer-sheet`}>
        {open ? <ArtifactViewer jobId={jobId} run={run} file={open} /> : null}
      </Sheet>
    </View>
  );
}

/** What the agents captured while testing, newest first: images inline, text opens, other files listed. */
export function EvidenceList({ jobId, items, testID = 'evidence' }: { jobId: string; items: EvidenceItem[]; testID?: string }) {
  const [open, setOpen] = useState<EvidenceItem | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open || open.kind !== 'text') return undefined;
    let alive = true;
    setText(null); setError('');
    evidenceText(jobId, open.file).then(t => { if (alive) setText(t); }).catch(e => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [jobId, open]);
  if (!items.length) return null;
  const meta = (e: EvidenceItem) => [e.stage, e.kind, e.bytes ? sizeLabel(e.bytes) : ''].filter(Boolean).join(' · ');
  return (
    <View testID={testID} style={styles.col}>
      {items.map(e => (
        <View key={e.id} style={styles.col}>
          {e.kind === 'image' ? <Image accessibilityLabel={e.caption || e.name} source={evidenceImageSource(jobId, e.file)} resizeMode="contain" style={styles.evidenceImage} testID={`${testID}-image-${e.id}`} /> : null}
          <Row title={e.caption || e.name} sub={meta(e)} testID={`${testID}-${e.id}`}
            onPress={e.kind === 'text' || e.kind === 'image' ? () => setOpen(e) : undefined}
            accessibilityLabel={`${e.caption || e.name}, ${meta(e)}`} />
          {e.kind !== 'text' && e.kind !== 'image' ? <SaveButton save={() => saveEvidenceFile(jobId, e)} testID={`${testID}-save-${e.id}`} /> : null}
        </View>
      ))}
      <Sheet visible={!!open} onClose={() => setOpen(null)} title={open?.caption || open?.name || ''} full scroll={open?.kind !== 'image'} testID={`${testID}-sheet`}>
        {open?.kind === 'image' ? <Image accessibilityLabel={open.caption || open.name} source={evidenceImageSource(jobId, open.file)} resizeMode="contain" style={styles.image} /> : null}
        {open?.kind === 'text' ? (error ? <Banner tone="danger" title="Couldn't open it">{error}</Banner> : text == null ? <Loading label="Opening…" /> : <Text variant="monoSm" selectable testID={`${testID}-text`}>{text}</Text>) : null}
      </Sheet>
    </View>
  );
}

/** One artifact file in the view suited to its kind. */
export function ArtifactViewer({ jobId, run, file }: { jobId: string; run: number; file: Pick<ArtifactFile, 'path' | 'name'> }) {
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<ViewMode | null>(null);
  useEffect(() => {
    let alive = true;
    setPreview(null); setError(''); setMode(null);
    checkArtifactsApi.preview(jobId, run, file.path)
      .then(p => { if (alive) setPreview(p); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [jobId, run, file.path]);
  if (error) return <Banner tone="danger" title="Couldn't open it" testID="artifact-error">{error}</Banner>;
  if (!preview) return <Loading label="Opening…" />;
  const modes = viewModes(preview);
  const current = mode && modes.some(m => m[0] === mode) ? mode : modes[0][0];
  const meta = [kindLabel(preview.kind), sizeLabel(preview.bytes || 0), preview.truncated ? 'partly shown' : '', preview.note || ''].filter(Boolean).join(' · ');
  return (
    <View style={styles.viewer} testID="artifact-viewer">
      <View style={styles.head}>
        <Text variant="caption" tone="ink3" style={styles.flex}>{meta}</Text>
        {preview.text != null ? <Button small variant="ghost" label="Share" icon="external-link" onPress={() => { void Share.share({ message: preview.text || '', title: file.name }); }} testID="artifact-share" /> : null}
        <SaveButton save={() => saveCheckArtifact(jobId, run, { path: file.path, name: file.name, bytes: preview.bytes })} testID="artifact-save" />
      </View>
      {preview.kind === 'html' ? <Text variant="caption" tone="ink3">HTML reports aren't rendered on the phone: this is its source. Save it to open it elsewhere.</Text> : null}
      {preview.error ? <Text variant="caption" tone="attention">{preview.error}</Text> : null}
      {modes.length > 1 ? (
        <Segmented<ViewMode> accessibilityLabel="View as" value={current} onChange={setMode} options={modes.map(([value, label]) => ({ value, label, testID: `artifact-mode-${value}` }))} />
      ) : null}
      <View style={styles.flex}>
        {current === 'text' ? <TextView preview={preview} /> : null}
        {current === 'junit' ? <JUnitView preview={preview} /> : null}
        {current === 'table' ? <TableView rows={preview.rows || []} truncated={!!preview.truncated} /> : null}
        {current === 'image' ? <Image accessibilityLabel={file.name} source={artifactImageSource(jobId, run, preview.path)} resizeMode="contain" style={styles.image} testID="artifact-image" /> : null}
        {current === 'packets' ? <PacketsView preview={preview} /> : null}
        {current === 'talks' ? (
          <TableView testID="artifact-talks" rows={[['Endpoint A', 'Endpoint B', 'Protocol', 'Packets', 'Bytes'],
            ...(preview.conversations || []).map(t => [t.a, t.b, t.proto, String(t.packets), sizeLabel(t.bytes)])]} empty="No IP conversations." />
        ) : null}
        {current === 'archive' ? (
          <TableView testID="artifact-archive" rows={[[`${preview.archive || 'archive'} entry`, 'Size'], ...(preview.entries || []).map(e => [e.name, e.dir ? '' : sizeLabel(e.bytes || 0)])]}
            truncated={!!preview.truncated} />
        ) : null}
        {current === 'hex' ? (
          <ScrollView><Text variant="monoSm" selectable testID="artifact-hex">{preview.hex || ''}</Text><Text variant="caption" tone="ink3">The first 4 KB.</Text></ScrollView>
        ) : null}
      </View>
    </View>
  );
}

function TextView({ preview }: { preview: ArtifactPreview }) {
  const { colors } = useTheme();
  const [find, setFind] = useState('');
  const [problems, setProblems] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const lineY = useRef<Record<number, number>>({});
  const { lines, total } = useMemo(() => textLines(preview.text || '', { find, problems }), [preview.text, find, problems]);
  const firstError = lines.find(l => l.level === 'error');
  return (
    <View style={[styles.flex, styles.col]}>
      <TextField accessibilityLabel="Show lines containing" placeholder="Show lines containing…" value={find} onChangeText={setFind} autoCapitalize="none" autoCorrect={false} testID="artifact-find" />
      <View style={styles.tools}>
        <CheckRow label="errors and warnings only" checked={problems} onChange={setProblems} testID="artifact-problems" />
        <Button small variant="ghost" label="First error" disabled={!firstError} testID="artifact-first-error"
          onPress={() => { if (firstError) scroller.current?.scrollTo({ y: lineY.current[firstError.no] || 0, animated: true }); }} />
      </View>
      <Text variant="caption" tone="ink3" testID="artifact-count">{find || problems ? `${lines.length.toLocaleString()} of ${total.toLocaleString()} lines` : `${total.toLocaleString()} lines`}</Text>
      <ScrollView ref={scroller} style={[styles.flex, styles.box, { borderColor: colors.hairline, backgroundColor: colors.code }]} testID="artifact-text">
        <ScrollView horizontal>
          <View>
            {lines.length ? lines.map(l => (
              <View key={l.no} onLayout={e => { lineY.current[l.no] = e.nativeEvent.layout.y; }}
                style={[styles.line, l.level === 'error' ? { backgroundColor: colors.danger.wash } : l.level === 'warn' ? { backgroundColor: colors.attention.wash } : null]}>
                <Text variant="monoSm" tone="ink3" numeric style={styles.lineNo}>{l.no}</Text>
                <Text variant="monoSm" selectable accessibilityLabel={l.level ? `${l.level} line ${l.no}: ${l.text}` : undefined}>{l.text || ' '}</Text>
              </View>
            )) : <Text variant="caption" tone="ink3">No line matches.</Text>}
          </View>
        </ScrollView>
      </ScrollView>
    </View>
  );
}

function JUnitView({ preview }: { preview: ArtifactPreview }) {
  const t = preview.junit!.totals;
  const bad = t.failures + t.errors;
  const [filter, setFilter] = useState<JUnitFilter>(bad ? 'problems' : 'all');
  const [open, setOpen] = useState<string | null>(null);
  const cases = junitCases(preview, filter);
  const passed = t.tests - bad - t.skipped;
  return (
    <ScrollView style={styles.flex} contentContainerStyle={styles.col} testID="artifact-junit">
      <Text variant="label">
        <Text variant="label" tone="ok">✓ {passed} passed</Text>{'   '}<Text variant="label" tone={bad ? 'danger' : 'ink3'}>✕ {bad} failed</Text>{t.skipped ? <Text variant="label" tone="ink3">{`   – ${t.skipped} skipped`}</Text> : null}
      </Text>
      <Segmented<JUnitFilter> accessibilityLabel="Show" value={filter} onChange={setFilter}
        options={[{ value: 'problems', label: 'Failures' }, { value: 'all', label: 'All' }, { value: 'passed', label: 'Passed' }, { value: 'skipped', label: 'Skipped' }]} />
      {cases.length ? cases.slice(0, 2000).map((c, i) => {
        const key = `${c.suite}:${c.classname}:${c.name}:${i}`;
        const details = !!(c.message || c.detail);
        const mark = c.status === 'passed' ? '✓' : c.status === 'skipped' ? '–' : '✕';
        const tone = c.status === 'passed' ? 'ok' : c.status === 'skipped' ? 'ink3' : 'danger';
        return (
          <View key={key}>
            <Pressable accessibilityRole="button" accessibilityState={{ expanded: open === key, disabled: !details }} disabled={!details}
              accessibilityLabel={`${c.name}, ${c.status}`} onPress={() => setOpen(open === key ? null : key)} style={styles.case}>
              <Text variant="label" tone={tone}>{mark}</Text>
              <View style={styles.flex}>
                <Text variant="mono" numberOfLines={open === key ? undefined : 2}>{c.name}</Text>
                <Text variant="caption" tone="ink3" numberOfLines={1}>{[c.classname || c.suite, c.time != null ? `${c.time.toFixed(2)} s` : ''].filter(Boolean).join(' · ')}</Text>
              </View>
            </Pressable>
            {open === key ? (
              <View style={styles.col}>
                {c.message ? <Text variant="caption" tone="danger" selectable>{c.message}</Text> : null}
                {c.detail ? <Text variant="monoSm" selectable>{c.detail}</Text> : null}
                {c.stdout ? <Text variant="monoSm" tone="ink3" selectable>{c.stdout}</Text> : null}
              </View>
            ) : null}
          </View>
        );
      }) : <Text variant="caption" tone="ink3">No test matches.</Text>}
      {cases.length > 2000 ? <Text variant="caption" tone="ink3">First 2,000 of {cases.length} shown.</Text> : null}
    </ScrollView>
  );
}

function PacketsView({ preview }: { preview: ArtifactPreview }) {
  const [proto, setProto] = useState('');
  const [find, setFind] = useState('');
  const rows = filterPackets(preview, proto, find);
  const protos = Object.entries(preview.protocols || {}).slice(0, 10);
  return (
    <View style={[styles.flex, styles.col]} testID="artifact-packets">
      <Text variant="caption" tone="ink3">{(preview.total || 0).toLocaleString()} packets · {preview.format}</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tools}>
        {protos.map(([k, n]) => (
          <Button key={k} small variant={proto === k ? 'primary' : 'secondary'} label={`${k} ${n}`} accessibilityState={{ selected: proto === k }}
            onPress={() => setProto(proto === k ? '' : k)} testID={`artifact-proto-${k}`} />
        ))}
      </ScrollView>
      <TextField accessibilityLabel="Filter packets" placeholder="Filter: address, port, text…" value={find} onChangeText={setFind} autoCapitalize="none" autoCorrect={false} />
      <TableView rows={[['No.', 'Time', 'Source', 'Destination', 'Protocol', 'Length', 'Info'],
        ...rows.slice(0, 2000).map(k => [String(k.no), k.time.toFixed(6), k.src || '', k.dst || '', k.proto, String(k.length), k.info || ''])]}
        empty="No packet matches." />
      {preview.truncated ? <Text variant="caption" tone="ink3">First {(preview.packets || []).length.toLocaleString()} packets shown. Save the capture to open it in Wireshark.</Text> : null}
    </View>
  );
}

/** A plain table: first row is the header; scrolls both ways. */
function TableView({ rows, truncated, empty = 'Empty.', testID = 'artifact-table' }: { rows: string[][]; truncated?: boolean; empty?: string; testID?: string }) {
  const { colors } = useTheme();
  const [head, ...body] = rows;
  const widths = useMemo(() => (head || []).map((_, i) => Math.min(280, Math.max(56, ...rows.slice(0, 200).map(r => String(r[i] ?? '').length * 7 + 16)))), [rows, head]);
  return (
    <ScrollView style={styles.flex} testID={testID}>
      <ScrollView horizontal>
        <View>
          <View style={[styles.tr, { borderBottomColor: colors.hairlineStrong }]}>
            {(head || []).map((h, i) => <Text key={i} variant="label" style={[styles.td, { width: widths[i] }]} numberOfLines={1}>{h}</Text>)}
          </View>
          {body.length ? body.map((r, ri) => (
            <View key={ri} style={[styles.tr, { borderBottomColor: colors.hairline }]}>
              {r.map((c, i) => <Text key={i} variant="monoSm" selectable style={[styles.td, { width: widths[i] }]} numberOfLines={3}>{c}</Text>)}
            </View>
          )) : <Text variant="caption" tone="ink3" style={styles.td}>{empty}</Text>}
        </View>
      </ScrollView>
      {truncated ? <Text variant="caption" tone="ink3">Partly shown.</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  col: { gap: spacing.sm },
  flex: { flex: 1 },
  viewer: { flex: 1, gap: spacing.sm, padding: spacing.base },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  tools: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  box: { borderWidth: 1, borderRadius: radii.sm },
  line: { flexDirection: 'row', gap: spacing.sm, paddingHorizontal: spacing.xs },
  lineNo: { minWidth: 40, textAlign: 'right' },
  case: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, minHeight: 48, paddingVertical: spacing.xs },
  image: { flex: 1, minHeight: 240 },
  evidenceImage: { width: '100%', height: 200, borderRadius: radii.sm },
  tr: { flexDirection: 'row', borderBottomWidth: StyleSheet.hairlineWidth },
  td: { paddingHorizontal: spacing.xs, paddingVertical: spacing.xs },
});
