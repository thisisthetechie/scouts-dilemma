# Scout Dilemma

A small Flask/SQLite game for running a variant of the Prisoners Dilemma within a group.

# Purpose

This project is intended for use by my local Scout Group during a session to outline consequences and group strategy. It has been coded entirely via ChatGPT (with a couple of direct tweaks as needed) as both a personal test of the ability of the LLM and also to speed up development.

# Setup

Scouts are assembled around the hut, ideally where they can keep their choices secret. 

The setup requires several devices:

- One Admin Laptop
- One Scoreboard System
- One device per Scout, to record their decision

Set up the game on the Admin Laptop by visiting the webpage URL.

You have the option to change the default scoring for the game.

Once you are ready, click **Create Game**. This will generate the game resources which are available on the next page, the Admin Portal.

Each Scout can access a QR Code to access the player screen, where they will be asked for their name. Once all Scouts are registered you are ready to begin, click **Start Game**.

# Objective

The idea of the game is to come out with the lowest score possible. 

Each Scout will be paired with all other Scouts at least once per round. They will make the choice to either **Snitch** or stay **Silent**. Depending on what their opposing Scout does in each pairing, they will be awarded or deducted points.

This should expose the Scouts to 2 key factors: **Consequence** and **Strategy**.

## Consequence
The act of either snitching or staying quiet will have an outcome. Depending on how the scoring is set up, at least one option can either have a great benefit or a great loss - and it all relies on how their opponent votes relative to themselves. 

## Strategy
For the second round, invite the Scouts to discuss how they played the first round and have them consider if they discussed their strategies if the next round could be any different.

You can either reset the game after a round (to compare directly against how they performed in the first round) or simply pause after the first round 

## Features
- Participant setup and configurable round count
- Everyone starts on 10 points
- Random pairings each round
- Independent mobile-friendly player-device pages
- Silent/Snitch choices with blue/orange high-contrast controls and text/icons
- Choices hidden after submission
- Configurable (default: 10s) delay after both choices before the next pairing
- Scores committed/revealed only when the whole round completes
- Scoreboard sorted by points ascending, then surname
- Admin start, pause/resume and reset controls
- Admin live pairing status showing who is current/up next and whether each answer has been entered (never the answer itself)
- SQLite persistence and refresh/reconnect tolerance
- Six-character game code and reusable URLs
- Configurable Port (default: 8080)

## Scoring
- Silent / Silent: +1, +1
- Snitch / Silent: -1, +5
- Silent / Snitch: +5, -1
- Snitch / Snitch: +3, +3

This can be configured within the `.env` file or Environment Variables

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

Docker Compose:

    docker compose build
    docker compose up

Open http://localhost:8080 on the host laptop (or http://localhost if using default Docker Compose configuration).

For phones/tablets on the same Wi-Fi, find the laptop's LAN IP (for example 192.168.1.50) and open `http://192.168.1.50:8080`. The Flask server listens on all interfaces.

## Important deployment note
This is designed for a trusted Scout-group/local-network setting. The admin URL is not password protected. If you expose the app publicly on the internet, add authentication and deploy behind a production WSGI server/HTTPS rather than Flask's built-in server.

## Game flow
1. Open the URL and set any configuration changes required.
2. Create a game and invite participants to scan the QR Code to access their player page.
3. Press Start game.
4. During the game, each partipant is paired with another at random.
5. Each chooses Silent or Snitch. Their selection disappears immediately.
6. Once both have chosen, the admin prepares the next pairing after 10 seconds; the player devices advance after 10 seconds.
7. At the end of the round, accumulated results are applied to the scoreboard and the game is paused.
8. Once the results have been discussed, admin presses Resume Game and the next round begins.

The `game.db` SQLite database is created automatically beside `app.py`.

## Configuration with `.env`
Configuration is loaded from a `.env` file using `python-dotenv`. Copy `.env.example` to `.env` and edit it as needed:

    PORT=8080
    SCOUT_DB=game.db
    DEVICE_TRANSITION_SECONDS=10
    DEFAULT_STARTING_POINTS=10
    DEFAULT_SILENT_SILENT=1
    DEFAULT_SNITCH_SILENT_SNITCH=-1
    DEFAULT_SNITCH_SILENT_SILENT=5
    DEFAULT_SNITCH_SNITCH=3

`PORT` controls the Flask listening port. `SCOUT_DB` controls the SQLite database path. The `DEFAULT_*` values control the scoring shown on a fresh setup page. Use a negative value to have **Remove points** checked automatically; the setup page displays the absolute value in the number field. The real `.env` file is ignored by Git; `.env.example` documents the available settings.

## Device QR codes
The admin page has a QR Code for participants to access the player page and enter their name.

The QR code uses the hostname/IP address from the admin page URL. For another device on the LAN to scan it successfully, open the admin page using the laptop's LAN address (for example `http://192.168.1.50:8080/play/ABC123/admin`) rather than `localhost` before displaying the QR codes.

## Configurable scoring
The setup page lets the organiser choose the starting score and the adjustment for every outcome. Each adjustment has a **Remove points** checkbox; when selected, the entered number is stored as a deduction. These rules are stored with the game in SQLite and are retained across application restarts. Resetting a game restores players to that game's configured starting score.


### Pairings
Each configured round is a complete round-robin: every participant plays every other participant exactly once during that round.

### Scoring verification
The admin page shows the signed scoring values saved with the game, so the organiser can verify the configuration after setup.

# Known Issues
There is no way to change the participants mid-game, and if a Scout loses their player page the game cannot progress, as it waits for every participant to enter a decision before moving onto the next match up. This would require the game to be reset, the player removed and re-added.

Clicking "Remove" does not work if the popup activity is not immediately acknowleged since the page refreshes and loses its context. You need to be ready to click "Remove" and hitting return immediately is the best way to ensure smooth removal.

On occasion, the page fails to reload after a new match starts. Refreshing the page will resolve that.

If your WiFi network is configured as a "Guest" network, then you will not be able to access local resources (such as this app) over this network.

The app relies on many refresh and poll activities happening per second. This can cause one or more page to fail to reload or take longer for no specific reason.