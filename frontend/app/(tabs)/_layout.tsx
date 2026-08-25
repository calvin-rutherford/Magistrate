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
      <Tabs.Screen name='index' options={{ title: 'Situation Room' }} />
      <Tabs.Screen name='agents' options={{ title: 'Agents' }} />
      <Tabs.Screen name='attention' options={{ title: 'Attention' }} />
      <Tabs.Screen name='prs' options={{ title: 'PRs' }} />
      <Tabs.Screen name='chat' options={{ title: 'Chat' }} />
    </Tabs>
  );
}
