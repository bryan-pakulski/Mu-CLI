import React, { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { jobCoreApi, optional, type JobAction, type PushArgs, type PushPreview } from '../api/jobCore';
import { useTheme } from '../theme/ThemeContext';
import { fonts, radii, spacing } from '../theme/tokens';
import { verdictStyle } from '../theme/status';
import { Banner, Button, Field, KV, Loading, Pill, Sheet, SwitchRow, Text, TextField } from '../ui';

export type JobPushDialogProps = {
  visible: boolean;
  jobId: string;
  action: JobAction | null;
  busy?: boolean;
  onClose: () => void;
  onSubmit: (args: PushArgs) => void;
};

/**
 * Wash push dialog (CONTRACT §2): prefilled from
 * GET /api/jobs/vm/job/{id}/push-preview, editable PR/MR title and body, a
 * draft switch and — when the review blocks the push — a required override
 * reason. Older servers without push-preview still get the dialog with the
 * server defaults (empty fields fall back to them).
 */
export function JobPushDialog({ visible, jobId, action, busy, onClose, onSubmit }: JobPushDialogProps) {
  const { colors } = useTheme();
  const [preview, setPreview] = useState<PushPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [missing, setMissing] = useState(false);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [draft, setDraft] = useState(true);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setLoading(true); setError(''); setReason(''); setMissing(false);
    optional(jobCoreApi.pushPreview(jobId))
      .then(next => {
        if (!alive) return;
        setPreview(next);
        setMissing(!next);
        setTitle(next?.title || '');
        setBody(next?.body || '');
        setDraft(next ? next.draft !== false : true);
      })
      .catch(err => { if (alive) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [visible, jobId]);

  const needsOverride = !!(preview?.requires_override || action?.args?.requires_override);
  const submit = () => {
    if (needsOverride && !reason.trim()) { setError('Enter a reason for overriding the blocking review.'); return; }
    if (title.length > 200) { setError('Title must be 200 characters or fewer.'); return; }
    const args: PushArgs = { pr_title: title.trim(), pr_body: body, draft };
    if (needsOverride) args.override_reason = reason.trim();
    onSubmit(args);
  };
  const [vTone, vLabel] = verdictStyle(preview?.verdict);

  return (
    <Sheet visible={visible} onClose={onClose} title={action?.label || 'Push'}>
      {loading ? <Loading label="Preparing the pull request…" /> : (
        <View style={styles.root} testID="job-push-dialog">
          {preview ? (
            <View style={[styles.meta, { borderColor: colors.hairline, backgroundColor: colors.canvas }]}>
              <KV label="Branch"><Text variant="xs" style={{ fontFamily: fonts.mono }}>{preview.branch || '—'} → {preview.base || '—'}</Text></KV>
              <KV label="Publish via"><Text variant="xs">{preview.mode || 'none'}{preview.remote ? ` · ${preview.remote}` : ''}</Text></KV>
              {preview.head_sha ? <KV label="Verified head"><Text variant="xs" style={{ fontFamily: fonts.mono }}>{preview.head_sha.slice(0, 12)}</Text></KV> : null}
              {preview.verdict ? <KV label="Review"><Pill tone={vTone}>{vLabel}</Pill></KV> : null}
            </View>
          ) : null}
          {missing ? <Banner tone="muted" glyph="ℹ" title="This server has no push preview. Leave the fields blank to use the default title and description." /> : null}
          {preview?.reason ? <Banner tone="warn" glyph="◆" title={preview.reason} /> : null}
          {preview?.token && !preview.token.source ? (
            <Banner tone="warn" glyph="◆" testID="push-token-missing"
              title={`No ${preview.token.variable} is set, so the branch will be pushed but no ${preview.token.provider === 'gitlab' ? 'merge request' : 'pull request'} can be opened. Add a ${preview.token.provider} token in Settings → Push on the web.`} />
          ) : null}
          <Field label="Title" hint="Pull request / merge request title.">
            <TextField accessibilityLabel="Pull request title" value={title} onChangeText={setTitle} maxLength={200} placeholder="Default title" />
          </Field>
          <Field label="Description" optional>
            <TextField accessibilityLabel="Pull request description" value={body} onChangeText={setBody} multiline maxLength={20000} placeholder="Default description" style={{ minHeight: 140 }} />
          </Field>
          <SwitchRow testID="push-draft-switch" label="Open as draft" hint="Turn off to open a ready-for-review pull request." value={draft} onValueChange={setDraft} />
          {needsOverride ? (
            <Field label="Override reason" hint="The review blocks this push. Say why it is safe to publish anyway." error={error && !reason.trim() ? error : undefined}>
              <TextField accessibilityLabel="Override reason" value={reason} onChangeText={t => { setReason(t); setError(''); }} multiline invalid={!!error && !reason.trim()} />
            </Field>
          ) : null}
          {error && !(needsOverride && !reason.trim()) ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
          <View style={styles.actions}>
            <Button label="Cancel" onPress={onClose} />
            <Button primary tone={needsOverride ? 'warn' : 'ok'} loading={busy} label={needsOverride ? 'Override and push' : draft ? 'Push draft' : 'Push'} onPress={submit} testID="push-submit" />
          </View>
        </View>
      )}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  root: { gap: spacing.sm },
  meta: { borderWidth: 1, borderRadius: radii.control, padding: spacing.sm, gap: 2, marginBottom: spacing.xs },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.sm },
});
