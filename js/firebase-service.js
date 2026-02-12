/**
 * Firebase Realtime Database service for hosted two-player games.
 * If FIREBASE_CONFIG is not set or invalid, hosted mode is disabled.
 */

(function () {
  let db = null;
  let gameRef = null;
  let unsubscribe = null;

  function isConfigured() {
    const c = window.FIREBASE_CONFIG;
    return c && c.apiKey && c.apiKey !== 'YOUR_API_KEY' && c.databaseURL;
  }

  function init() {
    if (!isConfigured()) return false;
    if (typeof firebase === 'undefined') return false;
    try {
      if (!firebase.apps.length) firebase.initializeApp(window.FIREBASE_CONFIG);
      db = firebase.database();
      return true;
    } catch (e) {
      console.warn('Firebase init failed', e);
      return false;
    }
  }

  /**
   * Create a room as host (player 1 = O). Returns roomId.
   * @param {string} playerName
   * @returns {Promise<string>} roomId
   */
  function createRoom(playerName) {
    if (!init()) return Promise.reject(new Error('Firebase not configured'));
    const roomRef = db.ref('rooms').push();
    const roomId = roomRef.key;
    const state = {
      hostName: playerName,
      hostReady: true,
      guestName: null,
      guestReady: false,
      game: null,
      createdAt: Date.now()
    };
    return roomRef.set(state).then(() => roomId);
  }

  /**
   * Listen for guest join; callback(guestName) when guestReady is true.
   */
  function onGuestJoin(roomId, callback) {
    if (!init()) return () => {};
    const ref = db.ref('rooms/' + roomId);
    const fn = ref.on('value', snap => {
      const d = snap.val();
      if (d && d.guestReady && d.guestName) callback(d.guestName);
    });
    return () => ref.off('value', fn);
  }

  /**
   * Join as guest (player 2 = X). Returns true when joined.
   */
  function joinRoom(roomId, playerName) {
    if (!init()) return Promise.reject(new Error('Firebase not configured'));
    const ref = db.ref('rooms/' + roomId);
    return ref.once('value').then(snap => {
      const d = snap.val();
      if (!d) throw new Error('Room not found');
      if (d.guestReady) throw new Error('Room is full');
      return ref.update({ guestName: playerName, guestReady: true });
    });
  }

  /**
   * List rooms waiting for guest (hostReady, !guestReady). For "join" flow by URL with room id.
   */
  function getRoom(roomId) {
    if (!init()) return Promise.resolve(null);
    return db.ref('rooms/' + roomId).once('value').then(snap => snap.val());
  }

  /**
   * Subscribe to game state for a room. callback(state) on any change.
   * state: { pieces, scoreO, scoreX, pendingO, pendingX, gameOver, lastOrder, myPending?, ... }
   */
  function subscribeGame(roomId, callback) {
    if (!init()) return () => {};
    gameRef = db.ref('rooms/' + roomId + '/game');
    unsubscribe = gameRef.on('value', snap => {
      callback(snap.val() || null);
    });
    return () => {
      if (unsubscribe) {
        gameRef.off('value', unsubscribe);
        unsubscribe = null;
      }
    };
  }

  /**
   * Submit a pending move (only visible to me until opponent moves).
   * role: 'O' | 'X'
   * If we're O: set pendingO. If we're X: set pendingX.
   * Server doesn't resolve conflict; we do on client when applying both.
   */
  function submitPending(roomId, role, row, col) {
    if (!init()) return Promise.reject(new Error('Firebase not configured'));
    const ref = db.ref('rooms/' + roomId + '/game');
    return ref.transaction(current => {
      const next = current || {};
      if (role === 'O') next.pendingO = { row, col };
      else next.pendingX = { row, col };
      return next;
    });
  }

  /**
   * Commit both pending moves (called by both clients after both have set pending).
   * We need a single "committer" to avoid races. Use: when both pendingO and pendingX are set,
   * either player can run commit. We'll do commit from the client that has both pending set
   * and then clear pendings and update pieces. Use transaction to merge pieces and clear pendings.
   */
  function commitMoves(roomId, stateWithBothPending) {
    if (!init()) return Promise.reject(new Error('Firebase not configured'));
    const ref = db.ref('rooms/' + roomId + '/game');
    return ref.transaction(current => {
      if (!current || !current.pendingO || !current.pendingX) return; // abort
      const po = current.pendingO;
      const px = current.pendingX;
      let pieces = current.pieces || [];
      const lastOrder = (current.lastOrder || 0) + 1;
      const sameCell = po.row === px.row && po.col === px.col;
      const orderO = lastOrder;
      const orderX = lastOrder + 1;
      if (!sameCell) {
        let res = window.WhiskGame.addPiece(pieces, po.row, po.col, 'O', orderO);
        pieces = res.pieces;
        res = window.WhiskGame.addPiece(pieces, px.row, px.col, 'X', orderX);
        pieces = res.pieces;
      } else {
        // Only O gets placed; X tried same cell
        const res = window.WhiskGame.addPiece(pieces, po.row, po.col, 'O', orderO);
        pieces = res.pieces;
      }
      const { scoreO, scoreX } = window.WhiskGame.computeScores(pieces);
      const gameOver = scoreO >= 50 || scoreX >= 50;
      return {
        ...current,
        pieces,
        scoreO,
        scoreX,
        lastOrder: lastOrder + 2,
        pendingO: null,
        pendingX: null,
        gameOver,
        conflictForX: sameCell  // true only when X tried same cell as O
      };
    });
  }

  window.FirebaseService = {
    isConfigured: () => init(),
    createRoom,
    onGuestJoin,
    joinRoom,
    getRoom,
    subscribeGame,
    submitPending,
    commitMoves
  };
})();
