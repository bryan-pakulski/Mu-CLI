// A workspace panel screen (Teacher, Research, Memory, Files, …) as a Mu page: the PageHeader
// (title, state sentence, tabs, ⋯, back to the chat) over one scrolling body. Loading, error and
// empty states are the page's, so every panel says them the same way. The route hides the
// navigator header (routes/chat.tsx).

import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { spacing } from '../../theme/tokens';
import { BoardSection, Chip, EmptyState, ErrorState, PageHeader, Row, Skeleton, Text, type PageHeaderProps } from '../../ui';

export type PanelPageProps = Omit<PageHeaderProps, 'back'> & {
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** Shown instead of the body (e.g. no active loop). */
  empty?: { title: string; message?: string; actionLabel?: string; onAction?: () => void } | null;
  refreshing?: boolean;
  onRefresh?: () => void;
  /** false: the body manages its own scrolling (lists, editors). */
  scroll?: boolean;
  /** Pinned under the header, above the scrolling body (search, filters). */
  toolbar?: React.ReactNode;
  /** Floats over the body (PrimaryPill, composer). */
  overlay?: React.ReactNode;
  children?: React.ReactNode;
};

export function PanelPage({ loading, error, onRetry, empty, refreshing = false, onRefresh, scroll = true, toolbar, overlay, children, testID, ...header }: PanelPageProps) {
  const navigation = useNavigation<any>();
  const back = navigation?.canGoBack?.() ? { label: 'Chat', onPress: () => navigation.goBack() } : undefined;
  let body: React.ReactNode;
  if (loading) body = <View style={styles.pad}>{[1, 2, 3].map(i => <Skeleton key={i} height={56} />)}</View>;
  else if (error) body = <ErrorState message={error} onRetry={onRetry} />;
  else if (empty) body = <EmptyState title={empty.title} message={empty.message} actionLabel={empty.actionLabel} onAction={empty.onAction} />;
  else if (!scroll) body = <View style={styles.flex}>{children}</View>;
  else {
    body = (
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"
        refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} /> : undefined}>
        {children}
      </ScrollView>
    );
  }
  return (
    <View style={styles.flex} testID={testID}>
      <PageHeader {...header} back={back} testID={testID ? `${testID}-header` : undefined} />
      {toolbar && !loading && !error && !empty ? <View style={styles.toolbar}>{toolbar}</View> : null}
      {body}
      {overlay}
    </View>
  );
}

/** A section of a panel: a quiet title and count, then rows (BoardSection, with its empty line). */
export function PanelSection({ title, count, empty, right, children, testID }: {
  title: string; count?: number; empty?: string; right?: React.ReactNode; children?: React.ReactNode; testID?: string;
}) {
  return <BoardSection title={title} count={count} empty={empty} right={right} testID={testID}>{children}</BoardSection>;
}

/** A recorded entry (note, finding, checkpoint): content, then "↳ source" and its tags. */
export function EntryRow({ content, source, tags, status, when, onPress, actions, testID }: {
  content: string; source?: string | null; tags?: string[]; status?: string; when?: string; onPress?: () => void; actions?: React.ReactNode; testID?: string;
}) {
  const sub = source || tags?.length ? (
    <View style={styles.entrySub}>
      {source ? <Text variant="monoSm" tone="ink3" numberOfLines={2}>↳ {source}</Text> : null}
      {tags?.length ? <View style={styles.tags}>{tags.map(t => <Chip key={t} variant="meta" label={t} />)}</View> : null}
    </View>
  ) : undefined;
  return <Row status={status} title={content} sub={sub} when={when} onPress={onPress} actions={actions} testID={testID} />;
}

/** A label: value fact line (no stat tiles). */
export function Fact({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <View style={styles.fact}>
      <Text variant="caption" tone="ink3" style={styles.factLabel}>{label}</Text>
      <Text variant={mono ? 'monoSm' : 'body'} tone="ink2" style={styles.flex} selectable>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  pad: { padding: spacing.base, gap: spacing.sm },
  content: { padding: spacing.base, paddingBottom: spacing['2xl'] },
  toolbar: { paddingHorizontal: spacing.base, paddingTop: spacing.sm, gap: spacing.sm },
  entrySub: { gap: spacing.xs },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  fact: { flexDirection: 'row', gap: spacing.sm, paddingVertical: spacing.xs },
  factLabel: { width: 112 },
});
