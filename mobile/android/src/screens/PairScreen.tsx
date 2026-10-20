// Pair with mucli (documentation/plans/mobile_parity.md §7a): the phone signs in by pairing.
//
// On the computer, Settings → Devices → Pair a device (or `mucli gui pair`) shows a QR code and an
// 8-character code. Scan the QR (it carries the GUI address and the code) or type both. The code
// is exchanged for this phone's token, kept in the secure store. A `mucli://pair?...` link (the QR
// opened from the system camera) lands here with the fields filled in.

import React, { useCallback, useEffect, useState } from 'react';
import { Platform, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { devicesApi, type PairedDevice } from '../api/devices';
import { normalizeBase, normalizeCode, parsePairingLink } from '../api/auth';
import { checkHealth } from '../api/client';
import { reconnectAllStreams } from '../api/sse';
import { validateBaseUrl } from '../api/urlSafety';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { spacing, radii } from '../theme/tokens';
import { Banner, Button, Field, PageHeader, Sheet, Text, TextField, useToast } from '../ui';

export function defaultDeviceName(): string {
  const c = (Platform.constants || {}) as { Brand?: string; Model?: string; Manufacturer?: string };
  const name = [c.Brand || c.Manufacturer, c.Model].filter(Boolean).join(' ').trim();
  return name || (Platform.OS === 'ios' ? 'iPhone' : 'Android phone');
}

export function PairScreen() {
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const { colors } = useTheme();
  const toast = useToast();
  const store = useConnectionStore();
  const [url, setUrl] = useState<string>(route.params?.url || store.baseUrl);
  const [code, setCode] = useState<string>(route.params?.code || '');
  const [name, setName] = useState(defaultDeviceName());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [scanning, setScanning] = useState(false);
  const [me, setMe] = useState<PairedDevice | null>(null);
  const [permission, requestPermission] = useCameraPermissions();

  useEffect(() => {
    if (route.params?.url) setUrl(route.params.url);
    if (route.params?.code) setCode(route.params.code);
  }, [route.params?.url, route.params?.code]);

  useEffect(() => {
    let cancelled = false;
    if (store.pairingRequired) { setMe(null); return () => { cancelled = true; }; }
    devicesApi.whoami(store.baseUrl).then(d => { if (!cancelled) setMe(d); }).catch(() => { if (!cancelled) setMe(null); });
    return () => { cancelled = true; };
  }, [store.baseUrl, store.pairingRequired]);

  const pair = useCallback(async (targetUrl = url, targetCode = code) => {
    setError('');
    const validated = validateBaseUrl(targetUrl);
    if (!validated.ok) { setError(validated.error); return; }
    if (normalizeCode(targetCode).length !== 8) { setError('The code has 8 letters and digits, e.g. K7QM-3XRT.'); return; }
    setBusy(true);
    try {
      if (!(await checkHealth(validated.url, { timeoutMs: 6_000 }))) {
        setError(`Can't reach ${validated.url}. Is the GUI running, and listening on the network (MUCLI_GUI_ALLOW_REMOTE=1 mucli --gui --host 0.0.0.0)?`);
        return;
      }
      const device = await devicesApi.pair(validated.url, targetCode, name.trim() || defaultDeviceName());
      store.setBaseUrl(validated.url);
      store.setPairingRequired(false);
      store.setConnected(true);
      reconnectAllStreams();
      void store.replayPending();
      setMe(device);
      toast(`Paired as ${device.name}`, 'ok');
      if (navigation.canGoBack()) navigation.goBack();
      else navigation.navigate('Tabs', { screen: 'Chat' });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [code, name, navigation, store, toast, url]);

  const onScanned = useCallback((data: string) => {
    const link = parsePairingLink(data);
    if (!link) return;
    setScanning(false);
    const target = link.url || url;
    setUrl(target);
    setCode(link.code);
    void pair(target, link.code);
  }, [pair, url]);

  const startScan = useCallback(async () => {
    const granted = permission?.granted || (await requestPermission()).granted;
    if (!granted) { setError('Camera access is off. Type the address and code instead, or allow the camera in Android settings.'); return; }
    setScanning(true);
  }, [permission, requestPermission]);

  const signOut = useCallback(async () => {
    await devicesApi.signOut(store.baseUrl);
    store.setPairingRequired(true);
    setMe(null);
    toast('Signed out', 'ok');
  }, [store, toast]);

  return (
    <View style={{ flex: 1 }} testID="pair-screen">
      <PageHeader title="Pair with mucli" back={navigation.canGoBack() ? { label: 'Back', onPress: () => navigation.goBack() } : undefined} state={[me ? { text: `Signed in as ${me.name}`, tone: 'ok' } : store.pairingRequired ? { text: 'Not paired', tone: 'attention' } : null, { text: normalizeBase(store.baseUrl), tone: 'quiet' }]} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {me ? (
          <Banner tone="ok" title={`This phone is signed in to ${normalizeBase(store.baseUrl)}`} action={{ label: 'Sign out', onPress: () => void signOut() }} testID="pair-signed-in">
            Paired as {me.name}. Revoke it any time from Settings → Devices on the computer.
          </Banner>
        ) : null}
        <Text variant="body" style={{ color: colors.ink2 }}>
          On the computer, open Settings → Devices → Pair a device (or run mucli gui pair). Scan the QR code it shows, or type the address and the code.
        </Text>
        <Button label="Scan the pairing code" icon="scan-line" variant="primary" onPress={() => void startScan()} testID="pair-scan" />
        <Field label="GUI address" hint="Shown under the QR code, e.g. http://192.168.1.20:30311">
          <TextField value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" testID="pair-url" />
        </Field>
        <Field label="Pairing code">
          <TextField value={code} onChangeText={t => setCode(t.toUpperCase())} autoCapitalize="characters" autoCorrect={false} mono placeholder="K7QM-3XRT" testID="pair-code" />
        </Field>
        <Field label="This phone's name" hint="How it appears in Settings → Devices">
          <TextField value={name} onChangeText={setName} testID="pair-name" />
        </Field>
        {error ? <Banner tone="danger" title="Couldn't pair" testID="pair-error">{error}</Banner> : null}
        <Button label={busy ? 'Pairing…' : 'Pair'} variant="secondary" onPress={() => void pair()} disabled={busy} loading={busy} testID="pair-submit" />
      </ScrollView>
      <Sheet visible={scanning} onClose={() => setScanning(false)} title="Scan the pairing code" full scroll={false} testID="pair-scanner">
        <View style={[styles.camera, { borderColor: colors.hairline }]}>
          {scanning ? (
            <CameraView style={StyleSheet.absoluteFill} facing="back" barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
              onBarcodeScanned={({ data }) => onScanned(String(data || ''))} />
          ) : null}
        </View>
        <Text variant="caption" style={{ color: colors.ink3, marginTop: spacing.sm }}>Point the camera at the QR code in Settings → Devices.</Text>
      </Sheet>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.base, paddingBottom: spacing['2xl'] },
  camera: { flex: 1, minHeight: 320, borderRadius: radii.lg, overflow: 'hidden', borderWidth: 1 },
});
