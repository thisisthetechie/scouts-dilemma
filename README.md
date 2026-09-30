# Scout Dilemma

A small Flask/SQLite game for running the described two-device dilemma activity.

## Features
- Participant setup and configurable round count
- Everyone starts on 10 points
- Random pairings each round; setup requires an even number of participants
- Two independent mobile-friendly player-device pages
- Silent/Snitch choices with blue/orange high-contrast controls and text/icons
- Choices hidden after submission
- 30-second delay after both choices before the next pairing
- Scores committed/revealed only when the whole round completes
- Scoreboard sorted by points descending, then surname
- Admin start, pause/resume and reset controls
- Admin live pairing status showing who is current/up next and whether each answer has been entered (never the answer itself)
- SQLite persistence and refresh/reconnect tolerance
- Six-character game code and reusable URLs

## Scoring
- Silent / Silent: +1, +1
- Snitch / Silent: -1, +5
- Silent / Snitch: +5, -1
- Snitch / Snitch: +3, +3

## Run
Requires Python 3.10+.

    python -m venv .venv

Linux/macOS:

    source .venv/bin/activate
    pip install -r requirements.txt
    python app.py

Windows PowerShell:

    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    python app.py

Open http://localhost:5000 on the host laptop.

For phones/tablets on the same Wi-Fi, find the laptop's LAN IP (for example 192.168.1.50) and open `http://192.168.1.50:5000`. The Flask server listens on all interfaces.

## Important deployment note
This is designed for a trusted Scout-group/local-network setting. The admin URL is not password protected. If you expose the app publicly on the internet, add authentication and deploy behind a production WSGI server/HTTPS rather than Flask's built-in server.

## Game flow
1. Create a game and enter an even number of participant names.
2. Open Device 1, Device 2 and Scoreboard from the admin page.
3. Press Start game.
4. Send the displayed participant on each device into the room.
5. Each chooses Silent or Snitch. Their selection disappears immediately.
6. Once both have chosen, the admin prepares the next pairing after 10 seconds; the player devices advance after 30 seconds.
7. At the end of the round, accumulated results are applied to the scoreboard and the next round begins.

The `game.db` SQLite database is created automatically beside `app.py`.

## Configuration with `.env`
Configuration is loaded from a `.env` file using `python-dotenv`. Copy `.env.example` to `.env` and edit it as needed:

    PORT=5000
    SCOUT_DB=game.db

`PORT` controls the Flask listening port. `SCOUT_DB` controls the SQLite database path. The real `.env` file is ignored by Git; `.env.example` documents the available settings.

## Device QR codes
The admin page has a **Show QR codes for player devices** checkbox. Unticked, the original Device 1 / Device 2 buttons are shown for convenient local testing. Ticked, the two device URLs are displayed as QR codes for phones/tablets to scan. The preference is retained in that browser.

The QR code uses the hostname/IP address from the admin page URL. For another device on the LAN to scan it successfully, open the admin page using the laptop's LAN address (for example `http://192.168.1.50:5000/play/ABC123/admin`) rather than `localhost` before displaying the QR codes.
