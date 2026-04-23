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

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function trapFocus(container) {
  const prevActive = document.activeElement;
  const getFocusable = () =>
    Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
      (el) => el.offsetParent !== null
    );

  const initial = getFocusable();
  if (initial.length) initial[0].focus();

  function onKeyDown(e) {
    if (e.key !== 'Tab') return;
    const els = getFocusable();
    if (!els.length) return;
    const first = els[0];
    const last = els[els.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !container.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }
  container.addEventListener('keydown', onKeyDown);
  return () => {
    container.removeEventListener('keydown', onKeyDown);
    if (prevActive && typeof prevActive.focus === 'function') prevActive.focus();
  };
}

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
    wantsToVote: hostVoteDecision !== null ? hostVoteDecision === 'true' : undefined,
  });
}

function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Mirrors server's sanitize_username: strip control chars, then match
// unicode letters/digits/spaces/hyphen/apostrophe/underscore, 1-30 chars.
// Keep in sync with USERNAME_RE in server.py.
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS_RE = /[\x00-\x1F\x7F]/g;
const USERNAME_RE = /^[\p{L}\p{N}\s\-'_]{1,30}$/u;
const USERNAME_MAX_LEN = 30;

function isValidUsername(name) {
  if (typeof name !== 'string') return false;
  const cleaned = name.replace(CONTROL_CHARS_RE, '').trim();
  return USERNAME_RE.test(cleaned);
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

let username =
  sessionStorage.getItem('jiraPokerUsername') || localStorage.getItem('jiraPokerUsername') || '';
let clientId = sessionStorage.getItem('jiraPokerClientId');
if (!clientId) {
  const bytes = new Uint8Array(7);
  crypto.getRandomValues(bytes);
  clientId =
    'client-' +
    Array.from(bytes, (b) => b.toString(36).padStart(2, '0'))
      .join('')
      .slice(0, 7);
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
        .map(
          ([v, items]) =>
            `<h4>v${escapeHTML(v)}</h4><ul>${items.map((c) => `<li>${escapeHTML(c)}</li>`).join('')}</ul>`
        )
        .join('');
    }
  } catch (err) {
    console.error('Version badge error:', err);
  }
}

let selectedCard = null;
let hasChangedVote = false;

const DEFAULT_TITLE = 'Pokering Points';
function setDocTitle(prefix) {
  document.title = prefix ? `${prefix} — ${DEFAULT_TITLE}` : DEFAULT_TITLE;
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
      username = name.replace(CONTROL_CHARS_RE, '').trim().slice(0, USERNAME_MAX_LEN);
      sessionStorage.setItem('jiraPokerUsername', username);
      localStorage.setItem('jiraPokerUsername', username);

      document.getElementById('mainContent').classList.remove('hidden');

      const hostVoteDecision = sessionStorage.getItem(`jiraPokerHostVoteDecision_${sessionId}`);
      socket.emit('join', {
        sessionId,
        username,
        clientId,
        deckType: currentDeckType,
        wantsToVote: hostVoteDecision !== null ? hostVoteDecision === 'true' : undefined,
      });
    },
    true,
    false,
    true,
    username
  );
}

window.addEventListener('load', () => {
  updateVersionBadge();

  document.getElementById('newRoundBtn').addEventListener('click', startNewRound);
  document.getElementById('copyLinkBtn').addEventListener('click', copyLink);
  document.getElementById('toggleSpectateBtn').addEventListener('click', toggleSpectate);
  document.getElementById('hostUsernameInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      document.getElementById('hostSettingsConfirm').click();
    }
  });
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
    sessionStorage.setItem('pokeringIsCreator', '1');
    postCreate();
  });
  document.getElementById('hostSettingsConfirm').addEventListener('click', confirmHostSettings);

  const isCreator = sessionStorage.getItem('pokeringIsCreator') === '1';
  const sessionUsername = sessionStorage.getItem('jiraPokerUsername');

  if (isCreator) {
    sessionStorage.removeItem('pokeringIsCreator');
    window.hostSettingsShown = true;
    if (sessionUsername && isValidUsername(sessionUsername)) {
      username = sessionUsername;
    }
    showHostSettingsModal(true);
  } else if (sessionUsername && isValidUsername(sessionUsername)) {
    username = sessionUsername;
    const hostVoteDecision = sessionStorage.getItem(`jiraPokerHostVoteDecision_${sessionId}`);
    document.getElementById('mainContent').classList.remove('hidden');

    socket.emit('join', {
      sessionId,
      username,
      clientId,
      deckType: currentDeckType,
      wantsToVote: hostVoteDecision !== null ? hostVoteDecision === 'true' : undefined,
    });
  } else {
    promptUsername();
  }
});

const DECK_PRESETS = {
  fibonacci: [1, 2, 3, 5, 8, 13, 21, '?'],
  hours: [1, 2, 4, 8, 16, 24, 40, '?'],
  tshirt: ['XS', 'S', 'M', 'L', 'XL', 'XXL', '?'],
};

const DECK_LABELS = {
  fibonacci: 'Fibonacci (1-21)',
  hours: 'Hours (1-40)',
  tshirt: 'T-Shirt (XS-XXL)',
};

let currentDeckType = sessionStorage.getItem('jiraPokerDeckType') || 'fibonacci';
if (!DECK_PRESETS[currentDeckType]) currentDeckType = 'fibonacci';
let cardValues = DECK_PRESETS[currentDeckType];
let votingEnabled = true;
let votesRevealed = false;
let pendingVotingEnabled = null;
document.getElementById('deckSelector').value = currentDeckType;
const cardContainer = document.getElementById('cardOptions');

function renderCards() {
  cardContainer.innerHTML = '';
  selectedCard = null;
  cardValues.forEach((value) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.classList.add('card');
    card.dataset.value = value;
    card.textContent = value;
    card.setAttribute('aria-label', `Vote ${value}`);
    if (votesRevealed) card.disabled = true;
    card.addEventListener('click', () => selectCard(card, value));
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

function isUserSpectator(u) {
  if (!u) return false;
  return Boolean(u.isSpectator) || (u.isHost && u.wantsToVote === false);
}

function selectCard(element, value) {
  if (votesRevealed || !votingEnabled) return;
  if (isUserSpectator(myUser)) return;
  if (selectedCard === element) return;

  // First vote: select + emit, keep other cards clickable for one swap.
  if (!selectedCard) {
    selectedCard = element;
    element.classList.add('selected');
    applyVoteDimState();
    socket.emit('vote', { sessionId, value });
    return;
  }

  // Already voted — this is the 1 allowed change.
  if (hasChangedVote) return;

  selectedCard.classList.remove('selected');
  selectedCard = element;
  element.classList.add('selected');
  hasChangedVote = true;
  applyVoteDimState();

  socket.emit('vote', { sessionId, value });
}

function applyVoteDimState() {
  // States:
  //   no selection → cards normal, enabled
  //   selected (no change used) → non-selected dimmed but clickable (swap allowed)
  //   change used → all non-selected locked + dimmed, selected highlighted
  const cards = document.querySelectorAll('.card');
  cards.forEach((c) => {
    c.classList.remove('vote-dimmed', 'vote-swappable');
    if (votesRevealed) {
      c.disabled = true;
      return;
    }
    if (!selectedCard) {
      c.disabled = !votingEnabled;
      return;
    }
    if (c === selectedCard) {
      c.disabled = false;
      return;
    }
    c.classList.add('vote-dimmed');
    if (hasChangedVote) {
      c.disabled = true;
    } else {
      c.disabled = !votingEnabled;
      c.classList.add('vote-swappable');
    }
  });
}

function toggleSpectate() {
  if (!myUser || myUser.isHost) return;
  socket.emit('setSpectator', { sessionId, isSpectator: !myUser.isSpectator });
}

function startNewRound() {
  const payload = { sessionId, deckType: currentDeckType };
  if (pendingVotingEnabled !== null) {
    payload.votingEnabled = pendingVotingEnabled;
  }
  socket.emit('requestNewRound', payload);
}

function promptRename() {
  showModal(
    'Change your username:',
    () => {
      const name = document.getElementById('modalInput').value.trim();
      username = name.replace(CONTROL_CHARS_RE, '').trim().slice(0, USERNAME_MAX_LEN);
      sessionStorage.setItem('jiraPokerUsername', username);
      localStorage.setItem('jiraPokerUsername', username);
      showToast(`Username changed to "${username}"`, 'success');
      const hostVoteDecision = sessionStorage.getItem(`jiraPokerHostVoteDecision_${sessionId}`);
      socket.emit('join', {
        sessionId,
        username,
        clientId,
        deckType: currentDeckType,
        wantsToVote: hostVoteDecision !== null ? hostVoteDecision === 'true' : undefined,
      });
    },
    true,
    false,
    false,
    username
  );
}

function copyLink() {
  const url = `${window.location.origin}/session/${sessionId}`;
  navigator.clipboard
    .writeText(url)
    .then(() => {
      showToast('Session link copied to clipboard', 'success');
    })
    .catch((err) => {
      console.error('Failed to copy:', err);
      showToast('Failed to copy session link', 'error');
    });
}

socket.on('roundReset', ({ deckType, votingEnabled: enabled }) => {
  selectedCard = null;
  hasChangedVote = false;
  votesRevealed = false;
  pendingVotingEnabled = null;
  const votingChanged = votingEnabled !== enabled;
  votingEnabled = enabled;

  document.querySelectorAll('.card').forEach((c) => c.classList.remove('selected'));
  document.getElementById('countdown').textContent = '';
  document.getElementById('votesDisplay').innerHTML = '';
  document.getElementById('voteSummary').innerHTML = '';

  if (deckType && DECK_PRESETS[deckType]) {
    currentDeckType = deckType;
    document.getElementById('deckSelector').value = deckType;
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

let currentUsers = {};
let myUser = null;

function refreshMyUser() {
  myUser = Object.values(currentUsers).find((u) => u.clientId === clientId) || null;
  return myUser;
}

function renderUserList() {
  const users = currentUsers;
  const isHost = myUser?.isHost;

  // Sync vote-UI state from server-preserved data (covers mid-round reconnects).
  if (myUser) {
    if (myUser.voteChanged) hasChangedVote = true;
    if (!selectedCard && !votesRevealed && myUser.vote !== null && myUser.vote !== true) {
      const candidate = cardContainer.querySelector(
        `.card[data-value="${CSS.escape(String(myUser.vote))}"]`
      );
      if (candidate) {
        selectedCard = candidate;
        candidate.classList.add('selected');
        applyVoteDimState();
      }
    }
  }

  document.getElementById('newRoundBtn').classList.toggle('hidden', !isHost);
  document.getElementById('deckSelector').classList.remove('hidden');
  const toggleBtn = document.getElementById('toggleVotingBtn');
  toggleBtn.classList.toggle('hidden', !isHost);

  const hasVotes = Object.values(users).some((u) => u.vote !== null);

  if (isHost) {
    document.getElementById('deckSelector').disabled = hasVotes || votesRevealed;
    toggleBtn.disabled = hasVotes && !votesRevealed;
    updateToggleBtnLabel();
  } else {
    document.getElementById('deckSelector').disabled = true;
  }

  const spectateBtn = document.getElementById('toggleSpectateBtn');
  if (myUser && !isHost) {
    spectateBtn.classList.remove('hidden');
    spectateBtn.disabled = hasVotes || votesRevealed;
    spectateBtn.textContent = myUser.isSpectator ? '🗳️ Join voting' : '👁 Spectate';
    spectateBtn.setAttribute('aria-pressed', myUser.isSpectator ? 'true' : 'false');
  } else {
    spectateBtn.classList.add('hidden');
  }

  const cardOpts = document.getElementById('cardOptions');
  if (myUser) {
    cardOpts.classList.toggle('hidden', isUserSpectator(myUser));
  }

  const userList = Object.values(users);
  const votingUsers = userList.filter((u) => !isUserSpectator(u));
  const selected = votingUsers.filter((u) => u.vote !== null).length;
  document.getElementById('status').textContent = `${selected} of ${votingUsers.length} selected`;

  const userCountEl = document.getElementById('userCount');
  if (userCountEl) userCountEl.textContent = `${selected}/${votingUsers.length} voted`;

  if (votesRevealed) setDocTitle('Votes revealed');
  else if (votingUsers.length > 0) setDocTitle(`${selected}/${votingUsers.length} voted`);
  else setDocTitle(null);

  const userListContent = document.getElementById('userListContent');
  if (!userListContent) return;
  userListContent.innerHTML = '';

  userList.forEach((user) => {
    const isSpectator = isUserSpectator(user);
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
    status.title = isSpectator ? 'Spectating' : hasVoted ? 'Voted' : 'Waiting';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'user-name';
    nameSpan.textContent = user.username;
    nameSpan.title = user.username;

    row.appendChild(status);
    row.appendChild(nameSpan);

    if (user.clientId === clientId) {
      const editBtn = document.createElement('button');
      editBtn.className = 'user-edit-btn';
      editBtn.textContent = '✏️';
      editBtn.title = 'Edit username';
      editBtn.setAttribute('aria-label', 'Edit username');
      editBtn.disabled = votesRevealed || hasVoted;
      editBtn.addEventListener('click', promptRename);
      row.appendChild(editBtn);
    }

    if (user.isHost) {
      const badge = document.createElement('span');
      badge.className = 'user-badge host';
      badge.textContent = '👑';
      badge.title = 'Host';
      row.appendChild(badge);
    }

    if (user.voteChanged && !isSpectator) {
      const changed = document.createElement('span');
      changed.className = 'user-vote-changed';
      changed.textContent = '↻';
      changed.title = 'Changed vote this round';
      changed.setAttribute('aria-label', 'Changed vote');
      row.appendChild(changed);
    }

    if (votesRevealed && hasVoted && !isSpectator) {
      const chip = document.createElement('span');
      chip.className = 'user-vote-chip';
      chip.textContent = user.vote;
      row.appendChild(chip);
    }

    userListContent.appendChild(row);
  });
}

socket.on('usersUpdate', (users) => {
  ensureConnectionIndicator();
  currentUsers = users;
  refreshMyUser();
  const isHost = myUser?.isHost;

  if (isHost && !window.hostSettingsShown) {
    window.hostSettingsShown = true;
    if (myUser?.wantsToVote === false) {
      document.getElementById('cardOptions').classList.add('hidden');
    } else if (myUser?.wantsToVote === undefined || myUser?.wantsToVote === null) {
      showHostSettingsModal();
    }
  }

  renderUserList();
});

// Diff event: vote cast/changed. Patches local snapshot; avoids full dict broadcast.
// Real vote value arrives later via revealVotes (we use `true` as a "voted" sentinel).
socket.on('userVoted', ({ clientId: votedId, voteChanged }) => {
  if (!votedId) return;
  const user =
    votedId === clientId ? myUser : Object.values(currentUsers).find((u) => u.clientId === votedId);
  if (!user) return;
  const firstFlag = voteChanged && !user.voteChanged;
  if (user.vote === null) user.vote = true;
  if (voteChanged) user.voteChanged = true;
  if (firstFlag && votedId !== clientId) {
    showToast(`${user.username} changed their vote`, 'info');
  }
  renderUserList();
});

socket.on('countdown', (seconds) => {
  document.getElementById('countdown').textContent = `Revealing in: ${seconds}`;
  document.getElementById('toggleVotingBtn').disabled = true;
  document.getElementById('newRoundBtn').disabled = true;
  document.getElementById('newSessionBtn').disabled = true;
  document.getElementById('deckSelector').disabled = true;
  document.querySelectorAll('.user-edit-btn').forEach((b) => (b.disabled = true));
});

socket.on('revealVotes', ({ users, stats }) => {
  votesRevealed = true;
  currentUsers = users;
  refreshMyUser();
  updateToggleBtnLabel();
  const toggleBtn = document.getElementById('toggleVotingBtn');
  toggleBtn.disabled = false;
  document.getElementById('newRoundBtn').disabled = false;
  document.getElementById('newSessionBtn').disabled = false;
  document.getElementById('countdown').textContent = '';

  // Disable all cards once revealed (covers late joiners whose cards were still live)
  document.querySelectorAll('.card').forEach((c) => {
    c.disabled = true;
  });

  // Filter out users with null vote (late joiners joining at reveal state)
  const votingUsers = Object.values(users).filter((u) => u.vote !== null && !isUserSpectator(u));

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

  const realVotesForStats = votingUsers.map((u) => u.vote).filter((v) => v !== null && v !== '?');
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

  document.getElementById('votesDisplay').innerHTML = results;
  document.getElementById('voteSummary').innerHTML = summary;
  renderUserList();

  // Consensus detection: all non-"?" votes identical, at least 2 voters
  const realVotes = votingUsers.map((u) => u.vote).filter((v) => v !== null && v !== '?');
  if (realVotes.length >= 2 && realVotes.every((v) => v === realVotes[0])) {
    const totalDelay = votingUsers.length * 80 + 500;
    setTimeout(() => launchConfetti(), totalDelay);
  }
});

function launchConfetti() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const colors = ['#ff3b3b', '#ffbf00', '#2ecc40', '#0074d9', '#b10dc9', '#ff851b', '#ffffff'];
  const dpr = window.devicePixelRatio || 1;
  const canvas = document.createElement('canvas');
  canvas.style.position = 'fixed';
  canvas.style.inset = '0';
  canvas.style.pointerEvents = 'none';
  canvas.style.zIndex = '9998';
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  canvas.style.width = window.innerWidth + 'px';
  canvas.style.height = window.innerHeight + 'px';
  document.body.appendChild(canvas);

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const w = window.innerWidth;
  const h = window.innerHeight;

  const pieces = Array.from({ length: 80 }, () => ({
    x: Math.random() * w,
    y: -20 - Math.random() * h * 0.3,
    width: 6 + Math.random() * 6,
    height: 10 + Math.random() * 8,
    color: colors[Math.floor(Math.random() * colors.length)],
    vx: (Math.random() - 0.5) * 60,
    vy: 180 + Math.random() * 140,
    rot: Math.random() * Math.PI * 2,
    rotV: (Math.random() - 0.5) * 8,
  }));

  const DURATION = 5000;
  let start = null;
  let last = null;

  function frame(ts) {
    if (start === null) start = ts;
    if (last === null) last = ts;
    const dt = (ts - last) / 1000;
    last = ts;
    const elapsed = ts - start;
    const alpha = Math.max(0, 1 - elapsed / DURATION);

    ctx.clearRect(0, 0, w, h);
    for (const p of pieces) {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.rot += p.rotV * dt;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.width / 2, -p.height / 2, p.width, p.height);
      ctx.restore();
    }

    if (elapsed < DURATION) {
      requestAnimationFrame(frame);
    } else {
      canvas.remove();
    }
  }
  requestAnimationFrame(frame);
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

socket.on('serverShutdown', ({ reason } = {}) => {
  showToast(reason || 'Server is restarting. Reconnecting...', 'info', 5000);
});

function showModal(
  message,
  onConfirm,
  withInput = false,
  yesNoMode = false,
  hideCancel = false,
  prefill = ''
) {
  const backdrop = document.getElementById('modalBackdrop');
  const messageEl = document.getElementById('modalMessage');
  const confirmBtn = document.getElementById('modalConfirm');
  const cancelBtn = document.getElementById('modalCancel');
  const errorEl = document.getElementById('modalError');

  messageEl.innerHTML = message;
  if (withInput) {
    messageEl.appendChild(document.createElement('br'));
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'modalInput';
    input.maxLength = USERNAME_MAX_LEN;
    input.value = prefill;
    messageEl.appendChild(input);
  }
  errorEl.textContent = '';
  cancelBtn.style.display = hideCancel ? 'none' : '';

  if (yesNoMode) {
    confirmBtn.textContent = 'Yes';
    cancelBtn.textContent = 'No';
  } else {
    confirmBtn.textContent = 'Confirm';
    cancelBtn.textContent = 'Cancel';
  }

  backdrop.classList.remove('hidden');
  const releaseFocus = trapFocus(document.getElementById('modalContent'));
  // Focus the input if present (overrides trap's default first-focusable).
  const inputEl = document.getElementById('modalInput');
  if (inputEl) inputEl.focus();

  function onEscape(e) {
    if (e.key !== 'Escape') return;
    if (hideCancel) return;
    e.preventDefault();
    cancelHandler();
  }
  document.addEventListener('keydown', onEscape);

  function cleanup() {
    backdrop.classList.add('hidden');
    confirmBtn.removeEventListener('click', confirmHandler);
    cancelBtn.removeEventListener('click', cancelHandler);
    document.removeEventListener('keydown', onEscape);
    releaseFocus();
  }

  function confirmHandler() {
    const errorEl = document.getElementById('modalError');

    if (withInput) {
      const inputEl = document.getElementById('modalInput');
      if (!inputEl || !isValidUsername(inputEl.value.trim())) {
        errorEl.textContent = "Name: letters, digits, spaces, - _ ' (max 30).";
        return;
      }
    }

    errorEl.textContent = '';
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

function updateVotingLockState() {
  const lockEl = document.getElementById('votingLockedIndicator');
  const cards = document.querySelectorAll('.card');
  if (!votingEnabled) {
    lockEl.classList.remove('hidden');
    cards.forEach((c) => {
      c.classList.add('voting-locked');
      c.disabled = true;
    });
  } else {
    lockEl.classList.add('hidden');
    cards.forEach((c) => c.classList.remove('voting-locked'));
    applyVoteDimState();
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
let releaseHostSettingsFocus = null;

function showHostSettingsModal(withUsername = false) {
  const usernameRow = document.getElementById('hostUsernameRow');
  if (withUsername) {
    usernameRow.classList.remove('hidden');
    const input = document.getElementById('hostUsernameInput');
    input.value = username;
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

function confirmHostSettings() {
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
    username = name.replace(CONTROL_CHARS_RE, '').trim().slice(0, USERNAME_MAX_LEN);
    sessionStorage.setItem('jiraPokerUsername', username);
    localStorage.setItem('jiraPokerUsername', username);
    document.getElementById('mainContent').classList.remove('hidden');
  }

  const wantsToVote = document.getElementById('toggleJoinVoting').checked;
  const votingEnabledVal = document.getElementById('toggleVotingEnabled').checked;

  document.getElementById('hostSettingsBackdrop').classList.add('hidden');
  if (releaseHostSettingsFocus) {
    releaseHostSettingsFocus();
    releaseHostSettingsFocus = null;
  }

  if (isCreatorFlow) {
    socket.emit('join', {
      sessionId,
      username,
      clientId,
      deckType: currentDeckType,
      wantsToVote,
    });
  }

  socket.emit('hostVotingDecision', { sessionId, wantsToVote });
  socket.emit('setVotingEnabled', { sessionId, votingEnabled: votingEnabledVal });

  if (!wantsToVote) {
    document.getElementById('cardOptions').classList.add('hidden');
  }

  sessionStorage.setItem(`jiraPokerHostVoteDecision_${sessionId}`, String(wantsToVote));
}

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const modal = document.getElementById('modalBackdrop');
  const hostModal = document.getElementById('hostSettingsBackdrop');
  if (modal && !modal.classList.contains('hidden')) return;
  if (hostModal && !hostModal.classList.contains('hidden')) return;

  const active = document.activeElement;
  const tag = active?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || active?.isContentEditable) {
    return;
  }

  if (e.key >= '1' && e.key <= '9') {
    if (votesRevealed || !votingEnabled) return;
    if (selectedCard && hasChangedVote) return;
    const idx = parseInt(e.key, 10) - 1;
    const cards = cardContainer.querySelectorAll('.card');
    if (idx >= cards.length) return;
    const target = cards[idx];
    if (target.disabled) return;
    e.preventDefault();
    target.click();
  } else if (e.key === 'Enter') {
    if (tag === 'BUTTON' || tag === 'A') return;
    if (!votesRevealed) return;
    const btn = document.getElementById('newRoundBtn');
    if (!btn || btn.disabled || btn.classList.contains('hidden')) return;
    e.preventDefault();
    startNewRound();
  }
});

document.getElementById('newSessionBtn').addEventListener('click', (e) => {
  e.preventDefault();
  showModal(
    'Start a new session?<br><br>' +
      'This will create a fresh session.<br>' +
      'Currently connected users will <span class="emph-warning">NOT</span> be moved.',
    () => {
      Object.keys(sessionStorage)
        .filter((k) => k.startsWith('jiraPokerHostVoteDecision_'))
        .forEach((k) => sessionStorage.removeItem(k));
      sessionStorage.setItem('pokeringIsCreator', '1');
      postCreate();
    }
  );
});
