import React, { useEffect, useMemo, useState } from 'react';
import { StyleSheet, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import type { ArtifactDescriptor } from '../api/artifacts';
import { artifactsApi } from '../api/artifacts';
import { openExternalUrl } from '../api/urlSafety';
import { authHeaders } from '../api/auth';
import { baseUrl } from '../api/client';
import { navigateTo } from '../navigation/nav';
import { shareServerFile } from '../api/artifactDownload';
import type { ThemeColors } from '../theme/tokens';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, radii, spacing, touchTarget } from '../theme/tokens';
import { ModalFrame, Pressable, Text, Icon, InkDrop, useToast } from '../ui';

const MUCLI_MOBILE_VISUALIZATION_CONTROLS_V1 = true;

interface Props {
  artifact: ArtifactDescriptor;
  sessionName: string;
  onInteractionChange?: (active: boolean) => void;
}

interface InteractiveWebViewProps {
  uri: string;
  colors: ThemeColors;
  /** Links the visualization itself opens (outside the server). */
  onOpenExternal: (target?: string) => void;
  /** When the inline preview fails: the in-app viewer (a browser has no device token). */
  onOpenFallback: () => void;
  onInteractionChange?: (active: boolean) => void;
}

const SCROLL_CONTAINMENT_SCRIPT = `
(() => {
  const apply = () => {
    document.documentElement.style.overscrollBehavior = 'contain';
    if (document.body) document.body.style.overscrollBehavior = 'contain';
  };
  apply();
  document.addEventListener('DOMContentLoaded', apply, { once: true });
})();
true;
`;

function InteractiveWebView({
  uri,
  colors,
  onOpenExternal,
  onOpenFallback,
  onInteractionChange,
}: InteractiveWebViewProps) {
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setLoading(true);
    setFailed(false);
  }, [uri]);

  const finishInteraction = () => onInteractionChange?.(false);

  return (
    <View
      style={styles.webviewHost}
      onTouchStart={() => onInteractionChange?.(true)}
      onTouchEnd={finishInteraction}
      onTouchCancel={finishInteraction}
    >
      {failed ? (
        <View style={styles.fallback}>
          <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
          <Text variant="sm" dim style={styles.fallbackText}>Inline preview failed.</Text>
          <Pressable onPress={() => onOpenFallback()} style={{ minHeight: touchTarget, justifyContent: 'center' }}>
            <Text variant="sm" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>
              Open in session files
            </Text>
          </Pressable>
        </View>
      ) : (
        <>
          <WebView
            // The device token on the preview's request (the page's scripts can't read it).
            source={{ uri, headers: authHeaders(baseUrl()) }}
            style={[styles.webview, { backgroundColor: colors.canvas }]}
            originWhitelist={['http://*', 'https://*']}
            javaScriptEnabled
            domStorageEnabled
            cacheEnabled={false}
            incognito
            sharedCookiesEnabled={false}
            thirdPartyCookiesEnabled={false}
            allowFileAccess={false}
            allowUniversalAccessFromFileURLs={false}
            javaScriptCanOpenWindowsAutomatically={false}
            setSupportMultipleWindows={false}
            mixedContentMode="never"
            scrollEnabled
            nestedScrollEnabled
            showsVerticalScrollIndicator
            showsHorizontalScrollIndicator
            overScrollMode="content"
            injectedJavaScriptBeforeContentLoaded={SCROLL_CONTAINMENT_SCRIPT}
            onLoadEnd={() => setLoading(false)}
            onError={() => {
              setLoading(false);
              setFailed(true);
              finishInteraction();
            }}
            onHttpError={() => {
              setLoading(false);
              setFailed(true);
              finishInteraction();
            }}
            onShouldStartLoadWithRequest={(request) => {
              if (request.url === uri || request.url === 'about:blank') return true;
              onOpenExternal(request.url);
              return false;
            }}
          />
          {loading ? (
            <View style={[styles.loading, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
              <InkDrop />
            </View>
          ) : null}
        </>
      )}
    </View>
  );
}

function VisualizationCardImpl({
  artifact,
  sessionName,
  onInteractionChange,
}: Props) {
  const { colors, isDark } = useTheme();
  const { height: windowHeight } = useWindowDimensions();
  const [expanded, setExpanded] = useState(false);
  const [fullScreen, setFullScreen] = useState(false);
  const uri = useMemo(
    () => `${artifactsApi.viewUrl(sessionName, artifact.artifact_id)}?mucli_theme=${isDark ? 'dark' : 'light'}`,
    [artifact.artifact_id, isDark, sessionName],
  );
  const frameHeight = Math.max(
    220,
    Math.min(Number(artifact.height) || 480, Math.min(620, windowHeight * 0.62)),
  );

  const openExternal = (target: string = uri) => {
    void openExternalUrl(target);
  };
  const toast = useToast();
  // The HTML file through the share sheet, downloaded with the device token (a browser has none).
  const saveOrShare = () => {
    shareServerFile(artifactsApi.downloadUrl(sessionName, artifact.artifact_id), artifact.name, artifact.mime_type)
      .catch(error => toast(`Couldn\u2019t download ${artifact.name}: ${error instanceof Error ? error.message : String(error)}`, 'danger'));
  };

  const hidePreview = () => {
    onInteractionChange?.(false);
    setExpanded(false);
  };

  const openFullScreen = () => {
    onInteractionChange?.(false);
    setFullScreen(true);
  };

  return (
    <>
      <View style={[styles.card, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
        <View style={styles.header}>
          <View style={styles.titleWrap}>
            <Text variant="xs" dim style={styles.eyebrow}>Visualization</Text>
            <Text numberOfLines={1} style={[styles.title, { color: colors.ink }]}>
              {artifact.title || artifact.name}
            </Text>
          </View>

          <View style={styles.headerActions}>
            <Pressable
              onPress={openFullScreen}
              style={[styles.iconButton, { backgroundColor: colors.sunken }]}
              accessibilityRole="button"
              accessibilityLabel="Open visualization full screen"
            >
              <Icon name="maximize-2" size="sm" color={colors.ink3} />
            </Pressable>
            <Pressable
              onPress={saveOrShare}
              style={[styles.iconButton, { backgroundColor: colors.sunken }]}
              accessibilityRole="button"
              accessibilityLabel="Save or share visualization"
            >
              <Icon name="download" size="sm" color={colors.ink3} />
            </Pressable>
          </View>
        </View>

        {!expanded ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Show interactive visualization preview"
            onPress={() => setExpanded(true)}
            style={styles.previewPrompt}
          >
            <Icon name="eye" size="md" color={colors.accent.ink} />
            <Text variant="sm" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>
              Show interactive preview
            </Text>
          </Pressable>
        ) : (
          <>
            <View style={styles.previewActions}>
              <Pressable
                onPress={hidePreview}
                style={styles.textAction}
                accessibilityRole="button"
                accessibilityLabel="Hide visualization preview"
              >
                <Icon name="eye-off" size="sm" color={colors.ink3} />
                <Text variant="xs" style={{ color: colors.ink2, fontWeight: fontWeights.semibold }}>
                  Hide
                </Text>
              </Pressable>
              <Pressable
                onPress={openFullScreen}
                style={styles.textAction}
                accessibilityRole="button"
                accessibilityLabel="Open visualization full screen"
              >
                <Icon name="maximize-2" size="sm" color={colors.ink3} />
                <Text variant="xs" style={{ color: colors.ink2, fontWeight: fontWeights.semibold }}>
                  Full screen
                </Text>
              </Pressable>
            </View>
            <View style={[styles.frame, { height: frameHeight, borderColor: colors.hairline }]}>
              <InteractiveWebView
                uri={uri}
                colors={colors}
                onOpenExternal={openExternal}
                onOpenFallback={() => navigateTo('Artifacts', { view: artifact.artifact_id })}
                onInteractionChange={onInteractionChange}
              />
            </View>
          </>
        )}
      </View>

      <ModalFrame transparent={false}
        visible={fullScreen}
        animationType="fade"
        presentationStyle="fullScreen"
        statusBarTranslucent
        onRequestClose={() => setFullScreen(false)}
      >
        <SafeAreaView style={[styles.fullScreenRoot, { backgroundColor: colors.canvas }]}>
          <View style={[styles.fullScreenHeader, { borderColor: colors.hairline }]}>
            <View style={styles.fullScreenTitleWrap}>
              <Text variant="xs" dim style={styles.eyebrow}>Visualization</Text>
              <Text numberOfLines={1} style={[styles.fullScreenTitle, { color: colors.ink }]}>
                {artifact.title || artifact.name}
              </Text>
            </View>
            <Pressable
              onPress={saveOrShare}
              style={[styles.iconButton, { backgroundColor: colors.sunken }]}
              accessibilityRole="button"
              accessibilityLabel="Save or share visualization"
            >
              <Icon name="download" size="sm" color={colors.ink3} />
            </Pressable>
            <Pressable
              onPress={() => setFullScreen(false)}
              style={[styles.closeButton, { backgroundColor: colors.sunken }]}
              accessibilityRole="button"
              accessibilityLabel="Exit full-screen visualization"
            >
              <Icon name="x" size="lg" color={colors.ink} />
            </Pressable>
          </View>
          <View style={styles.fullScreenFrame}>
            <InteractiveWebView
              uri={uri}
              colors={colors}
              onOpenExternal={openExternal}
              onOpenFallback={() => navigateTo('Artifacts', { view: artifact.artifact_id })}
            />
          </View>
        </SafeAreaView>
      </ModalFrame>
    </>
  );
}

export const VisualizationCard = React.memo(
  VisualizationCardImpl,
  (previous, next) => (
    previous.sessionName === next.sessionName
    && previous.artifact.artifact_id === next.artifact.artifact_id
    && previous.artifact.title === next.artifact.title
    && previous.artifact.name === next.artifact.name
    && previous.artifact.height === next.artifact.height
    && previous.onInteractionChange === next.onInteractionChange
  ),
);

const styles = StyleSheet.create({
  card: {
    marginHorizontal: spacing.base,
    marginVertical: spacing.sm,
    borderRadius: radii.panel,
    overflow: 'hidden',
  },
  header: {
    minHeight: 54,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  titleWrap: { flex: 1, minWidth: 0 },
  eyebrow: { fontWeight: fontWeights.semibold, marginBottom: 2 },
  title: { fontSize: fontSizes.base, fontWeight: fontWeights.semibold },
  headerActions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconButton: {
    width: touchTarget,
    height: touchTarget,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeButton: {
    width: touchTarget,
    height: touchTarget,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  previewPrompt: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  previewActions: {
    minHeight: touchTarget,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: spacing.xs,
  },
  textAction: {
    minHeight: touchTarget,
    paddingHorizontal: spacing.md,
    borderRadius: radii.sm,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  frame: {
    borderTopWidth: 1,
    position: 'relative',
  },
  webviewHost: { flex: 1, position: 'relative' },
  webview: { flex: 1 },
  loading: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
  },
  fallback: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
  },
  fallbackText: { marginTop: spacing.sm, marginBottom: spacing.sm },
  fullScreenRoot: { flex: 1 },
  fullScreenHeader: {
    minHeight: 64,
    paddingHorizontal: spacing.md,
    borderBottomWidth: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  fullScreenTitleWrap: { flex: 1, minWidth: 0 },
  fullScreenTitle: { fontSize: fontSizes.base, fontWeight: fontWeights.semibold },
  fullScreenFrame: { flex: 1 },
});
