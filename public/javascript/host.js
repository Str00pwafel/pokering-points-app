import { S, sessionId, saveUsername } from './state.js';
import { socket } from './connection.js';
import { trapFocus } from './modal.js';
import { isValidUsername } from './utils.js';

let releaseHostSettingsFocus = null;

export function showHostSettingsModal(withUsername = false) {
  const usernameRow = document.getElementById('hostUsernameRow');
  if (withUsername) {
    usernameRow.classList.remove('hidden');
    document.getElementById('hostUsernameInput').value = S.username;
    document.getElementById('hostUsernameError').textContent = '';
  } else {
    usernameRow.classList.add('hidden');
  }
  const backdrop = document.getElementById('hostSettingsBackdrop');
  backdrop.classList.remove('hidden');
  releaseHostSettingsFocus = trapFocus(document.getElementById('hostSettingsContent'));
  if (withUsername) {
    const input = document.getElementById('hostUsernameInput');
    if (input) input.focus();
  }
}

export function confirmHostSettings() {
  const usernameRow = document.getElementById('hostUsernameRow');
  const isCreatorFlow = usernameRow && !usernameRow.classList.contains('hidden');

  if (isCreatorFlow) {
    const nameInput = document.getElementById('hostUsernameInput');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!isValidUsername(name)) {
      document.getElementById('hostUsernameError').textContent =
        "Name: letters, digits, spaces, - _ ' (max 30).";
      return;
    }
    saveUsername(name);
    document.getElementById('mainContent').classList.remove('hidden');
  }

  const wantsToVote = document.getElementById('toggleJoinVoting').checked;
  const votingEnabledVal = document.getElementById('toggleVotingEnabled').checked;

  document.getElementById('hostSettingsBackdrop').classList.add('hidden');
  if (releaseHostSettingsFocus) {
    releaseHostSettingsFocus();
    releaseHostSettingsFocus = null;
  }

  const reconnectToken = sessionStorage.getItem(`pokeringReconnectToken_${sessionId}`);

  if (isCreatorFlow) {
    socket.emit('join', {
      sessionId,
      username: S.username,
      clientId: S.clientId,
      deckType: S.currentDeckType,
      wantsToVote,
      reconnectToken: reconnectToken || undefined,
    });
  }

  socket.emit('hostVotingDecision', { sessionId, wantsToVote });
  socket.emit('setVotingEnabled', { sessionId, votingEnabled: votingEnabledVal });

  if (!wantsToVote) {
    document.getElementById('cardOptions').classList.add('hidden');
  }

  sessionStorage.setItem(`pokeringHostVoteDecision_${sessionId}`, String(wantsToVote));
}
