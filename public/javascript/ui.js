import { S } from './state.js';
import { escapeHTML, setDocTitle } from './utils.js';
import { isUserSpectator, applyVoteDimState } from './cards.js';

export function updateVersionBadge() {
  fetch('/version', { cache: 'no-store' })
    .then((res) => {
      if (!res.ok) return;
      return res.json();
    })
    .then((data) => {
      if (!data) return;
      const { version, changelog } = data;
      const el = document.getElementById('versionBadge');
      if (el) el.textContent = `v${version}`;
      const tooltip = document.getElementById('versionTooltip');
      if (tooltip && changelog) {
        tooltip.innerHTML = Object.entries(changelog)
          .map(
            ([versionKey, items]) =>
              `<h4>v${escapeHTML(versionKey)}</h4><ul>${items.map((changelogItem) => `<li>${escapeHTML(changelogItem)}</li>`).join('')}</ul>`
          )
          .join('');
      }
    })
    .catch((err) => console.error('Version badge error:', err));
}

export function updateToggleBtnLabel() {
  const toggleBtn = document.getElementById('toggleVotingBtn');
  if (S.votesRevealed) {
    const effective = S.pendingVotingEnabled !== null ? S.pendingVotingEnabled : S.votingEnabled;
    toggleBtn.textContent = effective
      ? '🔒 Lock Voting (next round)'
      : '🔓 Unlock Voting (next round)';
  } else {
    toggleBtn.textContent = S.votingEnabled ? '🔒 Lock Voting' : '🔓 Unlock Voting';
  }
}

export function renderUserList(onEditBtn) {
  const users = S.currentUsers;
  const isHost = S.myUser?.isHost;

  // Sync vote-UI state from server-preserved data (covers mid-round reconnects).
  if (S.myUser) {
    if (S.myUser.voteChanged) S.hasChangedVote = true;
    if (!S.selectedCard && !S.votesRevealed && S.myUser.vote !== null && S.myUser.vote !== true) {
      const cardContainer = document.getElementById('cardOptions');
      const candidate = cardContainer.querySelector(
        `.card[data-value="${CSS.escape(String(S.myUser.vote))}"]`
      );
      if (candidate) {
        S.selectedCard = candidate;
        candidate.classList.add('selected');
        applyVoteDimState();
      }
    }
  }

  document.getElementById('newRoundBtn').classList.toggle('hidden', !isHost);
  document.getElementById('deckSelector').classList.remove('hidden');
  const toggleBtn = document.getElementById('toggleVotingBtn');
  toggleBtn.classList.toggle('hidden', !isHost);

  const hasVotes = users.some((user) => user.vote !== null);

  if (isHost) {
    document.getElementById('deckSelector').disabled = hasVotes || S.votesRevealed;
    toggleBtn.disabled = hasVotes && !S.votesRevealed;
    updateToggleBtnLabel();
  } else {
    document.getElementById('deckSelector').disabled = true;
  }

  const spectateBtn = document.getElementById('toggleSpectateBtn');
  if (S.myUser && !isHost) {
    spectateBtn.classList.remove('hidden');
    spectateBtn.disabled = hasVotes || S.votesRevealed;
    spectateBtn.textContent = S.myUser.isSpectator ? '🗳️ Join voting' : '👁 Spectate';
    spectateBtn.setAttribute('aria-pressed', S.myUser.isSpectator ? 'true' : 'false');
  } else {
    spectateBtn.classList.add('hidden');
  }

  const cardOpts = document.getElementById('cardOptions');
  if (S.myUser) {
    cardOpts.classList.toggle('hidden', isUserSpectator(S.myUser));
  }

  const votingUsers = users.filter((user) => !isUserSpectator(user));
  const selected = votingUsers.filter((user) => user.vote !== null).length;
  document.getElementById('status').textContent = `${selected} of ${votingUsers.length} selected`;

  const userCountEl = document.getElementById('userCount');
  if (userCountEl) userCountEl.textContent = `${selected}/${votingUsers.length} voted`;

  if (S.votesRevealed) setDocTitle('Votes revealed');
  else if (votingUsers.length > 0) setDocTitle(`${selected}/${votingUsers.length} voted`);
  else setDocTitle(null);

  const userListContent = document.getElementById('userListContent');
  if (!userListContent) return;
  userListContent.innerHTML = '';

  users.forEach((user) => {
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

    if (user.clientId === S.clientId) {
      const editBtn = document.createElement('button');
      editBtn.className = 'user-edit-btn';
      editBtn.textContent = '✏️';
      editBtn.title = 'Edit username';
      editBtn.setAttribute('aria-label', 'Edit username');
      editBtn.disabled = S.votesRevealed || hasVoted;
      editBtn.addEventListener('click', onEditBtn);
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

    if (S.votesRevealed && hasVoted && !isSpectator) {
      const chip = document.createElement('span');
      chip.className = 'user-vote-chip';
      chip.textContent = user.vote;
      row.appendChild(chip);
    }

    userListContent.appendChild(row);
  });
}
