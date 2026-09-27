import { Tabs } from 'expo-router';
import React from 'react';

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: { display: 'none' }
      }}
    >
      <Tabs.Screen name='index' options={{ title: 'Chat' }} />
      <Tabs.Screen name='chat' options={{ title: 'Chat' }} />
      <Tabs.Screen name='attention' options={{ title: 'Attention' }} />
      <Tabs.Screen name='account' options={{ title: 'Account & Settings' }} />
      <Tabs.Screen name='prs' options={{ href: null }} />
      <Tabs.Screen name='diagnostics' options={{ href: null }} />
      <Tabs.Screen name='map' options={{ href: null }} />
    </Tabs>
  );
}
