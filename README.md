# Whisk

Two-player 8×8 board game based on tic-tac-toe with simultaneous moves, limited pieces, and scoring.

## Rules

- **Board**: 8×8 grid.
- **Scoring**: 1 pt for 3 in a row, 4 pts for 4 in a row, 9 pts for 5 in a row. First to **50** (or both at 50+) ends the game.
- **Pieces**: At most 5 Os and 5 Xs on the board. When a 6th is placed, the oldest of that symbol is removed.
- **Visual aging**: Os (blue) and Xs (red) fade by saturation: newest 100%, then 80%, 60%, 40%, oldest 20%.
- **Two-computer play**: Moves are simultaneous. When O places, only O sees it until X places; then both see both. If X picks the same square as O, X is blocked and sees: "That square is already occupied."

## How to play

1. Open the same URL on both computers (or one for local play).
2. Enter your name. The first to continue chooses:
   - **Play locally** – one computer, alternating O then X.
   - **Host game** – get a link with `#roomId` and share it; the second player opens the link, enters their name, and joins as X.
3. O goes first (first player), X second (second player). In hosted mode, place when ready; both moves reveal together.

## Setup

### 1. Firebase (for two-computer play)

Hosted games use **Firebase Realtime Database** for real-time sync. GitHub Pages only serves static files, so the app uses Firebase as the backend.

1. Create a project at [Firebase Console](https://console.firebase.google.com).
2. Add a **Realtime Database** (not Firestore) and start in **test mode** (or set rules as needed).
3. In Project settings → General → Your apps, add a web app and copy the config object.
4. In this repo, edit **`js/firebase-config.js`** and replace the placeholder with your config:

```js
window.FIREBASE_CONFIG = {
  apiKey: "...",
  authDomain: "...",
  databaseURL: "https://YOUR_PROJECT_ID-default-rtdb.firebaseio.com",
  projectId: "...",
  storageBucket: "...",
  messagingSenderId: "...",
  appId: "..."
};
```

Without Firebase, only **Play locally** works.

### 2. Run locally

Open `index.html` in a browser, or serve the folder:

```bash
# Python
python3 -m http.server 8000

# Node
npx serve .
```

Then go to `http://localhost:8000`.

### 3. Deploy to GitHub Pages

1. Push this repo to **github.com/phrwtz/Whisk2** (or your repo).
2. In the repo: **Settings → Pages**.
3. Source: **Deploy from a branch**. Branch: **main** (or **master**), folder: **/ (root)**.
4. Save. The site will be at `https://phrwtz.github.io/Whisk2/`.

Share that URL for two-computer play; use the link with `#roomId` after hosting a game.

## Version control

The project uses **Git**. To initialize and push:

```bash
git init
git add .
git commit -m "Initial Whisk game"
git remote add origin https://github.com/phrwtz/Whisk2.git
git branch -M main
git push -u origin main
```

## Files

- `index.html` – Lobby and game UI
- `css/style.css` – Layout and saturation styling
- `js/firebase-config.js` – Your Firebase config (edit this)
- `js/firebase-service.js` – Room create/join and game sync
- `js/game.js` – Board, pieces, scoring, aging
- `js/lobby.js` – Name prompt, first/second player, local vs host
- `js/app.js` – Board rendering, clicks, status, game over
