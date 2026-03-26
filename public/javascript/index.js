const socket = io();

function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function isValidUsername(name) {
  return /^[a-zA-Z]{1,20}$/.test(name);
}

const sessionId = window.location.pathname.split('/').pop();
if (!sessionId || sessionId === 'session' || sessionId === 'undefined') {
  window.location.href = '/create';
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

    const hostVoteDecision = sessionStorage.getItem("jiraPokerHostVoteDecision");
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
    const hostVoteDecision = sessionStorage.getItem("jiraPokerHostVoteDecision");
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
  document.getElementById('hostLeftNewSession').addEventListener('click', () => {
    window.location.href = '/create';
  });
});

const DECK_PRESETS = {
  fibonacci: [1, 2, 3, 5, 8, 13, 21, "?"],
  hours: [1, 2, 4, 8, 16, 24, 40, "?"],
  tshirt: ["XS", "S", "M", "L", "XL", "XXL", "?"]
};

let currentDeckType = sessionStorage.getItem("jiraPokerDeckType") || "fibonacci";
if (!DECK_PRESETS[currentDeckType]) currentDeckType = "fibonacci";
let cardValues = DECK_PRESETS[currentDeckType];
document.getElementById('deckSelector').value = currentDeckType;
const cardContainer = document.getElementById('cardOptions');

function renderCards() {
  cardContainer.innerHTML = '';
  selectedCard = null;
  cardValues.forEach(value => {
    const card = document.createElement('div');
    card.classList.add('card');
    card.innerText = value;
    card.onclick = () => selectCard(card, value);
    cardContainer.appendChild(card);
  });
}

renderCards();

document.getElementById('deckSelector').addEventListener('change', (e) => {
  const newDeckType = e.target.value;
  socket.emit('changeDeck', { sessionId, deckType: newDeckType });
});

socket.on('deckChanged', ({ deckType }) => {
  if (DECK_PRESETS[deckType]) {
    currentDeckType = deckType;
    cardValues = DECK_PRESETS[currentDeckType];
    document.getElementById('deckSelector').value = currentDeckType;
    renderCards();
  }
});

function selectCard(element, value) {
  if (selectedCard) return;

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
  showModal(
    "Are you sure you want to start a new round?<br>" +
    "<span style='color:green;font-weight:bold;'>Everyone will be redirected.</span>",
    () => {
      sessionStorage.setItem("jiraPokerUsername", username);
      socket.emit('requestNewRound', { sessionId, deckType: currentDeckType });
    }
  );
}

function copyLink() {
  const url = `${window.location.origin}/session/${sessionId}`;
  navigator.clipboard.writeText(url).then(() => {
    const msg = document.getElementById('copiedMsg');
    msg.style.display = 'block';
    setTimeout(() => msg.style.display = 'none', 2000);
  }).catch(err => {
    console.error('Failed to copy:', err);
    showModal('Failed to copy session link to clipboard.', null, false, false, true);
  });
}

socket.on('redirectToNewSession', ({ url, usernames, wantsToVote, deckType }) => {
  if (deckType && DECK_PRESETS[deckType]) {
    sessionStorage.setItem("jiraPokerDeckType", deckType);
  }
  const mySocketId = socket.id;
  const myName = usernames?.[mySocketId];
  const myWantsToVote = wantsToVote?.[mySocketId];

  if (myName) {
    sessionStorage.setItem("jiraPokerUsername", myName);
    localStorage.setItem("jiraPokerUsername", myName);
  }
  if (myWantsToVote !== undefined) {
    sessionStorage.setItem("jiraPokerHostVoteDecision", myWantsToVote);
  }

  window.location.href = url;
});

socket.on('usersUpdate', users => {
  const myUser = Object.values(users).find(u => u.clientId === clientId);
  const isHost = myUser?.isHost;

  document.getElementById('newRoundBtn').style.display = isHost ? 'inline-block' : 'none';
  document.getElementById('deckSelector').style.display = isHost ? 'inline-block' : 'none';

  // Grey out deck selector if votes have been cast
  if (isHost) {
    const hasVotes = Object.values(users).some(u => u.vote !== null);
    document.getElementById('deckSelector').disabled = hasVotes;
  }

  if (isHost && !window.hasBeenAskedToVote) {
    window.hasBeenAskedToVote = true;

    if (myUser?.wantsToVote === true) {
      console.log('Host already decided to vote');
    } else if (myUser?.wantsToVote === false) {
      console.log('Host already decided not to vote');
      document.getElementById('cardOptions').style.display = 'none';
    } else {
      showModal("Do you want to join voting?", (wantsToVote) => {
        socket.emit('hostVotingDecision', { sessionId, wantsToVote });
        if (!wantsToVote) {
          document.getElementById('cardOptions').style.display = 'none';
        }
      }, false, true);
    }
  }

  const userList = Object.values(users);
  const votingUsers = userList.filter(u => !(u.isHost && u.wantsToVote === false));
  const selected = votingUsers.filter(u => u.vote !== null).length;
  document.getElementById('status').innerText = `${selected} of ${votingUsers.length} selected`;

  const userListContent = document.getElementById('userListContent');
  if (userListContent) {
    userListContent.innerHTML = '';

    userList.forEach(user => {
      const userItem = document.createElement('div');
      userItem.style.display = 'flex';
      userItem.style.alignItems = 'center';
      userItem.style.justifyContent = 'flex-start';
      userItem.style.gap = '8px';
      userItem.style.padding = '4px 0';
      userItem.style.fontSize = '16px';

      const dot = document.createElement('span');
      dot.style.display = 'inline-block';
      dot.style.width = '10px';
      dot.style.height = '10px';
      dot.style.borderRadius = '50%';

      dot.style.backgroundColor = (user.vote !== null) ? 'limegreen' : 'gray';

      const nameSpan = document.createElement('span');
      nameSpan.className = 'user-name';
      nameSpan.textContent = user.username;
      nameSpan.title = user.username;

      if (user.isHost && user.wantsToVote === false) {
        nameSpan.style.opacity = '0.6';
        nameSpan.textContent += ' (Host is not voting)';
      } else if (user.isHost && user.wantsToVote === true) {
        nameSpan.textContent += ' (Host)';
      }

      userItem.appendChild(dot);
      userItem.appendChild(nameSpan);
      userListContent.appendChild(userItem);
    });
  }
});

socket.on('countdown', seconds => {
  document.getElementById('countdown').innerText = `Revealing in: ${seconds}`;
});

socket.on('revealVotes', ({ users, stats }) => {
  document.getElementById('countdown').innerText = "";
  const votingUsers = Object.values(users).filter(u => !(u.isHost && u.wantsToVote === false));

  const results = votingUsers
    .map(u => {
      const isOutlier = stats?.outliers?.includes(u.username);
      return `
        <div class="vote-card${isOutlier ? ' outlier' : ''}">
          <p class="voter-name" title="${escapeHTML(u.username)}">${escapeHTML(u.username)}</p>
          <div class="vote-value">${u.vote}</div>
        </div>
      `;
    })
    .join('');

  let summary = "";
  if (stats?.average !== undefined) {
    summary += `<div class="vote-summary">Average: ${stats.average}</div>`;
  }
  if (stats?.median !== undefined) {
    summary += `<div class="vote-summary">Median: ${stats.median}</div>`;
  }

  document.getElementById('votesDisplay').innerHTML = results;
  document.getElementById('voteSummary').innerHTML = summary;
});

socket.on('hostLeft', () => {
  const overlay = document.getElementById('hostLeftOverlay');
  if (overlay) overlay.classList.remove('hidden');
});

socket.on('joinFailed', ({ reason }) => {
  showModal(`Failed to join session: ${reason}`, () => {
    window.location.href = '/';
  });
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

document.getElementById('newSessionLink').addEventListener('click', (e) => {
  e.preventDefault();
  showModal(
    "Start a new session?<br><br>" +
    "This will create a fresh session.<br>" +
    "Currently connected users will <span style='color:red;font-weight:bold;'>NOT</span> be moved.",
    () => {
      sessionStorage.removeItem("jiraPokerHostVoteDecision");
      window.location.href = '/create';
    }
  );
});
