import os
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template, g, abort

DATABASE = "/data/brewlog.db"
app = Flask(__name__)

# ── Auth ──────────────────────────────────────────────────────────────────────
def require_auth(f):
    """
    Verify request arrives via HA ingress proxy.
    HA always injects X-Ingress-Path on legitimate ingress requests.
    Direct external access without going through HA will lack this header.
    """
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        # Allow request if it came through HA ingress (header present)
        # or if running on local network (no Ingress-Path means direct access
        # which is only possible from inside the Docker network anyway)
        ingress_path = request.headers.get("X-Ingress-Path")
        ha_source    = request.headers.get("X-Forwarded-For") or \
                       request.headers.get("X-Real-IP")
        # If neither header is present, reject — requires ingress proxy
        if ingress_path is None and ha_source is None:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            brand         TEXT DEFAULT '',
            photo_data    TEXT,
            roast_date    TEXT,
            purchase_date TEXT,
            notes         TEXT DEFAULT '',
            created_at    INTEGER DEFAULT (strftime('%s','now')),
            last_brewed_at INTEGER,
            brew_count    INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS brews (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            product_name     TEXT NOT NULL,
            brew_method      TEXT NOT NULL,
            coffee_weight_g  REAL NOT NULL,
            water_weight_g   REAL DEFAULT 0,
            grind_size       TEXT DEFAULT '',
            brew_time_secs   INTEGER DEFAULT 0,
            notes            TEXT DEFAULT '',
            brewed_at        INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()

# ── Helpers ───────────────────────────────────────────────────────────────────
def relative_time(ts):
    if not ts:
        return None
    now = int(datetime.now(timezone.utc).timestamp())
    diff = now - int(ts)
    hours = diff // 3600
    days  = diff // 86400
    if hours < 1:   return "Just now"
    if hours < 24:  return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if days == 1:   return "Yesterday"
    if days < 7:    return f"{days} days ago"
    return datetime.fromtimestamp(int(ts)).strftime("%-d %b")

def format_time(secs):
    if not secs: return "00:00"
    return f"{int(secs)//60:02d}:{int(secs)%60:02d}"

def ratio_str(coffee, water):
    try:
        if float(coffee) > 0 and float(water) > 0:
            return f"1:{float(water)/float(coffee):.1f}"
    except Exception:
        pass
    return "—"

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/products", methods=["GET"])
def get_products():
    rows = get_db().execute(
        "SELECT * FROM products ORDER BY last_brewed_at DESC NULLS LAST, created_at DESC"
    ).fetchall()
    return jsonify([{
        "id": r["id"], "name": r["name"], "brand": r["brand"],
        "photo_data": r["photo_data"], "roast_date": r["roast_date"],
        "purchase_date": r["purchase_date"], "notes": r["notes"],
        "brew_count": r["brew_count"], "last_brewed_at": r["last_brewed_at"],
        "last_brewed_rel": relative_time(r["last_brewed_at"]),
    } for r in rows])

@app.route("/api/products/<int:pid>", methods=["GET"])
def get_product(pid):
    db = get_db()
    r = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r: return jsonify({"error": "Not found"}), 404
    brews = db.execute(
        "SELECT * FROM brews WHERE product_id=? ORDER BY brewed_at DESC", (pid,)
    ).fetchall()
    return jsonify({
        "id": r["id"], "name": r["name"], "brand": r["brand"],
        "photo_data": r["photo_data"], "roast_date": r["roast_date"],
        "purchase_date": r["purchase_date"], "notes": r["notes"],
        "brew_count": r["brew_count"],
        "last_brewed_rel": relative_time(r["last_brewed_at"]),
        "brews": [{
            "id": b["id"], "brew_method": b["brew_method"],
            "coffee_weight_g": b["coffee_weight_g"], "water_weight_g": b["water_weight_g"],
            "grind_size": b["grind_size"], "brew_time_secs": b["brew_time_secs"],
            "brew_time_fmt": format_time(b["brew_time_secs"]),
            "notes": b["notes"], "brewed_at": b["brewed_at"],
            "brewed_at_rel": relative_time(b["brewed_at"]),
            "ratio": ratio_str(b["coffee_weight_g"], b["water_weight_g"]),
        } for b in brews],
    })

@app.route("/api/products", methods=["POST"])
def add_product():
    d = request.json
    db = get_db()
    cur = db.execute(
        "INSERT INTO products (name,brand,photo_data,roast_date,purchase_date,notes) VALUES (?,?,?,?,?,?)",
        (d.get("name","").strip(), d.get("brand","").strip(),
         d.get("photo_data"), d.get("roast_date"), d.get("purchase_date"),
         d.get("notes","").strip())
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201

@app.route("/api/products/<int:pid>", methods=["PUT"])
def update_product(pid):
    d = request.json
    db = get_db()
    db.execute(
        "UPDATE products SET name=?,brand=?,photo_data=?,roast_date=?,purchase_date=?,notes=? WHERE id=?",
        (d.get("name","").strip(), d.get("brand","").strip(),
         d.get("photo_data"), d.get("roast_date"), d.get("purchase_date"),
         d.get("notes","").strip(), pid)
    )
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/products/<int:pid>", methods=["DELETE"])
def delete_product(pid):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/brews", methods=["GET"])
def get_brews():
    limit = request.args.get("limit", 20, type=int)
    rows = get_db().execute(
        "SELECT * FROM brews ORDER BY brewed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return jsonify([{
        "id": b["id"], "product_id": b["product_id"], "product_name": b["product_name"],
        "brew_method": b["brew_method"], "coffee_weight_g": b["coffee_weight_g"],
        "water_weight_g": b["water_weight_g"], "grind_size": b["grind_size"],
        "brew_time_secs": b["brew_time_secs"], "brew_time_fmt": format_time(b["brew_time_secs"]),
        "notes": b["notes"], "brewed_at": b["brewed_at"],
        "brewed_at_rel": relative_time(b["brewed_at"]),
        "ratio": ratio_str(b["coffee_weight_g"], b["water_weight_g"]),
    } for b in rows])

@app.route("/api/brews", methods=["POST"])
def add_brew():
    d = request.json
    db = get_db()
    now = int(datetime.now(timezone.utc).timestamp())
    cur = db.execute(
        """INSERT INTO brews
           (product_id,product_name,brew_method,coffee_weight_g,water_weight_g,
            grind_size,brew_time_secs,notes,brewed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (d["product_id"], d["product_name"], d["brew_method"],
         d["coffee_weight_g"], d.get("water_weight_g", 0),
         d.get("grind_size",""), d.get("brew_time_secs", 0),
         d.get("notes","").strip(), now)
    )
    db.execute(
        "UPDATE products SET last_brewed_at=?, brew_count=brew_count+1 WHERE id=?",
        (now, d["product_id"])
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201

@app.route("/api/brews/<int:bid>", methods=["DELETE"])
def delete_brew(bid):
    db = get_db()
    db.execute("DELETE FROM brews WHERE id=?", (bid,))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    db = get_db()
    if request.method == "POST":
        for k, v in request.json.items():
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(v)))
        db.commit()
        return jsonify({"ok": True})
    rows = db.execute("SELECT key,value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})

@app.route("/api/clear", methods=["POST"])
def clear_all():
    db = get_db()
    db.execute("DELETE FROM brews")
    db.execute("DELETE FROM products")
    db.commit()
    return jsonify({"ok": True})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8099, debug=False)
