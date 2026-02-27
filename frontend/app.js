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
const instructionsBtnSetup = document.getElementById('instructionsBtnSetup');
const instructionsBtnGame = document.getElementById('instructionsBtnGame');
const instructionsModalEl = document.getElementById('instructionsModal');
const instructionsTitleEl = document.getElementById('instructionsTitle');
const instructionsBodyEl = document.getElementById('instructionsBody');
const closeInstructionsBtn = document.getElementById('closeInstructionsBtn');

const modePicker = document.getElementById('modePicker');
const modeLabel = document.getElementById('modeLabel');
const localBtn = document.getElementById('localBtn');
const remoteBtn = document.getElementById('remoteBtn');

const newGameBtn = document.getElementById('newGameBtn');
const celebrationEl = document.getElementById('celebration');
const celebrationTextEl = document.getElementById('celebrationText');

const boardEl = document.getElementById('board');
const scoresEl = document.getElementById('scores');
const scoresCard = document.getElementById('scoresCard');
const messagesCard = document.getElementById('messagesCard');
const messagesEl = document.getElementById('messages');
const setupNoticeEl = document.getElementById('setupNotice');

let ws;
let myMark = null;              // "O" or "X"
let modeChosen = null;
let selectedJoinMode = null;
let lobbyMode = null;
let lobbyHostName = null;
let localNextMark = 'O';
let scoreFlashTimer = null;
let isScoreFlashActive = false;
let visiblePieceKeys = new Set();
let moveAnimationQueue = Promise.resolve();

let players = { O: null, X: null }; // backend sends strings or null
let scores = { O: 0, X: 0 };        // backend sends {O: number, X: number}
let pendingFlags = { O: false, X: false }; // backend sends booleans

let serverPieces = []; // array of {row,col,mark,age_rank}
let isGameOver = false;
const HIGHLIGHT_COLOR = '#ffe2b4';
let highlightedCells = new Set();

let lastKnownPlayers = { O: null, X: null };
let opponentJoinAnnounced = false;

const LOCAL_INSTRUCTIONS = [
  'Whisk is a variant of Tic Tac Toe with some important differences:',
  'It is played on an 8 by 8, rather than 3 by 3, board.',
  'Players get points for getting 3, 4, or 5 of their icons in a row: 1 point for 3 in a row, 4 points for 4 in a row, and nine points for 5 in a row.',
  'The game ends when either player gets 50 or more points.',
  "Neither player can get more than 5 icons in a row due to a unique feature of Whisk: when a player places a sixth icon on the board, the oldest icon disappears, leaving just the five most recent ones. As a visual reminder of this, the icons will fade progressively as new ones are placed.",
  'You are now playing the game in local mode, meaning that both players are on the same computer. Players alternate moves by clicking on empty squares to place their icon. The first player to move will automatically be assigned the O icon, the second player will be X.',
];

const REMOTE_INSTRUCTIONS = [
  'Whisk is a variant of Tic Tac Toe with some important differences:',
  'It is played on an 8 by 8, rather than 3 by 3, board.',
  'Players get points for getting 3, 4, or 5 of their icons in a row: 1 point for 3 in a row, 4 points for 4 in a row, and nine points for 5 in a row.',
  'The game ends when either player gets 50 or more points.',
  "Neither player can get more than 5 icons in a row due to a unique feature of Whisk: when a player places a sixth icon on the board, the oldest icon disappears, leaving just the five most recent ones. As a visual reminder of this, the icons will fade progressively as new ones are placed.",
  "You are now playing the game in remote mode, meaning that the players are on two different computers and cannot see each other's screen. Whisk takes advantage of this fact to eliminate the advantage that the first player often enjoys in a game of this kind and has the players move, in effect, simultaneously. The first player to make a move sees that move as expected but the second player's screen is not updated until they have made a move. If the second player clicks on the same square that the first player chose, that results in an error message and the player can try again.",
];

function updateJoinButtonState() {
  if (!joinBtn) return;
  const hasName = !!nameInput?.value.trim();
  const hasMode = !!selectedJoinMode || !!lobbyMode;
  joinBtn.disabled = !(hasName && hasMode);
  updateInstructionsButtonState();
}

function instructionsMode() {
  return modeChosen || selectedJoinMode || lobbyMode;
}

function updateInstructionsButtonState() {
  const preJoinActive = !myMark && !joinBtn.disabled && !!instructionsMode();
  if (instructionsBtnSetup) {
    instructionsBtnSetup.classList.toggle('hidden', !preJoinActive);
    instructionsBtnSetup.disabled = !preJoinActive;
  }

  const inGameActive = !!(myMark && modeChosen);
  if (instructionsBtnGame) {
    instructionsBtnGame.classList.toggle('hidden', !inGameActive);
    instructionsBtnGame.disabled = !inGameActive;
  }
}

function showSetup() {
  setupEl.classList.remove('hidden');
  gameEl.classList.add('hidden');
  if (newGameBtn) newGameBtn.classList.add('hidden');
  updateJoinButtonState();
}

function showGame() {
  setupEl.classList.add('hidden');
  gameEl.classList.remove('hidden');
  if (newGameBtn) newGameBtn.classList.remove('hidden');
  updateInstructionsButtonState();
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
  const html = formatMessageHtml(text);
  if (!myMark) {
    if (setupNoticeEl) setupNoticeEl.innerHTML = html;
    return;
  }
  if (!messagesEl) return;
  messagesEl.innerHTML = html;
}

function setStatusHtml(html) {
  if (!myMark) {
    if (setupNoticeEl) setupNoticeEl.innerHTML = html;
    return;
  }
  if (!messagesEl) return;
  messagesEl.innerHTML = html;
}

function openInstructionsModal() {
  if (!instructionsModalEl || !instructionsTitleEl || !instructionsBodyEl) return;
  const activeMode = instructionsMode();
  if (!activeMode) return;
  const isLocal = activeMode === 'local';
  const lines = isLocal ? LOCAL_INSTRUCTIONS : REMOTE_INSTRUCTIONS;
  instructionsTitleEl.textContent = isLocal ? 'Local Mode Instructions' : 'Remote Mode Instructions';
  instructionsBodyEl.innerHTML = lines.map((line) => `<p>${escapeHtml(line)}</p>`).join('');
  instructionsModalEl.classList.remove('hidden');
}

function closeInstructionsModal() {
  if (!instructionsModalEl) return;
  instructionsModalEl.classList.add('hidden');
}

function updateModePanels() {
  const hideScores = !modeChosen;
  if (scoresCard) scoresCard.classList.toggle('hidden', hideScores);
  if (messagesCard) messagesCard.classList.remove('hidden');
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

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function formatMessageHtml(text) {
  let html = escapeHtml(text);
  if (modeChosen !== 'remote') return html;

  const oName = players?.O;
  const xName = players?.X;

  if (typeof oName === 'string' && oName.trim()) {
    const safeO = escapeHtml(oName.trim());
    const reO = new RegExp(escapeRegExp(safeO), 'g');
    html = html.replace(reO, `<span class="msg-player-o">${escapeHtml(oName.trim())}</span>`);
  }
  if (typeof xName === 'string' && xName.trim()) {
    const safeX = escapeHtml(xName.trim());
    const reX = new RegExp(escapeRegExp(safeX), 'g');
    html = html.replace(reX, `<span class="msg-player-x">${escapeHtml(xName.trim())}</span>`);
  }
  return html;
}

function playVictoryMotif() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return;
  const ctx = new AudioCtx();
  const start = ctx.currentTime;
  const notes = [523.25, 659.25, 783.99, 1046.5];

  notes.forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(freq, start + i * 0.15);
    gain.gain.setValueAtTime(0.0001, start + i * 0.15);
    gain.gain.exponentialRampToValueAtTime(0.18, start + i * 0.15 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + i * 0.15 + 0.18);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(start + i * 0.15);
    osc.stop(start + i * 0.15 + 0.2);
  });
}

function rowLengthFromScore(addedScore) {
  if (addedScore >= 9) return 5;
  if (addedScore >= 4) return 4;
  return 3;
}

function showScoreFlash(mark, addedScore) {
  if (addedScore <= 0) return;
  const cls = mark === 'O' ? 'msg-player-o' : 'msg-player-x';
  let flashText = '';
  if (addedScore === 2) {
    flashText = 'You got 3 in a row two different ways! You scored 2 points!';
  } else {
    const rowLen = rowLengthFromScore(addedScore);
    const pointWord = addedScore === 1 ? 'point' : 'points';
    flashText = `You got ${rowLen} in a row! You scored ${addedScore} ${pointWord}!`;
  }
  isScoreFlashActive = true;
  setStatusHtml(`<span class="${cls}">${flashText}</span>`);
  if (scoreFlashTimer) {
    window.clearTimeout(scoreFlashTimer);
  }
  scoreFlashTimer = window.setTimeout(() => {
    isScoreFlashActive = false;
    setStatusMessage(computeTurnMessage());
    scoreFlashTimer = null;
  }, 2000);
}

function showCelebration(message) {
  if (!celebrationEl || !celebrationTextEl) return;
  celebrationTextEl.textContent = message;
  celebrationEl.classList.remove('hidden');
  playVictoryMotif();
  window.setTimeout(() => {
    celebrationEl.classList.add('hidden');
  }, 4500);
}

function pieceKey(p) {
  return `${p.mark}:${p.row},${p.col}`;
}

function detectAppearingPieces(nextPieces) {
  const nextKeys = new Set(nextPieces.map(pieceKey));
  const appearing = nextPieces.filter((p) => !visiblePieceKeys.has(pieceKey(p)));
  visiblePieceKeys = nextKeys;
  return appearing;
}

function animateMoveDrop(mark, row, col) {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return Promise.resolve();
  }

  const target = boardEl.querySelector(`.cell[data-row="${row}"][data-col="${col}"]`);
  if (!target) return Promise.resolve();

  const targetRect = target.getBoundingClientRect();
  const targetX = targetRect.left + targetRect.width / 2;
  const targetY = targetRect.top + targetRect.height / 2;

  const originX = mark === 'O' ? 36 : window.innerWidth - 36;
  const originY = window.innerHeight - 36;

  const layer = document.createElement('div');
  layer.className = 'move-hand-layer';
  const hand = document.createElement('div');
  hand.className = `move-hand move-hand-${mark}`;
  hand.innerHTML =
    `<span class="move-hand-emoji">🤏</span>` +
    `<span class="move-hand-token move-hand-token-${mark}">${mark}</span>`;
  layer.appendChild(hand);
  document.body.appendChild(layer);

  hand.style.left = `${originX}px`;
  hand.style.top = `${originY}px`;
  hand.style.transform = 'translate(-50%, -50%) scale(0.95)';
  hand.style.opacity = '0.2';

  return new Promise((resolve) => {
    const travelMs = 450;
    const dropMs = 100;
    const retreatMs = 450;

    requestAnimationFrame(() => {
      hand.style.transition = `left ${travelMs}ms ease-out, top ${travelMs}ms ease-out, opacity 120ms ease-out, transform ${travelMs}ms ease-out`;
      hand.style.left = `${targetX}px`;
      hand.style.top = `${targetY - 10}px`;
      hand.style.opacity = '1';
      hand.style.transform = 'translate(-50%, -50%) scale(1)';
    });

    window.setTimeout(() => {
      hand.classList.add('dropping');
      window.setTimeout(() => {
        hand.classList.remove('dropping');
        hand.style.transition = `left ${retreatMs}ms ease-in, top ${retreatMs}ms ease-in, opacity ${retreatMs}ms ease-in, transform ${retreatMs}ms ease-in`;
        hand.style.left = `${originX}px`;
        hand.style.top = `${originY}px`;
        hand.style.opacity = '0.1';
        hand.style.transform = 'translate(-50%, -50%) scale(0.92)';

        window.setTimeout(() => {
          layer.remove();
          resolve();
        }, retreatMs);
      }, dropMs);
    }, travelMs);
  });
}

function queueMoveAnimations(pieces) {
  if (!pieces || pieces.length === 0) return;
  for (const p of pieces) {
    moveAnimationQueue = moveAnimationQueue.then(() => animateMoveDrop(p.mark, p.row, p.col));
  }
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
  // Pre-join mode selection is required before enabling Join.
  if (!myMark) {
    selectedJoinMode = mode;
    lobbyMode = mode;
    updateJoinButtonState();
    if (localBtn) localBtn.classList.toggle('mode-btn-selected', mode === 'local');
    if (remoteBtn) remoteBtn.classList.toggle('mode-btn-selected', mode === 'remote');
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

function updatePreJoinUiFromLobby() {
  if (myMark) return;
  const hostPresent = !!lobbyHostName;

  if (hostPresent) {
    if (modePicker) modePicker.classList.add('hidden');
    if (modeLabel) modeLabel.classList.add('hidden');
    selectedJoinMode = lobbyMode || selectedJoinMode;
    updateJoinButtonState();
    if (lobbyMode === 'local') {
      setStatusMessage(`${lobbyHostName} is playing Whisk in local mode so you can't join at this time. But you are welcome to play in local mode. Just enter your name and click "Join".`);
    } else if (lobbyMode === 'remote') {
      setStatusMessage(`Please join ${lobbyHostName} to play Whisk.`);
    }
  } else {
    if (modePicker) modePicker.classList.remove('hidden');
    if (modeLabel) modeLabel.classList.remove('hidden');
    if (!selectedJoinMode) {
      joinBtn.disabled = true;
      setStatusMessage('Enter your name and choose whether you want to play the game locally or remotely, then click Join.');
    }
  }
  updateInstructionsButtonState();
}

function connect() {
  ws = new WebSocket(wsUrl());

  ws.addEventListener('open', () => {
    statusEl.textContent = 'Connected.';
    send({ type: 'lobby' });
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
      if (setupNoticeEl) setupNoticeEl.textContent = '';

      opponentJoinAnnounced = false;
      // Always show the board as soon as a player joins.
      showGame();
      updateInstructionsButtonState();

      setStatusMessage('Joined. Waiting for game state...');
      break;
    }

    case 'lobby': {
      lobbyMode = msg.mode || null;
      lobbyHostName = msg.players?.O || null;
      if (!myMark) {
        updatePreJoinUiFromLobby();
      }
      break;
    }

    case 'need_mode':
      setStatusMessage('Choose Local or Remote before joining.');
      break;

    case 'mode':
      modeChosen = msg.mode;
      showGame();
      updateModePanels();
      updateInstructionsButtonState();
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
      if (msg.message === 'Square already occupied') {
        const opponentName = myMark === 'O'
          ? playerName('X', 'your opponent')
          : myMark === 'X'
            ? playerName('O', 'your opponent')
            : 'your opponent';
        setStatusMessage(
          `Oops! It looks as though ${opponentName} has already gone there. You'll have to pick a different square. 😢`
        );
      } else {
        setStatusMessage(msg.message || 'Invalid move.');
      }
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
      if (isScoreFlashActive) break;
      const oppName = (myMark === 'O') ? playerName('X', 'Player 2') : playerName('O', 'Player 1');
      const youMoved = (myMark === 'O') ? !!pendingFlags.O : !!pendingFlags.X;
      const oppMoved = (myMark === 'O') ? !!pendingFlags.X : !!pendingFlags.O;
      if (!youMoved && oppMoved) {
        setStatusMessage(`${oppName} has moved. Waiting for you!`);
        break;
      }
      setStatusMessage(computeTurnMessage());
      break;

    case 'score_event':
      if (msg && typeof msg.mark === 'string' && typeof msg.added === 'number') {
        showScoreFlash(msg.mark, msg.added);
      }
      break;

    case 'turn_committed':
      // After commit, pending clears; state will follow.
      pendingFlags = { O: false, X: false };
      if (isScoreFlashActive) break;
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
      if (msg.message && msg.message.includes('wins')) {
        showCelebration(msg.message);
      }
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
        updateInstructionsButtonState();
      }
      if (msg.local_next_mark) {
        localNextMark = msg.local_next_mark;
      }

      if (typeof msg.game_over === 'boolean') {
        isGameOver = msg.game_over;
      }

      if (msg.pieces) {
        const appearing = detectAppearingPieces(msg.pieces);
        serverPieces = msg.pieces;
        queueMoveAnimations(appearing);
      }

      setScoresUI();

      highlightedCells = new Set();
      if (Array.isArray(msg.highlight)) {
        msg.highlight.forEach((coord) => highlightedCells.add(`${coord.row},${coord.col}`));
      }

      // Don’t overwrite explicit server info messages like “X has joined” or “You reset”
      // unless we’re in normal play flow.
      if (!isScoreFlashActive && !msg.refresh && (modeChosen || myMark !== 'O')) {
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
joinBtn.addEventListener('click', () => {
  if (joinBtn.disabled || !(selectedJoinMode || lobbyMode) || !nameInput.value.trim()) return;
  join(selectedJoinMode || lobbyMode);
});
nameInput.addEventListener('input', updateJoinButtonState);
localBtn.addEventListener('click', () => setMode('local'));
remoteBtn.addEventListener('click', () => setMode('remote'));

if (newGameBtn) {
  newGameBtn.addEventListener('click', () => {
    if (!myMark) return;
    send({ type: 'new_game' });
  });
}

if (instructionsBtnSetup) {
  instructionsBtnSetup.addEventListener('click', openInstructionsModal);
}
if (instructionsBtnGame) {
  instructionsBtnGame.addEventListener('click', openInstructionsModal);
}
if (closeInstructionsBtn) {
  closeInstructionsBtn.addEventListener('click', closeInstructionsModal);
}
if (instructionsModalEl) {
  instructionsModalEl.addEventListener('click', (evt) => {
    if (evt.target === instructionsModalEl) closeInstructionsModal();
  });
}

// Init
createBoard();
updateModePanels();
updateJoinButtonState();
updateInstructionsButtonState();
connect();
