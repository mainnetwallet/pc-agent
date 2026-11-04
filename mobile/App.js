// @flow

import React, { useState, useEffect, useRef } from 'react';
import {
  StyleSheet, Text, View, TextInput, TouchableOpacity, FlatList,
  ActivityIndicator, Image, Dimensions, ScrollView, Platform, Alert,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';

const { width, height } = Dimensions.get('window');
const API_BASE = 'http://localhost:8000'; // set to your relay URL

export default function App() {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [live, setLive] = useState(false);
  const [frame, setFrame] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadTasks();
    const iv = setInterval(loadTasks, 5000);
    return () => clearInterval(iv);
  }, []);

  const loadTasks = async () => {
    try {
      const r = await fetch(`${API_BASE}/tasks`);
      const d = await r.json();
      setTasks(d.tasks || []);
    } catch { }
  };

  const sendTask = async () => {
    if (!input.trim()) return;
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: input }),
      });
      const d = await r.json();
      setMessages([...messages, { me: true, text: input }]);
      setMessages([...messages, { me: false, text: `Task ${d.task_id} created (${d.status}).` }]);
      setInput('');
    } catch (e) {
      Alert.alert('Error', String(e));
    } finally { setLoading(false); }
  };

  const toggleLive = async () => {
    setLive(!live);
    if (!live) {
      await fetch(`${API_BASE}/pc/stream/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fps: 5, quality: 60, monitor: 0 }) });
      startPolling();
    } else {
      await fetch(`${API_BASE}/pc/stream/stop`, { method: 'POST' });
    }
  };

  let pollInterval;
  const startPolling = () => {
    pollInterval = setInterval(async () => {
      try {
        const r = await fetch(`${API_BASE}/pc/screenshot`);
        const buf = await r.arrayBuffer();
        const base64 = `data:image/jpeg;base64,${Buffer ? '' : ''}`;
        // React Native: convert to base64 manually
        const bytes = new Uint8Array(buf);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
        setFrame(`data:image/jpeg;base64,${btoa(binary)}`);
      } catch { }
    }, 1000);
  };
  useEffect(() => () => clearInterval(pollInterval), []);

  return (
    <View style={styles.container}>
      <StatusBar style="auto" />
      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        <View style={styles.header}>
          <Text style={styles.title}>PC Agent</Text>
          <TouchableOpacity style={styles.btn} onPress={toggleLive}>
            <Text style={{ color: live ? '#10b981' : '#ef4444', fontWeight: '600' }}>{live ? 'Stop Live PC' : 'Live PC View'}</Text>
          </TouchableOpacity>
        </View>

        {live && (
          <View style={styles.liveContainer}>
            {frame ? (
              <Image source={{ uri: frame }} style={styles.liveImage} />
            ) : (
              <View style={styles.livePlaceholder}>
                <ActivityIndicator size="large" color="#0ea5e9" />
                <Text style={styles.muted}>Connecting to PC...</Text>
              </View>
            )}
          </View>
        )}

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Send a Task</Text>
          <View style={styles.inputRow}>
            <TextInput style={styles.input} value={input} onChangeText={setInput} placeholder="Type a task..." />
            <TouchableOpacity style={styles.sendBtn} onPress={sendTask} disabled={loading}>
              <Text style={styles.sendText}>Send</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Tasks</Text>
          <FlatList
            data={tasks}
            keyExtractor={item => item.id}
            renderItem={({ item }) => (
              <View style={styles.taskRow}>
                <Text style={styles.taskText}>{item.instruction}</Text>
                <Text style={styles.taskStatus}>{item.status}</Text>
              </View>
            )}
          />
        </View>
      </ScrollView>

      {loading && (
        <View style={styles.overlay}>
          <ActivityIndicator size="large" color="#0ea5e9" />
          <Text style={styles.muted}>Working...</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8fafc' },
  scroll: { flex: 1 },
  scrollContent: { padding: 16 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  title: { fontSize: 22, fontWeight: '700', color: '#0f172a' },
  btn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#cbd5e1' },
  liveContainer: { width: '100%', aspectRatio: 16 / 9, borderRadius: 12, overflow: 'hidden', backgroundColor: '#000', marginBottom: 16, alignItems: 'center', justifyContent: 'center' },
  liveImage: { width: '100%', height: '100%' },
  livePlaceholder: { alignItems: 'center', justifyContent: 'center', padding: 32 },
  muted: { marginTop: 8, color: '#64748b', fontSize: 14 },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 16, shadowColor: '#000', shadowOpacity: 0.05, shadowOffset: { width: 0, height: 1 }, shadowRadius: 4, elevation: 2 },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#0f172a', marginBottom: 10 },
  inputRow: { flexDirection: 'row', gap: 8 },
  input: { flex: 1, borderWidth: 1, borderColor: '#cbd5e1', borderRadius: 8, padding: 12, color: '#0f172a' },
  sendBtn: { backgroundColor: '#0ea5e9', paddingHorizontal: 16, paddingVertical: 12, borderRadius: 8 },
  sendText: { color: '#fff', fontWeight: '600' },
  taskRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: '#f1f5f9' },
  taskText: { color: '#334155' },
  taskStatus: { fontSize: 12, color: '#64748b' },
  overlay: { position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center', alignItems: 'center', zIndex: 10 },
});
