/**
 * Game logic: 8×8 board, 5 O / 5 X max, aging, scoring (3→1, 4→4, 5→9), end at 50+.
 */

const ROWS = 8;
const COLS = 8;
const MAX_PIECES = 5;
const SCORE_3 = 1;
const SCORE_4 = 4;
const SCORE_5 = 9;
const TARGET_SCORE = 50;

const SATURATION_CLASSES = ['sat-20', 'sat-40', 'sat-60', 'sat-80', 'sat-100']; // index 0 = oldest

/**
 * @typedef {{ row: number, col: number, player: 'O'|'X', order: number }} Piece
 */

/**
 * Create empty game state.
 * @returns {{ pieces: Piece[], scoreO: number, scoreX: number, pendingO: {row:number,col:number}|null, pendingX: {row:number,col:number}|null, gameOver: boolean }}
 */
function createGameState() {
  return {
    pieces: [],
    scoreO: 0,
    scoreX: 0,
    pendingO: null,
    pendingX: null,
    gameOver: false
  };
}

/**
 * Get piece at (row, col) from pieces array (only placed pieces, considering max 5 per player).
 */
function getPieceAt(pieces, row, col) {
  return pieces.find(p => p.row === row && p.col === col) || null;
}

/**
 * Add a placement; enforce max 5 per player by removing oldest.
 * @param {Piece[]} pieces
 * @param {number} row
 * @param {number} col
 * @param {'O'|'X'} player
 * @param {number} order - global order (higher = newer)
 * @returns {{ pieces: Piece[], removed: Piece|null }}
 */
function addPiece(pieces, row, col, player, order) {
  const samePlayer = pieces.filter(p => p.player === player).sort((a, b) => a.order - b.order);
  let next = [...pieces];
  let removed = null;
  if (samePlayer.length >= MAX_PIECES) {
    const oldest = samePlayer[0];
    removed = oldest;
    next = next.filter(p => !(p.row === oldest.row && p.col === oldest.col));
  }
  next.push({ row, col, player, order });
  return { pieces: next, removed };
}

/**
 * Best run length in a string of cell values (e.g. "O.OOO.X" -> 3 for O).
 */
function bestRun(s, player) {
  let max = 0;
  let cur = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === player) {
      cur++;
      if (cur > max) max = cur;
    } else cur = 0;
  }
  return max;
}

/**
 * Score one line: 5→9, 4→4, 3→1 (best run only, no double-count).
 */
function scoreLine(s) {
  const o = bestRun(s, 'O');
  const x = bestRun(s, 'X');
  return {
    o: o >= 5 ? SCORE_5 : o >= 4 ? SCORE_4 : o >= 3 ? SCORE_3 : 0,
    x: x >= 5 ? SCORE_5 : x >= 4 ? SCORE_4 : x >= 3 ? SCORE_3 : 0
  };
}

/**
 * Compute scores from current board (pieces). Best run per line only.
 * @param {Piece[]} pieces
 * @returns {{ scoreO: number, scoreX: number }}
 */
function computeScores(pieces) {
  const grid = Array(ROWS).fill(null).map(() => Array(COLS).fill(null));
  pieces.forEach(p => { grid[p.row][p.col] = p.player; });

  let scoreO = 0;
  let scoreX = 0;

  for (let r = 0; r < ROWS; r++) {
    let row = '';
    for (let c = 0; c < COLS; c++) row += grid[r][c] || '.';
    const t = scoreLine(row);
    scoreO += t.o;
    scoreX += t.x;
  }
  for (let c = 0; c < COLS; c++) {
    let col = '';
    for (let r = 0; r < ROWS; r++) col += grid[r][c] || '.';
    const t = scoreLine(col);
    scoreO += t.o;
    scoreX += t.x;
  }
  for (let d = -(ROWS - 1); d <= COLS - 1; d++) {
    let diag = '';
    for (let r = 0; r < ROWS; r++) {
      const c = d + r;
      if (c >= 0 && c < COLS) diag += grid[r][c] || '.';
    }
    if (diag.length >= 3) {
      const t = scoreLine(diag);
      scoreO += t.o;
      scoreX += t.x;
    }
  }
  for (let d = 0; d < ROWS + COLS - 1; d++) {
    let diag = '';
    for (let r = 0; r < ROWS; r++) {
      const c = d - r;
      if (c >= 0 && c < COLS) diag += grid[r][c] || '.';
    }
    if (diag.length >= 3) {
      const t = scoreLine(diag);
      scoreO += t.o;
      scoreX += t.x;
    }
  }

  return { scoreO, scoreX };
}

/**
 * Get saturation class for a piece by its age (order): newest = sat-100, oldest = sat-20.
 * Assumes pieces for same player are sorted by order and we pass index 0..4.
 */
function saturationClass(ageIndex) {
  return SATURATION_CLASSES[Math.min(ageIndex, 4)] || 'sat-20';
}

/**
 * Build display list: each cell that has a piece, with player and saturation class.
 * Only the 5 most recent per player are shown; order by placement (oldest first for indexing).
 */
function getDisplayPieces(pieces) {
  const byPlayer = { O: [], X: [] };
  pieces.forEach(p => byPlayer[p.player].push(p));
  ['O', 'X'].forEach(pl => {
    byPlayer[pl].sort((a, b) => a.order - b.order);
    if (byPlayer[pl].length > MAX_PIECES) byPlayer[pl] = byPlayer[pl].slice(-MAX_PIECES);
  });
  const result = [];
  ['O', 'X'].forEach(pl => {
    byPlayer[pl].forEach((p, i) => {
      result.push({ row: p.row, col: p.col, player: pl, satClass: saturationClass(i) });
    });
  });
  return result;
}

window.WhiskGame = {
  ROWS,
  COLS,
  MAX_PIECES,
  TARGET_SCORE,
  createGameState,
  getPieceAt,
  addPiece,
  computeScores,
  getDisplayPieces,
  saturationClass
};
