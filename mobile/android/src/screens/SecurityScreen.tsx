import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge, Button } from '../components';
import { securityApi, SecurityFinding } from '../api/security';
import { spacing } from '../theme/tokens';

export function SecurityScreen() {
  const { colors } = useTheme();
  const [findings, setFindings] = useState<SecurityFinding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<SecurityFinding | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await securityApi.getState();
      setFindings(res.findings);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  const approve = (id: string) => {
    Alert.alert('Approve finding?', 'Finalize this security finding?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Approve', onPress: async () => {
        await securityApi.approveFinding(id);
        load();
      }},
    ]);
  };

  const refute = (id: string) => {
    Alert.alert('Refute finding?', 'Abandon this finding as invalid?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Refute', style: 'destructive', onPress: async () => {
        await securityApi.refuteFinding(id, 'Refuted via mobile UI');
        load();
      }},
    ]);
  };

  const sevVariant = (sev: string) => {
    switch (sev) {
      case 'critical': return 'error' as const;
      case 'high': return 'error' as const;
      case 'medium': return 'warning' as const;
      case 'low': return 'neutral' as const;
      default: return 'neutral' as const;
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3].map(i => <Skeleton key={i} height={80} style={{ marginBottom: spacing.sm }} />)}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (findings.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No security findings" message="No security scan or findings available" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={findings}
        keyExtractor={item => item.finding_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <Badge label={item.severity} variant={sevVariant(item.severity)} />
              <Badge label={item.status} variant={item.status === 'approved' ? 'success' : 'neutral'} />
            </View>
            <Text variant="sm" style={{ fontWeight: '500' }}>{item.title}</Text>
            <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }} numberOfLines={3}>{item.summary}</Text>
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
              <Text variant="xs" style={{ color: item.has_proof ? colors.success : colors.textDim }}>
                PoC: {item.proof_verified ? 'Verified' : item.has_proof ? 'Pending' : 'None'}
              </Text>
              <Text variant="xs" style={{ color: item.has_remediation ? colors.success : colors.textDim }}>
                Fix: {item.remediation_verified ? 'Verified' : item.has_remediation ? 'Pending' : 'None'}
              </Text>
            </View>
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
              <Button title="Details" variant="ghost" onPress={() => setSelected(item)} />
              {item.status !== 'approved' && <Button title="Approve" onPress={() => approve(item.finding_id)} />}
              {item.status !== 'refuted' && <Button title="Refute" variant="ghost" onPress={() => refute(item.finding_id)} />}
            </View>
          </Card>
        )}
      />
      {selected && (
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <View style={{ flex: 1, justifyContent: 'center', padding: spacing.base }}>
            <Card style={{ maxHeight: '80%' }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                <Text variant="base" style={{ fontWeight: '600' }}>{selected.title}</Text>
                <TouchableOpacity onPress={() => setSelected(null)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  <Text variant="sm" style={{ color: colors.accent }}>Close</Text>
                </TouchableOpacity>
              </View>
              <FlatList
                data={[
                  { k: 'Severity', v: selected.severity },
                  { k: 'Class', v: selected.vulnerability_class },
                  { k: 'Status', v: selected.status },
                  { k: 'Exploit path', v: selected.exploit_path },
                  { k: 'PoC command', v: selected.proof_command },
                  { k: 'Affected paths', v: selected.affected_paths.join(', ') },
                ]}
                keyExtractor={item => item.k}
                renderItem={({ item }) => (
                  <View style={{ paddingVertical: 4, minHeight: 28 }}>
                    <Text variant="xs" style={{ color: colors.textDim }}>{item.k}</Text>
                    <Text variant="sm" style={{ color: colors.text, fontFamily: 'monospace' }}>{item.v}</Text>
                  </View>
                )}
              />
            </Card>
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}