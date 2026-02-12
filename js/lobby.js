/**
 * Lobby: same URL, name prompt, first player chooses local vs host, second joins.
 */

(function () {
  const Firebase = window.FirebaseService;
  const hasFirebase = Firebase && Firebase.isConfigured();

  function showScreen(id) {
    document.querySelectorAll('[data-screen]').forEach(el => {
      el.classList.toggle('hidden', el.dataset.screen !== id);
    });
  }

  function showMessage(selector, text, type) {
    const el = document.querySelector(selector);
    if (!el) return;
    el.textContent = text;
    el.className = 'message ' + (type || 'info');
    el.classList.remove('hidden');
  }

  function hideMessage(selector) {
    const el = document.querySelector(selector);
    if (el) el.classList.add('hidden');
  }

  /**
   * Start lobby: name input, then either "first" (choose local/host) or "second" (join).
   */
  function startLobby() {
    const nameInput = document.getElementById('player-name');
    const nameBtn = document.getElementById('name-submit');
    const firstActions = document.getElementById('first-player-actions');
    const localBtn = document.getElementById('play-local');
    const hostBtn = document.getElementById('host-game');
    const roomInput = document.getElementById('room-id-input');
    const joinBtn = document.getElementById('join-game');
    const lobbyMessage = document.getElementById('lobby-message');

    if (!nameInput || !nameBtn) return;

    const hash = (window.location.hash || '').replace(/^#/, '');
    if (hash) {
      const msg = document.getElementById('second-player-message');
      if (msg) { msg.classList.remove('hidden'); msg.textContent = 'A game has been started. Enter your name to join.'; }
    }

    nameBtn.onclick = () => {
      const name = (nameInput.value || '').trim();
      if (!name) return;
      window.playerName = name;
      checkFirstOrSecond(name);
    };

    function checkFirstOrSecond(name) {
      const roomId = (window.location.hash || '').replace(/^#/, '') || null;

      if (roomId) {
        // URL has #roomId -> try to join as second player
        if (!Firebase || !Firebase.getRoom) {
          showMessage('lobby-message', 'Hosted games require Firebase configuration.', 'warning');
          showFirstPlayerChoices();
          return;
        }
        Firebase.getRoom(roomId).then(room => {
          if (!room) {
            showMessage('lobby-message', 'Room not found. You can start a new game.', 'warning');
            showFirstPlayerChoices();
            return;
          }
          if (room.guestReady) {
            showMessage('lobby-message', 'This room is full. Start a new game.', 'warning');
            showFirstPlayerChoices();
            return;
          }
          // Join as second player
          Firebase.joinRoom(roomId, name).then(() => {
            showMessage('lobby-message', 'Game found! Joining as X...', 'info');
            window.playerRole = 'X';
            window.roomId = roomId;
            window.isHosted = true;
            window.otherPlayerName = room.hostName;
            startGame();
          }).catch(err => {
            showMessage('lobby-message', err.message || 'Could not join room.', 'warning');
            showFirstPlayerChoices();
          });
        }).catch(() => {
          showFirstPlayerChoices();
        });
        return;
      }

      // No room in URL: we're first (or we'll create/host)
      showFirstPlayerChoices();
    }

    function showFirstPlayerChoices() {
      document.getElementById('name-prompt').classList.add('hidden');
      firstActions.classList.remove('hidden');
      document.getElementById('first-hello').textContent = window.playerName + ', you\'re first. Choose how to play:';

      if (localBtn) {
        localBtn.onclick = () => {
          window.isHosted = false;
          window.playerRole = 'O';
          window.roomId = null;
          startGame();
        };
      }

      if (hostBtn && hasFirebase) {
        hostBtn.classList.remove('hidden');
        hostBtn.onclick = () => {
          hostBtn.disabled = true;
          Firebase.createRoom(window.playerName).then(rid => {
            window.roomId = rid;
            window.playerRole = 'O';
            window.isHosted = true;
            window.otherPlayerName = null;
            // Update URL so second player can use same URL + #roomId
            window.location.hash = rid;
            showMessage('lobby-message', 'Game created. Share this URL with the other player: ' + window.location.href, 'info');
            Firebase.onGuestJoin(rid, (guestName) => {
              window.otherPlayerName = guestName;
              showMessage('lobby-message', guestName + ' has joined. Starting game...', 'info');
              setTimeout(() => startGame(), 800);
            });
          }).catch(err => {
            showMessage('lobby-message', err.message || 'Could not create room.', 'warning');
            hostBtn.disabled = false;
          });
        };
      } else if (hostBtn) {
        hostBtn.classList.add('hidden');
      }

      // Optional: join by pasting room ID (for second player without clicking shared link)
      if (joinBtn && roomInput && hasFirebase) {
        joinBtn.onclick = () => {
          const rid = (roomInput.value || '').trim();
          if (!rid) return;
          window.location.hash = rid;
          window.location.reload(); // re-run lobby with #roomId
        };
      }
    }
  }

  function startGame() {
    showScreen('game');
    if (window.startGameUI) window.startGameUI();
  }

  window.Lobby = {
    start: startLobby,
    showScreen,
    showMessage,
    hideMessage
  };
})();
