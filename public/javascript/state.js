import { CONTROL_CHARS_RE } from './utils.js';

// One-time migration: jiraPoker* → pokering* storage keys.
// Runs once on load; old keys deleted after migration.
function migrateStorageKeys() {
  const ssMigrations = [
    ['jiraPokerUsername', 'pokeringUsername'],
    ['jiraPokerClientId', 'pokeringClientId'],
  ];
  for (const [oldKey, newKey] of ssMigrations) {
    const val = sessionStorage.getItem(oldKey);
    if (val !== null) {
      sessionStorage.setItem(newKey, val);
      sessionStorage.removeItem(oldKey);
    }
  }
  const lsVal = localStorage.getItem('jiraPokerUsername');
  if (lsVal !== null) {
    localStorage.setItem('pokeringUsername', lsVal);
    localStorage.removeItem('jiraPokerUsername');
  }
  // Migrate all jiraPokerHostVoteDecision_* session keys
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith('jiraPokerHostVoteDecision_')) {
      const newKey = key.replace('jiraPokerHostVoteDecision_', 'pokeringHostVoteDecision_');
      sessionStorage.setItem(newKey, sessionStorage.getItem(key));
      sessionStorage.removeItem(key);
    }
  }
}

migrateStorageKeys();

export const sessionId = window.location.pathname.split('/').pop();

// Shared mutable game state — imported by all modules as `import { S } from './state.js'`.
export const S = {
  username:
    sessionStorage.getItem('pokeringUsername') || localStorage.getItem('pokeringUsername') || '',
  clientId: (() => {
    let id = sessionStorage.getItem('pokeringClientId');
    if (!id) {
      const bytes = new Uint8Array(7);
      crypto.getRandomValues(bytes);
      id =
        'client-' +
        Array.from(bytes, (b) => b.toString(36).padStart(2, '0'))
          .join('')
          .slice(0, 7);
      sessionStorage.setItem('pokeringClientId', id);
    }
    return id;
  })(),

  // Game state
  currentUsers: [],
  myUser: null,
  currentDeckType: 'fibonacci',
  cardValues: [1, 2, 3, 5, 8, 13, 21, '?'],
  votingEnabled: true,
  votesRevealed: false,
  pendingVotingEnabled: null,
  selectedCard: null,
  hasChangedVote: false,
  deckInitialized: false,
  hostSettingsShown: false,

  // Deck presets — populated from /decks on load, fallback hardcoded
  deckPresets: {
    fibonacci: [1, 2, 3, 5, 8, 13, 21, '?'],
    hours: [1, 2, 4, 8, 16, 24, 40, '?'],
    tshirt: ['XS', 'S', 'M', 'L', 'XL', 'XXL', '?'],
  },
  deckLabels: {
    fibonacci: 'Fibonacci (1-21)',
    hours: 'Hours (1-40)',
    tshirt: 'T-Shirt (XS-XXL)',
  },
};

export function refreshMyUser() {
  S.myUser = S.currentUsers.find((u) => u.clientId === S.clientId) || null;
  return S.myUser;
}

export function saveUsername(name) {
  S.username = name.replace(CONTROL_CHARS_RE, '').trim();
  sessionStorage.setItem('pokeringUsername', S.username);
  localStorage.setItem('pokeringUsername', S.username);
}
