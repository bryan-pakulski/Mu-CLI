// More (mobile_parity.md §3): Settings, pairing, the connection and the provider.

import React from 'react';
import { ScrollView, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useConnectionStore } from '../store/connection';
import { spacing } from '../theme/tokens';
import { PageHeader, Row, Rows } from '../ui';

export function MoreScreen() {
  const navigation = useNavigation<any>();
  const { baseUrl, isConnected, pairingRequired } = useConnectionStore();
  const server = baseUrl.replace(/^https?:\/\//, '');
  return (
    <View style={{ flex: 1 }} testID="more-screen">
      <PageHeader title="More" state={[
        { text: isConnected ? `Connected to ${server}` : 'Not connected', tone: isConnected ? '' : 'attention' },
        pairingRequired ? { text: 'not paired', tone: 'attention' } : null,
      ]} />
      <ScrollView contentContainerStyle={{ padding: spacing.base }}>
        <Rows>
          <Row testID="more-Settings" title="Settings" sub="Appearance, areas and paired devices" onPress={() => navigation.navigate('Settings')} />
          <Row testID="more-Pair" status={pairingRequired ? 'waiting' : undefined} title="Pair with mucli" sub={pairingRequired ? 'This phone needs pairing to sign in' : 'Sign in, or sign out'} onPress={() => navigation.navigate('Pair')} />
          <Row testID="more-Connection" title="Connection" sub={server} onPress={() => navigation.navigate('Connection')} />
          <Row testID="more-Providers" title="Provider and model" sub="The model for this session" onPress={() => navigation.navigate('Providers')} />
        </Rows>
      </ScrollView>
    </View>
  );
}
