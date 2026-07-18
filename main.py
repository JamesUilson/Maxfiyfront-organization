# -*- coding: utf-8 -*-
"""
OPERATSIYA «SOYA» — SHTAB BOSHQARUV PANELI  v2
FastAPI + SQLite. Rollar: ADMIN / KOORDINATOR (nuqtaga biriktirilgan) / MENTOR.
- START ni jamoaning 1-nuqtasidagi KOORDINATOR beradi (jamoa yetib kelganda).
- Admin markazda: jonli XARITA, to'liq nazorat, tuzatish, jurnal.
Ishga tushirish:  uvicorn main:app --host 0.0.0.0 --port 8100
"""
import sqlite3, time, json, os, csv, io, zipfile
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse

# ================= SOZLAMALAR =================
ADMIN_PIN  = "2026"
KOORD_PIN  = "1122"
MENTOR_PIN = "3344"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soya.db")

FINISH = "FINISH · Bolalar eko maydonchasi"
START_PT = "Administratsiya"

TEAMS_SEED = [
    ("burgut", "BURGUT",  "#7a1f1f", ["Befit Eco", "Kuzatuv maydonchasi", "Ochiq trenajyorlar", FINISH]),
    ("lochin", "LOCHIN",  "#1f3a6e", ["Pirs", "Amfiteatr", "Yoga terassasi", FINISH]),
    ("qalqon", "QALQON",  "#144d33", ["Favvora", "Piknik zonasi", "Kuzatuv maydonchasi", FINISH]),
    ("tulpor", "TULPOR",  "#5c3a10", ["Amfiteatr", "Ochiq trenajyorlar", "Befit Eco", FINISH]),
    ("mashal", "MASH'AL", "#5a1a4a", ["Yoga terassasi", "Pirs", "Piknik zonasi", FINISH]),
]

# Xarita koordinatalari (rasmning % nuqtalari: x — chapdan, y — tepadan)
# v4: rasm PIRS.png ga almashtirildi (butunlay boshqa rakurs) — quyidagi raqamlar
# admin XARITA bo'limida rasm ustiga bosib aniqlangan HAQIQIY koordinatalar
# (2026-07-19 da joyida kalibrlangan). Favvora rasmda alohida belgilanmagan —
# hali taxminiy, kerak bo'lsa joyida aniqlab shu yerga yozing.
MAP_XY = {
    START_PT:                 (54.7, 55.1),   # Administratsiya
    "Befit Eco":              (47.5, 38.8),
    "Kuzatuv maydonchasi":    (47.5, 22.5),
    "Pirs":                   (75.9, 57.9),
    "Amfiteatr":              (49.7, 47.7),
    "Favvora":                (31.5, 19.0),   # taxminiy — rasmda yorlig'i yo'q, kalibrlang
    "Yoga terassasi":         (35.7, 38.7),
    "Piknik zonasi":          (38.2, 26.4),
    "Ochiq trenajyorlar":     (61.4, 53.6),
    FINISH:                   (38.2, 17.9),   # Bolalar eko (sport) maydonchasi
}
# Koordinator tanlaydigan nuqtalar
KOORD_LOCS = ["Befit Eco", "Kuzatuv maydonchasi", "Pirs", "Amfiteatr", "Favvora",
              "Yoga terassasi", "Piknik zonasi", "Ochiq trenajyorlar", FINISH]

COIN_ARRIVE = 1
COIN_KAZUS  = 1
COIN_HINT   = -1
COIN_TRAITOR_OK = 3
COIN_TRAITOR_NO = -1

app = FastAPI(title="SOYA Shtab Paneli")

# ================= DB =================
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS teams(
        id TEXT PRIMARY KEY, name TEXT, color TEXT, route TEXT);
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id TEXT, type TEXT, stage INTEGER, delta INTEGER,
        note TEXT, actor TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS coordinators(
        token TEXT PRIMARY KEY, loc TEXT, last_seen REAL);
    """)
    if c.execute("SELECT COUNT(*) n FROM teams").fetchone()["n"] == 0:
        for tid, name, color, route in TEAMS_SEED:
            c.execute("INSERT INTO teams VALUES(?,?,?,?)",
                      (tid, name, color, json.dumps(route, ensure_ascii=False)))
    c.commit(); c.close()

init_db()

# ================= AUTH =================
def role_of(request: Request):
    pin = request.headers.get("X-Pin", "")
    if pin == ADMIN_PIN:  return "admin"
    if pin == KOORD_PIN:  return "koordinator"
    if pin == MENTOR_PIN: return "mentor"
    return None

def require(request: Request, roles):
    r = role_of(request)
    if r not in roles:
        raise HTTPException(403, "Ruxsat yo'q")
    return r

# ================= HOLAT =================
def team_state(team, events, now):
    route = json.loads(team["route"])
    st = {
        "id": team["id"], "name": team["name"], "color": team["color"],
        "route": route, "coins": 0, "status": "kutmoqda",
        "stage": 1, "start_ts": None, "finish_ts": None,
        "total_sec": 0, "leg_sec": 0, "leg_label": "",
        "stages": [], "hints": 0, "traitor": None,
    }
    stages = [{"n": i+1, "name": route[i], "arrive": None, "depart": None,
               "kazus": False} for i in range(4)]
    for e in events:
        st["coins"] += e["delta"]
        t = e["type"]
        if t == "start":
            st["start_ts"] = e["ts"]
            st["status"] = "yolda"  # admin umumiy START berdi — jamoa 1-nuqtaga yo'lda
        elif t == "arrive":
            stages[e["stage"]-1]["arrive"] = e["ts"]
            st["status"] = "nuqtada"; st["stage"] = e["stage"]
        elif t == "depart":
            stages[e["stage"]-1]["depart"] = e["ts"]
            st["status"] = "yolda"; st["stage"] = min(4, e["stage"]+1)
        elif t == "kazus":
            stages[e["stage"]-1]["kazus"] = True
        elif t == "hint":
            st["hints"] += 1
        elif t == "traitor_ok":
            st["traitor"] = True
        elif t == "traitor_no":
            st["traitor"] = False
        elif t == "finish":
            st["finish_ts"] = e["ts"]; st["status"] = "yakunlandi"
    prev = st["start_ts"]
    for s in stages:
        item = {"n": s["n"], "name": s["name"], "kazus": s["kazus"],
                "arrived": s["arrive"] is not None,
                "departed": s["depart"] is not None,
                "travel_sec": None, "stay_sec": None}
        if s["arrive"] and prev:
            item["travel_sec"] = int(s["arrive"] - prev)
        if s["arrive"]:
            end = s["depart"] or (st["finish_ts"] if s["n"] == 4 else None) or now
            item["stay_sec"] = int(end - s["arrive"])
        prev = s["depart"] or s["arrive"] or prev
        st["stages"].append(item)
    if st["start_ts"]:
        end = st["finish_ts"] or now
        st["total_sec"] = int(end - st["start_ts"])
    if st["status"] == "yolda":
        last = st["start_ts"] or now
        for s in stages:
            if s["depart"]: last = max(last, s["depart"])
        st["leg_sec"] = int(now - last)
        st["leg_label"] = str(st["stage"]) + "-nuqtaga yolda"
    elif st["status"] == "nuqtada":
        s = stages[st["stage"]-1]
        if s["arrive"]:
            st["leg_sec"] = int(now - s["arrive"])
            st["leg_label"] = str(st["stage"]) + "-nuqtada"
    st["expected_loc"] = route[st["stage"]-1]
    return st

def full_state():
    now = time.time()
    c = db()
    teams = c.execute("SELECT * FROM teams").fetchall()
    out = []
    for t in teams:
        evs = c.execute("SELECT * FROM events WHERE team_id=? ORDER BY id",
                        (t["id"],)).fetchall()
        out.append(team_state(t, evs, now))
    log = c.execute(
        "SELECT e.*, t.name tname FROM events e JOIN teams t ON t.id=e.team_id "
        "ORDER BY e.id DESC LIMIT 50").fetchall()
    c.close()
    board = sorted(out, key=lambda s: (-s["coins"],
                   s["total_sec"] if s["start_ts"] else 10**9))
    starts = [s["start_ts"] for s in out if s["start_ts"]]
    return {"now": now, "teams": out, "board": [s["id"] for s in board],
            "log": [dict(r) for r in log],
            "map_xy": MAP_XY, "koord_locs": KOORD_LOCS,
            "start_pt": START_PT, "finish_pt": FINISH,
            "global_start_ts": min(starts) if starts else None,
            "all_started": len(starts) == len(out)}

LABELS = {
    "start": "START — jamoa yo'lga tushdi", "arrive": "Hududga yetib keldi",
    "kazus": "Kazus to'g'ri yechildi", "depart": "Keyingi nuqtaga yo'lga chiqdi",
    "hint": "Mentor ishorasi olindi", "traitor_ok": "Xoin TO'G'RI topildi",
    "traitor_no": "Xoin topilmadi", "finish": "Operatsiya yakunlandi",
    "adjust": "Qo'lda tuzatish",
}

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    pin = body.get("pin", "")
    for p, r in ((ADMIN_PIN, "admin"), (KOORD_PIN, "koordinator"), (MENTOR_PIN, "mentor")):
        if pin == p:
            return {"role": r, "locs": KOORD_LOCS}
    raise HTTPException(401, "PIN noto'g'ri")

@app.get("/api/state")
async def state(request: Request):
    require(request, ("admin", "koordinator", "mentor"))
    return JSONResponse(full_state())

@app.post("/api/action")
async def action(request: Request):
    role = require(request, ("admin", "koordinator", "mentor"))
    body = await request.json()
    tid, act = body.get("team"), body.get("action")
    delta = 0
    c = db()

    # koordinatorning nuqtasi FAQAT serverdagi bog'lanishdan olinadi (client
    # yuborgan qiymatga ishonilmaydi) — shu bilan "o'z lokatsiyasini o'zi
    # o'zgartira olmaydi" qoidasi serverda ham ta'minlanadi.
    coord_loc = None
    if role == "koordinator":
        ctoken = request.headers.get("X-Coord-Token", "")
        crow = c.execute("SELECT loc FROM coordinators WHERE token=?",
                          (ctoken,)).fetchone()
        coord_loc = crow["loc"] if crow else None
        if ctoken:
            c.execute("UPDATE coordinators SET last_seen=? WHERE token=?",
                      (time.time(), ctoken))
    loc = coord_loc
    team = c.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
    if not team:
        c.close(); raise HTTPException(404, "Jamoa topilmadi")
    evs = c.execute("SELECT * FROM events WHERE team_id=? ORDER BY id",
                    (tid,)).fetchall()
    st = team_state(team, evs, time.time())
    stage = st["stage"]

    # rol cheklovlari
    if act in ("adjust", "undo", "reset_all") and role != "admin":
        c.close(); raise HTTPException(403, "Faqat shtab (admin)")
    if act == "hint" and role not in ("admin", "mentor"):
        c.close(); raise HTTPException(403, "Faqat mentor")
    if act == "start" and role != "admin":
        c.close(); raise HTTPException(
            403, "START faqat admin orqali beriladi (umumiy START tugmasi)")
    if act in ("arrive", "kazus", "depart", "traitor_ok", "traitor_no",
               "finish") and role not in ("admin", "koordinator"):
        c.close(); raise HTTPException(403, "Faqat koordinator")

    # koordinator faqat O'Z nuqtasidagi jamoa bilan ishlaydi
    if role == "koordinator" and act in ("arrive", "kazus", "depart",
                                          "traitor_ok", "traitor_no", "finish"):
        if not loc:
            c.close(); raise HTTPException(
                400, "Nuqtangiz hali tanlanmagan yoki admin tomonidan bog'lanmagan")
        if st["expected_loc"] != loc:
            c.close(); raise HTTPException(
                400, "Bu jamoa sizning nuqtangizga tegishli emas")

    if act == "start":
        # BARCHA jamoalar eshik oldidan birga yo'lga tushadi — odatda
        # /api/admin/start_all orqali hammasi birga boshlanadi. Bu yakka
        # amal faqat admin uchun zaxira (masalan kechikkan jamoa uchun).
        if st["start_ts"]:
            c.close(); raise HTTPException(400, "Allaqachon boshlangan")
        ts = time.time()
        c.execute("INSERT INTO events(team_id,type,stage,delta,note,actor,ts) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (tid, "start", 1, 0, LABELS["start"], role, ts))
        c.commit(); c.close()
        return {"ok": True}
    elif act == "arrive":
        if not st["start_ts"]:
            c.close(); raise HTTPException(400, "Avval admin umumiy STARTni bersin")
        if st["status"] != "yolda":
            c.close(); raise HTTPException(400, "Jamoa yo'lda emas")
        delta = COIN_ARRIVE
    elif act == "kazus":
        if st["status"] != "nuqtada" or stage == 4:
            c.close(); raise HTTPException(400, "Kazus 1–3-nuqtalarda, jamoa nuqtada bo'lganda")
        if st["stages"][stage-1]["kazus"]:
            c.close(); raise HTTPException(400, "Bu bosqich kazusi allaqachon belgilangan")
        delta = COIN_KAZUS
    elif act == "depart":
        if st["status"] != "nuqtada" or stage == 4:
            c.close(); raise HTTPException(400, "Jamoa nuqtada emas yoki bu final")
    elif act == "hint":
        if st["status"] in ("kutmoqda", "yakunlandi"):
            c.close(); raise HTTPException(400, "O'yin faol emas")
        delta = COIN_HINT
    elif act in ("traitor_ok", "traitor_no"):
        if stage != 4 or st["status"] != "nuqtada":
            c.close(); raise HTTPException(400, "Faqat finalda, jamoa yetib kelgach")
        if st["traitor"] is not None:
            c.close(); raise HTTPException(400, "Hukm allaqachon qabul qilingan")
        delta = COIN_TRAITOR_OK if act == "traitor_ok" else COIN_TRAITOR_NO
    elif act == "finish":
        if stage != 4 or st["status"] != "nuqtada":
            c.close(); raise HTTPException(400, "Jamoa finalda emas")
        if st["traitor"] is None:
            c.close(); raise HTTPException(400, "Avval xoin hukmi belgilansin")
    elif act == "adjust":
        delta = int(body.get("delta", 0))
        if delta == 0: c.close(); raise HTTPException(400, "delta=0")
    elif act == "undo":
        last = c.execute("SELECT * FROM events WHERE team_id=? ORDER BY id DESC LIMIT 1",
                         (tid,)).fetchone()
        if not last: c.close(); raise HTTPException(400, "Bekor qiladigan amal yo'q")
        c.execute("DELETE FROM events WHERE id=?", (last["id"],))
        # start bilan birga kelgan arrive juftini ham tozalash
        if last["type"] == "arrive" and last["stage"] == 1:
            pass
        c.commit(); c.close()
        return {"ok": True}
    elif act == "reset_all":
        c.execute("DELETE FROM events"); c.commit(); c.close()
        return {"ok": True}
    else:
        c.close(); raise HTTPException(400, "Noma'lum amal")

    note = body.get("note", "") or LABELS.get(act, act)
    actor = role + (" @ " + loc if loc else "")
    c.execute("INSERT INTO events(team_id,type,stage,delta,note,actor,ts) "
              "VALUES(?,?,?,?,?,?,?)",
              (tid, act, stage, delta, note, actor, time.time()))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/admin/start_all")
async def start_all(request: Request):
    """Barcha jamoalar eshik oldidan birga yo'lga tushadi — admin BIR marta
    bosadi, hali boshlanmagan jamoalarning hammasi SHU ondan taymer oladi."""
    require(request, ("admin",))
    c = db()
    teams = c.execute("SELECT * FROM teams").fetchall()
    ts = time.time()
    started = 0
    for t in teams:
        evs = c.execute("SELECT * FROM events WHERE team_id=? ORDER BY id",
                        (t["id"],)).fetchall()
        st = team_state(t, evs, ts)
        if st["start_ts"]:
            continue
        c.execute("INSERT INTO events(team_id,type,stage,delta,note,actor,ts) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (t["id"], "start", 1, 0,
                   "OPERATSIYA START — barcha jamoalar yo'lga tushdi", "admin", ts))
        started += 1
    c.commit(); c.close()
    return {"ok": True, "started": started}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_SRC = os.path.join(BASE_DIR, "PIRS.png")          # asl rasm
MAP_IMG = os.path.join(BASE_DIR, "pirs_web.jpg")       # optimallashtirilgan nusxa (avtomatik yaratiladi)

def ensure_map_optimized():
    """PIRS.png dan telefonlar uchun yengil (kichraytirilgan, siqilgan) JPEG yasaydi.
    Pillow o'rnatilmagan bo'lsa jim o'tkazib yuboradi — shunda asl PNG xizmat qiladi."""
    if os.path.exists(MAP_IMG) or not os.path.exists(MAP_SRC):
        return
    try:
        from PIL import Image
        img = Image.open(MAP_SRC)
        if img.mode != "RGB":
            img = img.convert("RGB")
        max_w = 1600
        if img.width > max_w:
            new_h = round(img.height * (max_w / img.width))
            img = img.resize((max_w, new_h), Image.LANCZOS)
        img.save(MAP_IMG, "JPEG", quality=78, optimize=True)
    except Exception:
        pass

ensure_map_optimized()

@app.get("/pirs.jpg")
async def map_img():
    if os.path.exists(MAP_IMG):
        return FileResponse(MAP_IMG, media_type="image/jpeg")
    if os.path.exists(MAP_SRC):
        return FileResponse(MAP_SRC, media_type="image/png")
    raise HTTPException(404, "Xarita rasmi topilmadi")

@app.get("/api/mapinfo")
async def mapinfo():
    return {"has_img": os.path.exists(MAP_IMG) or os.path.exists(MAP_SRC)}

# ================= KOORDINATOR LOKATSIYASI (qurilma darajasida qulflangan) =================
@app.get("/api/coord/state")
async def coord_state(request: Request):
    require(request, ("koordinator", "admin"))
    token = request.headers.get("X-Coord-Token", "")
    c = db()
    row = c.execute("SELECT loc FROM coordinators WHERE token=?", (token,)).fetchone()
    c.close()
    return {"loc": row["loc"] if row else None}

@app.post("/api/coord/loc")
async def coord_set_loc(request: Request):
    require(request, ("koordinator",))
    body = await request.json()
    token = request.headers.get("X-Coord-Token", "")
    loc = body.get("loc")
    if not token:
        raise HTTPException(400, "Qurilma ID topilmadi")
    if loc not in KOORD_LOCS:
        raise HTTPException(400, "Noma'lum nuqta")
    c = db()
    row = c.execute("SELECT loc FROM coordinators WHERE token=?", (token,)).fetchone()
    if row and row["loc"]:
        c.close()
        raise HTTPException(
            403, "Nuqtangiz allaqachon tanlangan. O'zgartirish uchun ADMINga murojaat qiling.")
    now = time.time()
    if row:
        c.execute("UPDATE coordinators SET loc=?, last_seen=? WHERE token=?", (loc, now, token))
    else:
        c.execute("INSERT INTO coordinators(token,loc,last_seen) VALUES(?,?,?)", (token, loc, now))
    c.commit(); c.close()
    return {"ok": True}

# ================= ADMIN: KOORDINATORLAR NAZORATI =================
@app.get("/api/admin/coordinators")
async def admin_coordinators(request: Request):
    require(request, ("admin",))
    c = db()
    rows = c.execute(
        "SELECT token, loc, last_seen FROM coordinators ORDER BY last_seen DESC").fetchall()
    c.close()
    now = time.time()
    return {
        "coordinators": [
            {"token": r["token"], "loc": r["loc"], "last_seen": r["last_seen"],
             "online": bool(r["last_seen"] and (now - r["last_seen"]) < 60)}
            for r in rows
        ],
        "locs": KOORD_LOCS,
    }

@app.post("/api/admin/coord/setloc")
async def admin_set_coord_loc(request: Request):
    require(request, ("admin",))
    body = await request.json()
    token, loc = body.get("token"), body.get("loc")
    if not token:
        raise HTTPException(400, "token yo'q")
    if loc not in KOORD_LOCS:
        raise HTTPException(400, "Noma'lum nuqta")
    c = db()
    row = c.execute("SELECT token FROM coordinators WHERE token=?", (token,)).fetchone()
    now = time.time()
    if row:
        c.execute("UPDATE coordinators SET loc=?, last_seen=? WHERE token=?", (loc, now, token))
    else:
        c.execute("INSERT INTO coordinators(token,loc,last_seen) VALUES(?,?,?)", (token, loc, now))
    c.commit(); c.close()
    return {"ok": True}

# ================= ADMIN: NATIJALARNI YUKLAB OLISH (ZIP) =================
def _fmt_dur(sec):
    if sec is None:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""

@app.get("/api/admin/export")
async def admin_export(request: Request):
    require(request, ("admin",))
    now = time.time()
    c = db()
    teams = c.execute("SELECT * FROM teams").fetchall()

    res_buf = io.StringIO()
    w = csv.writer(res_buf)
    w.writerow(["Jamoa", "Coin", "Umumiy vaqt", "Holat", "Ishoralar soni", "Xoin hukmi",
                "1-nuqta", "2-nuqta", "3-nuqta", "FINISH"])
    for t in teams:
        evs = c.execute("SELECT * FROM events WHERE team_id=? ORDER BY id",
                        (t["id"],)).fetchall()
        st = team_state(t, evs, now)
        traitor = ("TO'G'RI topildi (+3)" if st["traitor"] is True
                   else "Topilmadi (-1)" if st["traitor"] is False else "")
        stage_cells = []
        for s in st["stages"]:
            cell = s["name"]
            if s["arrived"]:
                bits = []
                if s["travel_sec"] is not None:
                    bits.append("yo'lda " + _fmt_dur(s["travel_sec"]))
                if s["stay_sec"] is not None:
                    bits.append("nuqtada " + _fmt_dur(s["stay_sec"]))
                if bits:
                    cell += " (" + ", ".join(bits) + ")"
                if s["kazus"]:
                    cell += " [kazus: to'g'ri]"
            stage_cells.append(cell)
        w.writerow([st["name"], st["coins"], _fmt_dur(st["total_sec"]), st["status"],
                    st["hints"], traitor, *stage_cells])

    log_buf = io.StringIO()
    w2 = csv.writer(log_buf)
    w2.writerow(["Vaqt", "Jamoa", "Amal", "Bosqich", "Coin o'zgarishi", "Izoh", "Bajaruvchi"])
    rows = c.execute(
        "SELECT e.*, t.name tname FROM events e JOIN teams t ON t.id=e.team_id "
        "ORDER BY e.id").fetchall()
    for r in rows:
        w2.writerow([_fmt_ts(r["ts"]), r["tname"], LABELS.get(r["type"], r["type"]),
                     r["stage"], r["delta"], r["note"], r["actor"]])
    c.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("natijalar.csv", "﻿" + res_buf.getvalue())
        zf.writestr("jurnal.csv", "﻿" + log_buf.getvalue())
    buf.seek(0)
    fname = "SOYA_hisobot_" + datetime.now().strftime("%Y%m%d_%H%M") + ".zip"
    return StreamingResponse(buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>«SOYA» · Shtab paneli</title>
<style>
:root{ --paper:#efe6cf; --paper2:#f7f1e0; --ink:#2b241a; --line:#6b5327;
  --red:#a11616; --green:#144d33; --amber:#8a6a1a; }
*{box-sizing:border-box; -webkit-tap-highlight-color:transparent}
body{margin:0; background:
  radial-gradient(ellipse at 15% 5%, rgba(120,90,40,.10), transparent 55%),
  radial-gradient(ellipse at 90% 95%, rgba(90,60,20,.12), transparent 50%), var(--paper);
  color:var(--ink); font-family:Georgia,'DejaVu Serif',serif; padding-bottom:64px;}
header{position:sticky; top:0; z-index:5; background:var(--ink); color:var(--paper);
  padding:9px 12px; display:flex; align-items:center; justify-content:space-between;
  border-bottom:3px double var(--line); gap:8px; flex-wrap:wrap;}
header .t{font-family:ui-monospace,Menlo,monospace; letter-spacing:2px; font-size:13px}
header .role{font-size:10px; border:1px solid var(--paper); padding:2px 8px;
  letter-spacing:1px; text-transform:uppercase; font-family:ui-monospace,monospace}
main{max-width:980px; margin:0 auto; padding:12px}
.login{max-width:340px; margin:12vh auto; background:var(--paper2);
  border:2px solid var(--line); box-shadow:4px 4px 0 rgba(107,83,39,.3); padding:26px; text-align:center}
.login h1{font-size:20px; letter-spacing:3px; margin:0 0 4px}
.login .sub{font-family:ui-monospace,monospace; font-size:11px; color:var(--line); letter-spacing:2px; margin-bottom:18px}
.login input{width:100%; font-size:26px; text-align:center; letter-spacing:8px;
  padding:10px; border:2px solid var(--line); background:#fff; font-family:ui-monospace,monospace}
.login button{margin-top:14px; width:100%}
.err{color:var(--red); font-size:13px; margin-top:10px; min-height:16px}
.locgrid{display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:14px}
.locgrid button{width:100%; font-size:12px; padding:12px 6px}
button{font-family:ui-monospace,Menlo,monospace; font-size:13px; letter-spacing:1px;
  border:2px solid var(--ink); background:var(--paper2); color:var(--ink);
  padding:10px 12px; cursor:pointer; box-shadow:2px 2px 0 rgba(43,36,26,.5)}
button:active{transform:translate(2px,2px); box-shadow:none}
button:disabled{opacity:.35; pointer-events:none}
button.primary{background:var(--ink); color:var(--paper)}
button.good{border-color:var(--green); color:var(--green)}
button.bad{border-color:var(--red); color:var(--red)}
button.big{font-size:15px; padding:14px 12px}
button.small{padding:6px 8px; font-size:11px}
.tabs{display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap}
.tabs button{flex:1; min-width:92px; padding:9px 6px; font-size:12px}
.tabs button.on{background:var(--ink); color:var(--paper)}
.card{background:var(--paper2); border:2px solid var(--line); margin-bottom:14px;
  box-shadow:4px 4px 0 rgba(107,83,39,.25); position:relative; overflow:hidden}
.card.dim{opacity:.55}
.card .bar{height:6px}
.card .head{display:flex; justify-content:space-between; align-items:center; padding:10px 12px 6px}
.card .name{font-weight:bold; letter-spacing:2px; font-size:17px}
.coins{font-family:ui-monospace,monospace; font-weight:bold; font-size:16px;
  border:2px solid var(--amber); color:var(--amber); border-radius:999px;
  padding:3px 12px; background:#fff8e3}
.status{font-family:ui-monospace,monospace; font-size:11px; letter-spacing:1px;
  padding:2px 8px; border:1px solid var(--ink); display:inline-block}
.status.yolda{border-color:var(--amber); color:var(--amber)}
.status.nuqtada{border-color:var(--green); color:var(--green)}
.status.yakunlandi{background:var(--green); color:#fff; border-color:var(--green)}
.timers{display:flex; gap:12px; padding:8px 12px; font-family:ui-monospace,monospace}
.timers .tm{flex:1; border:1.5px dashed var(--line); padding:7px 9px; background:rgba(255,255,255,.4)}
.timers .lbl{font-size:9.5px; letter-spacing:1px; color:var(--line); text-transform:uppercase}
.timers .val{font-size:21px; font-weight:bold; margin-top:2px}
.route{display:flex; gap:4px; padding:4px 12px 10px; flex-wrap:wrap}
.step{flex:1; min-width:88px; border:1.5px solid var(--line); padding:5px 7px;
  font-size:10.5px; background:rgba(255,255,255,.35); position:relative}
.step .n{font-family:ui-monospace,monospace; font-weight:bold; font-size:10px; color:var(--line)}
.step.done{background:#e2ecd9; border-color:var(--green)}
.step.cur{background:#fdf3d0; border-color:var(--amber); border-width:2px}
.step .t{font-family:ui-monospace,monospace; font-size:10px; margin-top:2px}
.step .kz{position:absolute; top:3px; right:5px; font-size:11px}
.actions{display:flex; gap:8px; padding:0 12px 12px; flex-wrap:wrap}
.actions button{flex:1; min-width:130px}
.board{width:100%; border-collapse:collapse; background:var(--paper2);
  border:2px solid var(--line); box-shadow:4px 4px 0 rgba(107,83,39,.25); font-size:14px}
.board th{font-family:ui-monospace,monospace; font-size:10.5px; letter-spacing:1px;
  text-transform:uppercase; border-bottom:2px solid var(--line); padding:8px; text-align:left; color:var(--line)}
.board td{padding:9px 8px; border-bottom:1px solid rgba(107,83,39,.25); font-family:ui-monospace,monospace}
.board tr:first-child td{font-weight:bold}
.log{background:var(--ink); color:#d9cfae; font-family:ui-monospace,monospace;
  font-size:11.5px; padding:12px; border:2px solid var(--line); max-height:340px; overflow:auto}
.log .row{padding:3px 0; border-bottom:1px dashed rgba(217,207,174,.2)}
.log .plus{color:#9fce8f} .log .minus{color:#e08a8a}
h2.sec{font-size:13px; font-family:ui-monospace,monospace; letter-spacing:2px;
  text-transform:uppercase; border-bottom:2px solid var(--line); padding-bottom:4px}
.stamp{position:absolute; top:8px; right:-34px; transform:rotate(35deg);
  background:var(--red); color:#fff; font-family:ui-monospace,monospace;
  font-size:9px; letter-spacing:2px; padding:2px 40px; opacity:.85}
.pill{font-family:ui-monospace,monospace; font-size:10px; border:1px solid var(--line);
  padding:1px 6px; margin-left:6px; color:var(--line)}
footer{position:fixed; bottom:0; left:0; right:0; background:var(--ink); color:var(--paper);
  font-family:ui-monospace,monospace; font-size:10px; letter-spacing:1px;
  display:flex; justify-content:space-between; padding:7px 12px; z-index:5}
/* xarita */
.mapbox{background:#dfe3c8; border:2px solid var(--line);
  box-shadow:4px 4px 0 rgba(107,83,39,.25); position:relative}
.mapbox svg{display:block; width:100%; height:auto}
.maplegend{display:flex; gap:10px; flex-wrap:wrap; padding:8px 10px;
  font-family:ui-monospace,monospace; font-size:10.5px; background:var(--paper2);
  border-top:2px solid var(--line)}
.maplegend .it{display:flex; align-items:center; gap:5px}
.dotc{width:11px; height:11px; border-radius:50%; border:1.5px solid #fff; display:inline-block}
.mapwrap2{position:relative; background:#fff; border:2px solid var(--line);
  box-shadow:4px 4px 0 rgba(107,83,39,.25); overflow:hidden}
.mapwrap2 img{display:block; width:100%; height:auto; user-select:none; -webkit-user-drag:none}
.mapwrap2 svg.ovl{position:absolute; inset:0; width:100%; height:100%; pointer-events:none}
.tmark{position:absolute; transform:translate(-50%,-100%); pointer-events:none;
  display:flex; flex-direction:column; align-items:center; z-index:3}
.tmark .dot{width:22px; height:22px; border-radius:50%; border:2.5px solid #fff;
  box-shadow:0 1px 4px rgba(0,0,0,.45); color:#fff; font-family:ui-monospace,monospace;
  font-weight:bold; font-size:12px; display:flex; align-items:center; justify-content:center}
.tmark .tag{margin-top:1px; font-family:ui-monospace,monospace; font-size:8.5px;
  font-weight:bold; background:rgba(43,36,26,.85); color:#efe6cf; padding:0 4px;
  border-radius:2px; letter-spacing:.5px; white-space:nowrap}
.tmark.walking .dot{animation:walk 1s infinite}
.lmark{position:absolute; transform:translate(-50%,-50%); width:10px; height:10px;
  border-radius:50%; background:rgba(161,22,22,.14); border:1.6px solid var(--red);
  pointer-events:none; z-index:2}
.lmark.fin{background:rgba(20,77,51,.18); border-color:var(--green)}
.maptools{display:flex; gap:8px; margin-bottom:8px; flex-wrap:wrap}
.coordtip{position:fixed; bottom:70px; left:50%; transform:translateX(-50%);
  background:var(--ink); color:var(--paper); font-family:ui-monospace,monospace;
  font-size:12px; padding:6px 12px; z-index:9; border:1px solid var(--paper); display:none}
@keyframes walk {0%{transform:translateY(0)} 50%{transform:translateY(-1.3px)} 100%{transform:translateY(0)}}
.walking{animation:walk 1s infinite}
.sect{font-family:ui-monospace,monospace; font-size:11px; letter-spacing:2px;
  color:var(--line); text-transform:uppercase; margin:14px 0 8px; border-bottom:1.5px dashed var(--line); padding-bottom:3px}
@media(max-width:560px){ .timers .val{font-size:17px} .card .name{font-size:15px} }
</style>
</head>
<body>
<div id="app"></div>
<script>
const $ = s => document.querySelector(s);
let PIN = sessionStorage.getItem("soya_pin") || "";
let ROLE = sessionStorage.getItem("soya_role") || "";
let MYLOC = sessionStorage.getItem("soya_loc") || "";
let STATE = null, COORDS = null, TAB = "", TICK = null, OFFSET = 0;

// Koordinator qurilmasi uchun doimiy ID (lokatsiya shunga bog'lanadi va qulflanadi)
let CTOKEN = localStorage.getItem("soya_ctoken") || "";
if(!CTOKEN){
  CTOKEN = (crypto.randomUUID ? crypto.randomUUID()
    : "c-" + Date.now() + "-" + Math.random().toString(16).slice(2));
  localStorage.setItem("soya_ctoken", CTOKEN);
}

const CONF = {
  hint: "Ishora berildi, -1 coin. Tasdiqlaysizmi?",
  traitor_ok: "Xoin TO'G'RI topildi? (+3)",
  traitor_no: "Xoin topilmadi? (-1)",
  undo: "Oxirgi amal bekor qilinsinmi?",
  start: "Bu jamoaga ALOHIDA START berilsinmi? (odatda umumiy START tugmasi ishlatiladi — bu faqat kechikkan jamoa uchun zaxira)"
};
function fmt(sec){
  if(sec==null) return "--:--";
  sec = Math.max(0, Math.floor(sec));
  const m = Math.floor(sec/60), s = sec%60, h = Math.floor(m/60);
  if(h>0) return h+":"+String(m%60).padStart(2,"0")+":"+String(s).padStart(2,"0");
  return String(m).padStart(2,"0")+":"+String(s).padStart(2,"0");
}
async function api(path, body){
  const opt = {headers:{"X-Pin":PIN,"X-Coord-Token":CTOKEN,"Content-Type":"application/json"}};
  if(body){opt.method="POST"; opt.body=JSON.stringify(body);}
  const r = await fetch(path, opt);
  if(!r.ok){ const e = await r.json().catch(()=>({detail:"Xato"}));
    throw new Error(e.detail||"Xato"); }
  return r.json();
}
function loginView(msg){
  clearInterval(TICK);
  $("#app").innerHTML = `
  <div class="login">
    <h1>OPERATSIYA «SOYA»</h1>
    <div class="sub">SHTAB BOSHQARUV PANELI</div>
    <input id="pin" type="password" inputmode="numeric" placeholder="····" maxlength="8"
      autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" name="soya-pin">
    <button class="primary" onclick="doLogin()">KIRISH</button>
    <div class="err">${msg||""}</div>
  </div>`;
  $("#pin").focus();
  $("#pin").addEventListener("keydown", e=>{ if(e.key==="Enter") doLogin(); });
}
let LOGGING_IN = false;
async function doLogin(){
  if(LOGGING_IN) return;               // ikki marta bosilsa-da bitta so'rov
  const btn = document.querySelector(".login button.primary");
  LOGGING_IN = true;
  if(btn){ btn.disabled = true; btn.textContent = "..."; }
  try{
    const pin = $("#pin").value.trim();
    const r = await fetch("/api/login",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({pin})});
    if(!r.ok) throw new Error("bad_pin");
    const d = await r.json();
    PIN = pin; ROLE = d.role;
    sessionStorage.setItem("soya_pin", PIN);
    sessionStorage.setItem("soya_role", ROLE);
    if(ROLE==="koordinator"){
      // login MUVAFFAQIYATLI o'tdi — keyingi (coord/state) so'rov tarmoq
      // xatosiga uchrasa ham buni "PIN noto'g'ri" deb ko'rsatmaymiz.
      let cs = {loc:null};
      try{ cs = await api("/api/coord/state"); }catch(e){}
      if(cs.loc){ MYLOC=cs.loc; sessionStorage.setItem("soya_loc", MYLOC); boot(); }
      else locPicker(d.locs);
      return;
    }
    boot();
  }catch(e){ loginView("PIN noto'g'ri"); }
  finally{ LOGGING_IN = false; }
}
function locPicker(locs){
  const btns = locs.map(l=>`<button onclick="setLoc(this.dataset.l,this)" data-l="${l}">${l.replace("FINISH · ","🏁 ")}</button>`).join("");
  $("#app").innerHTML = `
  <div class="login" style="max-width:400px">
    <h1>NUQTANGIZ?</h1>
    <div class="sub">SIZ TURGAN LOKATSIYANI TANLANG — FAQAT BIR MARTA TANLANADI</div>
    <div class="locgrid">${btns}</div>
    <div class="err" id="locerr"></div>
  </div>`;
}
async function setLoc(l, btnEl){
  if(btnEl) btnEl.disabled = true;
  try{ await api("/api/coord/loc", {loc:l}); MYLOC=l; sessionStorage.setItem("soya_loc", l); boot(); }
  catch(e){
    const el=$("#locerr"); if(el) el.textContent=e.message; else alert(e.message);
    if(btnEl) btnEl.disabled = false;
  }
}
function logout(){ sessionStorage.clear(); PIN=""; ROLE=""; MYLOC="";
  clearInterval(TICK); loginView(); }

async function refresh(){
  try{ STATE = await api("/api/state");
    OFFSET = Date.now()/1000 - STATE.now;
    if(ROLE==="admin"){
      try{ COORDS = await api("/api/admin/coordinators"); }catch(e){}
    }
    if(ROLE==="koordinator"){
      try{ const cs = await api("/api/coord/state");
        if(cs.loc && cs.loc!==MYLOC){ MYLOC=cs.loc; sessionStorage.setItem("soya_loc", MYLOC); }
      }catch(e){}
    }
    render();
  }catch(e){ if((e+"").includes("Ruxsat")) logout(); }
}
async function act(team, action, extra){
  if(CONF[action] && !confirm(CONF[action])) return;
  const body = Object.assign({team, action, loc: MYLOC||null}, extra||{});
  try{ await api("/api/action", body); await refresh(); }
  catch(e){ alert(e.message); }
}
function nowSec(){ return Date.now()/1000 - OFFSET; }
function liveTotal(t){ return t.start_ts ? (t.finish_ts? t.total_sec : nowSec()-t.start_ts) : null; }
function liveLeg(t){
  if(t.status==="yolda"||t.status==="nuqtada") return t.leg_sec + (nowSec()-STATE.now);
  return null;
}
function liveGlobal(){
  return STATE.global_start_ts ? (nowSec() - STATE.global_start_ts) : null;
}
async function startAll(){
  if(!confirm("BARCHA jamoalarga umumiy START berilsinmi? Barcha taymerlar shu ondan boshlanadi.")) return;
  try{ await api("/api/admin/start_all", {}); await refresh(); }
  catch(e){ alert(e.message); }
}

// ============ JAMOA KARTASI ============
function teamCard(t, ctx){
  const running = t.start_ts && !t.finish_ts;
  const steps = t.stages.map(s=>{
    let cls="step";
    if(s.arrived) cls+=" done";
    if(t.status!=="yakunlandi" && s.n===t.stage) cls+=" cur";
    const tt = s.travel_sec!=null ? "🏃"+fmt(s.travel_sec) : "";
    const ss = s.stay_sec!=null ? " 📍"+fmt(s.stay_sec) : "";
    return `<div class="${cls}"><span class="n">${s.n===4?"FIN":s.n}</span> ${s.name.replace("FINISH · ","")}
      ${s.kazus?'<span class="kz">🧩</span>':""}<div class="t">${tt}${ss}</div></div>`;
  }).join("");

  let btns = "";
  const canOps = (ROLE==="admin") || (ROLE==="koordinator" && t.expected_loc===MYLOC);
  if(canOps){
    if(t.status==="kutmoqda" && ROLE==="admin")
      btns += `<button class="small" onclick="act('${t.id}','start')">▶ START (yakka — kechikkan jamoa uchun)</button>`;
    if(t.status==="yolda" && t.start_ts)
      btns += `<button class="good big" onclick="act('${t.id}','arrive')">📍 Yetib keldi (+1)</button>`;
    if(t.status==="nuqtada" && t.stage<4){
      const kz = t.stages[t.stage-1].kazus;
      btns += `<button class="good" ${kz?"disabled":""} onclick="act('${t.id}','kazus')">🧩 Kazus (+1)</button>`;
      btns += `<button onclick="act('${t.id}','depart')">🏃 Yo'lga chiqdi →</button>`;
    }
    if(t.status==="nuqtada" && t.stage===4){
      if(t.traitor===null){
        btns += `<button class="good" onclick="act('${t.id}','traitor_ok')">🎭 Xoin topildi (+3)</button>`;
        btns += `<button class="bad" onclick="act('${t.id}','traitor_no')">✖ Topilmadi (−1)</button>`;
      } else
        btns += `<button class="primary big" onclick="act('${t.id}','finish')">🏁 Yakunlash</button>`;
    }
  }
  if((ROLE==="mentor"||ROLE==="admin") && running)
    btns += `<button class="bad" onclick="act('${t.id}','hint')">💡 Ishora (−1)</button>`;
  if(ROLE==="admin"){
    btns += `<button class="small" onclick="act('${t.id}','adjust',{delta:1})">+1</button>`;
    btns += `<button class="small" onclick="act('${t.id}','adjust',{delta:-1})">−1</button>`;
    btns += `<button class="small" onclick="act('${t.id}','undo')">↩ Undo</button>`;
  }
  const trBadge = t.traitor===true ? '<span class="pill">XOIN ✔</span>'
                : t.traitor===false ? '<span class="pill">XOIN ✖</span>' : "";
  const dim = ctx==="dim" ? " dim" : "";
  return `
  <div class="card${dim}">
    ${t.status==="yakunlandi"?'<div class="stamp">YAKUN</div>':""}
    <div class="bar" style="background:${t.color}"></div>
    <div class="head">
      <div><span class="name" style="color:${t.color}">«${t.name}»</span> ${trBadge}
        <div style="margin-top:4px"><span class="status ${t.status}">${t.status.toUpperCase()}</span>
        <span class="pill">💡 ${t.hints}</span>
        <span class="pill">➜ ${t.expected_loc.replace("FINISH · ","🏁 ")}</span></div></div>
      <div class="coins">🪙 ${t.coins}</div>
    </div>
    <div class="timers">
      <div class="tm"><div class="lbl">Umumiy vaqt</div><div class="val">${fmt(liveTotal(t))}</div></div>
      <div class="tm"><div class="lbl">${t.leg_label||"Joriy"}</div><div class="val">${fmt(liveLeg(t))}</div></div>
    </div>
    <div class="route">${steps}</div>
    ${btns?`<div class="actions">${btns}</div>`:""}
  </div>`;
}

// ============ XARITA (admin) ============
function shortName(n){ return n.replace("FINISH · ","🏁 ").replace(" maydonchasi","").replace(" zonasi",""); }
const LEG_EST_SEC = 180; // taxminiy yurish davomiyligi (soniya) — chiziq bo'ylab silliq harakat uchun
function teamPos(t, XY){
  // "kutmoqda" = admin umumiy STARTni hali bermagan — jamoa eshik oldida turibdi
  if(t.status==="kutmoqda")   return {p:XY[STATE.start_pt], from:null, wait:true};
  if(t.status==="yakunlandi") return {p:XY[STATE.finish_pt], from:null};
  const target = XY[t.expected_loc];
  if(t.status==="nuqtada") return {p:target, from:null};
  const from = t.stage===1 ? XY[STATE.start_pt] : XY[t.route[t.stage-2]];
  // vaqtga qarab chiziq bo'ylab siljiydi (hech qachon 92% dan oshmaydi —
  // haqiqiy yetib kelish faqat koordinator "Yetib keldi" bosganda qayd etiladi)
  const elapsed = liveLeg(t) || 0;
  const frac = Math.min(0.92, elapsed / LEG_EST_SEC);
  const p = [from[0]+(target[0]-from[0])*frac, from[1]+(target[1]-from[1])*frac];
  return {p, from:from, to:target, walk:true};
}
function mapView(){
  const XY = STATE.map_xy;
  // lokatsiya belgilari (halqalar)
  let lmarks = "";
  for(const [name,[x,y]] of Object.entries(XY)){
    const isF = name===STATE.finish_pt;
    lmarks += `<div class="lmark${isF?" fin":""}" style="left:${x}%; top:${y}%" title="${name}"></div>`;
  }
  // jamoa yo'nalish chiziqlari (SVG overlay) + markerlar
  let lines = "", marks = "";
  // 1-pass: pozitsiyalarni oldindan hisoblab, bir xil nuqtaga to'g'ri kelganlarni guruhlaymiz
  const positions = STATE.teams.map(t=>({t, pos: teamPos(t, XY)}));
  const groups = {};
  positions.forEach(({t,pos})=>{
    const key = Math.round(pos.p[0]/3)+"_"+Math.round(pos.p[1]/3);
    (groups[key] = groups[key] || []).push(t.id);
  });
  const seenInGroup = {};
  positions.forEach(({t,pos})=>{
    if(pos.walk){
      lines += `<line x1="${pos.from[0]}" y1="${pos.from[1]}" x2="${pos.to[0]}" y2="${pos.to[1]}"
        stroke="${t.color}" stroke-width=".45" stroke-dasharray="1.6 1.2" opacity=".8"
        vector-effect="non-scaling-stroke"/>`;
    }
    const key = Math.round(pos.p[0]/3)+"_"+Math.round(pos.p[1]/3);
    const groupSize = groups[key].length;
    const n = seenInGroup[key] || 0; seenInGroup[key] = n+1;
    // YAKKA jamoa uchun HECH QANDAY siljish yo'q — aniq kalibrlangan nuqtaga tushadi.
    // Bir nechta jamoa bir joyga to'g'ri kelsa, guruh markazidan simmetrik taqsimlanadi.
    let dx = 0, dy = 0;
    if(groupSize > 1){
      const col = n % 3, row = Math.floor(n/3);
      const colsInRow = Math.min(groupSize - row*3, 3);
      dx = (col - (colsInRow-1)/2) * 4.5;
      dy = row * 4.5;
    }
    marks += `<div class="tmark${pos.walk?" walking":""}"
      style="left:${pos.p[0]+dx}%; top:${pos.p[1]+dy}%">
      <div class="dot" style="background:${t.color}">${t.name[0]}</div>
      <div class="tag">${t.name}${pos.wait?" ⏳":""}</div></div>`;
  });
  const leg = STATE.teams.map(t=>{
    const stt = t.status==="yolda" ? "yolda ➜ "+shortName(t.expected_loc)
             : t.status==="nuqtada" ? "📍 "+shortName(t.expected_loc)
             : t.status==="kutmoqda" ? "⏳ eshik oldida, START kutilmoqda → "+shortName(t.route[0])
             : t.status;
    return `<div class="it"><span class="dotc" style="background:${t.color}"></span>
      <b>${t.name}</b> · ${stt} · 🪙${t.coins} · ${fmt(liveTotal(t))} · 💡${t.hints}</div>`;
  }).join("");
  return `<div class="maptools">
    <h2 class="sec" style="flex:1; margin:0">Eco Park — jonli xarita</h2>
    <button class="small" onclick="fullMap()">⛶ TABLO</button>
  </div>
  <div class="mapwrap2" id="mapwrap" onclick="mapClick(event)">
    <img src="/pirs.jpg" alt="Eco Park">
    <svg class="ovl" viewBox="0 0 100 100" preserveAspectRatio="none">${lines}</svg>
    ${lmarks}${marks}
  </div>
  <div class="maplegend" style="border:2px solid var(--line); border-top:none">${leg}</div>
  <div style="font-family:ui-monospace,monospace; font-size:10.5px; color:#6b5327; margin-top:8px">
  Pulsatsiya — harakatda · ⏳ — 1-nuqtasida START kutilmoqda · shtrix chiziq — joriy yo'nalish<br>
  Kalibrlash: rasm ustiga bossangiz % koordinata ko'rinadi (MAP_XY ga yozish uchun)</div>
  `;
}
function mapClick(ev){
  const box = document.getElementById("mapwrap");
  const r = box.getBoundingClientRect();
  const x = ((ev.clientX-r.left)/r.width*100).toFixed(1);
  const y = ((ev.clientY-r.top)/r.height*100).toFixed(1);
  let tip = document.getElementById("coordtip_fx");
  if(!tip){ tip = document.createElement("div"); tip.id="coordtip_fx";
    tip.className="coordtip"; document.body.appendChild(tip); }
  tip.textContent = "koordinata: ("+x+", "+y+")";
  tip.style.display = "block";
  clearTimeout(window.__ct); window.__ct = setTimeout(()=>{tip.style.display="none"},2500);
}
function fullMap(){
  const el = document.getElementById("mapwrap");
  if(el.requestFullscreen) el.requestFullscreen();
  else if(el.webkitRequestFullscreen) el.webkitRequestFullscreen();
}

// ============ KOORDINATOR ko'rinishi ============
function koordView(){
  const mine = STATE.teams.filter(t => t.expected_loc===MYLOC && t.status!=="yakunlandi");
  const here = mine.filter(t=>t.status==="nuqtada");
  const coming = mine.filter(t=>t.status==="yolda" || t.status==="kutmoqda");
  let html = "";
  if(here.length){ html += `<div class="sect">📍 Hozir nuqtamda</div>` + here.map(t=>teamCard(t)).join(""); }
  if(coming.length){ html += `<div class="sect">➜ Menga kelmoqda</div>` + coming.map(t=>teamCard(t)).join(""); }
  if(!mine.length) html += `<div class="card" style="padding:18px; font-family:ui-monospace,monospace">
    Hozircha sizning nuqtangizga biriktirilgan faol jamoa yo'q. Jamoa yaqinlashsa shu yerda paydo bo'ladi.</div>`;
  const others = STATE.teams.filter(t => t.expected_loc!==MYLOC && t.status!=="yakunlandi");
  if(others.length){
    html += `<div class="sect">Boshqa jamoalar (faqat kuzatuv)</div>`;
    html += others.map(t=>`<div class="card dim"><div class="bar" style="background:${t.color}"></div>
      <div class="head"><div><span class="name" style="color:${t.color}">«${t.name}»</span>
      <span class="pill">➜ ${t.expected_loc.replace("FINISH · ","🏁 ")}</span>
      <span class="status ${t.status}" style="margin-left:6px">${t.status}</span></div>
      <div class="coins">🪙 ${t.coins}</div></div></div>`).join("");
  }
  return html;
}

// ============ ADMIN: KOORDINATORLAR nazorati ============
function agoLabel(last_seen){
  if(!last_seen) return "faollik yo'q";
  const s = Math.max(0, Date.now()/1000 - last_seen);
  return fmt(s) + " oldin";
}
function coordsView(){
  if(!COORDS || !COORDS.coordinators.length)
    return `<h2 class="sec">Koordinatorlar nazorati</h2>
    <div class="card" style="padding:18px; font-family:ui-monospace,monospace">
    Hozircha hech bir koordinator tizimga kirmagan.</div>`;
  const rows = COORDS.coordinators.map(cd=>{
    const opts = COORDS.locs.map(l=>
      `<option value="${l}" ${l===cd.loc?"selected":""}>${l.replace("FINISH · ","🏁 ")}</option>`
    ).join("");
    return `<div class="card" style="padding:12px">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px">
        <div>
          <b>${(cd.loc||"— tanlanmagan").replace("FINISH · ","🏁 ")}</b>
          <span class="pill">${cd.online?"🟢 onlayn":"⚪ "+agoLabel(cd.last_seen)}</span>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap">
          <select id="sel_${cd.token}" style="font-family:ui-monospace,monospace; padding:6px; border:2px solid var(--ink)">${opts}</select>
          <button class="small" onclick="reassignCoord('${cd.token}')">O'zgartirish</button>
        </div>
      </div>
      <div style="font-family:ui-monospace,monospace; font-size:9.5px; opacity:.5; margin-top:6px">ID: ${cd.token.slice(0,8)}</div>
    </div>`;
  }).join("");
  return `<h2 class="sec">Koordinatorlar nazorati</h2>${rows}`;
}
async function reassignCoord(token){
  const sel = document.getElementById("sel_"+token);
  try{ await api("/api/admin/coord/setloc", {token, loc: sel.value}); await refresh(); }
  catch(e){ alert(e.message); }
}
async function exportReport(){
  try{
    const r = await fetch("/api/admin/export", {headers:{"X-Pin":PIN}});
    if(!r.ok){ const e = await r.json().catch(()=>({detail:"Xato"})); throw new Error(e.detail||"Xato"); }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "SOYA_hisobot.zip";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }catch(e){ alert("Yuklab olishda xato: " + e.message); }
}

function boardView(){
  const order = STATE.board.map(id=>STATE.teams.find(t=>t.id===id));
  const medals = ["🥇","🥈","🥉","④","⑤"];
  const rows = order.map((t,i)=>`<tr><td>${medals[i]||i+1}</td>
      <td style="color:${t.color};font-weight:bold">«${t.name}»</td>
      <td>🪙 ${t.coins}</td><td>${fmt(liveTotal(t))}</td><td>${t.status}</td></tr>`).join("");
  return `<h2 class="sec">Reyting — coin ko'p, vaqt kam</h2>
  <table class="board"><tr><th>#</th><th>Jamoa</th><th>Coin</th><th>Vaqt</th><th>Holat</th></tr>${rows}</table>`;
}
function logView(){
  const rows = STATE.log.map(e=>{
    const d = new Date(e.ts*1000);
    const hh = String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0");
    const sign = e.delta>0? `<span class="plus">+${e.delta}</span>`
               : e.delta<0? `<span class="minus">${e.delta}</span>` : "·";
    return `<div class="row">[${hh}] <b>${e.tname}</b> — ${e.note} ${sign}
      <span style="opacity:.5">(${e.actor}${e.stage?", "+e.stage+"-bosqich":""})</span></div>`;
  }).join("");
  return `<h2 class="sec">Harakatlar jurnali</h2><div class="log">${rows||"Hozircha bo&#39;sh"}</div>`;
}

function render(){
  if(!STATE) return;
  if(!TAB) TAB = ROLE==="admin" ? "map" : "teams";
  let tabs = "";
  if(ROLE==="admin"){
    tabs = `<div class="tabs">
      <button class="${TAB==="map"?"on":""}" onclick="TAB='map';render()">🗺 XARITA</button>
      <button class="${TAB==="teams"?"on":""}" onclick="TAB='teams';render()">JAMOALAR</button>
      <button class="${TAB==="board"?"on":""}" onclick="TAB='board';render()">REYTING</button>
      <button class="${TAB==="log"?"on":""}" onclick="TAB='log';render()">JURNAL</button>
      <button class="${TAB==="coords"?"on":""}" onclick="TAB='coords';render()">KOORDINATORLAR</button>
      <button class="small" onclick="exportReport()">⬇ Hisobot</button>
      <button class="bad" onclick="resetAll()">⟲</button>
    </div>`;
  } else {
    tabs = `<div class="tabs">
      <button class="${TAB==="teams"?"on":""}" onclick="TAB='teams';render()">JAMOALAR</button>
      <button class="${TAB==="board"?"on":""}" onclick="TAB='board';render()">REYTING</button>
    </div>`;
  }
  let body = "";
  if(TAB==="map") body = mapView();
  else if(TAB==="teams")
    body = ROLE==="koordinator" ? koordView() : STATE.teams.map(t=>teamCard(t)).join("");
  else if(TAB==="board") body = boardView();
  else if(TAB==="log") body = logView();
  else if(TAB==="coords") body = coordsView();
  const locBadge = ROLE==="koordinator"
    ? `<span class="pill" title="Lokatsiya faqat admin tomonidan o'zgartiriladi">📍 ${(MYLOC||"?").replace("FINISH · ","🏁 ")}</span>` : "";
  let masterBar = "";
  if(STATE.global_start_ts){
    masterBar = `<div class="card" style="padding:10px 14px; display:flex; justify-content:space-between; align-items:center; margin-bottom:12px">
      <span style="font-family:ui-monospace,monospace; letter-spacing:1px">⏱ OPERATSIYA VAQTI</span>
      <span style="font-family:ui-monospace,monospace; font-size:22px; font-weight:bold">${fmt(liveGlobal())}</span>
    </div>`;
  } else if(ROLE==="admin"){
    masterBar = `<div class="card" style="padding:12px; text-align:center">
      <button class="primary big" style="width:100%" onclick="startAll()">🚀 BARCHA JAMOALARGA START BERISH</button>
    </div>`;
  } else {
    masterBar = `<div class="card" style="padding:10px 14px; text-align:center; font-family:ui-monospace,monospace; opacity:.7">⏳ Admin umumiy STARTni kutilmoqda…</div>`;
  }
  $("#app").innerHTML = `
  <header><div class="t">☰ «SOYA»</div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      ${locBadge}<span class="role">${ROLE}</span>
      <button class="small" onclick="logout()">chiqish</button>
    </div></header>
  <main>${masterBar}${tabs}${body}</main>
  <footer><span>Antinarko shtabi · jonli panel</span><span id="clock"></span></footer>`;
  $("#clock").textContent = new Date().toLocaleTimeString("uz-UZ");
}
async function resetAll(){
  if(!confirm("DIQQAT: barcha natijalar o'chiriladi. Davom etilsinmi?")) return;
  if(!confirm("Ishonchingiz komilmi? Bu amal qaytmaydi.")) return;
  try{ await api("/api/action",{team:"burgut",action:"reset_all"}); refresh(); }
  catch(e){ alert(e.message); }
}
function boot(){
  refresh();
  clearInterval(TICK);
  TICK = setInterval(()=>{ if(STATE && TAB!=="log") render(); }, 1000);
  setInterval(refresh, 4000);
}
if(PIN && ROLE){
  if(ROLE==="koordinator"){
    api("/api/coord/state").then(cs=>{
      if(cs.loc){ MYLOC=cs.loc; sessionStorage.setItem("soya_loc", MYLOC); boot(); }
      else if(MYLOC){ boot(); }  // avvalgi lokatsiya keshda bor — davom etamiz
      else fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({pin:PIN})}).then(r=>r.json()).then(d=>locPicker(d.locs));
    }).catch(()=>{
      // tarmoq xatosi: sessiyani buzmaymiz — keshdagi lokatsiya bilan davom etamiz,
      // faqat u ham bo'lmasa loginga qaytamiz.
      if(MYLOC) boot(); else loginView();
    });
  } else boot();
} else loginView();
</script>
</body>
</html>"""
