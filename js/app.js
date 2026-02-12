/**
 * Main UI: board rendering, clicks, simultaneous move reveal, saturation, scores, game over.
 */

(function () {
  const Game = window.WhiskGame;
  const Firebase = window.FirebaseService;

  let gameState = null;
  let orderCounter = 0;
  let unsubFirebase = null;
  let lastConflictForX = false;

  function getBoardEl() { return document.getElementById('board'); }
  function getScoreOEl() { return document.getElementById('score-o'); }
  function getScoreXEl() { return document.getElementById('score-x'); }
  function getStatusEl() { return document.getElementById('status'); }
  function getGameOverEl() { return document.getElementById('game-over'); }

  function renderBoard(state, options) {
    options = options || {};
    const role = window.playerRole || 'O';
    const isHosted = window.isHosted === true;
    const boardEl = getBoardEl();
    if (!boardEl) return;

    const pieces = state.pieces || [];
    const displayPieces = Game.getDisplayPieces(pieces);
    const occupied = {};
    displayPieces.forEach(p => { occupied[p.row + ',' + p.col] = { player: p.player, satClass: p.satClass }; });

    // Pending: show O's pending only to O until X has also placed; then show both (then commit)
    const pendingO = state.pendingO || null;
    const pendingX = state.pendingX || null;
    const showPendingO = pendingO && (role === 'O' || pendingX); // O always sees own; X sees O's only when X has placed too
    const showPendingX = pendingX && role === 'X';

    boardEl.innerHTML = '';
    for (let r = 0; r < Game.ROWS; r++) {
      for (let c = 0; c < Game.COLS; c++) {
        const cell = document.createElement('div');
        cell.className = 'cell';
        cell.dataset.row = r;
        cell.dataset.col = c;
        const key = r + ',' + c;
        let content = occupied[key];
        if (!content && showPendingO && pendingO.row === r && pendingO.col === c) content = { player: 'O', satClass: 'sat-100' };
        if (!content && showPendingX && pendingX.row === r && pendingX.col === c) content = { player: 'X', satClass: 'sat-100' };
        if (content) {
          cell.classList.add('occupied');
          const span = document.createElement('span');
          span.className = 'piece ' + content.player + ' ' + content.satClass;
          span.textContent = content.player;
          cell.appendChild(span);
        } else {
          cell.addEventListener('click', () => onCellClick(r, c));
        }
        boardEl.appendChild(cell);
      }
    }
  }

  function onCellClick(row, col) {
    const state = gameState;
    if (!state || state.gameOver) return;
    const role = window.playerRole;
    const pieces = state.pieces || [];
    const displayPieces = Game.getDisplayPieces(pieces);
    const occupied = {};
    displayPieces.forEach(p => { occupied[p.row + ',' + p.col] = true; });
    const pendingO = state.pendingO;
    const pendingX = state.pendingX;
    if (role === 'O' && pendingO) return; // already placed this round
    if (role === 'X' && pendingX) return;
    const key = row + ',' + col;
    if (occupied[key]) return;
    if (pendingO && pendingO.row === row && pendingO.col === col) return;
    if (pendingX && pendingX.row === row && pendingX.col === col) return;

    if (window.isHosted && Firebase && Firebase.isConfigured()) {
      Firebase.submitPending(window.roomId, role, row, col).then(() => {
        getStatusEl().textContent = role === 'O' ? 'Waiting for X to move...' : 'Revealing moves...';
        getStatusEl().classList.remove('error');
      }).catch(() => {
        getStatusEl().textContent = 'Failed to send move.';
        getStatusEl().classList.add('error');
      });
      return;
    }

    // Local play: alternate O then X (or we could do "both place then reveal" - prompt says simultaneous for two-computer; for local we do simple turns)
    applyLocalMove(role, row, col);
  }

  function applyLocalMove(role, row, col) {
    const state = gameState;
    const pieces = state.pieces || [];
    const lastOrder = orderCounter++;
    const res = Game.addPiece(pieces, row, col, role, lastOrder);
    state.pieces = res.pieces;
    const { scoreO, scoreX } = Game.computeScores(state.pieces);
    state.scoreO = scoreO;
    state.scoreX = scoreX;
    state.gameOver = scoreO >= Game.TARGET_SCORE || scoreX >= Game.TARGET_SCORE;
    updateScores(state);
    renderBoard(state);
    const other = role === 'O' ? 'X' : 'O';
    getStatusEl().textContent = state.gameOver ? 'Game over!' : other + "'s turn";
    getStatusEl().classList.remove('error');
    if (state.gameOver) showGameOver(state);
  }

  function updateScores(state) {
    const so = getScoreOEl();
    const sx = getScoreXEl();
    if (so) so.textContent = 'O: ' + (state.scoreO || 0);
    if (sx) sx.textContent = 'X: ' + (state.scoreX || 0);
  }

  function showGameOver(state) {
    const el = getGameOverEl();
    if (!el) return;
    const o = state.scoreO || 0;
    const x = state.scoreX || 0;
    if (o >= 50 && x >= 50) el.textContent = 'Tie! Both reached 50+.';
    else if (o >= 50) el.textContent = 'O wins with ' + o + ' points!';
    else el.textContent = 'X wins with ' + x + ' points!';
    el.classList.remove('hidden');
  }

  function setStatus(text, isError) {
    const el = getStatusEl();
    if (el) {
      el.textContent = text;
      el.classList.toggle('error', !!isError);
    }
  }

  function startGameUI() {
    document.getElementById('game-area').classList.add('visible');
    const oName = document.getElementById('player-o-name');
    const xName = document.getElementById('player-x-name');
    if (window.isHosted) {
      if (oName) oName.textContent = '(' + (window.playerRole === 'O' ? window.playerName : (window.otherPlayerName || 'O')) + ')';
      if (xName) xName.textContent = '(' + (window.playerRole === 'X' ? window.playerName : (window.otherPlayerName || 'X')) + ')';
    } else {
      if (oName) oName.textContent = '';
      if (xName) xName.textContent = '';
    }

    gameState = Game.createGameState();
    orderCounter = 0;
    lastConflictForX = false;

    const goEl = getGameOverEl();
    if (goEl) goEl.classList.add('hidden');

    if (window.isHosted && window.roomId && Firebase && Firebase.isConfigured()) {
      setStatus(window.playerRole === 'O' ? 'Place your O. X will see it when they move.' : 'Waiting for O to move, then place your X.');
      unsubFirebase = Firebase.subscribeGame(window.roomId, (remote) => {
        if (!remote) return;
        gameState = {
          pieces: remote.pieces || [],
          scoreO: remote.scoreO || 0,
          scoreX: remote.scoreX || 0,
          pendingO: remote.pendingO || null,
          pendingX: remote.pendingX || null,
          gameOver: !!remote.gameOver
        };
        updateScores(gameState);
        renderBoard(gameState);

        if (remote.gameOver) {
          showGameOver(gameState);
          return;
        }

        if (remote.conflictForX && !lastConflictForX) {
          lastConflictForX = true;
          setStatus('That square is already occupied. Choose another.', true);
        } else if (!remote.conflictForX) {
          lastConflictForX = false;
        }

        if (remote.pendingO && remote.pendingX) {
          Firebase.commitMoves(window.roomId, remote);
          setStatus(window.playerRole === 'O' ? 'Place your O.' : 'Place your X.');
        } else if (remote.pendingO && window.playerRole === 'X') {
          setStatus('O has moved. Place your X.');
        } else if (remote.pendingO && window.playerRole === 'O') {
          setStatus('Waiting for X to move...');
        } else {
          setStatus(window.playerRole === 'O' ? 'Place your O.' : 'Place your X.');
        }
      });
    } else {
      setStatus("O's turn (local game)");
      renderBoard(gameState);
      updateScores(gameState);
    }
  }

  window.startGameUI = startGameUI;

  document.addEventListener('DOMContentLoaded', () => {
    window.Lobby.start();
  });
})();
