import { showToast } from './toast.js';
import { showModal } from './modal.js';
import { S, sessionId, refreshMyUser, saveUsername, buildJoinPayload } from './state.js';
import { socket, ensureConnectionIndicator, hasConnectedOnce } from './connection.js';
import {
  isUserSpectator,
  renderCards,
  updateVotingLockState,
  launchConfetti,
  onDeckChanged,
  loadDecks,
  populateDeckSelector,
} from './cards.js';
import { updateToggleBtnLabel, renderUserList, updateVersionBadge } from './ui.js';
import { showHostSettingsModal, confirmHostSettings } from './host.js';
import { postCreate, escapeHTML, isValidUsername, setDocTitle } from './utils.js';

window.addEventListener('error', (e) => {
  console.error('Uncaught error:', e.error || e.message);
  try {
    showToast('Something went wrong. Refresh if issues persist.', 'error', 4000);
  } catch {}
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('Unhandled promise rejection:', e.reason);
  try {
    showToast('Something went wrong. Refresh if issues persist.', 'error', 4000);
  } catch {}
});

if (!sessionId || sessionId === 'session' || sessionId === 'undefined') {
  postCreate();
}

function promptRename() {
  showModal(
    'Change your username:',
    () => {
      const name = document.getElementById('modalInput').value.trim();
      saveUsername(name);
      showToast(`Username changed to "${S.username}"`, 'success');
      socket.emit('join', buildJoinPayload());
    },
    true,
    false,
    false,
    S.username
  );
}

function promptUsername() {
  showModal(
    'Enter your name to join the session:',
    () => {
      const name = document.getElementById('modalInput').value.trim();
      const errorEl = document.getElementById('modalError');
      if (!isValidUsername(name)) {
        errorEl.textContent = "Name: letters, digits, spaces, - _ ' (max 30).";
        return;
      }
      errorEl.textContent = '';
      saveUsername(name);
      document.getElementById('mainContent').classList.remove('hidden');
      socket.emit('join', buildJoinPayload());
    },
    true,
    false,
    true,
    S.username
  );
}

// ── Socket event handlers ─────────────────────────────────────────────────────

socket.on('usersUpdate', (users) => {
  ensureConnectionIndicator();
  S.currentUsers = users;
  refreshMyUser();
  const isHost = S.myUser?.isHost;

  if (isHost && !S.hostSettingsShown) {
    S.hostSettingsShown = true;
    if (S.myUser?.wantsToVote === false) {
      document.getElementById('cardOptions').classList.add('hidden');
    } else if (S.myUser?.wantsToVote === undefined || S.myUser?.wantsToVote === null) {
      showHostSettingsModal();
    }
  }

  renderUserList(promptRename);
});

socket.on('userVoted', ({ clientId: votedId, voteChanged }) => {
  if (!votedId) return;
  const user =
    votedId === S.clientId
      ? S.myUser
      : S.currentUsers.find((currentUser) => currentUser.clientId === votedId);
  if (!user) return;
  const firstFlag = voteChanged && !user.voteChanged;
  if (user.vote === null) user.vote = true;
  if (voteChanged) user.voteChanged = true;
  if (firstFlag && votedId !== S.clientId) {
    showToast(`${user.username} changed their vote`, 'info');
  }
  renderUserList(promptRename);
});

socket.on('countdown', (seconds) => {
  document.getElementById('countdown').textContent = `Revealing in: ${seconds}`;
  document.getElementById('toggleVotingBtn').disabled = true;
  document.getElementById('newRoundBtn').disabled = true;
  document.getElementById('newSessionBtn').disabled = true;
  document.getElementById('deckSelector').disabled = true;
  document.querySelectorAll('.user-edit-btn').forEach((btn) => (btn.disabled = true));
});

socket.on('revealVotes', ({ users, stats }) => {
  S.votesRevealed = true;
  S.currentUsers = users;
  refreshMyUser();
  updateToggleBtnLabel();
  document.getElementById('toggleVotingBtn').disabled = false;
  document.getElementById('newRoundBtn').disabled = false;
  document.getElementById('newSessionBtn').disabled = false;
  document.getElementById('countdown').textContent = '';

  document.querySelectorAll('.card').forEach((card) => {
    card.disabled = true;
  });

  const votingUsers = users.filter((user) => user.vote !== null && !isUserSpectator(user));

  const results = votingUsers
    .map((user, index) => {
      const isOutlier = stats?.outliers?.includes(user.username);
      const delay = (index * 80).toString();
      const safeVote = escapeHTML(String(user.vote));
      return `
        <div class="vote-card-wrapper${isOutlier ? ' outlier' : ''}">
          <div class="vote-card${isOutlier ? ' outlier' : ''}" data-value="${safeVote}" style="animation-delay:${delay}ms">
            <div class="vote-value">${safeVote}</div>
          </div>
          <p class="voter-name" title="${escapeHTML(user.username)}">${escapeHTML(user.username)}</p>
        </div>
      `;
    })
    .join('');

  const realVotesForStats = votingUsers
    .map((user) => user.vote)
    .filter((vote) => vote !== null && vote !== '?');
  const uniqueVotes = new Set(realVotesForStats).size;
  const allAgreed = realVotesForStats.length >= 2 && uniqueVotes === 1;

  const statTiles = [];
  if (stats?.average !== undefined) {
    statTiles.push(
      `<div class="stat-tile"><span class="stat-label">Average</span><span class="stat-value">${stats.average}</span></div>`
    );
  }
  if (stats?.median !== undefined) {
    statTiles.push(
      `<div class="stat-tile"><span class="stat-label">Median</span><span class="stat-value">${escapeHTML(String(stats.median))}</span></div>`
    );
  }
  if (realVotesForStats.length > 0) {
    statTiles.push(
      `<div class="stat-tile"><span class="stat-label">Votes</span><span class="stat-value">${realVotesForStats.length}</span></div>`
    );
  }
  if (allAgreed) {
    statTiles.push(
      `<div class="stat-tile consensus"><span class="stat-label">Consensus</span><span class="stat-value">🎉</span></div>`
    );
  } else if (stats?.outliers?.length) {
    statTiles.push(
      `<div class="stat-tile outlier"><span class="stat-label">Outliers</span><span class="stat-value">${stats.outliers.length}</span></div>`
    );
  }
  const summary = statTiles.length ? `<div class="stat-row">${statTiles.join('')}</div>` : '';

  // innerHTML is intentional here: all user-supplied values go through escapeHTML(),
  // stat values are numeric or server-controlled. textContent cannot produce the card layout.
  document.getElementById('votesDisplay').innerHTML = results;
  document.getElementById('voteSummary').innerHTML = summary;
  renderUserList(promptRename);

  if (realVotesForStats.length >= 2 && realVotesForStats.every((vote) => vote === realVotesForStats[0])) {
    const totalDelay = votingUsers.length * 80 + 500;
    setTimeout(() => launchConfetti(), totalDelay);
  }
});

socket.on('roundReset', ({ deckType, votingEnabled: enabled }) => {
  S.selectedCard = null;
  S.hasChangedVote = false;
  S.votesRevealed = false;
  S.pendingVotingEnabled = null;
  const votingChanged = S.votingEnabled !== enabled;
  S.votingEnabled = enabled;

  document.querySelectorAll('.card').forEach((card) => card.classList.remove('selected'));
  document.getElementById('countdown').textContent = '';
  document.getElementById('votesDisplay').innerHTML = '';
  document.getElementById('voteSummary').innerHTML = '';

  if (deckType && S.deckPresets[deckType]) {
    S.currentDeckType = deckType;
    const sel = document.getElementById('deckSelector');
    if (sel) sel.value = deckType;
    renderCards();
  }

  updateVotingLockState();
  updateToggleBtnLabel();
  setDocTitle(null);
  showToast('New round started', 'info');
  if (votingChanged) {
    showToast(enabled ? 'Voting unlocked' : 'Voting locked', enabled ? 'success' : 'info');
  }
});

socket.on('deckChanged', ({ deckType }) => {
  onDeckChanged(deckType);
});

socket.on('hostLeft', () => {
  const overlay = document.getElementById('hostLeftOverlay');
  if (overlay) overlay.classList.remove('hidden');
});

socket.on('userLeft', ({ username: name }) => {
  if (name && name !== S.username) showToast(`${name} left the session`, 'info');
});

socket.on('userJoined', ({ username: name }) => {
  if (!name) return;
  showToast(`${name} joined the session`, 'success');
});

socket.on('joinFailed', ({ reason }) => {
  showModal(`Failed to join session: ${reason}`, () => {
    window.location.href = '/';
  });
});

socket.on('actionFailed', ({ action, reason }) => {
  showToast(reason || `Action failed${action ? ` (${action})` : ''}`, 'error', 4000);
});

socket.on('serverShutdown', ({ reason } = {}) => {
  showToast(reason || 'Server is restarting. Reconnecting...', 'info', 5000);
});

socket.on('sessionState', ({ votingEnabled: enabled }) => {
  const changed = S.votingEnabled !== enabled;
  S.votingEnabled = enabled;
  updateVotingLockState();
  if (changed && hasConnectedOnce && !S.votesRevealed) {
    showToast(enabled ? 'Voting unlocked' : 'Voting locked', enabled ? 'success' : 'info');
  }
});

// ── Load handler ──────────────────────────────────────────────────────────────

window.addEventListener('load', () => {
  updateVersionBadge();
  loadDecks();

  document.getElementById('newRoundBtn').addEventListener('click', () => {
    const payload = { sessionId, deckType: S.currentDeckType };
    if (S.pendingVotingEnabled !== null) payload.votingEnabled = S.pendingVotingEnabled;
    socket.emit('requestNewRound', payload);
  });

  document.getElementById('copyLinkBtn').addEventListener('click', () => {
    const url = `${window.location.origin}/session/${sessionId}`;
    navigator.clipboard
      .writeText(url)
      .then(() => showToast('Session link copied to clipboard', 'success'))
      .catch(() => showToast('Failed to copy session link', 'error'));
  });

  document.getElementById('toggleSpectateBtn').addEventListener('click', () => {
    if (!S.myUser || S.myUser.isHost) return;
    socket.emit('setSpectator', { sessionId, isSpectator: !S.myUser.isSpectator });
  });

  document.getElementById('hostUsernameInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      document.getElementById('hostSettingsConfirm').click();
    }
  });

  document.getElementById('toggleVotingBtn').addEventListener('click', () => {
    if (S.votesRevealed) {
      const current = S.pendingVotingEnabled !== null ? S.pendingVotingEnabled : S.votingEnabled;
      S.pendingVotingEnabled = !current;
      updateToggleBtnLabel();
    } else {
      socket.emit('setVotingEnabled', { sessionId, votingEnabled: !S.votingEnabled });
    }
  });

  document.getElementById('hostLeftNewSession').addEventListener('click', () => {
    sessionStorage.setItem('pokeringIsCreator', '1');
    postCreate();
  });

  document.getElementById('hostSettingsConfirm').addEventListener('click', confirmHostSettings);

  document.getElementById('newSessionBtn').addEventListener('click', (e) => {
    e.preventDefault();
    showModal(
      'Start a new session?<br><br>' +
        'This will create a fresh session.<br>' +
        'Currently connected users will <span class="emph-warning">NOT</span> be moved.',
      () => {
        Object.keys(sessionStorage)
          .filter((storageKey) => storageKey.startsWith('pokeringHostVoteDecision_'))
          .forEach((storageKey) => sessionStorage.removeItem(storageKey));
        sessionStorage.setItem('pokeringIsCreator', '1');
        postCreate();
      },
      false,
      false,
      false,
      '',
      true // allowHtml — trusted static string, no user data interpolated
    );
  });

  document.getElementById('deckSelector').addEventListener('change', (e) => {
    socket.emit('changeDeck', { sessionId, deckType: e.target.value });
  });

  // Join flow
  const isCreator = sessionStorage.getItem('pokeringIsCreator') === '1';

  if (isCreator) {
    sessionStorage.removeItem('pokeringIsCreator');
    S.hostSettingsShown = true;
    showHostSettingsModal(true);
  } else if (S.username && isValidUsername(S.username)) {
    document.getElementById('mainContent').classList.remove('hidden');
    socket.emit('join', buildJoinPayload());
  } else {
    promptUsername();
  }
});

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const modal = document.getElementById('modalBackdrop');
  const hostModal = document.getElementById('hostSettingsBackdrop');
  if (modal && !modal.classList.contains('hidden')) return;
  if (hostModal && !hostModal.classList.contains('hidden')) return;

  const active = document.activeElement;
  const tag = active?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || active?.isContentEditable)
    return;

  if (e.key >= '1' && e.key <= '9') {
    if (S.votesRevealed || !S.votingEnabled) return;
    if (S.selectedCard && S.hasChangedVote) return;
    const idx = parseInt(e.key, 10) - 1;
    const cards = document.getElementById('cardOptions').querySelectorAll('.card');
    if (idx >= cards.length) return;
    const target = cards[idx];
    if (target.disabled) return;
    e.preventDefault();
    target.click();
  } else if (e.key === 'Enter') {
    if (tag === 'BUTTON' || tag === 'A') return;
    if (!S.votesRevealed) return;
    const btn = document.getElementById('newRoundBtn');
    if (!btn || btn.disabled || btn.classList.contains('hidden')) return;
    e.preventDefault();
    const payload = { sessionId, deckType: S.currentDeckType };
    if (S.pendingVotingEnabled !== null) payload.votingEnabled = S.pendingVotingEnabled;
    socket.emit('requestNewRound', payload);
  }
});

// Initial render with fallback presets; re-renders after /decks fetch + deckChanged events
populateDeckSelector();
renderCards();
