import os
import io
import csv
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template, g, Response

DATABASE = "/data/brewlog.db"
app = Flask(__name__)

# ── Ingress path stripping ────────────────────────────────────────────────────
@app.before_request
def strip_ingress_prefix():
    ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")
    if ingress_path and request.path.startswith(ingress_path):
        new_path = request.path[len(ingress_path):] or "/"
        request.environ["PATH_INFO"] = new_path

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
            rating           INTEGER DEFAULT NULL,
            taste_sour       INTEGER DEFAULT 0,
            taste_bitter     INTEGER DEFAULT 0,
            taste_weak       INTEGER DEFAULT 0,
            taste_strong     INTEGER DEFAULT 0,
            brewed_at        INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # Migrate existing brews table if columns missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(brews)").fetchall()]
    for col, defn in [
        ("rating",       "INTEGER DEFAULT NULL"),
        ("taste_sour",   "INTEGER DEFAULT 0"),
        ("taste_bitter", "INTEGER DEFAULT 0"),
        ("taste_weak",   "INTEGER DEFAULT 0"),
        ("taste_strong", "INTEGER DEFAULT 0"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE brews ADD COLUMN {col} {defn}")
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

def brew_to_dict(b):
    return {
        "id": b["id"], "product_id": b["product_id"],
        "product_name": b["product_name"], "brew_method": b["brew_method"],
        "coffee_weight_g": b["coffee_weight_g"], "water_weight_g": b["water_weight_g"],
        "grind_size": b["grind_size"], "brew_time_secs": b["brew_time_secs"],
        "brew_time_fmt": format_time(b["brew_time_secs"]),
        "notes": b["notes"],
        "rating": b["rating"],
        "taste_sour": b["taste_sour"], "taste_bitter": b["taste_bitter"],
        "taste_weak": b["taste_weak"], "taste_strong": b["taste_strong"],
        "brewed_at": b["brewed_at"],
        "brewed_at_rel": relative_time(b["brewed_at"]),
        "ratio": ratio_str(b["coffee_weight_g"], b["water_weight_g"]),
    }

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
        "brews": [brew_to_dict(b) for b in brews],
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
    return jsonify([brew_to_dict(b) for b in rows])

@app.route("/api/brews", methods=["POST"])
def add_brew():
    d = request.json
    db = get_db()
    now = int(datetime.now(timezone.utc).timestamp())
    cur = db.execute(
        """INSERT INTO brews
           (product_id, product_name, brew_method, coffee_weight_g, water_weight_g,
            grind_size, brew_time_secs, notes, rating,
            taste_sour, taste_bitter, taste_weak, taste_strong, brewed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d["product_id"], d["product_name"], d["brew_method"],
         d["coffee_weight_g"], d.get("water_weight_g", 0),
         d.get("grind_size",""), d.get("brew_time_secs", 0),
         d.get("notes","").strip(), d.get("rating"),
         int(bool(d.get("taste_sour"))), int(bool(d.get("taste_bitter"))),
         int(bool(d.get("taste_weak"))), int(bool(d.get("taste_strong"))),
         now)
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

@app.route("/api/suggest/<int:pid>", methods=["GET"])
def suggest(pid):
    """Return the best-rated brew params for this product."""
    db = get_db()
    # Best rated brews for this product (rating 4-5), most recent first
    rows = db.execute("""
        SELECT * FROM brews
        WHERE product_id=? AND rating IS NOT NULL AND rating >= 4
        ORDER BY rating DESC, brewed_at DESC
        LIMIT 5
    """, (pid,)).fetchall()
    if not rows:
        # Fall back to any rated brew
        rows = db.execute("""
            SELECT * FROM brews
            WHERE product_id=? AND rating IS NOT NULL
            ORDER BY rating DESC, brewed_at DESC
            LIMIT 3
        """, (pid,)).fetchall()
    if not rows:
        return jsonify({"suggestion": None})

    best = rows[0]
    # Summarise taste issues from lower-rated brews to give advice
    bad_rows = db.execute("""
        SELECT * FROM brews
        WHERE product_id=? AND rating IS NOT NULL AND rating <= 2
        ORDER BY brewed_at DESC LIMIT 10
    """, (pid,)).fetchall()

    tips = []
    if bad_rows:
        sour_count    = sum(1 for b in bad_rows if b["taste_sour"])
        bitter_count  = sum(1 for b in bad_rows if b["taste_bitter"])
        weak_count    = sum(1 for b in bad_rows if b["taste_weak"])
        strong_count  = sum(1 for b in bad_rows if b["taste_strong"])
        if sour_count >= 2:
            tips.append("Your recent brews taste sour — try a finer grind or longer brew time")
        if bitter_count >= 2:
            tips.append("Recent brews taste bitter — try a coarser grind or shorter brew time")
        if weak_count >= 2:
            tips.append("Recent brews taste weak — try increasing your dose")
        if strong_count >= 2:
            tips.append("Recent brews taste strong — try reducing your dose or adding more water")

    return jsonify({
        "suggestion": {
            "brew_method":     best["brew_method"],
            "coffee_weight_g": best["coffee_weight_g"],
            "water_weight_g":  best["water_weight_g"],
            "grind_size":      best["grind_size"],
            "brew_time_secs":  best["brew_time_secs"],
            "rating":          best["rating"],
            "ratio":           ratio_str(best["coffee_weight_g"], best["water_weight_g"]),
        },
        "tips": tips,
    })

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

@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    db = get_db()
    rows = db.execute("""
        SELECT
            b.id, b.product_name, p.brand,
            b.brew_method, b.coffee_weight_g, b.water_weight_g,
            b.grind_size, b.brew_time_secs, b.notes,
            b.rating, b.taste_sour, b.taste_bitter, b.taste_weak, b.taste_strong,
            datetime(b.brewed_at, 'unixepoch') as brewed_at
        FROM brews b
        LEFT JOIN products p ON p.id = b.product_id
        ORDER BY b.brewed_at DESC
    """).fetchall()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "id", "coffee", "brand", "method",
        "coffee_g", "water_g", "ratio", "grind_size",
        "brew_time", "notes", "rating",
        "sour", "bitter", "weak", "strong", "brewed_at"
    ])
    for r in rows:
        coffee_g = r["coffee_weight_g"] or 0
        water_g  = r["water_weight_g"] or 0
        ratio    = f"1:{water_g/coffee_g:.1f}" if coffee_g > 0 and water_g > 0 else ""
        mins, secs = divmod(r["brew_time_secs"] or 0, 60)
        writer.writerow([
            r["id"], r["product_name"], r["brand"] or "",
            r["brew_method"], coffee_g, water_g, ratio,
            r["grind_size"] or "", f"{mins}:{secs:02d}",
            r["notes"] or "", r["rating"] or "",
            "yes" if r["taste_sour"] else "",
            "yes" if r["taste_bitter"] else "",
            "yes" if r["taste_weak"] else "",
            "yes" if r["taste_strong"] else "",
            r["brewed_at"],
        ])
    csv_bytes = out.getvalue().encode("utf-8")
    filename = f"brewlog_export_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

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
