import { S, sessionId } from './state.js';
import { socket } from './connection.js';
import { showToast } from './toast.js';

export function isUserSpectator(u) {
  if (!u) return false;
  return Boolean(u.isSpectator) || (u.isHost && u.wantsToVote === false);
}

export function renderCards() {
  const cardContainer = document.getElementById('cardOptions');
  cardContainer.innerHTML = '';
  S.selectedCard = null;
  S.cardValues.forEach((value) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.classList.add('card');
    card.dataset.value = value;
    card.textContent = value;
    card.setAttribute('aria-label', `Vote ${value}`);
    if (S.votesRevealed) card.disabled = true;
    card.addEventListener('click', () => selectCard(card, value));
    cardContainer.appendChild(card);
  });
  updateVotingLockState();
}

export function selectCard(element, value) {
  if (S.votesRevealed || !S.votingEnabled) return;
  if (isUserSpectator(S.myUser)) return;
  if (S.selectedCard === element) return;

  if (!S.selectedCard) {
    S.selectedCard = element;
    element.classList.add('selected');
    applyVoteDimState();
    socket.emit('vote', { sessionId, value });
    return;
  }

  if (S.hasChangedVote) return;

  S.selectedCard.classList.remove('selected');
  S.selectedCard = element;
  element.classList.add('selected');
  S.hasChangedVote = true;
  applyVoteDimState();
  socket.emit('vote', { sessionId, value });
}

export function applyVoteDimState() {
  const cards = document.querySelectorAll('.card');
  cards.forEach((c) => {
    c.classList.remove('vote-dimmed', 'vote-swappable');
    if (S.votesRevealed) {
      c.disabled = true;
      return;
    }
    if (!S.selectedCard) {
      c.disabled = !S.votingEnabled;
      return;
    }
    if (c === S.selectedCard) {
      c.disabled = false;
      return;
    }
    c.classList.add('vote-dimmed');
    if (S.hasChangedVote) {
      c.disabled = true;
    } else {
      c.disabled = !S.votingEnabled;
      c.classList.add('vote-swappable');
    }
  });
}

export function updateVotingLockState() {
  const lockEl = document.getElementById('votingLockedIndicator');
  const cards = document.querySelectorAll('.card');
  if (!S.votingEnabled) {
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
}

export function launchConfetti() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const existing = document.querySelector('canvas[data-confetti]');
  if (existing) existing.remove();
  const colors = ['#ff3b3b', '#ffbf00', '#2ecc40', '#0074d9', '#b10dc9', '#ff851b', '#ffffff'];
  const dpr = window.devicePixelRatio || 1;
  const canvas = document.createElement('canvas');
  canvas.dataset.confetti = '';
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
    if (!document.body.contains(canvas)) return;
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

// Populate deck selector options from fetched/state data
export function populateDeckSelector() {
  const sel = document.getElementById('deckSelector');
  if (!sel) return;
  sel.innerHTML = '';
  for (const [key, label] of Object.entries(S.deckLabels)) {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = label;
    sel.appendChild(opt);
  }
  sel.value = S.currentDeckType;
}

// Fetch deck presets from server; updates S.deckPresets/deckLabels and re-populates selector.
export async function loadDecks() {
  try {
    const res = await fetch('/decks', { cache: 'no-store' });
    if (!res.ok) return;
    const { decks, default: defaultDeck } = await res.json();
    if (!decks || typeof decks !== 'object') return;
    S.deckPresets = {};
    S.deckLabels = {};
    for (const [key, { label, values }] of Object.entries(decks)) {
      S.deckPresets[key] = values;
      S.deckLabels[key] = label;
    }
    if (defaultDeck && S.deckPresets[defaultDeck] && !S.deckInitialized) {
      S.currentDeckType = defaultDeck;
      S.cardValues = S.deckPresets[defaultDeck];
    }
    populateDeckSelector();
    renderCards();
  } catch (err) {
    console.error('Failed to load decks:', err);
  }
}

// Toast on deck change (called from index.js deckChanged handler)
export function onDeckChanged(deckType) {
  if (!S.deckPresets[deckType]) return;
  const changed = S.deckInitialized && deckType !== S.currentDeckType;
  S.currentDeckType = deckType;
  S.cardValues = S.deckPresets[deckType];
  const sel = document.getElementById('deckSelector');
  if (sel) sel.value = deckType;
  renderCards();
  if (changed) {
    showToast(`Deck changed to ${S.deckLabels[deckType] || deckType}`, 'info');
  }
  S.deckInitialized = true;
}
