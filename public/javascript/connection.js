import { S, sessionId, buildJoinPayload } from './state.js';
import { showToast } from './toast.js';

export const socket = io({
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 5000,
});

export let connectionStatus = 'connecting';
export let hasConnectedOnce = false;

export function updateConnectionIndicator() {
  const el = document.getElementById('connectionIndicator');
  if (!el) return;
  el.className = `connection-indicator ${connectionStatus}`;
  const labels = {
    connected: 'Connected',
    connecting: 'Connecting…',
    reconnecting: 'Reconnecting…',
    disconnected: 'Disconnected',
  };
  el.title = labels[connectionStatus] || '';
  el.setAttribute('aria-label', el.title);
}

export function ensureConnectionIndicator() {
  if (document.getElementById('connectionIndicator')) return;
  const header = document.querySelector('#userList .user-list-header');
  if (!header) return;
  const dot = document.createElement('span');
  dot.id = 'connectionIndicator';
  dot.className = `connection-indicator ${connectionStatus}`;
  header.appendChild(dot);
  updateConnectionIndicator();
}

export function rejoinSession() {
  if (!S.username) return;
  socket.emit('join', buildJoinPayload());
}

socket.on('connect', () => {
  const wasDisconnected =
    connectionStatus === 'disconnected' || connectionStatus === 'reconnecting';
  connectionStatus = 'connected';
  updateConnectionIndicator();
  if (wasDisconnected) {
    rejoinSession();
    showToast('Connection restored', 'success');
  }
  hasConnectedOnce = true;
});

socket.on('disconnect', () => {
  connectionStatus = 'disconnected';
  updateConnectionIndicator();
  if (hasConnectedOnce) showToast('Connection lost — reconnecting…', 'error', 2500);
});

socket.io.on('reconnect_attempt', () => {
  connectionStatus = 'reconnecting';
  updateConnectionIndicator();
});

// Store the server-issued reconnect token privately so it can be sent on rejoin.
socket.on('reconnectToken', ({ token }) => {
  if (token && sessionId) {
    sessionStorage.setItem(`pokeringReconnectToken_${sessionId}`, token);
  }
});
