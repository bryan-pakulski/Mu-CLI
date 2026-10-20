// Check run artifacts (mu/jobs/check_artifacts.py, mu/gui/routers/jobs.py): everything a checks run
// produced — suite output, JUnit reports and the files the suites wrote — with a server-built
// preview per file. Artifacts come from code under test: the app only ever renders the preview
// data as text/tables; HTML is never executed and raw files are only fetched for images.

import { api, baseUrl } from './client';
import { authHeaders } from './auth';

export type ArtifactKind = 'log' | 'text' | 'junit' | 'xml' | 'json' | 'csv' | 'html' | 'image' | 'pcap' | 'archive' | 'binary' | string;

export interface ArtifactFile {
  path: string; name: string; kind: ArtifactKind; bytes: number;
  /** 'suite output' for suite logs/JUnit, else the collecting label ('suites' or a suite name). */
  label?: string; suites?: string[];
}
export interface ArtifactListing {
  run: number; files: ArtifactFile[]; skipped: Array<{ name: string; reason: string }>; collected: number; bytes: number;
}
export interface JUnitCase { classname: string; name: string; time: number | null; status: 'passed' | 'failed' | 'error' | 'skipped' | string; message: string; detail: string; stdout: string }
export interface JUnitSuite { name: string; time: number | null; tests: number; failures: number; errors: number; skipped: number; cases: JUnitCase[] }
export interface Packet { no: number; time: number; src?: string; dst?: string; proto: string; length: number; info?: string }
export interface Conversation { a: string; b: string; proto: string; packets: number; bytes: number }
export interface ArtifactPreview {
  path: string; name?: string | null; kind: ArtifactKind; bytes: number;
  text?: string; truncated?: boolean; pretty?: boolean; note?: string; error?: string;
  rows?: string[][];
  junit?: { suites: JUnitSuite[]; totals: { tests: number; failures: number; errors: number; skipped: number } };
  format?: string; packets?: Packet[]; total?: number; protocols?: Record<string, number>; conversations?: Conversation[];
  archive?: string; entries?: Array<{ name: string; bytes?: number; dir?: boolean }>;
  hex?: string;
}

const enc = encodeURIComponent;
const runPath = (jobId: string, run: number) => `/api/jobs/${enc(jobId)}/checks/${enc(String(run))}`;

export const checkArtifactsApi = {
  listing: (jobId: string, run: number) => api.get<ArtifactListing>(`${runPath(jobId, run)}/artifacts`),
  preview: (jobId: string, run: number, path: string) => api.get<ArtifactPreview>(`${runPath(jobId, run)}/preview`, { query: { path } }),
};

/** An image artifact as an authenticated <Image> source (images are the only raw files the app loads). */
export function artifactImageSource(jobId: string, run: number, path: string) {
  const base = baseUrl();
  return { uri: `${base}${runPath(jobId, run)}/files/${path.split('/').map(enc).join('/')}`, headers: authHeaders(base) };
}

/** Human size: 512 B, 1.5 KB, 2.0 MB (as the web viewer). */
export function sizeLabel(n: number): string {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

export const ARTIFACT_KIND_LABEL: Record<string, string> = {
  log: 'Log', text: 'Text', junit: 'JUnit', xml: 'XML', json: 'JSON', csv: 'Table', html: 'HTML report', image: 'Image',
  pcap: 'Packet capture', archive: 'Archive', binary: 'Binary',
};
export const kindLabel = (kind: string) => ARTIFACT_KIND_LABEL[kind] || ARTIFACT_KIND_LABEL.binary;

/** Listing groups in the web's order: suite output, then what the suites produced. */
export function groupArtifacts(files: ArtifactFile[], filter = ''): Array<{ title: string; files: ArtifactFile[] }> {
  const q = filter.trim().toLowerCase();
  const groups = new Map<string, ArtifactFile[]>();
  for (const f of files) {
    const g = f.label === 'suite output' ? 'Suite output' : f.label === 'suites' ? 'Produced by the suites' : `Produced by ${(f.suites || [])[0] || f.label || 'the run'}`;
    if (q && !`${f.name} ${f.kind}`.toLowerCase().includes(q)) continue;
    groups.set(g, [...(groups.get(g) || []), f]);
  }
  return [...groups].map(([title, list]) => ({ title, files: list }));
}

/** The file to open first: the first produced artifact, else the first suite log. */
export function firstArtifact(files: ArtifactFile[]): ArtifactFile | undefined {
  return files.find(f => !f.path.startsWith('suite-')) || files[0];
}

export type ViewMode = 'text' | 'junit' | 'html' | 'image' | 'packets' | 'talks' | 'table' | 'archive' | 'hex';

/** Views a preview offers, first = default (mirrors work/artifacts.js modesFor). HTML shows its
 * source only: the phone never renders an untrusted report. */
export function viewModes(p: ArtifactPreview): Array<[ViewMode, string]> {
  switch (p.kind) {
    case 'junit': return p.junit ? [['junit', 'Tests'], ['text', 'XML']] : [['text', 'XML']];
    case 'html': return [['text', 'Source']];
    case 'image': return [['image', 'Image']];
    case 'pcap': return p.packets ? [['packets', 'Packets'], ['talks', 'Conversations']] : [['hex', 'Hex']];
    case 'csv': return p.rows ? [['table', 'Table']] : [['text', 'Text']];
    case 'archive': return p.entries ? [['archive', 'Contents']] : p.text != null ? [['text', 'Text']] : [['hex', 'Hex']];
    case 'binary': return [['hex', 'Hex']];
    default: return p.text != null ? [['text', p.kind === 'log' ? 'Log' : 'Text']] : [['hex', 'Hex']];
  }
}

const LEVEL = /\b(ERROR|ERR|FATAL|FAIL(?:ED|URE)?|Traceback|Exception|panic)\b/;
const WARN = /\b(WARN(?:ING)?|DEPRECAT\w*)\b/;
export type TextLine = { no: number; text: string; level: 'error' | 'warn' | '' };

/** Log lines with their level, filtered by text and "errors and warnings only". */
export function textLines(text: string, opts: { find?: string; problems?: boolean; limit?: number } = {}): { lines: TextLine[]; total: number } {
  const all = String(text || '').split('\n');
  const find = (opts.find || '').toLowerCase();
  const out: TextLine[] = [];
  for (let i = 0; i < all.length && out.length < (opts.limit ?? 5000); i++) {
    const line = all[i];
    const level = LEVEL.test(line) ? 'error' : WARN.test(line) ? 'warn' : '';
    if (opts.problems && !level) continue;
    if (find && !line.toLowerCase().includes(find)) continue;
    out.push({ no: i + 1, text: line, level });
  }
  return { lines: out, total: all.length };
}

export type JUnitFilter = 'problems' | 'all' | 'passed' | 'skipped';

export function junitCases(p: ArtifactPreview, filter: JUnitFilter): Array<JUnitCase & { suite: string }> {
  const cases = (p.junit?.suites || []).flatMap(s => s.cases.map(c => ({ ...c, suite: s.name })));
  return cases.filter(c => filter === 'all' || (filter === 'problems' ? ['failed', 'error'].includes(c.status) : c.status === filter));
}

export function filterPackets(p: ArtifactPreview, proto: string, find: string): Packet[] {
  const q = find.trim().toLowerCase();
  return (p.packets || []).filter(k => (!proto || k.proto === proto) && (!q || `${k.src || ''} ${k.dst || ''} ${k.proto} ${k.info || ''}`.toLowerCase().includes(q)));
}

/* ------------------------------------------------------------ evidence */

const evidencePath = (jobId: string, file: string) => `/api/jobs/${enc(jobId)}/evidence/${file.split('/').map(enc).join('/')}`;

/** An evidence image as an authenticated <Image> source. */
export function evidenceImageSource(jobId: string, file: string) {
  const base = baseUrl();
  return { uri: `${base}${evidencePath(jobId, file)}`, headers: authHeaders(base) };
}

/** A text evidence file (logs the agent captured; secrets were redacted when it was saved). */
export function evidenceText(jobId: string, file: string): Promise<string> {
  return api.get<string>(evidencePath(jobId, file), { query: { view: 'text' } }).then(v => (typeof v === 'string' ? v : JSON.stringify(v, null, 2)));
}
