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
const setupActionsEl = document.getElementById('setupActions');
const nameRow = document.getElementById('nameRow');
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
const humanVsBotBtn = document.getElementById('humanVsBotBtn');

const newGameBtn = document.getElementById('newGameBtn');
const celebrationEl = document.getElementById('celebration');
const celebrationGifEl = document.getElementById('celebrationGif');
const celebrationTextEl = document.getElementById('celebrationText');

const boardEl = document.getElementById('board');
const boardCard = document.getElementById('boardCard');
const scoresEl = document.getElementById('scores');
const scoresCard = document.getElementById('scoresCard');
const messagesCard = document.getElementById('messagesCard');
const messagesEl = document.getElementById('messages');
const setupNoticeEl = document.getElementById('setupNotice');
const analysisCard = document.getElementById('analysisCard');
const analysisPanelEl = document.getElementById('analysisPanel');

let ws;
let myMark = null;              // "O" or "X"
let modeChosen = null;
let selectedJoinMode = null;
let lobbyMode = null;
let lobbyHostName = null;
let localNextMark = 'O';
let scoreFlashTimer = null;
let isScoreFlashActive = false;
let scoreFlashExpiresAt = 0;
let lastScoreFlashSignature = '';
let visiblePieceKeys = new Set();
let moveAnimationQueue = Promise.resolve();
let suppressOpponentRevealAnimationOnce = false;
const botSeedParam = new URLSearchParams(window.location.search).get('bot_seed');
const botSeed = botSeedParam !== null && botSeedParam !== '' ? Number(botSeedParam) : null;
let botAnalysisHistory = [];
const MODE_LOCAL = 'local';
const MODE_REMOTE = 'remote';
const MODE_HUMAN_VS_BOT = 'human_vs_bot';

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
  'Click on "Join" to start the game.',
];

const REMOTE_INSTRUCTIONS = [
  'Whisk is a variant of Tic Tac Toe with some important differences:',
  'It is played on an 8 by 8, rather than 3 by 3, board.',
  'Players get points for getting 3, 4, or 5 of their icons in a row: 1 point for 3 in a row, 4 points for 4 in a row, and nine points for 5 in a row.',
  'The game ends when either player gets 50 or more points.',
  "Neither player can get more than 5 icons in a row due to a unique feature of Whisk: when a player places a sixth icon on the board, the oldest icon disappears, leaving just the five most recent ones. As a visual reminder of this, the icons will fade progressively as new ones are placed.",
  "You are now playing the game in remote mode, meaning that the players are on two different computers and cannot see each other's screen. Whisk takes advantage of this fact to eliminate the advantage that the first player often enjoys in a game of this kind and has the players move, in effect, simultaneously. The first player to make a move sees that move as expected but the second player's screen is not updated until they have made a move. If the second player clicks on the same square that the first player chose, that results in an error message and the player can try again.",
  'Click on "Join" to join the game.',
];

const BOT_INSTRUCTIONS = [
  'Whisk is a variant of Tic Tac Toe with an 8 by 8 board and scoring for 3, 4, or 5 in a row.',
  'You are playing against WhiskBot. You are always O, and the bot is X.',
  'After you click an empty square, the bot immediately chooses and commits its move.',
  'The game ends when either side reaches 50 or more points.',
  'Click on "Join" to start a bot game.',
];

const WIN_VIDEO_URL = '/static/media/Win!.mp4';
const TIE_VIDEO_URL = '/static/media/Tie!.mp4';

function updateJoinButtonState() {
  if (!joinBtn) return;
  const activeMode = instructionsMode();
  const requiresName = activeMode !== MODE_HUMAN_VS_BOT;
  const hasName = !requiresName || !!nameInput?.value.trim();
  const hasMode = !!activeMode;
  joinBtn.disabled = !(hasMode && hasName);
  updateInstructionsButtonState();
}

function instructionsMode() {
  const mode = modeChosen || selectedJoinMode || lobbyMode;
  return mode === 'bot' ? MODE_HUMAN_VS_BOT : mode;
}

function updateInstructionsButtonState() {
  const activeMode = instructionsMode();
  const preJoinActive = !myMark && !!activeMode;
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

  if (modeChosen === MODE_LOCAL) {
    scoresEl.textContent = `O: ${oScore}\nX: ${xScore}`;
    return;
  }
  if (modeChosen === MODE_HUMAN_VS_BOT) {
    scoresEl.textContent = `Human (O): ${oScore}\nWhiskBot (X): ${xScore}`;
    return;
  }

  const oName = playerName('O', 'Player 1');
  const xName = playerName('X', 'Player 2');
  scoresEl.textContent = `${oName} (O): ${oScore}\n${xName} (X): ${xScore}`;
}

function updatePreJoinSetupNotice() {
  if (myMark) return;
  const mode = instructionsMode();
  if (!mode || mode === MODE_HUMAN_VS_BOT) {
    if (setupNoticeEl) setupNoticeEl.textContent = '';
    return;
  }
  setStatusMessage('Enter your name then click Join.');
}

function updateNameInputVisibility() {
  if (myMark || !nameRow) return;
  const mode = instructionsMode();
  nameRow.classList.toggle('hidden', mode === MODE_HUMAN_VS_BOT);
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
  const isLocal = activeMode === MODE_LOCAL;
  const isHumanVsBot = activeMode === MODE_HUMAN_VS_BOT;
  const lines = isLocal
    ? LOCAL_INSTRUCTIONS
    : (isHumanVsBot ? BOT_INSTRUCTIONS : REMOTE_INSTRUCTIONS);
  instructionsTitleEl.textContent = isLocal
    ? 'Local Mode Instructions'
    : (isHumanVsBot ? 'Play Against Computer Instructions' : 'Remote Mode Instructions');
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
  if (boardCard) boardCard.classList.remove('hidden');
  if (analysisCard) analysisCard.classList.add('hidden');
}

function resetModeButtons() {
  if (localBtn) localBtn.classList.remove('mode-btn-selected');
  if (remoteBtn) remoteBtn.classList.remove('mode-btn-selected');
  if (humanVsBotBtn) humanVsBotBtn.classList.remove('mode-btn-selected');
}

function computeTurnMessage() {
  if (!myMark) return 'Not joined yet.';
  if (isGameOver) return 'Game over. Start a new game to play again.';
  if (modeChosen === MODE_LOCAL) {
    return `It is ${localNextMark}'s turn.`;
  }
  if (modeChosen === MODE_HUMAN_VS_BOT) {
    return "It's your move against WhiskBot.";
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
  if (modeChosen !== MODE_REMOTE) return html;

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

function scoreFlashActor(mark) {
  if (modeChosen === MODE_HUMAN_VS_BOT) {
    return mark === 'O' ? 'Human' : 'WhiskBot';
  }
  if (modeChosen === MODE_LOCAL) return mark;
  return mark === myMark ? 'You' : playerName(mark, mark === 'O' ? 'Player 1' : 'Player 2');
}

function scoreFlashLine(mark, addedScore) {
  const actor = scoreFlashActor(mark);
  const getVerb = actor === 'You' ? 'get' : 'gets';
  const scoreVerb = actor === 'You' ? 'score' : 'scores';
  if (addedScore === 2) {
    return `${actor} ${getVerb} 3 in a row two different ways and ${scoreVerb} 2 points!`;
  }
  const rowLen = rowLengthFromScore(addedScore);
  const pointWord = addedScore === 1 ? 'point' : 'points';
  return `${actor} ${getVerb} ${rowLen} in a row and ${scoreVerb} ${addedScore} ${pointWord}!`;
}

function showScoreFlashBatch(entries) {
  const validEntries = Array.isArray(entries)
    ? entries.filter((entry) => entry && (entry.mark === 'O' || entry.mark === 'X') && entry.added > 0)
    : [];
  if (validEntries.length === 0) return;
  const signature = validEntries
    .map((entry) => `${entry.mark}:${entry.added}`)
    .sort()
    .join('|');
  const now = Date.now();
  if (isScoreFlashActive && signature === lastScoreFlashSignature && now < scoreFlashExpiresAt) {
    return;
  }

  const flashHtml = validEntries
    .map((entry) => {
      const cls = entry.mark === 'O' ? 'msg-player-o' : 'msg-player-x';
      return `<span class="${cls}">${escapeHtml(scoreFlashLine(entry.mark, entry.added))}</span>`;
    })
    .join('<br>');
  isScoreFlashActive = true;
  lastScoreFlashSignature = signature;
  setStatusHtml(flashHtml);
  scoreFlashExpiresAt = Date.now() + 3000;
  if (scoreFlashTimer) {
    window.clearTimeout(scoreFlashTimer);
  }
  scoreFlashTimer = window.setTimeout(() => {
    isScoreFlashActive = false;
    setStatusMessage(computeTurnMessage());
    scoreFlashTimer = null;
  }, 3000);
}

function showScoreFlash(mark, addedScore) {
  showScoreFlashBatch([{ mark, added: addedScore }]);
}

function maybeShowScoreFlashFromState(prevScores, nextScores) {
  if (!myMark || !nextScores) return;

  if (modeChosen === MODE_HUMAN_VS_BOT) {
    const addedO = (nextScores.O ?? 0) - (prevScores.O ?? 0);
    const addedX = (nextScores.X ?? 0) - (prevScores.X ?? 0);
    const entries = [];
    if (addedO > 0) entries.push({ mark: 'O', added: addedO });
    if (addedX > 0) entries.push({ mark: 'X', added: addedX });
    if (entries.length > 0) showScoreFlashBatch(entries);
    return;
  }

  if (modeChosen === MODE_LOCAL) {
    const addedO = (nextScores.O ?? 0) - (prevScores.O ?? 0);
    const addedX = (nextScores.X ?? 0) - (prevScores.X ?? 0);
    if (addedO > 0) {
      showScoreFlash('O', addedO);
    } else if (addedX > 0) {
      showScoreFlash('X', addedX);
    }
    return;
  }

  const mine = myMark;
  const addedMine = (nextScores[mine] ?? 0) - (prevScores[mine] ?? 0);
  if (addedMine > 0) {
    showScoreFlash(mine, addedMine);
  }
}

function showGameOverMedia(kind, text, options = {}) {
  const loop = options.loop !== false;
  const src = (kind === 'tie') ? TIE_VIDEO_URL : WIN_VIDEO_URL;
  setStatusHtml(
    `<div class="gameover-row">` +
    `<div class="gameover-text">${escapeHtml(text)}</div>` +
    `<video class="gameover-video" src="${src}" autoplay muted ${loop ? 'loop' : ''} playsinline></video>` +
    `</div>`
  );
  if (typeof options.onEnded === 'function') {
    const video = messagesEl?.querySelector('.gameover-video');
    if (video) {
      video.addEventListener('ended', options.onEnded, { once: true });
    } else {
      window.setTimeout(options.onEnded, 9000);
    }
  }
  window.setTimeout(() => {
    if (!isGameOver) return;
    setStatusMessage('Game over...');
  }, 10000);
}

function formatBotExplanation(msg) {
  const mark = msg?.mark === 'O' || msg?.mark === 'X' ? msg.mark : null;
  const chosen = msg?.chosen;
  const source = msg?.source || 'bot';
  if (!chosen || typeof chosen.row !== 'number' || typeof chosen.col !== 'number') {
    return 'WhiskBot made its move.';
  }
  const candidates = Array.isArray(msg?.candidates) ? msg.candidates.slice(0, 3) : [];
  const topText = candidates
    .map((c) => `(${c.row},${c.col}) ${Number(c.score).toFixed(2)}`)
    .join(', ');
  const prefix = mark ? `WhiskBot (${mark})` : 'WhiskBot';
  if (topText) {
    return `${prefix} played (${chosen.row},${chosen.col}) using ${source}. Top options: ${topText}.`;
  }
  return `${prefix} played (${chosen.row},${chosen.col}) using ${source}.`;
}

function renderBotAnalysisPanel() {
  if (!analysisPanelEl) return;
  if (botAnalysisHistory.length === 0) {
    analysisPanelEl.innerHTML = '<div class="analysisEntry">Bot explanations will appear here after each move.</div>';
    return;
  }
  analysisPanelEl.innerHTML = botAnalysisHistory
    .map((entry) => (
      `<div class="analysisEntry">` +
      `<div class="analysisSource">Turn ${entry.turn} - ${escapeHtml(entry.source)}</div>` +
      `<div>${escapeHtml(entry.text)}</div>` +
      `</div>`
    ))
    .join('');
}

function resetBotAnalysisPanel() {
  botAnalysisHistory = [];
  renderBotAnalysisPanel();
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
  if (modeChosen === MODE_REMOTE) {
    const oppMark = myMark === 'O' ? 'X' : 'O';
    suppressOpponentRevealAnimationOnce = !!pendingFlags[oppMark];
  }
  send({ type: 'move', row: r, col: c });
}

function setMode(mode) {
  // Pre-join mode selection is required before enabling Join.
  if (!myMark) {
    selectedJoinMode = mode;
    lobbyMode = mode;
    updateJoinButtonState();
    updateNameInputVisibility();
    updatePreJoinSetupNotice();
    if (localBtn) localBtn.classList.toggle('mode-btn-selected', mode === MODE_LOCAL);
    if (remoteBtn) remoteBtn.classList.toggle('mode-btn-selected', mode === MODE_REMOTE);
    if (humanVsBotBtn) humanVsBotBtn.classList.toggle('mode-btn-selected', mode === MODE_HUMAN_VS_BOT);
    return;
  }

  if (myMark !== 'O') {
    setStatusMessage('Only Player 1 (O) can choose the mode.');
    return;
  }

  const payload = { type: 'set_mode', mode };
  if (mode === MODE_HUMAN_VS_BOT && Number.isFinite(botSeed)) {
    payload.bot_seed = Math.trunc(botSeed);
  }
  send(payload);
  modePicker.classList.add('hidden');
}

function join(mode = null) {
  const normalizedMode = mode === 'bot' ? MODE_HUMAN_VS_BOT : mode;
  const fallbackName = normalizedMode === MODE_HUMAN_VS_BOT ? 'Human' : 'Player';
  const name = (nameInput.value || '').trim() || fallbackName;
  const payload = { type: 'join', name, mode: normalizedMode };
  if (normalizedMode === MODE_HUMAN_VS_BOT && Number.isFinite(botSeed)) {
    payload.bot_seed = Math.trunc(botSeed);
  }
  send(payload);
}

function updatePreJoinUiFromLobby() {
  if (myMark) return;
  const hostPresent = !!lobbyHostName;

  if (hostPresent) {
    if (modePicker) modePicker.classList.add('hidden');
    if (modeLabel) modeLabel.classList.add('hidden');
    selectedJoinMode = lobbyMode || selectedJoinMode;
    updateNameInputVisibility();
    updateJoinButtonState();
    if (lobbyMode === MODE_LOCAL) {
      setStatusMessage(`${lobbyHostName} is playing Whisk in local mode so you can't join at this time. But you are welcome to play in local mode. Just enter your name and click "Join".`);
    } else if (lobbyMode === MODE_HUMAN_VS_BOT) {
      setStatusMessage(`${lobbyHostName} is playing against WhiskBot right now.`);
    } else if (lobbyMode === MODE_REMOTE) {
      setStatusMessage(`Please join ${lobbyHostName} to play Whisk.`);
    }
  } else {
    if (modePicker) modePicker.classList.remove('hidden');
    if (modeLabel) modeLabel.classList.remove('hidden');
    updateNameInputVisibility();
    updateJoinButtonState();
    updatePreJoinSetupNotice();
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
      showGame();
      updateInstructionsButtonState();

      setStatusMessage('Joined. Waiting for game state...');
      break;
    }

    case 'lobby': {
      lobbyMode = msg.mode === 'bot' ? MODE_HUMAN_VS_BOT : (msg.mode || null);
      lobbyHostName = msg.players?.O || null;
      if (!myMark) {
        updatePreJoinUiFromLobby();
      }
      break;
    }

    case 'need_mode':
      setStatusMessage('Choose Local, Remote, or Play Against Computer before joining.');
      break;

    case 'mode': {
      const prevMode = modeChosen;
      modeChosen = msg.mode === 'bot' ? MODE_HUMAN_VS_BOT : msg.mode;
      showGame();
      updateModePanels();
      updateInstructionsButtonState();
      if (modeChosen === MODE_HUMAN_VS_BOT) {
        resetBotAnalysisPanel();
      }
      setStatusMessage(computeTurnMessage());
      break;
    }

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
      if (Date.now() >= scoreFlashExpiresAt) {
        setStatusMessage(`${oppName} has moved. Waiting for you!`);
        break;
      }
      break;
    }
    if (Date.now() >= scoreFlashExpiresAt) {
      setStatusMessage(computeTurnMessage());
    }
      break;

    case 'score_event':
      if (modeChosen === MODE_HUMAN_VS_BOT) break;
      if (msg && typeof msg.mark === 'string' && typeof msg.added === 'number') {
        showScoreFlash(msg.mark, msg.added);
      }
      break;

    case 'bot_explanation':
      if (modeChosen === MODE_HUMAN_VS_BOT && Date.now() >= scoreFlashExpiresAt) {
        const text = formatBotExplanation(msg);
        const entry = {
          turn: Number.isFinite(msg?.turn) ? msg.turn : 0,
          source: msg?.source || 'bot',
          text,
        };
        botAnalysisHistory.unshift(entry);
        if (botAnalysisHistory.length > 8) botAnalysisHistory = botAnalysisHistory.slice(0, 8);
        renderBotAnalysisPanel();
        setStatusMessage(text);
      }
      break;

    case 'turn_committed':
      // After commit, pending clears; state will follow.
      pendingFlags = { O: false, X: false };
      if (isScoreFlashActive || Date.now() < scoreFlashExpiresAt) break;
      if (myMark && modeChosen === MODE_LOCAL) {
        setStatusMessage(computeTurnMessage());
      } else if (myMark && modeChosen === MODE_HUMAN_VS_BOT) {
        const oppName = (myMark === 'O') ? playerName('X', 'Player 2') : playerName('O', 'Player 1');
        setStatusMessage(`Waiting for you to make your next move. ${oppName} hasn't moved yet.`);
      }
      break;

    case 'game_over':
      isGameOver = true;
      // Show a big red banner + embedded MP4 in the Messages card.
      if (msg.message && msg.message.toLowerCase().includes('tie')) {
        showGameOverMedia('tie', "It's a tie!");
      } else if (msg.message && msg.message.includes('O wins')) {
        const winner = modeChosen === MODE_HUMAN_VS_BOT ? 'Human' : playerName('O', 'Player 1');
        showGameOverMedia('win', `${winner} wins!`);
      } else if (msg.message && msg.message.includes('X wins')) {
        const winner = modeChosen === MODE_HUMAN_VS_BOT ? 'WhiskBot' : playerName('X', 'Player 2');
        showGameOverMedia('win', `${winner} wins!`);
      } else {
        setStatusMessage(msg.message || 'Game over!');
      }
      render();
      break;

    case 'state': {
      const prevScores = { ...scores };

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
        maybeShowScoreFlashFromState(prevScores, scores);
      }

      if (msg.pending) {
        pendingFlags = {
          O: !!msg.pending.O,
          X: !!msg.pending.X,
        };
      }

      if (msg.mode) {
        const prevMode = modeChosen;
        modeChosen = msg.mode === 'bot' ? MODE_HUMAN_VS_BOT : msg.mode;
        updateModePanels();
        updateInstructionsButtonState();
        if (modeChosen !== MODE_HUMAN_VS_BOT) {
          resetBotAnalysisPanel();
        }
      }
      if (msg.local_next_mark) {
        localNextMark = msg.local_next_mark;
      }

      if (typeof msg.game_over === 'boolean') {
        isGameOver = msg.game_over;
      }

      if (msg.pieces) {
        let appearing = detectAppearingPieces(msg.pieces);
        // Remote-mode special case: when you're the second mover, the
        // opponent's piece may appear only as a reveal of an already-placed
        // move. Animate only your own newly committed piece.
        if (
          modeChosen === MODE_REMOTE &&
          msg.refresh &&
          myMark &&
          suppressOpponentRevealAnimationOnce
        ) {
          appearing = appearing.filter((p) => p.mark === myMark);
          suppressOpponentRevealAnimationOnce = false;
        }
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

      if (modeChosen === MODE_HUMAN_VS_BOT && msg.turn === 0 && msg.refresh) {
        resetBotAnalysisPanel();
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
  if (joinBtn.disabled || !(selectedJoinMode || lobbyMode)) return;
  join(selectedJoinMode || lobbyMode);
});
nameInput.addEventListener('input', updateJoinButtonState);
localBtn.addEventListener('click', () => setMode(MODE_LOCAL));
remoteBtn.addEventListener('click', () => setMode(MODE_REMOTE));
if (humanVsBotBtn) humanVsBotBtn.addEventListener('click', () => setMode(MODE_HUMAN_VS_BOT));

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
renderBotAnalysisPanel();
connect();
