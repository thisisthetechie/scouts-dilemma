import os
import random
import sqlite3
import string
import base64
import secrets
from io import BytesIO
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify
import qrcode

load_dotenv()

app = Flask(__name__)
DB = os.environ.get('SCOUT_DB', os.path.join(os.path.dirname(__file__), 'scout_group_dilemma.db'))
DEVICE_TRANSITION_SECONDS = int(os.getenv('DEVICE_TRANSITION_SECONDS', '10'))

DEFAULT_STARTING_POINTS = int(os.getenv('DEFAULT_STARTING_POINTS', '10'))
DEFAULT_SILENT_SILENT = int(os.getenv('DEFAULT_SILENT_SILENT', '1'))
DEFAULT_SNITCH_SILENT_SNITCH = int(os.getenv('DEFAULT_SNITCH_SILENT_SNITCH', '-1'))
DEFAULT_SNITCH_SILENT_SILENT = int(os.getenv('DEFAULT_SNITCH_SILENT_SILENT', '5'))
DEFAULT_SNITCH_SNITCH = int(os.getenv('DEFAULT_SNITCH_SNITCH', '3'))

DEFAULT_SCORING = {
    'starting_points': DEFAULT_STARTING_POINTS,
    'silent_silent': DEFAULT_SILENT_SILENT,
    'snitch_silent': DEFAULT_SNITCH_SILENT_SNITCH,
    'silent_snitch': DEFAULT_SNITCH_SILENT_SILENT,
    'snitch_snitch': DEFAULT_SNITCH_SNITCH,
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS games(
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE,
    total_rounds INTEGER,
    current_round INTEGER DEFAULT 1,
    current_cycle INTEGER DEFAULT 0,
    cycles_per_round INTEGER DEFAULT 0,
    status TEXT DEFAULT 'setup',
    advance_at TEXT,
    starting_points INTEGER DEFAULT 10,
    silent_silent INTEGER DEFAULT 1,
    snitch_silent INTEGER DEFAULT -1,
    silent_snitch INTEGER DEFAULT 5,
    snitch_snitch INTEGER DEFAULT 3,
    round_scored INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS players(
    id INTEGER PRIMARY KEY,
    game_id INTEGER,
    first_name TEXT,
    surname TEXT,
    score INTEGER DEFAULT 10,
    device_token TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS pairings(
    id INTEGER PRIMARY KEY,
    game_id INTEGER,
    round_no INTEGER,
    cycle_no INTEGER,
    pair_no INTEGER,
    player_a INTEGER NOT NULL,
    player_b INTEGER NOT NULL,
    choice_a TEXT,
    choice_b TEXT,
    resolved INTEGER DEFAULT 0,
    delta_a INTEGER,
    delta_b INTEGER,
    scored INTEGER DEFAULT 0
);
'''


def db():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript(SCHEMA)
        game_cols = {r['name'] for r in con.execute('PRAGMA table_info(games)')}
        additions = {
            'current_cycle': 'INTEGER DEFAULT 0',
            'cycles_per_round': 'INTEGER DEFAULT 0',
            'round_scored': 'INTEGER DEFAULT 0',
            'starting_points': 'INTEGER DEFAULT 10',
            'silent_silent': 'INTEGER DEFAULT 1',
            'snitch_silent': 'INTEGER DEFAULT -1',
            'silent_snitch': 'INTEGER DEFAULT 5',
            'snitch_snitch': 'INTEGER DEFAULT 3',
        }
        for name, definition in additions.items():
            if name not in game_cols:
                con.execute(f'ALTER TABLE games ADD COLUMN {name} {definition}')

        player_cols = {r['name'] for r in con.execute('PRAGMA table_info(players)')}
        if 'device_token' not in player_cols:
            con.execute('ALTER TABLE players ADD COLUMN device_token TEXT')
            rows = con.execute('SELECT id FROM players WHERE device_token IS NULL').fetchall()
            for row in rows:
                con.execute('UPDATE players SET device_token=? WHERE id=?', (secrets.token_urlsafe(18), row['id']))
        pairing_cols = {r['name'] for r in con.execute('PRAGMA table_info(pairings)')}
        if 'cycle_no' not in pairing_cols:
            con.execute('ALTER TABLE pairings ADD COLUMN cycle_no INTEGER DEFAULT 0')
        if 'scored' not in pairing_cols:
            con.execute('ALTER TABLE pairings ADD COLUMN scored INTEGER DEFAULT 0')


def game_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


def unique_token(con):
    while True:
        token = secrets.token_urlsafe(18)
        if not con.execute('SELECT 1 FROM players WHERE device_token=?', (token,)).fetchone():
            return token


def qr_data_uri(value):
    img = qrcode.make(value)
    out = BytesIO()
    img.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')


def game_or_404(c):
    with db() as con:
        g = con.execute('SELECT * FROM games WHERE code=?', (c.upper(),)).fetchone()
    if not g:
        abort(404)
    return g


def generate_round_robin(player_ids):
    """Return concurrent cycles covering every unique player pairing exactly once.

    Even player counts use n-1 cycles. Odd player counts use n cycles with one
    rotating bye per cycle. The None placeholder is never emitted as a pairing.
    """
    ids = list(player_ids)
    if len(ids) < 2:
        raise ValueError('At least two players are required.')
    random.shuffle(ids)
    if len(ids) % 2:
        ids.append(None)

    cycles = []
    arranged = ids[:]
    for _ in range(len(ids) - 1):
        pairs = []
        for i in range(len(ids) // 2):
            a = arranged[i]
            b = arranged[-1 - i]
            if a is None or b is None:
                continue
            if random.choice((True, False)):
                a, b = b, a
            pairs.append((a, b))
        random.shuffle(pairs)
        cycles.append(pairs)
        arranged = [arranged[0], arranged[-1]] + arranged[1:-1]
    random.shuffle(cycles)
    return cycles


def make_schedule(con, game_id):
    ids = [r['id'] for r in con.execute('SELECT id FROM players WHERE game_id=? ORDER BY id', (game_id,))]
    if len(ids) < 2:
        raise ValueError('At least two participants must join before the game can start.')
    game = con.execute('SELECT total_rounds FROM games WHERE id=?', (game_id,)).fetchone()
    first_cycles = generate_round_robin(ids)
    con.execute('UPDATE games SET cycles_per_round=? WHERE id=?', (len(first_cycles), game_id))
    for round_no in range(1, game['total_rounds'] + 1):
        round_cycles = first_cycles if round_no == 1 else generate_round_robin(ids)
        for cycle_no, pairs in enumerate(round_cycles):
            for pair_no, (a, b) in enumerate(pairs):
                con.execute(
                    '''INSERT INTO pairings(game_id,round_no,cycle_no,pair_no,player_a,player_b)
                       VALUES(?,?,?,?,?,?)''',
                    (game_id, round_no, cycle_no, pair_no, a, b),
                )


def calc(g, choice_a, choice_b):
    return {
        ('silent', 'silent'): (g['silent_silent'], g['silent_silent']),
        ('snitch', 'silent'): (g['snitch_silent'], g['silent_snitch']),
        ('silent', 'snitch'): (g['silent_snitch'], g['snitch_silent']),
        ('snitch', 'snitch'): (g['snitch_snitch'], g['snitch_snitch']),
    }[(choice_a, choice_b)]


def scoring_form():
    def adjustment(name, default):
        amount = max(0, int(request.form.get(name, abs(default))))
        return -amount if request.form.get(name + '_remove') == 'on' else amount
    return {
        'starting_points': max(0, int(request.form.get('starting_points', DEFAULT_STARTING_POINTS))),
        'silent_silent': adjustment('silent_silent', DEFAULT_SILENT_SILENT),
        'snitch_silent': adjustment('snitch_silent', DEFAULT_SNITCH_SILENT_SNITCH),
        'silent_snitch': adjustment('silent_snitch', DEFAULT_SNITCH_SILENT_SILENT),
        'snitch_snitch': adjustment('snitch_snitch', DEFAULT_SNITCH_SNITCH),
    }


def apply_resolved_pairing(con, g, pairing_id):
    p = con.execute('SELECT * FROM pairings WHERE id=?', (pairing_id,)).fetchone()
    if not p or not p['resolved'] or p['scored']:
        return False
    if p['delta_a'] is None or p['delta_b'] is None:
        da, dbb = calc(g, p['choice_a'], p['choice_b'])
        con.execute('UPDATE pairings SET delta_a=?,delta_b=? WHERE id=?', (da, dbb, pairing_id))
        p = con.execute('SELECT * FROM pairings WHERE id=?', (pairing_id,)).fetchone()
    con.execute('UPDATE players SET score=score+? WHERE id=?', (p['delta_a'], p['player_a']))
    con.execute('UPDATE players SET score=score+? WHERE id=?', (p['delta_b'], p['player_b']))
    con.execute('UPDATE pairings SET scored=1 WHERE id=? AND scored=0', (pairing_id,))
    return True


def cycle_pairings(con, g, cycle_no=None, round_no=None):
    rn = g['current_round'] if round_no is None else round_no
    cn = g['current_cycle'] if cycle_no is None else cycle_no
    return con.execute(
        'SELECT * FROM pairings WHERE game_id=? AND round_no=? AND cycle_no=? ORDER BY pair_no',
        (g['id'], rn, cn),
    ).fetchall()


def cycle_complete(pairings):
    return bool(pairings) and all(p['resolved'] for p in pairings)


def maybe_advance(con, g):
    if g['status'] != 'running' or not g['advance_at']:
        return
    if datetime.utcnow() < datetime.fromisoformat(g['advance_at']):
        return
    pairings = cycle_pairings(con, g)
    if not cycle_complete(pairings):
        return

    next_cycle = g['current_cycle'] + 1
    if next_cycle < g['cycles_per_round']:
        con.execute('UPDATE games SET current_cycle=?,advance_at=NULL WHERE id=?', (next_cycle, g['id']))
        return

    round_pairings = con.execute(
        'SELECT * FROM pairings WHERE game_id=? AND round_no=? ORDER BY cycle_no,pair_no',
        (g['id'], g['current_round']),
    ).fetchall()
    for p in round_pairings:
        if p['resolved'] and not p['scored']:
            apply_resolved_pairing(con, g, p['id'])

    if g['current_round'] >= g['total_rounds']:
        con.execute("UPDATE games SET status='finished',advance_at=NULL WHERE id=?", (g['id'],))
    else:
        con.execute(
            'UPDATE games SET current_round=?,current_cycle=0,advance_at=NULL, status="paused" WHERE id=?',
            (g['current_round'] + 1, g['id']),
        )


def player_name(con, player_id):
    p = con.execute('SELECT first_name,surname FROM players WHERE id=?', (player_id,)).fetchone()
    return f"{p['first_name']} {p['surname']}".strip()


def current_player_pairing(con, g, player_id):
    return con.execute(
        '''SELECT * FROM pairings
           WHERE game_id=? AND round_no=? AND cycle_no=?
             AND (player_a=? OR player_b=?)''',
        (g['id'], g['current_round'], g['current_cycle'], player_id, player_id),
    ).fetchone()


def admin_cycle_status(con, g, cycle_no=None, round_no=None):
    rn = g['current_round'] if round_no is None else round_no
    cn = g['current_cycle'] if cycle_no is None else cycle_no
    pairs = cycle_pairings(con, g, cn, rn)
    result = []
    for p in pairs:
        result.append({
            'a_name': player_name(con, p['player_a']),
            'b_name': player_name(con, p['player_b']),
            'a_submitted': bool(p['choice_a']),
            'b_submitted': bool(p['choice_b']),
            'resolved': bool(p['resolved']),
        })
    return result


def cycle_byes(con, g, cycle_no=None, round_no=None):
    rn = g['current_round'] if round_no is None else round_no
    cn = g['current_cycle'] if cycle_no is None else cycle_no
    paired_ids = set()
    for p in cycle_pairings(con, g, cn, rn):
        paired_ids.add(p['player_a'])
        paired_ids.add(p['player_b'])
    players = con.execute('SELECT id,first_name,surname FROM players WHERE game_id=? ORDER BY surname COLLATE NOCASE,first_name COLLATE NOCASE', (g['id'],)).fetchall()
    return [f"{p['first_name']} {p['surname']}".strip() for p in players if p['id'] not in paired_ids]


@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        rounds = max(1, min(50, int(request.form.get('rounds', 5))))
        scoring = scoring_form()
        c = game_code()
        with db() as con:
            while con.execute('SELECT 1 FROM games WHERE code=?', (c,)).fetchone():
                c = game_code()
            con.execute(
                '''INSERT INTO games(code,total_rounds,starting_points,silent_silent,snitch_silent,silent_snitch,snitch_snitch)
                   VALUES(?,?,?,?,?,?,?)''',
                (c, rounds, scoring['starting_points'], scoring['silent_silent'], scoring['snitch_silent'], scoring['silent_snitch'], scoring['snitch_snitch']),
            )
        return redirect(url_for('admin', c=c))
    return render_template('setup.html', **DEFAULT_SCORING)


@app.route('/play/<c>/join', methods=['GET', 'POST'])
def join_game(c):
    g = game_or_404(c)
    if g['status'] != 'setup':
        return render_template('join.html', g=g, closed=True)
    error = None
    entered_name = ''
    if request.method == 'POST':
        entered_name = ' '.join(request.form.get('name', '').split())
        if len(entered_name) < 2:
            error = 'Please enter your name.'
        elif len(entered_name) > 100:
            error = 'Please enter a shorter name.'
        else:
            parts = entered_name.split()
            first = ' '.join(parts[:-1]) if len(parts) > 1 else parts[0]
            surname = parts[-1] if len(parts) > 1 else ''
            with db() as con:
                duplicate = con.execute(
                    '''SELECT 1 FROM players WHERE game_id=?
                       AND lower(trim(first_name || ' ' || surname))=lower(?)''',
                    (g['id'], entered_name),
                ).fetchone()
                if duplicate:
                    error = 'That name is already registered. Ask the organiser if you need help reconnecting.'
                else:
                    token = unique_token(con)
                    con.execute(
                        'INSERT INTO players(game_id,first_name,surname,score,device_token) VALUES(?,?,?,?,?)',
                        (g['id'], first, surname, g['starting_points'], token),
                    )
                    return redirect(url_for('player_device', c=g['code'], token=token))
    return render_template('join.html', g=g, error=error, entered_name=entered_name, closed=False)


@app.route('/play/<c>/admin', methods=['GET', 'POST'])
def admin(c):
    g = game_or_404(c)
    error = request.args.get('error')
    with db() as con:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'start' and g['status'] == 'setup':
                count = con.execute('SELECT COUNT(*) AS n FROM players WHERE game_id=?', (g['id'],)).fetchone()['n']
                if count < 2:
                    return redirect(url_for('admin', c=c, error='At least two participants must join before the game can start.'))
                con.execute('DELETE FROM pairings WHERE game_id=?', (g['id'],))
                make_schedule(con, g['id'])
                con.execute("UPDATE games SET status='running',current_round=1,current_cycle=0,advance_at=NULL WHERE id=?", (g['id'],))
            elif action == 'remove_player' and g['status'] == 'setup':
                player_id = request.form.get('player_id', type=int)
                if player_id:
                    con.execute('DELETE FROM players WHERE id=? AND game_id=?', (player_id, g['id']))
            elif action == 'pause':
                con.execute("UPDATE games SET status='paused' WHERE id=?", (g['id'],))
            elif action == 'resume':
                con.execute("UPDATE games SET status='running' WHERE id=?", (g['id'],))
            elif action == 'reset':
                con.execute('DELETE FROM pairings WHERE game_id=?', (g['id'],))
                con.execute('UPDATE players SET score=? WHERE game_id=?', (g['starting_points'], g['id']))
                con.execute("UPDATE games SET current_round=1,current_cycle=0,cycles_per_round=0,status='setup',advance_at=NULL WHERE id=?", (g['id'],))
            return redirect(url_for('admin', c=c))

        g = con.execute('SELECT * FROM games WHERE id=?', (g['id'],)).fetchone()
        maybe_advance(con, g)
        g = con.execute('SELECT * FROM games WHERE id=?', (g['id'],)).fetchone()
        players = con.execute(
            'SELECT * FROM players WHERE game_id=? ORDER BY surname COLLATE NOCASE,first_name COLLATE NOCASE',
            (g['id'],),
        ).fetchall()
        current_status = admin_cycle_status(con, g) if g['status'] in ('running', 'paused') and g['cycles_per_round'] else []
        current_byes = cycle_byes(con, g) if current_status else []
        next_status = []
        next_byes = []
        next_round = g['current_round']
        next_cycle = g['current_cycle'] + 1
        if g['cycles_per_round'] and next_cycle >= g['cycles_per_round']:
            next_round += 1
            next_cycle = 0
        if next_round <= g['total_rounds'] and g['cycles_per_round']:
            next_status = admin_cycle_status(con, g, next_cycle, next_round)
            next_byes = cycle_byes(con, g, next_cycle, next_round)
        join_url = url_for('join_game', c=g['code'], _external=True)
        join_qr = qr_data_uri(join_url)
    return render_template(
        'admin.html', g=g, players=players, current_status=current_status,
        next_status=next_status, current_byes=current_byes, next_byes=next_byes,
        join_url=join_url, join_qr=join_qr, transition_seconds=DEVICE_TRANSITION_SECONDS,
        error=error,
    )


@app.route('/play/<c>/device/<token>', methods=['GET', 'POST'])
def player_device(c, token):
    g = game_or_404(c)
    with db() as con:
        player = con.execute('SELECT * FROM players WHERE game_id=? AND device_token=?', (g['id'], token)).fetchone()
        if not player:
            abort(404)
        maybe_advance(con, g)
        g = con.execute('SELECT * FROM games WHERE id=?', (g['id'],)).fetchone()
        if g['status'] == 'setup':
            return render_template('device.html', g=g, player=player, waiting=True, message='Registered — waiting for the organiser to start the game…')
        if g['status'] == 'paused':
            return render_template('device.html', g=g, player=player, waiting=True, message='Game paused…')
        if g['status'] == 'finished':
            return render_template('device.html', g=g, player=player, waiting=True, message='Game finished — check the scoreboard!')
        if g['status'] != 'running':
            return render_template('device.html', g=g, player=player, waiting=True, message='Waiting for the game…')

        p = current_player_pairing(con, g, player['id'])
        if not p:
            return render_template('device.html', g=g, player=player, waiting=True, message='Skip this match cycle — waiting for the next match…')
        field = 'choice_a' if p['player_a'] == player['id'] else 'choice_b'
        if request.method == 'POST' and not p[field]:
            choice = request.form.get('choice')
            if choice in ('silent', 'snitch'):
                con.execute(f'UPDATE pairings SET {field}=? WHERE id=? AND {field} IS NULL', (choice, p['id']))
                cycle_pairs = cycle_pairings(con, g)
                if cycle_pairs and all(rp['choice_a'] and rp['choice_b'] for rp in cycle_pairs):
                    for rp in cycle_pairs:
                        if not rp['resolved']:
                            da, dbb = calc(g, rp['choice_a'], rp['choice_b'])
                            con.execute(
                                'UPDATE pairings SET resolved=1,delta_a=?,delta_b=? WHERE id=? AND resolved=0',
                                (da, dbb, rp['id']),
                            )
                    existing = con.execute('SELECT advance_at FROM games WHERE id=?', (g['id'],)).fetchone()['advance_at']
                    if not existing:
                        con.execute(
                            'UPDATE games SET advance_at=? WHERE id=?',
                            ((datetime.utcnow() + timedelta(seconds=DEVICE_TRANSITION_SECONDS)).isoformat(), g['id']),
                        )
            return redirect(url_for('player_device', c=c, token=token))
        p = con.execute('SELECT * FROM pairings WHERE id=?', (p['id'],)).fetchone()
        submitted = bool(p[field])
        advance = bool(p['resolved'])
        opponent_id = p['player_b'] if p['player_a'] == player['id'] else p['player_a']
        opponent = con.execute('SELECT * FROM players WHERE id=?', (opponent_id,)).fetchone()
    return render_template('device.html', g=g, player=player, opponent=opponent, submitted=submitted, advance=advance, transition_seconds=DEVICE_TRANSITION_SECONDS)


@app.route('/play/<c>/scores')
def scores(c):
    g = game_or_404(c)
    with db() as con:
        maybe_advance(con, g)
        g = con.execute('SELECT * FROM games WHERE id=?', (g['id'],)).fetchone()
        players = con.execute(
            'SELECT * FROM players WHERE game_id=? ORDER BY score ASC, surname COLLATE NOCASE, first_name COLLATE NOCASE',
            (g['id'],),
        ).fetchall()
    return render_template('scores.html', g=g, players=players)


@app.route('/play/<c>/state')
def state(c):
    g = game_or_404(c)
    with db() as con:
        maybe_advance(con, g)
        g = con.execute('SELECT * FROM games WHERE id=?', (g['id'],)).fetchone()
    return jsonify(dict(g))


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
