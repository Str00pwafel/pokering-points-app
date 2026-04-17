const socket = io({
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 5000,
});

let connectionStatus = 'connecting';
let hasConnectedOnce = false;

function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  // Force reflow so transition runs
  void toast.offsetWidth;
  toast.classList.add('show');
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

function updateConnectionIndicator() {
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

function rejoinSession() {
  if (!username) return;
  const hostVoteDecision = sessionStorage.getItem(`jiraPokerHostVoteDecision_${sessionId}`);
  socket.emit('join', {
    sessionId,
    username,
    clientId,
    deckType: currentDeckType,
    wantsToVote: hostVoteDecision !== null ? (hostVoteDecision === "true") : undefined
  });
}

function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function isValidUsername(name) {
  return /^[a-zA-Z]{1,20}$/.test(name);
}

function postCreate() {
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = '/create';
  document.body.appendChild(form);
  form.submit();
}

const sessionId = window.location.pathname.split('/').pop();
if (!sessionId || sessionId === 'session' || sessionId === 'undefined') {
  postCreate();
}

let username = sessionStorage.getItem("jiraPokerUsername") || localStorage.getItem("jiraPokerUsername") || "";
let clientId = sessionStorage.getItem('jiraPokerClientId');
if (!clientId) {
  const bytes = new Uint8Array(7);
  crypto.getRandomValues(bytes);
  clientId = 'client-' + Array.from(bytes, b => b.toString(36).padStart(2, '0')).join('').slice(0, 7);
  sessionStorage.setItem('jiraPokerClientId', clientId);
}

async function updateVersionBadge() {
  try {
    const res = await fetch('/version', { cache: 'no-store' });
    if (!res.ok) return;
    const { version, changelog } = await res.json();
    const el = document.getElementById('versionBadge');
    if (el) el.textContent = `v${version}`;
    const tooltip = document.getElementById('versionTooltip');
    if (tooltip && changelog) {
      tooltip.innerHTML = Object.entries(changelog)
        .map(([v, items]) => `<h4>v${v}</h4><ul>${items.map(c => `<li>${c}</li>`).join('')}</ul>`)
        .join('');
    }
  } catch (err) {
    console.error('Version badge error:', err);
  }
}

let selectedCard = null;

function promptUsername() {
  showModal("Enter your name to join the session:", () => {
    const name = document.getElementById('modalInput').value.trim();
    const errorEl = document.getElementById('modalError');

    if (!isValidUsername(name)) {
      errorEl.textContent = "Name must be letters only (max 20 characters).";
      return;
    }

    errorEl.textContent = "";
    username = name.slice(0, 20);
    sessionStorage.setItem("jiraPokerUsername", username);
    localStorage.setItem("jiraPokerUsername", username);
    document.getElementById('welcomeUser').innerText = `Welcome, ${username}!`;
    document.getElementById('mainContent').classList.remove('hidden');

    const hostVoteDecision = sessionStorage.getItem(`jiraPokerHostVoteDecision_${sessionId}`);
    socket.emit('join', {
      sessionId,
      username,
      clientId,
      deckType: currentDeckType,
      wantsToVote: hostVoteDecision !== null ? (hostVoteDecision === "true") : undefined
    });
  }, true, false, true, username);
}

window.addEventListener('load', () => {
  // If username is in sessionStorage, this is a redirect (new round) - join directly
  const sessionUsername = sessionStorage.getItem("jiraPokerUsername");
  if (sessionUsername && isValidUsername(sessionUsername)) {
    username = sessionUsername;
    const hostVoteDecision = sessionStorage.getItem(`jiraPokerHostVoteDecision_${sessionId}`);
    document.getElementById('welcomeUser').innerText = `Welcome, ${username}!`;
    document.getElementById('mainContent').classList.remove('hidden');

    socket.emit('join', {
      sessionId,
      username,
      clientId,
      deckType: currentDeckType,
      wantsToVote: hostVoteDecision !== null ? (hostVoteDecision === "true") : undefined
    });
  } else {
    promptUsername();
  }
  updateVersionBadge();

  document.getElementById('newRoundBtn').addEventListener('click', startNewRound);
  document.getElementById('copyLinkBtn').addEventListener('click', copyLink);
  document.getElementById('toggleVotingBtn').addEventListener('click', () => {
    if (votesRevealed) {
      const current = pendingVotingEnabled !== null ? pendingVotingEnabled : votingEnabled;
      pendingVotingEnabled = !current;
      updateToggleBtnLabel();
    } else {
      socket.emit('setVotingEnabled', { sessionId, votingEnabled: !votingEnabled });
    }
  });
  document.getElementById('hostLeftNewSession').addEventListener('click', () => {
    postCreate();
  });
  document.getElementById('hostSettingsConfirm').addEventListener('click', confirmHostSettings);
});

const DECK_PRESETS = {
  fibonacci: [1, 2, 3, 5, 8, 13, 21, "?"],
  hours: [1, 2, 4, 8, 16, 24, 40, "?"],
  tshirt: ["XS", "S", "M", "L", "XL", "XXL", "?"]
};

const DECK_LABELS = {
  fibonacci: "Fibonacci (1-21)",
  hours: "Hours (1-40)",
  tshirt: "T-Shirt (XS-XXL)",
};

let currentDeckType = sessionStorage.getItem("jiraPokerDeckType") || "fibonacci";
if (!DECK_PRESETS[currentDeckType]) currentDeckType = "fibonacci";
let cardValues = DECK_PRESETS[currentDeckType];
let votingEnabled = true;
let votesRevealed = false;
let pendingVotingEnabled = null;
document.getElementById('deckSelector').value = currentDeckType;
const cardContainer = document.getElementById('cardOptions');

function renderCards() {
  cardContainer.innerHTML = '';
  selectedCard = null;
  cardValues.forEach(value => {
    const card = document.createElement('div');
    card.classList.add('card');
    card.dataset.value = value;
    card.textContent = value;
    card.onclick = () => selectCard(card, value);
    cardContainer.appendChild(card);
  });
  updateVotingLockState();
}

renderCards();

document.getElementById('deckSelector').addEventListener('change', (e) => {
  const newDeckType = e.target.value;
  socket.emit('changeDeck', { sessionId, deckType: newDeckType });
});

let deckInitialized = false;
socket.on('deckChanged', ({ deckType }) => {
  if (DECK_PRESETS[deckType]) {
    const changed = deckInitialized && deckType !== currentDeckType;
    currentDeckType = deckType;
    cardValues = DECK_PRESETS[currentDeckType];
    document.getElementById('deckSelector').value = currentDeckType;
    renderCards();
    if (changed) {
      const label = DECK_LABELS[deckType] || deckType;
      showToast(`Deck changed to ${label}`, 'info');
    }
    deckInitialized = true;
  }
});

function selectCard(element, value) {
  if (selectedCard || !votingEnabled) return;

  selectedCard = element;
  element.classList.add('selected');

  const allCards = document.querySelectorAll('.card');
  allCards.forEach(card => {
    card.classList.add('disabled');
    card.style.pointerEvents = 'none';
    card.style.opacity = '0.5';
  });

  selectedCard.classList.remove('disabled');
  selectedCard.style.pointerEvents = 'none';
  selectedCard.style.opacity = '1';

  socket.emit('vote', { sessionId, value });
}

function startNewRound() {
  const payload = { sessionId, deckType: currentDeckType };
  if (pendingVotingEnabled !== null) {
    payload.votingEnabled = pendingVotingEnabled;
  }
  socket.emit('requestNewRound', payload);
}

function copyLink() {
  const url = `${window.location.origin}/session/${sessionId}`;
  navigator.clipboard.writeText(url).then(() => {
    showToast('Session link copied to clipboard', 'success');
  }).catch(err => {
    console.error('Failed to copy:', err);
    showToast('Failed to copy session link', 'error');
  });
}

socket.on('roundReset', ({ deckType, votingEnabled: enabled }) => {
  selectedCard = null;
  votesRevealed = false;
  pendingVotingEnabled = null;
  const votingChanged = votingEnabled !== enabled;
  votingEnabled = enabled;

  document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
  document.getElementById('countdown').innerText = '';
  document.getElementById('votesDisplay').innerHTML = '';
  document.getElementById('voteSummary').innerHTML = '';

  if (deckType && DECK_PRESETS[deckType]) {
    currentDeckType = deckType;
    document.getElementById('deckSelector').value = deckType;
    renderCards();
  }

  updateVotingLockState();
  updateToggleBtnLabel();
  showToast('New round started', 'info');
  if (votingChanged) {
    showToast(enabled ? 'Voting unlocked' : 'Voting locked', enabled ? 'success' : 'info');
  }
});

function ensureConnectionIndicator() {
  if (document.getElementById('connectionIndicator')) return;
  const header = document.querySelector('#userList .user-list-header');
  if (!header) return;
  const dot = document.createElement('span');
  dot.id = 'connectionIndicator';
  dot.className = `connection-indicator ${connectionStatus}`;
  header.appendChild(dot);
  updateConnectionIndicator();
}

socket.on('usersUpdate', users => {
  ensureConnectionIndicator();
  const myUser = Object.values(users).find(u => u.clientId === clientId);
  const isHost = myUser?.isHost;

  document.getElementById('newRoundBtn').style.display = isHost ? 'inline-block' : 'none';
  document.getElementById('deckSelector').style.display = isHost ? 'inline-block' : 'none';

  const toggleBtn = document.getElementById('toggleVotingBtn');
  toggleBtn.style.display = isHost ? 'inline-block' : 'none';

  // Grey out deck selector if votes have been cast
  if (isHost) {
    const hasVotes = Object.values(users).some(u => u.vote !== null);
    document.getElementById('deckSelector').disabled = hasVotes || votesRevealed;
    toggleBtn.disabled = hasVotes && !votesRevealed;
    updateToggleBtnLabel();
  }

  if (isHost && !window.hostSettingsShown) {
    window.hostSettingsShown = true;

    if (myUser?.wantsToVote === false) {
      document.getElementById('cardOptions').style.display = 'none';
    } else if (myUser?.wantsToVote === undefined || myUser?.wantsToVote === null) {
      showHostSettingsModal();
    }
  }

  const userList = Object.values(users);
  const votingUsers = userList.filter(u => !(u.isHost && u.wantsToVote === false));
  const selected = votingUsers.filter(u => u.vote !== null).length;
  document.getElementById('status').innerText = `${selected} of ${votingUsers.length} selected`;

  const userCountEl = document.getElementById('userCount');
  if (userCountEl) {
    userCountEl.textContent = `${selected}/${votingUsers.length} voted`;
  }

  const userListContent = document.getElementById('userListContent');
  if (userListContent) {
    userListContent.innerHTML = '';

    userList.forEach(user => {
      const isSpectator = user.isHost && user.wantsToVote === false;
      const hasVoted = user.vote !== null;

      const row = document.createElement('div');
      row.className = 'user-row';
      if (isSpectator) row.classList.add('spectator');
      else if (hasVoted) row.classList.add('voted');
      else row.classList.add('pending');

      const status = document.createElement('span');
      status.className = 'user-status';
      if (isSpectator) status.textContent = '👁';
      else if (hasVoted) status.textContent = '✓';
      else status.textContent = '⋯';
      status.title = isSpectator ? 'Spectating' : (hasVoted ? 'Voted' : 'Waiting');

      const nameSpan = document.createElement('span');
      nameSpan.className = 'user-name';
      nameSpan.textContent = user.username;
      nameSpan.title = user.username;

      row.appendChild(status);
      row.appendChild(nameSpan);

      if (user.isHost) {
        const badge = document.createElement('span');
        badge.className = 'user-badge host';
        badge.textContent = '👑';
        badge.title = 'Host';
        row.appendChild(badge);
      }

      // Show vote value chip after reveal
      if (votesRevealed && hasVoted && !isSpectator) {
        const chip = document.createElement('span');
        chip.className = 'user-vote-chip';
        chip.textContent = user.vote;
        row.appendChild(chip);
      }

      userListContent.appendChild(row);
    });
  }
});

socket.on('countdown', seconds => {
  document.getElementById('countdown').innerText = `Revealing in: ${seconds}`;
  document.getElementById('toggleVotingBtn').disabled = true;
  document.getElementById('newRoundBtn').disabled = true;
  document.getElementById('newSessionBtn').disabled = true;
  document.getElementById('deckSelector').disabled = true;
});

socket.on('revealVotes', ({ users, stats }) => {
  votesRevealed = true;
  updateToggleBtnLabel();
  const toggleBtn = document.getElementById('toggleVotingBtn');
  toggleBtn.disabled = false;
  document.getElementById('newRoundBtn').disabled = false;
  document.getElementById('newSessionBtn').disabled = false;
  document.getElementById('countdown').innerText = "";
  const votingUsers = Object.values(users).filter(u => !(u.isHost && u.wantsToVote === false));

  const results = votingUsers
    .map((u, i) => {
      const isOutlier = stats?.outliers?.includes(u.username);
      const delay = (i * 80).toString();
      const safeVote = escapeHTML(String(u.vote));
      return `
        <div class="vote-card-wrapper${isOutlier ? ' outlier' : ''}">
          <div class="vote-card${isOutlier ? ' outlier' : ''}" data-value="${safeVote}" style="animation-delay:${delay}ms">
            <div class="vote-value">${safeVote}</div>
          </div>
          <p class="voter-name" title="${escapeHTML(u.username)}">${escapeHTML(u.username)}</p>
        </div>
      `;
    })
    .join('');

  const realVotesForStats = votingUsers.map(u => u.vote).filter(v => v !== null && v !== '?');
  const uniqueVotes = new Set(realVotesForStats).size;
  const allAgreed = realVotesForStats.length >= 2 && uniqueVotes === 1;

  const statTiles = [];
  if (stats?.average !== undefined) {
    statTiles.push(`<div class="stat-tile"><span class="stat-label">Average</span><span class="stat-value">${stats.average}</span></div>`);
  }
  if (stats?.median !== undefined) {
    statTiles.push(`<div class="stat-tile"><span class="stat-label">Median</span><span class="stat-value">${escapeHTML(String(stats.median))}</span></div>`);
  }
  if (realVotesForStats.length > 0) {
    statTiles.push(`<div class="stat-tile"><span class="stat-label">Votes</span><span class="stat-value">${realVotesForStats.length}</span></div>`);
  }
  if (allAgreed) {
    statTiles.push(`<div class="stat-tile consensus"><span class="stat-label">Consensus</span><span class="stat-value">🎉</span></div>`);
  } else if (stats?.outliers?.length) {
    statTiles.push(`<div class="stat-tile outlier"><span class="stat-label">Outliers</span><span class="stat-value">${stats.outliers.length}</span></div>`);
  }
  const summary = statTiles.length ? `<div class="stat-row">${statTiles.join('')}</div>` : '';

  document.getElementById('votesDisplay').innerHTML = results;
  document.getElementById('voteSummary').innerHTML = summary;

  // Consensus detection: all non-"?" votes identical, at least 2 voters
  const realVotes = votingUsers.map(u => u.vote).filter(v => v !== null && v !== '?');
  if (realVotes.length >= 2 && realVotes.every(v => v === realVotes[0])) {
    const totalDelay = votingUsers.length * 80 + 500;
    setTimeout(() => launchConfetti(), totalDelay);
  }
});

function launchConfetti() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const colors = ['#ff3b3b', '#ffbf00', '#2ecc40', '#0074d9', '#b10dc9', '#ff851b', '#ffffff'];
  const container = document.createElement('div');
  container.className = 'confetti-container';
  document.body.appendChild(container);

  const pieces = 80;
  for (let i = 0; i < pieces; i++) {
    const piece = document.createElement('div');
    piece.className = 'confetti-piece';
    piece.style.left = Math.random() * 100 + '%';
    piece.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
    piece.style.animationDuration = (2 + Math.random() * 2) + 's';
    piece.style.animationDelay = Math.random() * 0.5 + 's';
    piece.style.width = (6 + Math.random() * 6) + 'px';
    piece.style.height = (10 + Math.random() * 8) + 'px';
    piece.style.transform = `rotate(${Math.random() * 360}deg)`;
    container.appendChild(piece);
  }

  setTimeout(() => container.remove(), 5000);
}

socket.on('hostLeft', () => {
  const overlay = document.getElementById('hostLeftOverlay');
  if (overlay) overlay.classList.remove('hidden');
});

socket.on('userLeft', ({ username: name }) => {
  if (name && name !== username) showToast(`${name} left the session`, 'info');
});

socket.on('userJoined', ({ username: name, clientId: joinedClientId }) => {
  if (!name || joinedClientId === clientId) return;
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

function showModal(message, onConfirm, withInput = false, yesNoMode = false, hideCancel = false, prefill = '') {
  const backdrop = document.getElementById('modalBackdrop');
  const messageEl = document.getElementById('modalMessage');
  const confirmBtn = document.getElementById('modalConfirm');
  const cancelBtn = document.getElementById('modalCancel');
  const errorEl = document.getElementById('modalError');

  messageEl.innerHTML = withInput
    ? `${message}<br><input type="text" id="modalInput" maxlength="20" value="${prefill}">`
    : message;
  errorEl.textContent = "";
  cancelBtn.style.display = hideCancel ? 'none' : '';

  if (yesNoMode) {
    confirmBtn.textContent = "Yes";
    cancelBtn.textContent = "No";
  } else {
    confirmBtn.textContent = "Confirm";
    cancelBtn.textContent = "Cancel";
  }

  backdrop.classList.remove('hidden');

  function cleanup() {
    backdrop.classList.add('hidden');
    confirmBtn.removeEventListener('click', confirmHandler);
    cancelBtn.removeEventListener('click', cancelHandler);
  }

  function confirmHandler() {
    const errorEl = document.getElementById('modalError');

    if (withInput) {
      const inputEl = document.getElementById('modalInput');
      if (!inputEl || !isValidUsername(inputEl.value.trim())) {
        errorEl.textContent = "Name must be letters only (max 20 characters).";
        return;
      }
    }

    errorEl.textContent = "";
    if (onConfirm) {
      if (yesNoMode) {
        onConfirm(true);
      } else {
        onConfirm();
      }
    }
    cleanup();
  }

  function cancelHandler() {
    if (onConfirm && yesNoMode) {
      onConfirm(false);
    }
    cleanup();
  }

  confirmBtn.addEventListener('click', confirmHandler);
  cancelBtn.addEventListener('click', cancelHandler);
}

socket.on('sessionState', ({ votingEnabled: enabled }) => {
  const changed = votingEnabled !== enabled;
  votingEnabled = enabled;
  updateVotingLockState();
  if (changed && hasConnectedOnce && !votesRevealed) {
    showToast(enabled ? 'Voting unlocked' : 'Voting locked', enabled ? 'success' : 'info');
  }
});

socket.on('connect', () => {
  const wasDisconnected = connectionStatus === 'disconnected' || connectionStatus === 'reconnecting';
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

function updateVotingLockState() {
  const lockEl = document.getElementById('votingLockedIndicator');
  const cards = document.querySelectorAll('.card');
  if (!votingEnabled) {
    lockEl.classList.remove('hidden');
    cards.forEach(c => c.classList.add('voting-locked'));
  } else {
    lockEl.classList.add('hidden');
    cards.forEach(c => c.classList.remove('voting-locked'));
  }
  updateToggleBtnLabel();
}

function updateToggleBtnLabel() {
  const toggleBtn = document.getElementById('toggleVotingBtn');
  if (votesRevealed) {
    const effective = pendingVotingEnabled !== null ? pendingVotingEnabled : votingEnabled;
    toggleBtn.textContent = effective
      ? '🔒 Lock Voting (next round)'
      : '🔓 Unlock Voting (next round)';
  } else {
    toggleBtn.textContent = votingEnabled ? '🔒 Lock Voting' : '🔓 Unlock Voting';
  }
}

// ── Host settings modal ───────────────────────────────────────────────────────
function showHostSettingsModal() {
  document.getElementById('hostSettingsBackdrop').classList.remove('hidden');
}

function confirmHostSettings() {
  const wantsToVote = document.getElementById('toggleJoinVoting').checked;
  const votingEnabledVal = document.getElementById('toggleVotingEnabled').checked;

  document.getElementById('hostSettingsBackdrop').classList.add('hidden');

  socket.emit('hostVotingDecision', { sessionId, wantsToVote });
  socket.emit('setVotingEnabled', { sessionId, votingEnabled: votingEnabledVal });

  if (!wantsToVote) {
    document.getElementById('cardOptions').style.display = 'none';
  }

  sessionStorage.setItem(`jiraPokerHostVoteDecision_${sessionId}`, String(wantsToVote));
}

document.getElementById('newSessionBtn').addEventListener('click', (e) => {
  e.preventDefault();
  showModal(
    "Start a new session?<br><br>" +
    "This will create a fresh session.<br>" +
    "Currently connected users will <span style='color:red;font-weight:bold;'>NOT</span> be moved.",
    () => {
      Object.keys(sessionStorage)
        .filter(k => k.startsWith('jiraPokerHostVoteDecision_'))
        .forEach(k => sessionStorage.removeItem(k));
      postCreate();
    }
  );
});
