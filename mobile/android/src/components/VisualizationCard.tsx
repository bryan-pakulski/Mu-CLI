import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  StyleSheet,
  TouchableOpacity,
  useWindowDimensions,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { WebView } from 'react-native-webview';
import type { ArtifactDescriptor } from '../api/artifacts';
import { artifactsApi } from '../api/artifacts';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

interface Props {
  artifact: ArtifactDescriptor;
  sessionName: string;
}

export function VisualizationCard({ artifact, sessionName }: Props) {
  const { colors } = useTheme();
  const { height: windowHeight } = useWindowDimensions();
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const uri = useMemo(
    () => artifactsApi.viewUrl(sessionName, artifact.artifact_id),
    [artifact.artifact_id, sessionName],
  );
  const frameHeight = Math.max(
    220,
    Math.min(Number(artifact.height) || 480, Math.min(620, windowHeight * 0.62)),
  );

  const openExternal = (target: string = uri) => {
    if (!/^https?:\/\//i.test(target)) return;
    void Linking.openURL(target).catch(() => setFailed(true));
  };

  return (
    <View style={[styles.card, { borderColor: colors.border, backgroundColor: colors.bgLift }]}>
      <View style={styles.header}>
        <View style={styles.titleWrap}>
          <Text variant="xs" dim style={styles.eyebrow}>VISUALIZATION</Text>
          <Text numberOfLines={1} style={[styles.title, { color: colors.text }]}>
            {artifact.title || artifact.name}
          </Text>
        </View>
        <TouchableOpacity
          onPress={() => openExternal()}
          style={[styles.openButton, { backgroundColor: colors.bgHover }]}
          accessibilityRole="button"
          accessibilityLabel="Open visualization in browser"
        >
          <Ionicons name="open-outline" size={16} color={colors.textDim} />
        </TouchableOpacity>
      </View>

      <View style={[styles.frame, { height: frameHeight, borderColor: colors.border }]}>
        {failed ? (
          <View style={styles.fallback}>
            <Ionicons name="warning-outline" size={20} color={colors.error} />
            <Text variant="sm" dim style={styles.fallbackText}>Inline preview failed.</Text>
            <TouchableOpacity onPress={() => openExternal()}>
              <Text variant="sm" style={{ color: colors.accent, fontWeight: '600' }}>Open in browser</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <>
            <WebView
              source={{ uri }}
              style={styles.webview}
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
              onLoadEnd={() => setLoading(false)}
              onError={() => { setLoading(false); setFailed(true); }}
              onHttpError={() => { setLoading(false); setFailed(true); }}
              onShouldStartLoadWithRequest={(request) => {
                if (request.url === uri || request.url === 'about:blank') return true;
                openExternal(request.url);
                return false;
              }}
            />
            {loading ? (
              <View style={[styles.loading, { backgroundColor: colors.bgLift }]}>
                <ActivityIndicator color={colors.accent} />
              </View>
            ) : null}
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: 16,
    marginVertical: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 14,
    overflow: 'hidden',
  },
  header: {
    minHeight: 54,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  titleWrap: { flex: 1, minWidth: 0 },
  eyebrow: { fontWeight: '700', letterSpacing: 0.8, marginBottom: 2 },
  title: { fontSize: 14, fontWeight: '600' },
  openButton: {
    width: 34,
    height: 34,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  frame: { borderTopWidth: StyleSheet.hairlineWidth, position: 'relative' },
  webview: { flex: 1, backgroundColor: '#ffffff' },
  loading: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
  fallback: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  fallbackText: { marginTop: 8, marginBottom: 10 },
});
