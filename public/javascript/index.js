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

let username = sessionStorage.getItem("jiraPokerUsername") || "";
let clientId = sessionStorage.getItem('jiraPokerClientId');
if (!clientId) {
  clientId = 'client-' + Math.random().toString(36).slice(2, 9);
  sessionStorage.setItem('jiraPokerClientId', clientId);
}

async function updateVersionBadge() {
  try {
    const res = await fetch('/version', { cache: 'no-store' });
    if (!res.ok) return;
    const { version } = await res.json();
    const el = document.getElementById('versionBadge');
    if (el) el.textContent = `v${version}`;
  } catch (e) {
    // silently ignore
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
    document.getElementById('welcomeUser').innerText = `Welcome, ${username}!`;
    document.getElementById('mainContent').classList.remove('hidden');
    socket.emit('join', { sessionId, username, clientId });
  }, true);
}

window.addEventListener('load', () => {
  if (isValidUsername(username)) {
    const hostVoteDecision = sessionStorage.getItem("jiraPokerHostVoteDecision");
    document.getElementById('welcomeUser').innerText = `Welcome, ${username}!`;
    document.getElementById('mainContent').classList.remove('hidden');

    socket.emit('join', {
      sessionId,
      username,
      clientId,
      wantsToVote: hostVoteDecision !== null ? (hostVoteDecision === "true") : undefined
    });
  } else {
    promptUsername();
  }
  updateVersionBadge();
});

const cardValues = [0, 0.5, 1, 2, 3, 4, 5, 8, 13, 20, "?"];
const cardContainer = document.getElementById('cardOptions');
cardValues.forEach(value => {
  const card = document.createElement('div');
  card.classList.add('card');
  card.innerText = value;
  card.onclick = () => selectCard(card, value);
  cardContainer.appendChild(card);
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
      socket.emit('requestNewRound', { sessionId });
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
    alert('Failed to copy session link to clipboard.');
  });
}

socket.on('redirectToNewSession', ({ url, usernames, wantsToVote }) => {
  const mySocketId = socket.id;
  const myName = usernames?.[mySocketId];
  const myWantsToVote = wantsToVote?.[mySocketId];

  if (myName) {
    sessionStorage.setItem("jiraPokerUsername", myName);
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

      if (user.vote !== null) {
        dot.style.backgroundColor = 'limegreen';
      } else {
        dot.style.backgroundColor = 'gray';
      }

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

  document.getElementById('votesDisplay').innerHTML = results;
  document.getElementById('voteSummary').innerHTML = summary;
});

socket.on('joinFailed', ({ reason }) => {
  showModal(`Failed to join session: ${reason}`, () => {
    window.location.href = '/';
  });
});

function showModal(message, onConfirm, withInput = false, yesNoMode = false) {
  const backdrop = document.getElementById('modalBackdrop');
  const messageEl = document.getElementById('modalMessage');
  const confirmBtn = document.getElementById('modalConfirm');
  const cancelBtn = document.getElementById('modalCancel');
  const errorEl = document.getElementById('modalError');

  messageEl.innerHTML = withInput
    ? `${message}<br><input type="text" id="modalInput" maxlength="20">`
    : message;
  errorEl.textContent = "";

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
