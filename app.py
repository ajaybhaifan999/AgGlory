"""
ffglory.pro - Full Backend (Flask)
-----------------------------------
Features:
- Owner login (hardcoded): username "agajay official" / password "agajayofficial"
- Owner dashboard: manage pricing, view payments, view users, view guest accounts, push glory control
- User login (normal users register/login) -> User dashboard
- User adds guest account (FF UID + password) which is used to push glory
- Guild info: name, logo URL, level, members
- Payment system: user selects pricing plan, creates order, owner verifies/approves
- Real glory push: ff_runner.py handles actual FF API calls (plug your reverse-engineered code there)

Run:
    pip install -r requirements.txt
    python app.py
Deploy:
    Push to GitHub -> Render Web Service -> set env: FLASK_SECRET, FERNET_KEY
"""

import os, sqlite3, secrets, json, time, threading
from datetime import datetime
from functools import wraps
from flask import (Flask, request, jsonify, session, redirect,
                   url_for, render_template, send_from_directory, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet

# ---------- CONFIG ----------
OWNER_USERNAME = "agajay official"
OWNER_PASSWORD = "agajayofficial"

DB_PATH = os.environ.get("DB_PATH", "ffglory.db")
SECRET_KEY = os.environ.get("FLASK_SECRET", secrets.token_hex(32))
FERNET_KEY = os.environ.get("FERNET_KEY") or Fernet.generate_key().decode()
fernet = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = SECRET_KEY

# ---------- DB ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    cur = c.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS guest_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        ff_uid TEXT NOT NULL,
        ff_password_enc TEXT NOT NULL,
        nickname TEXT,
        level INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS guilds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        logo_url TEXT,
        level INTEGER DEFAULT 1,
        leader_uid TEXT,
        member_count INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS pricing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        glory_amount INTEGER NOT NULL,
        price INTEGER NOT NULL,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        plan_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        utr TEXT,
        status TEXT DEFAULT 'pending', -- pending/approved/rejected
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS glory_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        guest_id INTEGER NOT NULL,
        target_glory INTEGER NOT NULL,
        current_glory INTEGER DEFAULT 0,
        status TEXT DEFAULT 'queued', -- queued/running/done/failed
        log TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    # Seed default pricing if empty
    cur.execute("SELECT COUNT(*) FROM pricing")
    if cur.fetchone()[0] == 0:
        cur.executemany("INSERT INTO pricing(name,glory_amount,price) VALUES (?,?,?)", [
            ("Starter 1K Glory", 1000, 50),
            ("Pro 5K Glory", 5000, 200),
            ("Elite 10K Glory", 10000, 350),
        ])
    # Default UPI
    cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('upi_id','agajay@upi')")
    c.commit(); c.close()

init_db()

# ---------- AUTH HELPERS ----------
def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not session.get("user_id"):
            return redirect(url_for("login_page"))
        return f(*a, **kw)
    return w

def owner_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not session.get("is_owner"):
            return redirect(url_for("owner_login_page"))
        return f(*a, **kw)
    return w

# ---------- PUBLIC ROUTES ----------
@app.route("/")
def home():
    return redirect(url_for("login_page"))

@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    u = (data.get("username") or "").strip()
    p = data.get("password") or ""
    if len(u) < 3 or len(p) < 4:
        return jsonify(ok=False, error="Username min 3 / password min 4"), 400
    c = db()
    try:
        c.execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                  (u, generate_password_hash(p)))
        c.commit()
    except sqlite3.IntegrityError:
        return jsonify(ok=False, error="Username already taken"), 400
    finally:
        c.close()
    return jsonify(ok=True)

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    u = (data.get("username") or "").strip()
    p = data.get("password") or ""
    # Owner shortcut
    if u == OWNER_USERNAME and p == OWNER_PASSWORD:
        session.clear()
        session["is_owner"] = True
        session["owner_name"] = u
        return jsonify(ok=True, redirect="/owner")
    c = db()
    row = c.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    c.close()
    if not row or not check_password_hash(row["password_hash"], p):
        return jsonify(ok=False, error="Invalid credentials"), 401
    session.clear()
    session["user_id"] = row["id"]
    session["username"] = row["username"]
    return jsonify(ok=True, redirect="/dashboard")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ---------- USER DASHBOARD ----------
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session.get("username"))

@app.route("/api/me")
@login_required
def api_me():
    uid = session["user_id"]
    c = db()
    guests = [dict(r) for r in c.execute(
        "SELECT id,ff_uid,nickname,level,created_at FROM guest_accounts WHERE user_id=?", (uid,))]
    guild = c.execute("SELECT * FROM guilds WHERE user_id=?", (uid,)).fetchone()
    jobs = [dict(r) for r in c.execute(
        "SELECT * FROM glory_jobs WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,))]
    pricing = [dict(r) for r in c.execute("SELECT * FROM pricing WHERE active=1")]
    upi = c.execute("SELECT value FROM settings WHERE key='upi_id'").fetchone()["value"]
    c.close()
    return jsonify(
        username=session["username"],
        guests=guests,
        guild=dict(guild) if guild else None,
        jobs=jobs,
        pricing=pricing,
        upi=upi,
    )

@app.route("/api/guest", methods=["POST"])
@login_required
def api_add_guest():
    data = request.get_json() or {}
    uid = data.get("ff_uid","").strip()
    pw = data.get("ff_password","")
    nick = data.get("nickname","").strip()
    if not uid or not pw:
        return jsonify(ok=False, error="UID and password required"), 400
    enc = fernet.encrypt(pw.encode()).decode()
    c = db()
    c.execute("INSERT INTO guest_accounts(user_id,ff_uid,ff_password_enc,nickname) VALUES(?,?,?,?)",
              (session["user_id"], uid, enc, nick))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/guest/<int:gid>", methods=["DELETE"])
@login_required
def api_del_guest(gid):
    c = db()
    c.execute("DELETE FROM guest_accounts WHERE id=? AND user_id=?", (gid, session["user_id"]))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/guild", methods=["POST"])
@login_required
def api_save_guild():
    d = request.get_json() or {}
    c = db()
    existing = c.execute("SELECT id FROM guilds WHERE user_id=?", (session["user_id"],)).fetchone()
    if existing:
        c.execute("UPDATE guilds SET name=?,logo_url=?,level=?,leader_uid=?,member_count=? WHERE user_id=?",
                  (d.get("name"), d.get("logo_url"), int(d.get("level",1)),
                   d.get("leader_uid"), int(d.get("member_count",1)), session["user_id"]))
    else:
        c.execute("INSERT INTO guilds(user_id,name,logo_url,level,leader_uid,member_count) VALUES(?,?,?,?,?,?)",
                  (session["user_id"], d.get("name"), d.get("logo_url"),
                   int(d.get("level",1)), d.get("leader_uid"), int(d.get("member_count",1))))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/payment", methods=["POST"])
@login_required
def api_create_payment():
    d = request.get_json() or {}
    plan_id = int(d.get("plan_id",0))
    utr = (d.get("utr") or "").strip()
    if not utr:
        return jsonify(ok=False, error="UTR / transaction ref required"), 400
    c = db()
    plan = c.execute("SELECT * FROM pricing WHERE id=?", (plan_id,)).fetchone()
    if not plan:
        c.close(); return jsonify(ok=False, error="Invalid plan"), 400
    c.execute("INSERT INTO payments(user_id,plan_id,amount,utr) VALUES(?,?,?,?)",
              (session["user_id"], plan_id, plan["price"], utr))
    c.commit(); c.close()
    return jsonify(ok=True, message="Payment submitted. Wait for owner approval.")

# ---------- OWNER ----------
@app.route("/owner/login")
def owner_login_page():
    return render_template("login.html", owner=True)

@app.route("/owner")
@owner_required
def owner_dashboard():
    return render_template("owner.html", owner=session.get("owner_name"))

@app.route("/api/owner/stats")
@owner_required
def api_owner_stats():
    c = db()
    users = [dict(r) for r in c.execute("SELECT id,username,created_at FROM users ORDER BY id DESC")]
    guests = [dict(r) for r in c.execute("""
        SELECT g.id,g.ff_uid,g.nickname,g.level,u.username as owner
        FROM guest_accounts g JOIN users u ON u.id=g.user_id ORDER BY g.id DESC""")]
    payments = [dict(r) for r in c.execute("""
        SELECT p.*, u.username, pr.name as plan_name
        FROM payments p JOIN users u ON u.id=p.user_id
        JOIN pricing pr ON pr.id=p.plan_id ORDER BY p.id DESC""")]
    pricing = [dict(r) for r in c.execute("SELECT * FROM pricing")]
    jobs = [dict(r) for r in c.execute("""
        SELECT j.*, u.username FROM glory_jobs j
        JOIN users u ON u.id=j.user_id ORDER BY j.id DESC LIMIT 50""")]
    upi = c.execute("SELECT value FROM settings WHERE key='upi_id'").fetchone()["value"]
    c.close()
    return jsonify(users=users, guests=guests, payments=payments,
                   pricing=pricing, jobs=jobs, upi=upi)

@app.route("/api/owner/pricing", methods=["POST"])
@owner_required
def api_owner_pricing_save():
    d = request.get_json() or {}
    c = db()
    if d.get("id"):
        c.execute("UPDATE pricing SET name=?,glory_amount=?,price=?,active=? WHERE id=?",
                  (d["name"], int(d["glory_amount"]), int(d["price"]),
                   int(d.get("active",1)), int(d["id"])))
    else:
        c.execute("INSERT INTO pricing(name,glory_amount,price) VALUES(?,?,?)",
                  (d["name"], int(d["glory_amount"]), int(d["price"])))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/owner/pricing/<int:pid>", methods=["DELETE"])
@owner_required
def api_owner_pricing_del(pid):
    c = db(); c.execute("DELETE FROM pricing WHERE id=?", (pid,)); c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/owner/upi", methods=["POST"])
@owner_required
def api_owner_upi():
    d = request.get_json() or {}
    c = db()
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('upi_id',?)", (d.get("upi",""),))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/owner/payment/<int:pid>", methods=["POST"])
@owner_required
def api_owner_payment_action(pid):
    d = request.get_json() or {}
    action = d.get("action") # approved/rejected
    if action not in ("approved","rejected"):
        return jsonify(ok=False, error="bad action"), 400
    c = db()
    pay = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not pay:
        c.close(); return jsonify(ok=False, error="not found"), 404
    c.execute("UPDATE payments SET status=? WHERE id=?", (action, pid))
    # If approved -> create a glory job for the first guest of that user
    if action == "approved":
        plan = c.execute("SELECT * FROM pricing WHERE id=?", (pay["plan_id"],)).fetchone()
        guest = c.execute("SELECT id FROM guest_accounts WHERE user_id=? ORDER BY id LIMIT 1",
                          (pay["user_id"],)).fetchone()
        if guest and plan:
            c.execute("INSERT INTO glory_jobs(user_id,guest_id,target_glory) VALUES(?,?,?)",
                      (pay["user_id"], guest["id"], plan["glory_amount"]))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.route("/api/owner/job/<int:jid>/run", methods=["POST"])
@owner_required
def api_owner_run_job(jid):
    threading.Thread(target=run_glory_job, args=(jid,), daemon=True).start()
    return jsonify(ok=True, message="Job started")

# ---------- GLORY JOB RUNNER ----------
def run_glory_job(jid):
    try:
        from ff_runner import push_glory  # your real FF code
    except Exception:
        push_glory = None
    c = db()
    job = c.execute("SELECT * FROM glory_jobs WHERE id=?", (jid,)).fetchone()
    if not job: c.close(); return
    guest = c.execute("SELECT * FROM guest_accounts WHERE id=?", (job["guest_id"],)).fetchone()
    c.execute("UPDATE glory_jobs SET status='running', log=? WHERE id=?",
              ("Started at " + datetime.utcnow().isoformat(), jid))
    c.commit()
    try:
        ff_uid = guest["ff_uid"]
        ff_pw = fernet.decrypt(guest["ff_password_enc"].encode()).decode()
        if push_glory:
            # Real call -> your implementation in ff_runner.py
            result = push_glory(ff_uid, ff_pw, job["target_glory"])
            log = f"OK: {result}"
            current = job["target_glory"]
            status = "done"
        else:
            # Simulation fallback
            for i in range(1, 6):
                time.sleep(1)
            log = "Simulated push complete (ff_runner.push_glory missing)"
            current = job["target_glory"]
            status = "done"
        c.execute("UPDATE glory_jobs SET status=?, current_glory=?, log=? WHERE id=?",
                  (status, current, log, jid))
    except Exception as e:
        c.execute("UPDATE glory_jobs SET status='failed', log=? WHERE id=?",
                  (f"Error: {e}", jid))
    c.commit(); c.close()

# ---------- STATIC files (optional shared.css/js) ----------
@app.route("/static/<path:p>")
def static_proxy(p):
    return send_from_directory("static", p)

if __name__ == "__main__":
    print("FERNET_KEY (save this):", FERNET_KEY)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
