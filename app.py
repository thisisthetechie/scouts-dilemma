import os, random, sqlite3, string, base64
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify
from dotenv import load_dotenv
import qrcode

load_dotenv()

app = Flask(__name__)
DB = os.environ.get('SCOUT_DB', os.path.join(os.path.dirname(__file__), 'game.db'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, code TEXT UNIQUE, total_rounds INTEGER, current_round INTEGER DEFAULT 1, current_pair INTEGER DEFAULT 0, status TEXT DEFAULT 'setup', reveal_round INTEGER DEFAULT 0, advance_at TEXT);
CREATE TABLE IF NOT EXISTS players(id INTEGER PRIMARY KEY, game_id INTEGER, first_name TEXT, surname TEXT, score INTEGER DEFAULT 10);
CREATE TABLE IF NOT EXISTS pairings(id INTEGER PRIMARY KEY, game_id INTEGER, round_no INTEGER, pair_no INTEGER, player_a INTEGER NOT NULL, player_b INTEGER NOT NULL, choice_a TEXT, choice_b TEXT, resolved INTEGER DEFAULT 0, delta_a INTEGER, delta_b INTEGER);
'''

def db():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row; return con

def init_db():
    with db() as c: c.executescript(SCHEMA)

def code(): return ''.join(random.choices(string.ascii_uppercase+string.digits,k=6))

def qr_data_uri(value):
    img = qrcode.make(value)
    out = BytesIO()
    img.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')

def game_or_404(c):
    with db() as con: g=con.execute('SELECT * FROM games WHERE code=?',(c.upper(),)).fetchone()
    if not g: abort(404)
    return g

def make_round(con, game_id, round_no):
    ids=[r['id'] for r in con.execute('SELECT id FROM players WHERE game_id=?',(game_id,))]
    random.shuffle(ids)
    pair_no=0
    while len(ids)>=2:
        a=ids.pop(); b=ids.pop()
        con.execute('INSERT INTO pairings(game_id,round_no,pair_no,player_a,player_b) VALUES(?,?,?,?,?)',(game_id,round_no,pair_no,a,b)); pair_no+=1

def calc(a,b):
    return {('silent','silent'):(1,1),('snitch','silent'):(-1,5),('silent','snitch'):(5,-1),('snitch','snitch'):(3,3)}[(a,b)]

def maybe_advance(con,g):
    if not g['advance_at']: return
    if datetime.utcnow() < datetime.fromisoformat(g['advance_at']): return
    rows=con.execute('SELECT * FROM pairings WHERE game_id=? AND round_no=? ORDER BY pair_no',(g['id'],g['current_round'])).fetchall()
    nxt=g['current_pair']+1
    if nxt < len(rows):
        con.execute('UPDATE games SET current_pair=?, advance_at=NULL WHERE id=?',(nxt,g['id']))
    else:
        # Commit all resolved deltas at the end of the round, then reveal scoreboard.
        for p in rows:
            if p['resolved']:
                con.execute('UPDATE players SET score=score+? WHERE id=?',(p['delta_a'],p['player_a']))
                con.execute('UPDATE players SET score=score+? WHERE id=?',(p['delta_b'],p['player_b']))
        if g['current_round'] >= g['total_rounds']:
            con.execute("UPDATE games SET status='finished', reveal_round=?, advance_at=NULL WHERE id=?",(g['current_round'],g['id']))
        else:
            nr=g['current_round']+1
            con.execute('UPDATE games SET current_round=?, current_pair=0, reveal_round=?, advance_at=NULL WHERE id=?',(nr,g['current_round'],g['id']))


def pairing_view(con, pairing):
    if not pairing:
        return None
    a=con.execute('SELECT first_name,surname FROM players WHERE id=?',(pairing['player_a'],)).fetchone()
    b=con.execute('SELECT first_name,surname FROM players WHERE id=?',(pairing['player_b'],)).fetchone()
    return {
        'round_no': pairing['round_no'], 'pair_no': pairing['pair_no'],
        'a_name': f"{a['first_name']} {a['surname']}".strip(),
        'b_name': f"{b['first_name']} {b['surname']}".strip(),
        'a_submitted': bool(pairing['choice_a']), 'b_submitted': bool(pairing['choice_b']),
        'resolved': bool(pairing['resolved'])
    }

def get_admin_pairing_status(con, g):
    if g['status'] not in ('running','paused'):
        return {'display': None, 'up_next': None}
    current=con.execute('SELECT * FROM pairings WHERE game_id=? AND round_no=? AND pair_no=?',(g['id'],g['current_round'],g['current_pair'])).fetchone()
    next_pair=con.execute('SELECT * FROM pairings WHERE game_id=? AND (round_no>? OR (round_no=? AND pair_no>?)) ORDER BY round_no,pair_no LIMIT 1',(g['id'],g['current_round'],g['current_round'],g['current_pair'])).fetchone()
    display=current
    # Ten seconds after resolution, prepare the admin display for the next pairing while devices keep their 30s wait.
    if current and current['resolved'] and g['advance_at']:
        resolved_at=datetime.fromisoformat(g['advance_at'])-timedelta(seconds=30)
        if datetime.utcnow() >= resolved_at+timedelta(seconds=10) and next_pair:
            display=next_pair
            next_pair=con.execute('SELECT * FROM pairings WHERE game_id=? AND (round_no>? OR (round_no=? AND pair_no>?)) ORDER BY round_no,pair_no LIMIT 1',(g['id'],display['round_no'],display['round_no'],display['pair_no'])).fetchone()
    return {'display': pairing_view(con,display), 'up_next': pairing_view(con,next_pair)}

def current_pair(con,g):
    maybe_advance(con,g)
    g=con.execute('SELECT * FROM games WHERE id=?',(g['id'],)).fetchone()
    return g, con.execute('SELECT * FROM pairings WHERE game_id=? AND round_no=? AND pair_no=?',(g['id'],g['current_round'],g['current_pair'])).fetchone()

@app.route('/',methods=['GET','POST'])
def home():
    if request.method=='POST':
        rounds=max(1,min(50,int(request.form.get('rounds',5))))
        names=[x.strip() for x in request.form.get('participants','').splitlines() if x.strip()]
        if len(names)<2: return render_template('setup.html',error='Please enter at least two participants.', participants=request.form.get('participants',''), rounds=rounds)
        if len(names) % 2: return render_template('setup.html',error='Please enter an even number of participants. Byes are not supported.', participants=request.form.get('participants',''), rounds=rounds)
        c=code()
        with db() as con:
            cur=con.execute('INSERT INTO games(code,total_rounds) VALUES(?,?)',(c,rounds)); gid=cur.lastrowid
            for n in names:
                parts=n.split(); first=' '.join(parts[:-1]) if len(parts)>1 else parts[0]; sur=parts[-1] if len(parts)>1 else ''
                con.execute('INSERT INTO players(game_id,first_name,surname) VALUES(?,?,?)',(gid,first,sur))
        return redirect(url_for('admin',c=c))
    return render_template('setup.html')

@app.route('/play/<c>/admin',methods=['GET','POST'])
def admin(c):
    g=game_or_404(c)
    with db() as con:
        if request.method=='POST':
            action=request.form.get('action')
            if action=='start' and g['status']=='setup':
                # Pre-generate every round. This makes round transitions deterministic and lets admin preview across round boundaries.
                for round_no in range(1, g['total_rounds'] + 1): make_round(con,g['id'],round_no)
                con.execute("UPDATE games SET status='running' WHERE id=?",(g['id'],))
            elif action=='pause': con.execute("UPDATE games SET status='paused' WHERE id=?",(g['id'],))
            elif action=='resume': con.execute("UPDATE games SET status='running' WHERE id=?",(g['id'],))
            elif action=='reset':
                con.execute('DELETE FROM pairings WHERE game_id=?',(g['id'],)); con.execute('UPDATE players SET score=10 WHERE game_id=?',(g['id'],)); con.execute("UPDATE games SET current_round=1,current_pair=0,status='setup',reveal_round=0,advance_at=NULL WHERE id=?",(g['id'],))
            return redirect(url_for('admin',c=c))
        g=con.execute('SELECT * FROM games WHERE id=?',(g['id'],)).fetchone(); maybe_advance(con,g); g=con.execute('SELECT * FROM games WHERE id=?',(g['id'],)).fetchone()
        players=con.execute('SELECT * FROM players WHERE game_id=? ORDER BY surname,first_name',(g['id'],)).fetchall()
        pairing_status = get_admin_pairing_status(con, g)
    device_urls = {d: url_for('device', c=g['code'], d=d, _external=True) for d in (1, 2)}
    device_qrs = {d: qr_data_uri(device_urls[d]) for d in (1, 2)}
    return render_template('admin.html',g=g,players=players,pairing_status=pairing_status,device_urls=device_urls,device_qrs=device_qrs)

@app.route('/play/<c>/device/<int:d>',methods=['GET','POST'])
def device(c,d):
    if d not in (1,2): abort(404)
    g=game_or_404(c)
    with db() as con:
        g,p=current_pair(con,g)
        if g['status']!='running' or not p: return render_template('device.html',g=g,d=d,waiting=True)
        pid=p['player_a'] if d==1 else p['player_b']
        player=con.execute('SELECT * FROM players WHERE id=?',(pid,)).fetchone(); field='choice_a' if d==1 else 'choice_b'
        if request.method=='POST' and not p[field]:
            choice=request.form.get('choice')
            if choice in ('silent','snitch'):
                con.execute(f'UPDATE pairings SET {field}=? WHERE id=?',(choice,p['id']))
                p=con.execute('SELECT * FROM pairings WHERE id=?',(p['id'],)).fetchone()
                if p['choice_a'] and p['choice_b'] and not p['resolved']:
                    da,dbb=calc(p['choice_a'],p['choice_b'])
                    con.execute('UPDATE pairings SET resolved=1,delta_a=?,delta_b=? WHERE id=?',(da,dbb,p['id']))
                    con.execute('UPDATE games SET advance_at=? WHERE id=?',((datetime.utcnow()+timedelta(seconds=30)).isoformat(),g['id']))
            return redirect(url_for('device',c=c,d=d))
        p=con.execute('SELECT * FROM pairings WHERE id=?',(p['id'],)).fetchone()
        submitted=bool(p[field]); advance=p['resolved']
    return render_template('device.html',g=g,d=d,player=player,submitted=submitted,advance=advance)

@app.route('/play/<c>/scores')
def scores(c):
    g=game_or_404(c)
    with db() as con:
        maybe_advance(con,g); g=con.execute('SELECT * FROM games WHERE id=?',(g['id'],)).fetchone()
        players=con.execute('SELECT * FROM players WHERE game_id=? ORDER BY score ASC, surname COLLATE NOCASE, first_name COLLATE NOCASE',(g['id'],)).fetchall()
    return render_template('scores.html',g=g,players=players)

@app.route('/play/<c>/state')
def state(c):
    g=game_or_404(c)
    with db() as con:
        maybe_advance(con,g); g=con.execute('SELECT * FROM games WHERE id=?',(g['id'],)).fetchone()
    return jsonify(dict(g))

if __name__=='__main__':
    init_db(); app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)
