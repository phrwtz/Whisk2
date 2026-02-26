/*
Client-side app for Whisk.

Frontend responsibilities:
- Connect to server via WebSocket
- Render the board using pieces from server "state"
- Send moves and new_game
- Show user messages

IMPORTANT (new simplified model):
- The server decides what each player can see each moment.
- The client DOES NOT invent previews.
- We render exactly what arrives in msg.pieces.
*/

const statusEl = document.getElementById('status');
const setupEl = document.getElementById('setup');
const gameEl = document.getElementById('game');

const nameInput = document.getElementById('nameInput');
const joinBtn = document.getElementById('joinBtn');

const modePicker = document.getElementById('modePicker');
const localBtn = document.getElementById('localBtn');
const remoteBtn = document.getElementById('remoteBtn');

const newGameBtn = document.getElementById('newGameBtn');

const boardEl = document.getElementById('board');
const scoresEl = document.getElementById('scores');
const scoresCard = document.getElementById('scoresCard');
const messagesCard = document.getElementById('messagesCard');
const messagesEl = document.getElementById('messages');

let ws;
let myMark = null;              // "O" or "X"
let modeChosen = null;
let localNextMark = 'O';

let players = { O: null, X: null }; // backend sends strings or null
let scores = { O: 0, X: 0 };        // backend sends {O: number, X: number}
let pendingFlags = { O: false, X: false }; // backend sends booleans

let serverPieces = []; // array of {row,col,mark,age_rank}
let isGameOver = false;
const HIGHLIGHT_COLOR = '#ffe2b4';
let highlightedCells = new Set();

let lastKnownPlayers = { O: null, X: null };
let opponentJoinAnnounced = false;

function showSetup() {
  setupEl.classList.remove('hidden');
  gameEl.classList.add('hidden');
}

function showGame() {
  setupEl.classList.add('hidden');
  gameEl.classList.remove('hidden');
}

function wsUrl() {
  if (typeof window.WHISK_WEBSOCKET_URL === 'string' && window.WHISK_WEBSOCKET_URL.trim()) {
    return window.WHISK_WEBSOCKET_URL;
  }
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws`;
}

function send(obj) {
  // Guard against “CLOSING or CLOSED” errors
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    setStatusMessage('Not connected to server (WebSocket not open). Refresh the page.');
    return;
  }
  ws.send(JSON.stringify(obj));
}

function playerName(mark, fallback) {
  const p = players?.[mark];
  if (typeof p === 'string' && p.trim()) return p;
  return fallback;
}

function setScoresUI() {
  if (!scoresEl) return;

  if (!modeChosen) {
    scoresEl.textContent = '';
    return;
  }

  const oScore = scores?.O ?? 0;
  const xScore = scores?.X ?? 0;

  if (modeChosen === 'local') {
    scoresEl.textContent = `O: ${oScore}\nX: ${xScore}`;
    return;
  }

  const oName = playerName('O', 'Player 1');
  const xName = playerName('X', 'Player 2');
  scoresEl.textContent = `${oName} (O): ${oScore}\n${xName} (X): ${xScore}`;
}

function setStatusMessage(text) {
  if (!messagesEl) return;
  messagesEl.textContent = text;
}

function updateModePanels() {
  const hidePanels = !modeChosen;
  if (scoresCard) scoresCard.classList.toggle('hidden', hidePanels);
  if (messagesCard) messagesCard.classList.toggle('hidden', hidePanels);
}

function computeTurnMessage() {
  if (!myMark) return 'Not joined yet.';
  if (isGameOver) return 'Game over. Start a new game to play again.';
  if (modeChosen === 'local') {
    return `It is ${localNextMark}'s turn.`;
  }

  const oName = playerName('O', 'first player');
  const xName = playerName('X', 'second player');

  const youName = (myMark === 'O') ? oName : xName;
  const oppName = (myMark === 'O') ? xName : oName;

  const oppPresent = (myMark === 'O') ? !!players?.X : !!players?.O;
  if (!oppPresent) {
    const youMoved = (myMark === 'O') ? !!pendingFlags.O : !!pendingFlags.X;
    if (youMoved) {
      return "Waiting for second player's move.";
    }
    return `Welcome ${youName}! We're waiting for the second player to join the game.`;
  }

  const youMoved = (myMark === 'O') ? !!pendingFlags.O : !!pendingFlags.X;
  const oppMoved = (myMark === 'O') ? !!pendingFlags.X : !!pendingFlags.O;

  if (!youMoved && !oppMoved) {
    return `It's your move ${youName}, ${oppName} hasn't moved yet.`;
  }
  if (!youMoved && oppMoved) {
    return `It's your move ${youName}, ${oppName} has made his move.`;
  }
  if (youMoved && !oppMoved) {
    return `Waiting for ${oppName}'s move.`;
  }
  return `Both moves received.`;
}

function lightnessForAgeRank(ageRank) {
  // age_rank 0..4 => darker to lighter
  const mapping = [35, 55, 70, 82, 90];
  if (ageRank < 0 || ageRank > 4) return 35;
  return mapping[ageRank];
}

function markColor(mark, ageRank) {
  // Keep icons visible while getting lighter with age.
  const lightness = lightnessForAgeRank(ageRank);
  if (mark === 'O') return `hsl(210, 100%, ${lightness}%)`; // blue
  return `hsl(0, 100%, ${lightness}%)`; // red
}

function squareBgForAgeRank(ageRank) {
  // Same green hue; lighter and lighter until white.
  if (ageRank >= 4) return '#ffffff';
  const lightness = Math.min(lightnessForAgeRank(ageRank) + 15, 96);
  return `hsl(120, 60%, ${lightness}%)`;
}

function createBoard() {
  boardEl.innerHTML = '';
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const cell = document.createElement('button');
      cell.className = 'cell';
      cell.type = 'button';
      cell.dataset.row = String(r);
      cell.dataset.col = String(c);
      cell.addEventListener('click', () => onCellClick(r, c));
      boardEl.appendChild(cell);
    }
  }
}

function render() {
  const cells = boardEl.querySelectorAll('.cell');

  // Clear
  cells.forEach((cell) => {
    cell.textContent = '';
    cell.style.color = '';
    cell.style.backgroundColor = '';
    cell.disabled = false;
  });

  // Fill from serverPieces only (server controls visibility)
  for (const p of serverPieces) {
    const idx = p.row * 8 + p.col;
    const cell = cells[idx];
    if (!cell) continue;

    cell.textContent = p.mark;
    cell.style.color = markColor(p.mark, p.age_rank);
    const highlightKey = `${p.row},${p.col}`;
    if (highlightedCells.has(highlightKey)) {
      cell.style.backgroundColor = HIGHLIGHT_COLOR;
    } else {
      cell.style.backgroundColor = squareBgForAgeRank(p.age_rank);
    }
    cell.disabled = true;
  }

  // If not joined or game is over, disable all interactions.
  if (!myMark || isGameOver) {
    cells.forEach((c) => (c.disabled = true));
  }
}

function onCellClick(r, c) {
  if (!myMark || isGameOver) return;
  send({ type: 'move', row: r, col: c });
}

function setMode(mode) {
  // Allow the host to choose mode even if they click Local/Remote before joining.
  if (!myMark) {
    join(mode);
    modePicker.classList.add('hidden');
    return;
  }

  if (myMark !== 'O') {
    setStatusMessage('Only Player 1 (O) can choose the mode.');
    return;
  }

  send({ type: 'set_mode', mode });
  modePicker.classList.add('hidden');
}

function join(mode = null) {
  const name = (nameInput.value || '').trim() || 'Player';
  send({ type: 'join', name, mode });
}

function connect() {
  ws = new WebSocket(wsUrl());

  ws.addEventListener('open', () => {
    statusEl.textContent = 'Connected.';
  });

  ws.addEventListener('close', () => {
    statusEl.textContent = 'Disconnected. Refresh to reconnect.';
    setStatusMessage('Disconnected. Refresh to reconnect.');
  });

  ws.addEventListener('message', (evt) => {
    const msg = JSON.parse(evt.data);
    handleMessage(msg);
  });
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'joined': {
      myMark = msg.mark;

      opponentJoinAnnounced = false;
      // Always show the board as soon as a player joins.
      showGame();

      // If we're the host (O), we may still need to choose Local/Remote.
      if (myMark === 'O' && !modeChosen) {
        modePicker.classList.remove('hidden');
        setStatusMessage('Joined as Player 1 (O). Choose Local or Remote.');
      } else {
        setStatusMessage('Joined. Waiting for game state...');
      }
      break;
    }

    case 'need_mode':
      modePicker.classList.remove('hidden');
      // Don’t display the “Choose mode” line in the Messages box anymore.
      // The UI itself is the prompt.
      break;

    case 'mode':
      modeChosen = msg.mode;
      modePicker.classList.add('hidden');
      showGame();
      updateModePanels();
      setStatusMessage(computeTurnMessage());
      break;

    case 'info':
      // Keep this for things like “X has joined” or “You reset the game”
      setStatusMessage(msg.message);
      break;

    case 'error':
      setStatusMessage(`Error: ${msg.message}`);
      break;

    case 'invalid_move': {
      setStatusMessage(msg.message || 'Invalid move.');
      break;
    }

    case 'pending_ok':
      // Server will also send a 'state' snapshot right after this.
      // We rely on the server's state for what to display.
      pendingFlags = {
        O: (myMark === 'O') ? true : pendingFlags.O,
        X: (myMark === 'X') ? true : pendingFlags.X,
      };
      setStatusMessage(computeTurnMessage());
      break;

    case 'pending_flags':
      pendingFlags = {
        O: !!msg.pending?.O,
        X: !!msg.pending?.X,
      };
      const oppName = (myMark === 'O') ? playerName('X', 'Player 2') : playerName('O', 'Player 1');
      const youMoved = (myMark === 'O') ? !!pendingFlags.O : !!pendingFlags.X;
      const oppMoved = (myMark === 'O') ? !!pendingFlags.X : !!pendingFlags.O;
      if (!youMoved && oppMoved) {
        setStatusMessage(`${oppName} has moved. Waiting for you!`);
        break;
      }
      setStatusMessage(computeTurnMessage());
      break;

    case 'turn_committed':
      // After commit, pending clears; state will follow.
      pendingFlags = { O: false, X: false };
      if (myMark && modeChosen === 'local') {
        setStatusMessage(computeTurnMessage());
      } else if (myMark) {
        const oppName = (myMark === 'O') ? playerName('X', 'Player 2') : playerName('O', 'Player 1');
        setStatusMessage(`Waiting for you to make your next move. ${oppName} hasn't moved yet.`);
      }
      break;

    case 'game_over':
      isGameOver = true;
      setStatusMessage(msg.message || 'Game over!');
      render();
      break;

    case 'state': {
      if (msg.players) {
        const prevO = lastKnownPlayers.O;
        const prevX = lastKnownPlayers.X;

        players = msg.players;
        lastKnownPlayers = { O: players.O ?? null, X: players.X ?? null };

        // Announce when opponent first appears
        if (myMark === 'O' && !opponentJoinAnnounced) {
          if (!prevX && players.X) {
            opponentJoinAnnounced = true;
            setStatusMessage(`${playerName('X', 'Player 2')} has joined the game.`);
          }
        }
        if (myMark === 'X' && !opponentJoinAnnounced) {
          if (!prevO && players.O) {
            opponentJoinAnnounced = true;
            setStatusMessage(`${playerName('O', 'Player 1')} has joined the game.`);
          }
        }
      }

      if (msg.scores) {
        scores = msg.scores;
      }

      if (msg.pending) {
        pendingFlags = {
          O: !!msg.pending.O,
          X: !!msg.pending.X,
        };
      }

      if (msg.mode) {
        modeChosen = msg.mode;
        updateModePanels();
      }
      if (msg.local_next_mark) {
        localNextMark = msg.local_next_mark;
      }

      if (typeof msg.game_over === 'boolean') {
        isGameOver = msg.game_over;
      }

      if (msg.pieces) {
        serverPieces = msg.pieces;
      }

      setScoresUI();

      highlightedCells = new Set();
      if (Array.isArray(msg.highlight)) {
        msg.highlight.forEach((coord) => highlightedCells.add(`${coord.row},${coord.col}`));
      }

      // Don’t overwrite explicit server info messages like “X has joined” or “You reset”
      // unless we’re in normal play flow.
      if (!msg.refresh && (modeChosen || myMark !== 'O')) {
        setStatusMessage(computeTurnMessage());
      }

      // If mode is now known, make sure the game view is visible.
      if (myMark && modeChosen) {
        showGame();
      }

      render();
      break;
    }

    default:
      break;
  }
}

// Wire up UI
joinBtn.addEventListener('click', () => join(null));
localBtn.addEventListener('click', () => setMode('local'));
remoteBtn.addEventListener('click', () => setMode('remote'));

if (newGameBtn) {
  newGameBtn.addEventListener('click', () => {
    if (!myMark) return;
    send({ type: 'new_game' });
  });
}

// Init
createBoard();
updateModePanels();
connect();
