/*
Client-side app for Simul-Tac.

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
const messagesEl = document.getElementById('messages');

let ws;
let myMark = null;              // "O" or "X"
let modeChosen = null;

let players = { O: null, X: null }; // backend sends strings or null
let scores = { O: 0, X: 0 };        // backend sends {O: number, X: number}
let pendingFlags = { O: false, X: false }; // backend sends booleans

let serverPieces = []; // array of {row,col,mark,age_rank}
let isGameOver = false;

let lastKnownPlayers = { O: null, X: null };
let opponentJoinAnnounced = false;

function wsUrl() {
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
  const oName = playerName('O', 'Player 1');
  const xName = playerName('X', 'Player 2');
  const oScore = scores?.O ?? 0;
  const xScore = scores?.X ?? 0;

  if (!scoresEl) return;
  scoresEl.textContent =
    `${oName} (O): ${oScore}\n` +
    `${xName} (X): ${xScore}`;
}

function setStatusMessage(text) {
  if (!messagesEl) return;
  messagesEl.textContent = text;
}

function computeTurnMessage() {
  if (!myMark) return 'Not joined yet.';
  if (isGameOver) return 'Game over. Start a new game to play again.';

  const oName = playerName('O', 'Player 1');
  const xName = playerName('X', 'Player 2');

  const youName = (myMark === 'O') ? oName : xName;
  const oppName = (myMark === 'O') ? xName : oName;

  const oppPresent = (myMark === 'O') ? !!players?.X : !!players?.O;
  if (!oppPresent) {
    return `Welcome ${youName}! We're waiting for the second player to join the game.`;
  }

  const youMoved = (myMark === 'O') ? !!pendingFlags.O : !!pendingFlags.X;
  const oppMoved = (myMark === 'O') ? !!pendingFlags.X : !!pendingFlags.O;

  if (!youMoved && !oppMoved) {
    return `It's your move ${youName}, ${oppName} hasn't moved yet.`;
  }
  if (!youMoved && oppMoved) {
    return `${oppName} has moved. Waiting for you.`;
  }
  if (youMoved && !oppMoved) {
    return `Waiting for ${oppName}'s move.`;
  }
  return `Both moves received.`;
}

function satForAgeRank(ageRank) {
  // age_rank 0..4 => 100,80,60,40,20
  const mapping = [100, 80, 60, 40, 20];
  if (ageRank < 0 || ageRank > 4) return 100;
  return mapping[ageRank];
}

function markColor(mark, sat) {
  // Use HSL so we can control saturation precisely.
  if (mark === 'O') return `hsl(210, ${sat}%, 55%)`; // blue
  return `hsl(0, ${sat}%, 55%)`; // red
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
    cell.disabled = false;
  });

  // Fill from serverPieces only (server controls visibility)
  for (const p of serverPieces) {
    const idx = p.row * 8 + p.col;
    const cell = cells[idx];
    if (!cell) continue;

    const sat = satForAgeRank(p.age_rank);
    cell.textContent = p.mark;
    cell.style.color = markColor(p.mark, sat);
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
      setupEl.classList.add('hidden');
      gameEl.classList.remove('hidden');

      opponentJoinAnnounced = false;
      setStatusMessage('Joined. Waiting for game state...');
      break;
    }

    case 'need_mode':
      modePicker.classList.remove('hidden');
      // Don’t display the “Choose mode” line in the Messages box anymore.
      // The UI itself is the prompt.
      break;

    case 'mode':
      modeChosen = msg.mode;
      // Not necessary to show in messages; it’s just setup.
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
      setStatusMessage(computeTurnMessage());
      break;

    case 'turn_committed':
      // After commit, pending clears; state will follow.
      pendingFlags = { O: false, X: false };
      setStatusMessage('Turn committed. Make your next move.');
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

      if (typeof msg.game_over === 'boolean') {
        isGameOver = msg.game_over;
      }

      if (msg.pieces) {
        serverPieces = msg.pieces;
      }

      setScoresUI();

      // Don’t overwrite explicit server info messages like “X has joined” or “You reset”
      // unless we’re in normal play flow.
      if (!msg.refresh) {
        setStatusMessage(computeTurnMessage());
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
connect();
