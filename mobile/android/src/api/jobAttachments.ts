// Files for the New job sheet (mu/jobs/attachments.py): picked on the phone and sent base64-encoded
// in the create request, or a saved session's attachments and artifacts referenced by id (the
// server copies them). The same limits as the server, checked before anything is read.

import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';

import { artifactsApi } from './artifacts';
import { attachmentsApi } from './attachments';
import type { JobAttachmentInput } from './jobs';

export const MAX_JOB_ATTACHMENTS = 20;
export const MAX_JOB_ATTACHMENT_BYTES = 25 * 1024 * 1024;

/** One row of the sheet's attachment list; `input` is what the create request carries. */
export interface JobAttachmentDraft { key: string; name: string; size?: number; session?: string; input: JobAttachmentInput }

/** A session file the job can take (attachment or artifact). */
export interface SessionFileOption { key: string; name: string; size?: number; kind: 'attachment' | 'artifact' | 'chart'; input: JobAttachmentInput }

export type PickResult = { added: JobAttachmentDraft[]; skipped: string[] };

/** Pick files on the phone; files over the size limit are skipped (named in `skipped`). */
export async function pickJobFiles(room: number): Promise<PickResult> {
  const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
  if (result.canceled) return { added: [], skipped: [] };
  const added: JobAttachmentDraft[] = [];
  const skipped: string[] = [];
  for (const asset of result.assets || []) {
    if (added.length >= room) { skipped.push(asset.name); continue; }
    if (asset.size != null && asset.size > MAX_JOB_ATTACHMENT_BYTES) { skipped.push(asset.name); continue; }
    const content = await FileSystem.readAsStringAsync(asset.uri, { encoding: FileSystem.EncodingType.Base64 });
    added.push({
      key: `upload:${asset.uri}`, name: asset.name, size: asset.size ?? undefined,
      input: { name: asset.name, content_base64: content, ...(asset.mimeType ? { mime_type: asset.mimeType } : {}) },
    });
  }
  return { added, skipped };
}

/** A saved session's attachments and artifacts, as job attachment choices. */
export async function sessionFiles(session: string): Promise<SessionFileOption[]> {
  const [att, art] = await Promise.all([attachmentsApi.list(session), artifactsApi.list(session)]);
  return [
    ...(att.attachments || []).map(a => ({ key: `${session}:attachment:${a.attachment_id}`, name: a.name, size: a.size, kind: 'attachment' as const,
      input: { session, attachment_id: a.attachment_id } })),
    ...(art.artifacts || []).map(a => ({ key: `${session}:artifact:${a.artifact_id}`, name: a.name, size: a.size,
      kind: a.kind === 'visualization' ? 'chart' as const : 'artifact' as const, input: { session, artifact_id: a.artifact_id } })),
  ];
}

export function formatBytes(n?: number): string {
  if (n == null) return '';
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}
