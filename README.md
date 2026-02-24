# Whisk (8x8 simultaneous tic‑tac‑toe variant)

This is a 2‑player web board game inspired by tic‑tac‑toe, with:

- **8×8 board**
- **Simultaneous turns** (each player’s move is hidden until both have moved)
- **Scoring**
  - +1 for each **exact** 3‑in‑a‑row
  - +4 for each **exact** 4‑in‑a‑row
  - +9 for each **exact** 5‑in‑a‑row
  - Lines count **horizontal, vertical, diagonal**
  - Points are added to a **running total** each committed turn
- **Only 5 pieces per player** remain on the board (oldest disappears when a 6th would be added)
- **Fading pieces** to show age:
  - newest: 100% saturation
  - next: 80%
  - then: 60%, 40%, 20%
- Game ends when **one or both** players reach **50+** points.

Backend (Python) is authoritative: it maintains the board, applies the 5‑piece rule, calculates scores, and sends messages.
Frontend (JavaScript) renders the board, fades pieces, collects clicks, and shows messages.

---

## Repo layout

```
whisk2/
  backend/
    app/
      game.py        # core rules + scoring (unit tested)
      main.py        # FastAPI + WebSocket server
    requirements.txt
  frontend/
    index.html
    styles.css
    app.js
  tests/
    test_game.py
    conftest.py
```

---

## Run locally

### 1) Clone and create a virtual environment

```bash
git clone https://github.com/phrwtz/Whisk2.git
cd Whisk2
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\\Scripts\\activate  # Windows PowerShell
```

### 2) Install backend dependencies

```bash
pip install -r backend/requirements.txt
```

### 3) Start the server

From the **backend** folder:

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser:

- Player 1: `http://localhost:8000/`
- Player 2: open the **same URL** on another computer (or another browser/tab)

Player 1 is **O**, Player 2 is **X**.

---

## Run tests

From the repo root:

```bash
pytest -q
```

---

## How simultaneous turns work

1. Each player clicks a square.
2. The server validates the move:
   - if the square is already occupied (by an existing piece) or **reserved** this turn, the move is rejected.
3. After the first valid move, only the mover sees a private preview of their own pending piece (with aging/score preview). The opponent sees no board or score change yet.
4. When both moves are accepted, the server **commits the turn**, applies both moves, updates running totals, and broadcasts the same updated board/scores to both players.

Turn lifecycle is: `reserve -> wait -> commit -> reveal`.

---

## Local mode note

The host can choose **Local** or **Remote** in the UI.  
In the current version, Local mode is intentionally deferred and uses the same two-player server flow as Remote mode.

---

## Using GitHub Pages (frontend hosting)

GitHub Pages can only host **static** content (HTML/CSS/JS). The **Python backend must run elsewhere**.

### Option A: Use GitHub Pages for the frontend + Render/Fly.io for the backend

1. Deploy the backend to a host (examples: Render, Fly.io, Railway).
2. In `frontend/app.js`, change the WebSocket URL builder to point at your backend host if you are serving the frontend from pages.
   - Look for `wsUrl()` and adjust it to use your deployed domain.
3. Enable GitHub Pages:
   - GitHub repo → **Settings** → **Pages**
   - Source: **Deploy from a branch**
   - Branch: `main` and folder `/frontend`

Now the frontend is served from Pages, and it connects to your hosted backend.

### Option B: Run everything locally (best for classrooms)

If you’re using this as a classroom activity, running the FastAPI server on a laptop and having students join via
`http://<laptop-ip>:8000/` is usually the simplest approach.

---

## Notes for beginners

- `backend/app/game.py` contains the rules and scoring. It is written so you can test it without needing a browser.
- `backend/app/main.py` is the server that connects players and enforces the “hidden until both moved” rule.
- `frontend/app.js` draws the board and sends moves to the server.

---

## License

Add a license if you plan to distribute.
