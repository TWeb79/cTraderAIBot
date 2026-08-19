/**
 * API client for the dashboard backend.
 *
 * Base URL: http://localhost:8158
 */

const API_BASE = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
  ? 'http://localhost:8158'
  : '';

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

export async function fetchVersion() {
  return get('/api/version');
}

export async function fetchHealth() {
  return get('/api/health');
}

export async function fetchState() {
  return get('/api/state');
}

export async function fetchJournal(limit = 25) {
  return get(`/api/journal?limit=${limit}`);
}

export async function fetchDigest() {
  return get('/api/digest');
}

export async function fetchRegistry() {
  return get('/api/registry');
}

export function getApiHost() {
  if (API_BASE && (API_BASE.startsWith('http://') || API_BASE.startsWith('https://'))) {
    try {
      return new URL(API_BASE).host;
    } catch (e) {
      /* fall through to location.host */
    }
  }
  return location.host;
}

export function createWebSocket(onMessage) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = getApiHost();
  const ws = new WebSocket(`${proto}//${host}/ws`);
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      onMessage(data);
    } catch (e) {
      console.error('WS parse error', e);
    }
  };
  return ws;
}
