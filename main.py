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
USER_PIN   = "7788"
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
# (2026-07-19 da joyida kalibrlangan, hammasi tasdiqlangan).
MAP_XY = {
    START_PT:                 (54.7, 55.1),   # Administratsiya
    "Befit Eco":              (47.5, 38.8),
    "Kuzatuv maydonchasi":    (47.5, 22.5),
    "Pirs":                   (75.9, 57.9),
    "Amfiteatr":              (49.7, 47.7),
    "Favvora":                (80.8, 67.8),
    "Yoga terassasi":         (35.7, 38.7),
    "Piknik zonasi":          (38.2, 26.4),
    "Ochiq trenajyorlar":     (61.4, 53.6),
    FINISH:                   (38.2, 17.9),   # Bolalar eko (sport) maydonchasi
}
# Koordinator tanlaydigan nuqtalar
KOORD_LOCS = ["Befit Eco", "Kuzatuv maydonchasi", "Pirs", "Amfiteatr", "Favvora",
              "Yoga terassasi", "Piknik zonasi", "Ochiq trenajyorlar", FINISH]

# Eco Park haqiqiy GPS markazi (REAL xarita shu nuqtaga markazlashadi)
# https://yandex.uz/maps/-/CTbSUJ2M
MAP_CENTER = (41.310891, 69.295064)

COIN_ARRIVE = 1
COIN_KAZUS  = 1
COIN_HINT   = -1
COIN_TRAITOR_OK = 3
COIN_TRAITOR_NO = -1

app = FastAPI(title="SOYA Shtab Paneli")

# ================= GPS -> RASM PROYEKSIYASI (real xarita rejimi) =================
# Internetga (xarita plitkalariga) bog'liq bo'lmaslik uchun haqiqiy GPS koordinatalari
# to'g'ridan-to'g'ri MAVJUD pirs.jpg rasmiga proyeksiya qilinadi — buning uchun admin
# kamida 3 ta ma'lum nuqtada (masalan START, FINISH va yana bittasi) GPS "kalibrlash"
# qiladi, so'ng oddiy 2D affin transformatsiya (eng kichik kvadratlar usuli) hisoblanadi.
def _solve3x3(A, b):
    M = [A[i][:] + [b[i]] for i in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pivot = M[col][col]
        M[col] = [v / pivot for v in M[col]]
        for r in range(3):
            if r != col:
                factor = M[r][col]
                M[r] = [M[r][k] - factor * M[col][k] for k in range(4)]
    return [M[i][3] for i in range(3)]

def fit_affine(points):
    """points: [(lat, lon, img_x, img_y), ...]. img_x ≈ a*lat+b*lon+c, img_y ≈ d*lat+e*lon+f."""
    if len(points) < 3:
        return None
    sxx=sxy=sx=syy=sy=s1=0.0
    for lat, lon, _, _ in points:
        sxx += lat*lat; sxy += lat*lon; sx += lat
        syy += lon*lon; sy += lon; s1 += 1
    A = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, s1]]
    bx = [0.0, 0.0, 0.0]; by = [0.0, 0.0, 0.0]
    for lat, lon, ix, iy in points:
        bx[0] += lat*ix; bx[1] += lon*ix; bx[2] += ix
        by[0] += lat*iy; by[1] += lon*iy; by[2] += iy
    coefx = _solve3x3(A, bx)
    coefy = _solve3x3(A, by)
    if coefx is None or coefy is None:
        return None
    return (tuple(coefx), tuple(coefy))

def apply_affine(transform, lat, lon):
    (a,b,c), (d,e,f) = transform
    return (a*lat + b*lon + c, d*lat + e*lon + f)

def get_geo_transform(c):
    rows = c.execute("SELECT name, lat, lon FROM geo_calib").fetchall()
    pts = [(r["lat"], r["lon"], *MAP_XY[r["name"]]) for r in rows if r["name"] in MAP_XY]
    return fit_affine(pts)

def nearest_map_loc(x, y):
    """Rasm ustidagi (%) x,y nuqtaga eng yaqin nomlangan joyni topadi -> (nom, masofa%)."""
    best, bestd = None, None
    for name, (mx, my) in MAP_XY.items():
        d = ((mx - x) ** 2 + (my - y) ** 2) ** 0.5
        if bestd is None or d < bestd:
            best, bestd = name, d
    return best, bestd

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
    CREATE TABLE IF NOT EXISTS users(
        token TEXT PRIMARY KEY, team_id TEXT, last_seen REAL);
    CREATE TABLE IF NOT EXISTS mentors(
        token TEXT PRIMARY KEY, lat REAL, lon REAL, last_seen REAL);
    CREATE TABLE IF NOT EXISTS geo_calib(
        name TEXT PRIMARY KEY, lat REAL, lon REAL, ts REAL);
    """)
    if c.execute("SELECT COUNT(*) n FROM teams").fetchone()["n"] == 0:
        for tid, name, color, route in TEAMS_SEED:
            c.execute("INSERT INTO teams VALUES(?,?,?,?)",
                      (tid, name, color, json.dumps(route, ensure_ascii=False)))
    # eski bazalarda yo'q bo'lishi mumkin bo'lgan ustunlarni qo'shish (v6: jonli GPS)
    existing_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    for col in ("lat REAL", "lon REAL", "acc REAL"):
        cname = col.split()[0]
        if cname not in existing_cols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col}")
    existing_cols = {r["name"] for r in c.execute("PRAGMA table_info(mentors)").fetchall()}
    if "acc" not in existing_cols:
        c.execute("ALTER TABLE mentors ADD COLUMN acc REAL")
    # v7: koordinatorning ham jonli GPS lokatsiyasi (REAL xaritada aniq ko'rinishi uchun)
    existing_cols = {r["name"] for r in c.execute("PRAGMA table_info(coordinators)").fetchall()}
    for col in ("lat REAL", "lon REAL", "acc REAL"):
        cname = col.split()[0]
        if cname not in existing_cols:
            c.execute(f"ALTER TABLE coordinators ADD COLUMN {col}")
    c.commit(); c.close()

init_db()

# ================= AUTH =================
def role_of(request: Request):
    pin = request.headers.get("X-Pin", "")
    if pin == ADMIN_PIN:  return "admin"
    if pin == KOORD_PIN:  return "koordinator"
    if pin == MENTOR_PIN: return "mentor"
    if pin == USER_PIN:   return "user"
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
            "map_xy": MAP_XY, "koord_locs": KOORD_LOCS, "map_center": MAP_CENTER,
            "start_pt": START_PT, "finish_pt": FINISH,
            "global_start_ts": min(starts) if starts else None,
            "all_started": len(starts) == len(out)}

def mask_state_for_user(st):
    """Ishtirokchi marshrutini (keyingi nuqtalarni) YASHIRADI — ular manzilni
    joydagi haqiqiy ishoralar asosida topishlari kerak, saytdan emas.
    Faqat holat, coin, vaqt va reyting ko'rinadi; nuqta NOMLARI olib tashlanadi."""
    for t in st["teams"]:
        n = len(t.get("route", []))
        t["route"] = ["?"] * n
        t["expected_loc"] = None
        for s in t.get("stages", []):
            s["name"] = f'{s["n"]}-nuqta'
    # xarita/nuqta ma'lumotlari ham ishtirokchiga kerak emas
    st["map_xy"] = {}
    st["koord_locs"] = []
    st["start_pt"] = None
    st["finish_pt"] = None
    return st

LABELS = {
    "start": "START — jamoa yo'lga tushdi", "arrive": "Hududga yetib keldi",
    "kazus": "Kazus to'g'ri yechildi", "depart": "Keyingi nuqtaga yo'lga chiqdi",
    "hint": "Mentor ishorasi olindi", "traitor_ok": "Xoin TO'G'RI topildi",
    "traitor_no": "Xoin topilmadi", "finish": "Operatsiya yakunlandi",
    "adjust": "Qo'lda tuzatish", "help_request": "🆘 Mentordan yordam so'raldi",
}

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    pin = body.get("pin", "")
    c = db()
    teams = [dict(id=t["id"], name=t["name"], color=t["color"])
             for t in c.execute("SELECT id,name,color FROM teams").fetchall()]
    c.close()
    for p, r in ((ADMIN_PIN, "admin"), (KOORD_PIN, "koordinator"),
                 (MENTOR_PIN, "mentor"), (USER_PIN, "user")):
        if pin == p:
            return {"role": r, "locs": KOORD_LOCS, "teams": teams}
    raise HTTPException(401, "PIN noto'g'ri")

@app.get("/api/state")
async def state(request: Request):
    role = require(request, ("admin", "koordinator", "mentor", "user"))
    st = full_state()
    if role == "user":
        st = mask_state_for_user(st)
    return JSONResponse(st)

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

# ================= HTTPS uchun o'z-o'zini imzolagan sertifikat =================
# GPS (Geolocation) va kompas (DeviceOrientation) ko'pchilik brauzerlarda FAQAT
# xavfsiz kontekst (https:// yoki localhost)da ishlaydi. Tadbir odatda internetsiz
# lokal Wi-Fi/hotspot orqali o'tgani uchun internetga bog'liq (Let's Encrypt kabi)
# sertifikat olib bo'lmaydi — shuning uchun ishga tushganda avtomatik O'Z-O'ZINI
# IMZOLAGAN sertifikat yasaladi (agar `openssl` mavjud bo'lsa). Bu internetsiz ham
# ishlaydi: telefon brauzeri "ulanish xavfsiz emas" deb bir marta ogohlantiradi,
# foydalanuvchi "Baribir davom etish"ni bosgach GPS/kompasga to'liq ruxsat beriladi.
CERT_FILE = os.path.join(BASE_DIR, "cert.pem")
KEY_FILE = os.path.join(BASE_DIR, "key.pem")

def ensure_self_signed_cert():
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return
    import subprocess, shutil
    if not shutil.which("openssl"):
        return
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "3650",
            "-keyout", KEY_FILE, "-out", CERT_FILE,
            "-subj", "/CN=soya-shtab",
        ], check=True, capture_output=True, timeout=30)
    except Exception:
        pass

ensure_self_signed_cert()

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

@app.post("/api/coord/beacon")
async def coord_beacon(request: Request):
    require(request, ("koordinator",))
    body = await request.json()
    token = request.headers.get("X-Coord-Token", "")
    if not token:
        raise HTTPException(400, "Qurilma ID topilmadi")
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(400, "lat/lon yo'q")
    acc = body.get("acc")
    now = time.time()
    c = db()
    row = c.execute("SELECT token FROM coordinators WHERE token=?", (token,)).fetchone()
    if row:
        c.execute("UPDATE coordinators SET lat=?, lon=?, acc=?, last_seen=? WHERE token=?",
                  (lat, lon, acc, now, token))
    else:
        c.execute("INSERT INTO coordinators(token,lat,lon,acc,last_seen) VALUES(?,?,?,?,?)",
                  (token, lat, lon, acc, now))
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

# ================= FOYDALANUVCHI (jamoaga qulflangan qurilma) =================
@app.get("/api/user/state")
async def user_state(request: Request):
    require(request, ("user", "admin"))
    token = request.headers.get("X-User-Token", "")
    c = db()
    row = c.execute("SELECT team_id FROM users WHERE token=?", (token,)).fetchone()
    c.close()
    return {"team_id": row["team_id"] if row else None}

@app.post("/api/user/team")
async def user_set_team(request: Request):
    require(request, ("user",))
    body = await request.json()
    token = request.headers.get("X-User-Token", "")
    team_id = body.get("team_id")
    if not token:
        raise HTTPException(400, "Qurilma ID topilmadi")
    c = db()
    if not c.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone():
        c.close(); raise HTTPException(400, "Noma'lum jamoa")
    row = c.execute("SELECT team_id FROM users WHERE token=?", (token,)).fetchone()
    if row and row["team_id"]:
        c.close()
        raise HTTPException(
            403, "Jamoangiz allaqachon tanlangan. O'zgartirish uchun ADMINga murojaat qiling.")
    now = time.time()
    if row:
        c.execute("UPDATE users SET team_id=?, last_seen=? WHERE token=?", (team_id, now, token))
    else:
        c.execute("INSERT INTO users(token,team_id,last_seen) VALUES(?,?,?)", (token, team_id, now))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/user/help")
async def user_help(request: Request):
    require(request, ("user",))
    token = request.headers.get("X-User-Token", "")
    c = db()
    row = c.execute("SELECT team_id FROM users WHERE token=?", (token,)).fetchone()
    if not row or not row["team_id"]:
        c.close(); raise HTTPException(400, "Avval jamoangizni tanlang")
    team = c.execute("SELECT * FROM teams WHERE id=?", (row["team_id"],)).fetchone()
    evs = c.execute("SELECT * FROM events WHERE team_id=? ORDER BY id", (team["id"],)).fetchall()
    st = team_state(team, evs, time.time())
    if st["status"] in ("kutmoqda", "yakunlandi"):
        c.close(); raise HTTPException(400, "O'yin faol emas")
    c.execute("UPDATE users SET last_seen=? WHERE token=?", (time.time(), token))
    c.execute("INSERT INTO events(team_id,type,stage,delta,note,actor,ts) "
              "VALUES(?,?,?,?,?,?,?)",
              (team["id"], "help_request", 0, 0, LABELS["help_request"],
               "ishtirokchi @ " + team["name"], time.time()))
    c.commit(); c.close()
    return {"ok": True}

@app.get("/api/admin/users")
async def admin_users(request: Request):
    require(request, ("admin",))
    c = db()
    transform = get_geo_transform(c)
    rows = c.execute(
        "SELECT u.token, u.team_id, u.last_seen, u.lat, u.lon, u.acc, "
        "t.name tname, t.color tcolor "
        "FROM users u LEFT JOIN teams t ON t.id=u.team_id ORDER BY u.last_seen DESC").fetchall()
    teams = [dict(id=t["id"], name=t["name"], color=t["color"])
             for t in c.execute("SELECT id,name,color FROM teams").fetchall()]
    c.close()
    now = time.time()

    def loc_info(lat, lon):
        """Foydalanuvchi GPS'ini rasm ustiga proyeksiya qilib, eng yaqin nomlangan joyni beradi."""
        if transform is None or lat is None or lon is None:
            return {"pos": None, "near": None, "near_dist": None}
        x, y = apply_affine(transform, lat, lon)
        near, d = nearest_map_loc(x, y)
        return {"pos": {"x": x, "y": y}, "near": near, "near_dist": d}

    def user_out(r):
        li = loc_info(r["lat"], r["lon"])
        return {
            "token": r["token"], "team_id": r["team_id"], "team_name": r["tname"],
            "team_color": r["tcolor"], "last_seen": r["last_seen"],
            "online": bool(r["last_seen"] and (now - r["last_seen"]) < 60),
            "has_gps": r["lat"] is not None and bool(r["last_seen"] and (now - r["last_seen"]) < 90),
            "lat": r["lat"], "lon": r["lon"], "acc": r["acc"],
            "pos": li["pos"], "near": li["near"], "near_dist": li["near_dist"],
        }

    return {
        "users": [user_out(r) for r in rows],
        "teams": teams,
        "transform_ready": transform is not None,
    }

@app.post("/api/admin/user/setteam")
async def admin_set_user_team(request: Request):
    require(request, ("admin",))
    body = await request.json()
    token, team_id = body.get("token"), body.get("team_id")
    if not token:
        raise HTTPException(400, "token yo'q")
    c = db()
    if not c.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone():
        c.close(); raise HTTPException(400, "Noma'lum jamoa")
    row = c.execute("SELECT token FROM users WHERE token=?", (token,)).fetchone()
    now = time.time()
    if row:
        c.execute("UPDATE users SET team_id=?, last_seen=? WHERE token=?", (team_id, now, token))
    else:
        c.execute("INSERT INTO users(token,team_id,last_seen) VALUES(?,?,?)", (token, team_id, now))
    c.commit(); c.close()
    return {"ok": True}

# ================= MENTOR RADAR (mentorning jonli GPS lokatsiyasi) =================
@app.post("/api/mentor/beacon")
async def mentor_beacon(request: Request):
    require(request, ("mentor",))
    body = await request.json()
    token = request.headers.get("X-Mentor-Token", "")
    if not token:
        raise HTTPException(400, "Qurilma ID topilmadi")
    c = db()
    if body.get("online") is False:
        c.execute("DELETE FROM mentors WHERE token=?", (token,))
        c.commit(); c.close()
        return {"ok": True}
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None or lon is None:
        c.close(); raise HTTPException(400, "lat/lon yo'q")
    acc = body.get("acc")
    now = time.time()
    row = c.execute("SELECT token FROM mentors WHERE token=?", (token,)).fetchone()
    if row:
        c.execute("UPDATE mentors SET lat=?, lon=?, acc=?, last_seen=? WHERE token=?",
                  (lat, lon, acc, now, token))
    else:
        c.execute("INSERT INTO mentors(token,lat,lon,acc,last_seen) VALUES(?,?,?,?,?)",
                  (token, lat, lon, acc, now))
    c.commit(); c.close()
    return {"ok": True}

@app.get("/api/mentors/online")
async def mentors_online(request: Request):
    require(request, ("user", "admin", "koordinator", "mentor"))
    c = db()
    # token bo'yicha barqaror tartib — shu bilan "Mentor N" raqami har so'rovda
    # last_seen o'zgarishi tufayli boshqa mentorga sakramaydi.
    rows = c.execute(
        "SELECT token, lat, lon, acc, last_seen FROM mentors ORDER BY token ASC").fetchall()
    c.close()
    now = time.time()
    out = []
    for r in rows:
        if not r["last_seen"] or (now - r["last_seen"]) >= 60:
            continue
        out.append({"id": len(out)+1, "lat": r["lat"], "lon": r["lon"],
                     "acc": r["acc"], "last_seen": r["last_seen"]})
    return {"mentors": out}

# ================= FOYDALANUVCHI: jonli GPS beacon =================
@app.post("/api/user/beacon")
async def user_beacon(request: Request):
    require(request, ("user",))
    body = await request.json()
    token = request.headers.get("X-User-Token", "")
    if not token:
        raise HTTPException(400, "Qurilma ID topilmadi")
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(400, "lat/lon yo'q")
    acc = body.get("acc")
    now = time.time()
    c = db()
    row = c.execute("SELECT token FROM users WHERE token=?", (token,)).fetchone()
    if row:
        c.execute("UPDATE users SET lat=?, lon=?, acc=?, last_seen=? WHERE token=?",
                  (lat, lon, acc, now, token))
    else:
        c.execute("INSERT INTO users(token,team_id,lat,lon,acc,last_seen) VALUES(?,?,?,?,?,?)",
                  (token, None, lat, lon, acc, now))
    c.commit(); c.close()
    return {"ok": True}

# ================= ADMIN: GPS KALIBRLASH (real xarita uchun) =================
@app.get("/api/admin/geocal")
async def admin_geocal_get(request: Request):
    require(request, ("admin",))
    c = db()
    rows = c.execute("SELECT name, lat, lon, ts FROM geo_calib").fetchall()
    c.close()
    points = [{"name": r["name"], "lat": r["lat"], "lon": r["lon"], "ts": r["ts"]} for r in rows]
    return {"points": points, "all_locs": list(MAP_XY.keys()), "ready": len(points) >= 3, "min_needed": 3}

@app.post("/api/admin/geocal")
async def admin_geocal_set(request: Request):
    require(request, ("admin",))
    body = await request.json()
    name, lat, lon = body.get("name"), body.get("lat"), body.get("lon")
    if name not in MAP_XY:
        raise HTTPException(400, "Noma'lum nuqta")
    if lat is None or lon is None:
        raise HTTPException(400, "lat/lon yo'q")
    now = time.time()
    c = db()
    row = c.execute("SELECT name FROM geo_calib WHERE name=?", (name,)).fetchone()
    if row:
        c.execute("UPDATE geo_calib SET lat=?, lon=?, ts=? WHERE name=?", (lat, lon, now, name))
    else:
        c.execute("INSERT INTO geo_calib(name,lat,lon,ts) VALUES(?,?,?,?)", (name, lat, lon, now))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/admin/geocal/remove")
async def admin_geocal_remove(request: Request):
    require(request, ("admin",))
    body = await request.json()
    c = db()
    c.execute("DELETE FROM geo_calib WHERE name=?", (body.get("name"),))
    c.commit(); c.close()
    return {"ok": True}

# ================= ADMIN: REAL XARITA (jonli GPS pozitsiyalari) =================
@app.get("/api/admin/live_positions")
async def admin_live_positions(request: Request):
    require(request, ("admin",))
    c = db()
    transform = get_geo_transform(c)
    now = time.time()
    users_rows = c.execute(
        "SELECT u.token, u.team_id, u.lat, u.lon, u.acc, u.last_seen, t.name tname, t.color tcolor "
        "FROM users u LEFT JOIN teams t ON t.id=u.team_id").fetchall()
    mentor_rows = c.execute(
        "SELECT token, lat, lon, acc, last_seen FROM mentors ORDER BY token ASC").fetchall()
    coord_rows = c.execute("SELECT loc, lat, lon, acc, last_seen FROM coordinators").fetchall()
    c.close()

    def proj(lat, lon):
        if transform is None or lat is None or lon is None:
            return None
        x, y = apply_affine(transform, lat, lon)
        return {"x": x, "y": y}

    users_out = []
    for r in users_rows:
        if r["lat"] is None or not r["last_seen"] or (now - r["last_seen"]) >= 90:
            continue
        users_out.append({"team_id": r["team_id"], "team_name": r["tname"], "team_color": r["tcolor"],
                           "lat": r["lat"], "lon": r["lon"], "acc": r["acc"], "pos": proj(r["lat"], r["lon"])})
    mentors_out = []
    for i, r in enumerate(mentor_rows):
        if not r["last_seen"] or (now - r["last_seen"]) >= 60:
            continue
        mentors_out.append({"id": i+1, "lat": r["lat"], "lon": r["lon"], "acc": r["acc"],
                             "pos": proj(r["lat"], r["lon"])})
    coords_out = []
    for r in coord_rows:
        if r["loc"] and r["loc"] in MAP_XY:
            ix, iy = MAP_XY[r["loc"]]
            coords_out.append({"loc": r["loc"], "x": ix, "y": iy,
                                "lat": r["lat"], "lon": r["lon"], "acc": r["acc"],
                                "online": bool(r["last_seen"] and (now - r["last_seen"]) < 60)})
    return {"users": users_out, "mentors": mentors_out, "coordinators": coords_out,
            "transform_ready": transform is not None}

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
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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
.tmark.mentorpin .dot{border-color:#ffd23f; box-shadow:0 0 0 3px rgba(255,210,63,.35), 0 1px 4px rgba(0,0,0,.45)}
.tmark.coordpin .dot{border-radius:4px}
.maptools{display:flex; gap:8px; margin-bottom:8px; flex-wrap:wrap}
.maptoggle{display:flex; gap:0; margin-bottom:8px; border:2px solid var(--ink); width:fit-content}
.maptoggle button{border:none; box-shadow:none; border-radius:0}
.maptoggle button.on{background:var(--ink); color:var(--paper)}
.geocard{background:var(--paper2); border:1.5px solid var(--line); padding:8px 10px;
  margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;
  font-family:ui-monospace,monospace; font-size:12px; gap:8px; flex-wrap:wrap}
.geocard.done{border-color:var(--green)}
.coordtip{position:fixed; bottom:70px; left:50%; transform:translateX(-50%);
  background:var(--ink); color:var(--paper); font-family:ui-monospace,monospace;
  font-size:12px; padding:6px 12px; z-index:9; border:1px solid var(--paper); display:none}
@keyframes walk {0%{transform:translateY(0)} 50%{transform:translateY(-1.3px)} 100%{transform:translateY(0)}}
.walking{animation:walk 1s infinite}
.sect{font-family:ui-monospace,monospace; font-size:11px; letter-spacing:2px;
  color:var(--line); text-transform:uppercase; margin:14px 0 8px; border-bottom:1.5px dashed var(--line); padding-bottom:3px}
@media(max-width:560px){ .timers .val{font-size:17px} .card .name{font-size:15px} }
/* ===== user / radar / mentor ===== */
.rankbanner{background:var(--ink); color:var(--paper); padding:16px; text-align:center;
  border:2px solid var(--line); box-shadow:4px 4px 0 rgba(107,83,39,.3); margin-bottom:14px}
.rankbanner .big{font-family:ui-monospace,monospace; font-size:34px; font-weight:bold; letter-spacing:1px}
.rankbanner .sm{font-family:ui-monospace,monospace; font-size:11px; letter-spacing:1px; opacity:.75; margin-top:4px}
.board tr.mine{background:#fdf3d0}
.board tr.mine td{font-weight:bold}
button.sos{background:var(--red); color:#fff; border-color:var(--red); width:100%; font-size:15px; padding:14px}
.radarwrap{display:flex; flex-direction:column; align-items:center; gap:12px; padding:10px 0}
.radarstage{position:relative; width:min(92vw,320px); height:min(92vw,320px); border-radius:50%;
  box-shadow:0 0 0 4px rgba(107,83,39,.2), 0 6px 18px rgba(0,0,0,.35); background:#051205}
.radarstage canvas{display:block; width:100%; height:100%; border-radius:50%}
.accbadge{font-family:ui-monospace,monospace; font-size:11px; text-align:center; padding:4px 10px;
  border:1.5px dashed var(--line); display:inline-block}
.accbadge.bad{border-color:var(--red); color:var(--red)}
.accbadge.good{border-color:var(--green); color:var(--green)}
.compassstage{position:relative; width:132px; height:132px}
.compassstage canvas{display:block; width:100%; height:100%}
.compassreadout{font-family:ui-monospace,monospace; font-size:13px; text-align:center; font-weight:bold}
.radarstatus{font-family:ui-monospace,monospace; font-size:12px; text-align:center; color:var(--line); max-width:340px}
.radarlist{width:100%; max-width:340px}
.radarlist .it{background:var(--paper2); border:1.5px solid var(--line); padding:8px 10px;
  margin-bottom:6px; font-family:ui-monospace,monospace; font-size:12px; display:flex; justify-content:space-between}
.helpfeed{background:#3a1414; color:#f3d9d9; font-family:ui-monospace,monospace; font-size:12px;
  padding:10px; border:2px solid var(--red); margin-bottom:12px; max-height:160px; overflow:auto}
.helpfeed .row{padding:3px 0; border-bottom:1px dashed rgba(243,217,217,.25)}
.onlinebar{display:flex; align-items:center; justify-content:space-between; gap:8px;
  background:var(--paper2); border:2px solid var(--line); padding:10px 12px; margin-bottom:12px}
</style>
</head>
<body>
<div id="app"></div>
<script>
const $ = s => document.querySelector(s);
let PIN = sessionStorage.getItem("soya_pin") || "";
let ROLE = sessionStorage.getItem("soya_role") || "";
let MYLOC = sessionStorage.getItem("soya_loc") || "";
let MYTEAM = sessionStorage.getItem("soya_team") || "";
let STATE = null, COORDS = null, USERS = null, MENTORS = null, TAB = "", TICK = null, OFFSET = 0;

function devId(key){
  let v = localStorage.getItem(key) || "";
  if(!v){
    v = (crypto.randomUUID ? crypto.randomUUID()
      : "d-" + Date.now() + "-" + Math.random().toString(16).slice(2));
    localStorage.setItem(key, v);
  }
  return v;
}
// Koordinator/foydalanuvchi/mentor qurilmasi uchun doimiy ID (lokatsiya/jamoa shunga bog'lanadi)
let CTOKEN = devId("soya_ctoken");
let UTOKEN = devId("soya_utoken");
let MTOKEN = devId("soya_mtoken");

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
  const opt = {headers:{"X-Pin":PIN,"X-Coord-Token":CTOKEN,"X-User-Token":UTOKEN,
    "X-Mentor-Token":MTOKEN,"Content-Type":"application/json"}};
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
    if(ROLE==="user"){
      let us = {team_id:null};
      try{ us = await api("/api/user/state"); }catch(e){}
      if(us.team_id){ MYTEAM=us.team_id; sessionStorage.setItem("soya_team", MYTEAM); boot(); }
      else teamPicker(d.teams);
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
function teamPicker(teams){
  const btns = teams.map(t=>
    `<button onclick="setTeam('${t.id}',this)" style="border-color:${t.color}; color:${t.color}">«${t.name}»</button>`
  ).join("");
  $("#app").innerHTML = `
  <div class="login" style="max-width:400px">
    <h1>JAMOANGIZ?</h1>
    <div class="sub">O'Z JAMOANGIZNI TANLANG — FAQAT BIR MARTA TANLANADI</div>
    <div class="locgrid">${btns}</div>
    <div class="err" id="locerr"></div>
  </div>`;
}
async function setTeam(id, btnEl){
  if(btnEl) btnEl.disabled = true;
  try{ await api("/api/user/team", {team_id:id}); MYTEAM=id; sessionStorage.setItem("soya_team", id); boot(); }
  catch(e){
    const el=$("#locerr"); if(el) el.textContent=e.message; else alert(e.message);
    if(btnEl) btnEl.disabled = false;
  }
}
function logout(){
  if(MENTOR_ONLINE) mentorGoOffline();  // qurilma GPS kuzatuvini to'xtatib, serverga oflayn ekanini bildiradi
  stopCoordLocationSharing(); stopUserLocationSharing();
  sessionStorage.clear(); PIN=""; ROLE=""; MYLOC=""; MYTEAM="";
  clearInterval(TICK); stopRadar(); loginView();
}

async function refresh(){
  try{ STATE = await api("/api/state");
    OFFSET = Date.now()/1000 - STATE.now;
    if(ROLE==="admin"){
      try{ COORDS = await api("/api/admin/coordinators"); }catch(e){}
      try{ USERS = await api("/api/admin/users"); }catch(e){}
      try{ MENTORS = await api("/api/mentors/online"); }catch(e){}
      try{ LIVE_POS = await api("/api/admin/live_positions"); }catch(e){}
      try{ GEOCAL = await api("/api/admin/geocal"); }catch(e){}
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
  if(ROLE==="user" && t.id===MYTEAM && running)
    btns += `<button class="sos" onclick="requestHelp()">🆘 Mentordan yordam so'rash</button>`;
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
        ${t.expected_loc?`<span class="pill">➜ ${t.expected_loc.replace("FINISH · ","🏁 ")}</span>`:""}</div></div>
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
let MAP_MODE = "interaktiv", LIVE_POS = null, GEOCAL = null;
function clampPct(v){ return Math.max(2, Math.min(98, v)); }  // rasm chegarasidan chiqib "yo'qolib qolmasin"
function mentorPinsOverlay(){
  // Onlayn mentorlarni (agar GPS kalibrlash tayyor bo'lsa) HAR IKKI xarita rejimida
  // ko'rsatadi — admin qaysi rejimda bo'lishidan qat'i nazar mentorni topa oladi.
  const lp = LIVE_POS || {mentors:[]};
  return lp.mentors.filter(m=>m.pos).map(m=>
    `<div class="tmark mentorpin" style="left:${clampPct(m.pos.x)}%; top:${clampPct(m.pos.y)}%">
      <div class="dot" style="background:#8a6a1a">M</div>
      <div class="tag">Mentor ${m.id}${m.acc!=null?` ±${Math.round(m.acc)}m`:""}</div></div>`
  ).join("");
}
function mapView(){
  const toggle = `<div class="maptoggle">
    <button class="${MAP_MODE==='interaktiv'?'on':''}" onclick="MAP_MODE='interaktiv';render()">🗺 INTERAKTIV</button>
    <button class="${MAP_MODE==='real'?'on':''}" onclick="MAP_MODE='real';render()">📍 REAL (GPS)</button>
  </div>`;
  return toggle + (MAP_MODE==="real" ? realMapView() : interaktivMapView());
}
function interaktivMapView(){
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
    ${lmarks}${marks}${mentorPinsOverlay()}
  </div>
  <div class="maplegend" style="border:2px solid var(--line); border-top:none">${leg}</div>
  <div style="font-family:ui-monospace,monospace; font-size:10.5px; color:#6b5327; margin-top:8px">
  Pulsatsiya — harakatda · ⏳ — 1-nuqtasida START kutilmoqda · shtrix chiziq — joriy yo'nalish ·
  <span style="color:#8a6a1a">⬤ M</span> — onlayn mentor (jonli GPS)<br>
  Kalibrlash: rasm ustiga bossangiz % koordinata ko'rinadi (MAP_XY ga yozish uchun)</div>
  `;
}
// ============ REAL XARITA (Leaflet + OpenStreetMap, haqiqiy jonli GPS) ============
let REAL_MAP = null, REAL_MAP_EL = null, REAL_LAYERS = {};
function ensureRealMap(){
  if(REAL_MAP || typeof L === "undefined") return;
  REAL_MAP_EL = document.createElement("div");
  REAL_MAP_EL.style.cssText = "width:100%; height:60vh; min-height:340px;";
  const center = (STATE && STATE.map_center) || [41.310891, 69.295064];
  REAL_MAP = L.map(REAL_MAP_EL).setView(center, 17);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap"
  }).addTo(REAL_MAP);
  L.marker(center, {icon: L.divIcon({className:"", iconSize:[16,16], iconAnchor:[8,8], html:
    `<div style="width:16px;height:16px;border-radius:50%;background:#a11616;border:2.5px solid #fff;
      box-shadow:0 1px 4px rgba(0,0,0,.5)"></div>`})})
    .addTo(REAL_MAP).bindTooltip("Eco Park");
}
function pinIcon(bg, label, square){
  return L.divIcon({className:"", iconSize:[26,26], iconAnchor:[13,13], html:
    `<div style="width:26px;height:26px;border-radius:${square?"6px":"50%"};background:${bg};
      border:2.5px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.45);
      display:flex;align-items:center;justify-content:center;color:#fff;
      font:bold 12px ui-monospace,monospace">${label}</div>`});
}
function updateRealMarkers(){
  if(!REAL_MAP) return;
  const lp = LIVE_POS || {users:[], mentors:[], coordinators:[]};
  const seen = new Set();
  lp.coordinators.forEach(c=>{
    if(c.lat==null || c.lon==null) return;
    const key = "coord_"+c.loc; seen.add(key);
    const icon = pinIcon(c.online?"#144d33":"#6b5327", "K", true);
    const tip = `Koordinator · ${c.loc.replace("FINISH · ","🏁 ")}${c.acc!=null?` ±${Math.round(c.acc)}m`:""}`;
    if(REAL_LAYERS[key]){ REAL_LAYERS[key].setLatLng([c.lat,c.lon]); REAL_LAYERS[key].setIcon(icon); REAL_LAYERS[key].setTooltipContent(tip); }
    else REAL_LAYERS[key] = L.marker([c.lat,c.lon], {icon}).addTo(REAL_MAP).bindTooltip(tip);
  });
  lp.mentors.forEach(m=>{
    if(m.lat==null || m.lon==null) return;
    const key = "mentor_"+m.id; seen.add(key);
    const tip = `Mentor ${m.id}${m.acc!=null?` ±${Math.round(m.acc)}m`:""}`;
    if(REAL_LAYERS[key]){ REAL_LAYERS[key].setLatLng([m.lat,m.lon]); REAL_LAYERS[key].setTooltipContent(tip); }
    else REAL_LAYERS[key] = L.marker([m.lat,m.lon], {icon: pinIcon("#8a6a1a","M")}).addTo(REAL_MAP).bindTooltip(tip);
  });
  lp.users.forEach((u,i)=>{
    if(u.lat==null || u.lon==null) return;
    const key = "user_"+(u.token || i); seen.add(key);
    const icon = pinIcon(u.team_color||"#555", (u.team_name||"?")[0]);
    const tip = `${u.team_name||"?"}${u.acc!=null?` ±${Math.round(u.acc)}m`:""}`;
    if(REAL_LAYERS[key]){ REAL_LAYERS[key].setLatLng([u.lat,u.lon]); REAL_LAYERS[key].setIcon(icon); REAL_LAYERS[key].setTooltipContent(tip); }
    else REAL_LAYERS[key] = L.marker([u.lat,u.lon], {icon}).addTo(REAL_MAP).bindTooltip(tip);
  });
  Object.keys(REAL_LAYERS).forEach(key=>{
    if(!seen.has(key)){ REAL_MAP.removeLayer(REAL_LAYERS[key]); delete REAL_LAYERS[key]; }
  });
}
function realMapView(){
  ensureRealMap();
  const noLeaflet = typeof L === "undefined" ? `<div class="card" style="padding:12px; margin-bottom:10px; font-family:ui-monospace,monospace; border-color:var(--red); color:var(--red)">
    ⚠ Xarita kutubxonasi yuklanmadi — internet aloqasini tekshiring.</div>` : "";
  const legend = `<div class="maplegend" style="border:2px solid var(--line); border-top:none">
    <div class="it"><span class="dotc" style="background:#144d33"></span>Koordinator (jonli GPS)</div>
    <div class="it"><span class="dotc" style="background:#8a6a1a"></span>Mentor (jonli GPS)</div>
    <div class="it"><span class="dotc" style="background:#555"></span>Jamoa a'zosi (jonli GPS)</div>
  </div>`;
  const geo = GEOCAL || {points:[], all_locs:[], ready:false, min_needed:3};
  const calibMap = {}; geo.points.forEach(p=>calibMap[p.name]=p);
  const calibRows = geo.all_locs.map(name=>{
    const pt = calibMap[name];
    return `<div class="geocard${pt?" done":""}">
      <span>${pt?"✅":"⬜"} ${name.replace("FINISH · ","🏁 ")}${pt?` <span style="opacity:.6">(${pt.lat.toFixed(5)}, ${pt.lon.toFixed(5)})</span>`:""}</span>
      <span style="display:flex; gap:6px">
        <button class="small" onclick="calibrateHere('${name}')">📍 Shu yerda GPS olish</button>
        ${pt?`<button class="small bad" onclick="removeCalib('${name}')">o'chirish</button>`:""}
      </span></div>`;
  }).join("");
  return noLeaflet + `<div class="maptools"><h2 class="sec" style="flex:1; margin:0">Eco Park — REAL jonli pozitsiyalar</h2>
    <button class="small" onclick="fullMap()">⛶ TABLO</button></div>
  <div class="mapwrap2" id="realmapwrap"></div>${legend}
  <div style="font-family:ui-monospace,monospace; font-size:10.5px; color:#6b5327; margin-top:8px">
  Belgilar qurilmalarning haqiqiy GPS signalidan jonli yangilanadi (koordinator, mentor va jamoa a'zolari).</div>
  <div class="sect">🎯 GPS nuqta kalibrlash (INTERAKTIV xaritadagi mentor belgilari uchun)</div>${calibRows}`;
}
async function calibrateHere(name){
  if(!navigator.geolocation){ alert("Bu qurilmada GPS mavjud emas."); return; }
  navigator.geolocation.getCurrentPosition(
    async pos=>{
      try{
        await api("/api/admin/geocal", {name, lat:pos.coords.latitude, lon:pos.coords.longitude});
        GEOCAL = await api("/api/admin/geocal");
        render();
      }catch(e){ alert(e.message); }
    },
    err => alert("GPS xatosi: " + err.message + " (HTTPS talab qilinishi mumkin)"),
    {enableHighAccuracy:true, timeout:15000});
}
async function removeCalib(name){
  try{ await api("/api/admin/geocal/remove", {name}); GEOCAL = await api("/api/admin/geocal"); render(); }
  catch(e){ alert(e.message); }
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
  const el = document.getElementById("mapwrap") || document.getElementById("realmapwrap");
  if(!el) return;
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

// ============ FOYDALANUVCHI (jamoa a'zosi) ko'rinishi ============
function userHomeView(){
  const mine = STATE.teams.find(t=>t.id===MYTEAM);
  if(!mine) return `<div class="card" style="padding:18px">Jamoa topilmadi.</div>`;
  const pos = STATE.board.indexOf(MYTEAM) + 1;
  const medals = ["🥇","🥈","🥉","④","⑤"];
  const banner = `<div class="rankbanner">
    <div class="big">${medals[pos-1]||("#"+pos)} — ${pos}-O'RIN</div>
    <div class="sm">${STATE.teams.length} jamoadan · reyting: coin ko'p, vaqt kam</div></div>`;
  return banner + userTeamCard(mine);
}

// Ishtirokchi kartasi — MARSHRUT va KEYINGI NUQTA KO'RSATILMAYDI.
// Ular keyingi manzilni faqat joydagi haqiqiy ishoralar/topshiriqlar asosida topadi.
function userTeamCard(t){
  const running = t.status!=="kutmoqda" && t.status!=="yakunlandi";
  const statusTxt = t.status==="kutmoqda" ? "START kutilmoqda"
                  : t.status==="yolda"     ? "Yo'lda — keyingi nuqtani izlanglar"
                  : t.status==="nuqtada"   ? "Nuqtadasiz — topshiriqni bajaring"
                  : "Yakunlandi";
  const legTxt = t.status==="yolda" ? "Yo'ldagi vaqt"
               : t.status==="nuqtada" ? "Nuqtadagi vaqt" : "Joriy";
  const sos = (t.id===MYTEAM && running)
    ? `<div class="actions"><button class="sos" onclick="requestHelp()">🆘 Mentordan yordam so'rash</button></div>` : "";
  const hint = running
    ? `<div style="margin-top:10px; padding:10px 12px; border:2px dashed var(--ink); border-radius:8px; font-size:13px; line-height:1.4">
       🧭 <b>Keyingi manzil sayt orqali ko'rsatilmaydi.</b> Uni joydagi ishoralar, topshiriqlar va koordinator ko'rsatmalari asosida toping.</div>` : "";
  return `
  <div class="card">
    ${t.status==="yakunlandi"?'<div class="stamp">YAKUN</div>':""}
    <div class="bar" style="background:${t.color}"></div>
    <div class="head">
      <div><span class="name" style="color:${t.color}">«${t.name}»</span>
        <div style="margin-top:4px"><span class="status ${t.status}">${statusTxt}</span>
        <span class="pill">💡 ${t.hints}</span></div></div>
      <div class="coins">🪙 ${t.coins}</div>
    </div>
    <div class="timers">
      <div class="tm"><div class="lbl">Umumiy vaqt</div><div class="val">${fmt(liveTotal(t))}</div></div>
      <div class="tm"><div class="lbl">${legTxt}</div><div class="val">${fmt(liveLeg(t))}</div></div>
    </div>
    ${hint}
    ${sos}
  </div>`;
}
let HELP_SENDING = false;
async function requestHelp(){
  if(HELP_SENDING) return;
  if(!confirm("Mentordan yordam so'ralsinmi? Onlayn mentorlarga signal ketadi.")) return;
  HELP_SENDING = true;
  try{ await api("/api/user/help", {}); alert("Yordam so'rovi yuborildi! Radar bo'limidan eng yaqin mentorni toping."); }
  catch(e){ alert(e.message); }
  finally{ HELP_SENDING = false; }
}

// ============ RADAR (foydalanuvchi mentorni GPS+kompas orqali topadi) ============
let RADAR_HEADING = null, MY_POS = null, MY_ACC = null,
    RADAR_TIMER = null, RADAR_ON = false, RADAR_RAF = null, RADAR_T0 = 0,
    COMPASS_LAST_EVENT = 0;
const RADAR_PERIOD_MS = 3400;   // signal bir aylanishi (samolyot radari tezligida)
const RADAR_BLIP_STATE = {};    // mentorId -> yorqinlik (0..1), signal o'tganda 1, keyin so'nadi
function toRad(d){ return d*Math.PI/180; }
function toDeg(r){ return r*180/Math.PI; }
function haversine(lat1,lon1,lat2,lon2){
  const R=6371000, dLat=toRad(lat2-lat1), dLon=toRad(lon2-lon1);
  const a=Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
  return R*2*Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}
function bearingTo(lat1,lon1,lat2,lon2){
  const y = Math.sin(toRad(lon2-lon1))*Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1))*Math.sin(toRad(lat2)) - Math.sin(toRad(lat1))*Math.cos(toRad(lat2))*Math.cos(toRad(lon2-lon1));
  return (toDeg(Math.atan2(y,x))+360)%360;
}
function distLabel(m){ return m<1000 ? Math.round(m)+" m" : (m/1000).toFixed(2)+" km"; }
function cardinal(deg){
  const dirs=["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
  return dirs[Math.round(((deg%360)+360)%360/22.5)%16];
}
// GPS "jitter"ni kamaytirish uchun so'nggi o'lchovlarning aniqlikka qarab og'irlashtirilgan
// o'rtachasi — accuracy raqami xolis ko'rsatiladi (dizayn yolg'on aniqlik bermaydi).
function makeGeoSmoother(){
  const buf = [];
  return function(lat, lon, acc){
    const a = (typeof acc === "number" && acc > 0) ? acc : 50;
    buf.push({lat,lon,acc:a});
    if(buf.length > 5) buf.shift();
    let wsum=0, latS=0, lonS=0;
    for(const p of buf){ const w = 1/(p.acc*p.acc); wsum+=w; latS+=p.lat*w; lonS+=p.lon*w; }
    return {lat: latS/wsum, lon: lonS/wsum, acc: a};
  };
}
const smoothMyPos = makeGeoSmoother();
function onOrientation(e){
  let h = null;
  if(typeof e.webkitCompassHeading === "number") h = e.webkitCompassHeading; // iOS Safari — haqiqiy kompas
  else if(e.alpha!=null) h = (360 - e.alpha) % 360;                          // Android — eng yaqin taxmin
  if(h!=null){ RADAR_HEADING = h; COMPASS_LAST_EVENT = Date.now(); }
}
// Jonli lokatsiya ulashish (admin REAL xaritasi uchun) — RADAR tabidan mustaqil,
// jamoa tanlangach darhol ishga tushadi va foydalanuvchi ilovadan chiqmaguncha davom etadi.
// RADAR esa shu YAGONA GPS oqimidan foydalanadi (ikkinchi marta GPS so'ramaydi).
let USER_LOC_WATCH = null, USER_LOC_LAST_SENT = 0;
function startUserLocationSharing(){
  if(USER_LOC_WATCH != null || !navigator.geolocation) return;
  USER_LOC_WATCH = navigator.geolocation.watchPosition(
    pos => {
      const acc = pos.coords.accuracy;
      if(MY_POS && acc > 60 && (MY_ACC==null || acc > MY_ACC*2)) return;
      const sm = smoothMyPos(pos.coords.latitude, pos.coords.longitude, acc);
      MY_POS = {lat:sm.lat, lon:sm.lon}; MY_ACC = acc;
      const now = Date.now();
      if(now - USER_LOC_LAST_SENT > 5000){
        USER_LOC_LAST_SENT = now;
        api("/api/user/beacon", {lat:sm.lat, lon:sm.lon, acc}).catch(()=>{});
      }
    },
    () => {},
    {enableHighAccuracy:true, maximumAge:2000, timeout:15000});
}
// Koordinatorning jonli lokatsiya ulashishi (admin REAL xaritasida "aniq" ko'rinishi uchun) —
// jamoa a'zolari/mentor bilan bir xil naqsh: fon rejimida, ilovadan chiqmaguncha davom etadi.
let COORD_LOC_WATCH = null, COORD_LOC_LAST_SENT = 0;
const smoothCoordPos = makeGeoSmoother();
function startCoordLocationSharing(){
  if(COORD_LOC_WATCH != null || !navigator.geolocation) return;
  COORD_LOC_WATCH = navigator.geolocation.watchPosition(
    pos => {
      const acc = pos.coords.accuracy;
      const sm = smoothCoordPos(pos.coords.latitude, pos.coords.longitude, acc);
      const now = Date.now();
      if(now - COORD_LOC_LAST_SENT > 5000){
        COORD_LOC_LAST_SENT = now;
        api("/api/coord/beacon", {lat:sm.lat, lon:sm.lon, acc}).catch(()=>{});
      }
    },
    () => {},
    {enableHighAccuracy:true, maximumAge:4000, timeout:15000});
}
function stopCoordLocationSharing(){
  if(COORD_LOC_WATCH!=null && navigator.geolocation) navigator.geolocation.clearWatch(COORD_LOC_WATCH);
  COORD_LOC_WATCH = null;
}
function stopUserLocationSharing(){
  if(USER_LOC_WATCH!=null && navigator.geolocation) navigator.geolocation.clearWatch(USER_LOC_WATCH);
  USER_LOC_WATCH = null;
}
async function startRadar(){
  RADAR_ON = true;
  render();
  const st = $("#radarstatus");
  if(!navigator.geolocation){
    if(st) st.textContent = "Bu qurilmada GPS (Geolocation) qo'llab-quvvatlanmaydi.";
    return;
  }
  startUserLocationSharing();  // ehtiyot uchun — odatda boot() da allaqachon ishga tushgan
  try{
    if(typeof DeviceOrientationEvent !== "undefined" && typeof DeviceOrientationEvent.requestPermission === "function"){
      const p = await DeviceOrientationEvent.requestPermission();
      if(p !== "granted" && st) st.textContent = "Kompasga ruxsat berilmadi — masofa ko'rinadi, yo'nalishsiz.";
    }
  }catch(e){}
  window.addEventListener("deviceorientationabsolute", onOrientation, true);
  window.addEventListener("deviceorientation", onOrientation, true);
  clearInterval(RADAR_TIMER);
  RADAR_TIMER = setInterval(async ()=>{
    try{ MENTORS = await api("/api/mentors/online"); }catch(e){}
  }, 3000);
  try{ MENTORS = await api("/api/mentors/online"); }catch(e){}
  RADAR_T0 = 0;
  cancelAnimationFrame(RADAR_RAF);
  RADAR_RAF = requestAnimationFrame(radarLoop);
}
function pauseRadarWatch(){
  // Faqat RADAR ekranining o'zini (kompas, signal so'rovi, chizish sikli) to'xtatadi —
  // lokatsiya ulashish (USER_LOC_WATCH) tegilmaydi, u fon rejimida davom etadi.
  clearInterval(RADAR_TIMER); RADAR_TIMER = null;
  cancelAnimationFrame(RADAR_RAF); RADAR_RAF = null;
  window.removeEventListener("deviceorientationabsolute", onOrientation, true);
  window.removeEventListener("deviceorientation", onOrientation, true);
}
function stopRadar(){
  pauseRadarWatch();
  stopUserLocationSharing();
  RADAR_ON = false;
  MY_POS = null; MY_ACC = null; RADAR_HEADING = null;
}
function switchTab(t){
  if(TAB==="radar" && t!=="radar" && RADAR_ON) pauseRadarWatch();
  TAB = t;
  if(TAB==="radar" && RADAR_ON) startRadar();  // oldin yoqilgan bo'lsa qayta ruxsat so'ramay davom etadi
  else render();
}
function radarView(){
  return `<h2 class="sec">📡 Mentor radari</h2>
  <div class="radarwrap">
    <div class="radarstatus" id="radarstatus">
      ${RADAR_ON ? "Radar yoqilgan — atrofdagi onlayn mentorlarni izlamoqda…"
                 : "Radarni yoqib, onlayn mentorlarni GPS orqali toping (samolyot radari kabi)."}
    </div>
    ${RADAR_ON ? "" : `<button class="primary big" onclick="startRadar()">📡 Radarni yoqish</button>`}
    ${RADAR_ON ? `
    <div class="radarstage"><canvas id="radarcv" width="320" height="320"></canvas></div>
    <div id="accbadge"></div>
    <div class="compassstage"><canvas id="compasscv" width="132" height="132"></canvas></div>
    <div class="compassreadout" id="compassreadout">—</div>
    <div id="radarlisthost"></div>` : ""}
  </div>`;
}
function radarLoop(ts){
  if(!RADAR_ON || TAB!=="radar"){ RADAR_RAF = null; return; }
  if(!RADAR_T0) RADAR_T0 = ts;
  const angle = ((ts - RADAR_T0) / RADAR_PERIOD_MS * 360) % 360;
  drawRadarCanvas(angle);
  drawCompassCanvas();
  RADAR_RAF = requestAnimationFrame(radarLoop);
}
function drawRadarCanvas(sweepAngle){
  const cv = document.getElementById("radarcv");
  const st = document.getElementById("radarstatus");
  const badge = document.getElementById("accbadge");
  const listHost = document.getElementById("radarlisthost");
  if(!cv) return;
  const mentors = (MENTORS && MENTORS.mentors) || [];
  if(st) st.textContent = !MY_POS ? "GPS signal kutilmoqda…"
    : mentors.length ? mentors.length + " ta mentor onlayn topildi." : "Hozircha hech bir mentor onlayn emas.";
  if(badge){
    if(MY_ACC!=null){
      const cls = MY_ACC<=15 ? "good" : MY_ACC<=50 ? "" : "bad";
      badge.innerHTML = `<span class="accbadge ${cls}">📍 GPS aniqligi: ±${Math.round(MY_ACC)} m${MY_ACC>50?" — telefon sozlamalarida \"Aniq lokatsiya\"ni yoqing":""}</span>`;
    } else badge.innerHTML = "";
  }
  if(listHost) listHost.innerHTML = mentorListFallback(mentors);

  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, cx = W/2, cy = H/2, R = W/2 - 6;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle = "#051205";
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.fill();
  ctx.strokeStyle = "rgba(255,70,70,.3)"; ctx.lineWidth = 1;
  [0.33,0.66,1].forEach(f=>{ ctx.beginPath(); ctx.arc(cx,cy,R*f,0,Math.PI*2); ctx.stroke(); });
  ctx.beginPath(); ctx.moveTo(cx-R,cy); ctx.lineTo(cx+R,cy);
  ctx.moveTo(cx,cy-R); ctx.lineTo(cx,cy+R); ctx.stroke();

  // qizil signal chizig'i — orqasida so'nib boruvchi iz bilan (klassik radar)
  const trailDeg = 46, steps = 26;
  for(let i=steps;i>=0;i--){
    const a = sweepAngle - (i/steps)*trailDeg;
    const alpha = (1 - i/steps) * 0.55;
    const rad = toRad(a - 90);
    ctx.strokeStyle = `rgba(255,45,45,${alpha})`;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+R*Math.cos(rad), cy+R*Math.sin(rad)); ctx.stroke();
  }
  const mainRad = toRad(sweepAngle - 90);
  ctx.strokeStyle = "#ff3b3b"; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+R*Math.cos(mainRad), cy+R*Math.sin(mainRad)); ctx.stroke();

  // mentor "signal nuqtalari" — faqat qizil chiziq ustidan o'tganda yonadi, keyin so'nadi
  const heading = RADAR_HEADING || 0, MAXM = 800;
  mentors.forEach(m=>{
    const key = "m"+m.id;
    let bright = RADAR_BLIP_STATE[key] || 0;
    if(MY_POS){
      const brg = bearingTo(MY_POS.lat, MY_POS.lon, m.lat, m.lon);
      const rel = (brg - heading + 360) % 360;
      let diff = Math.abs(sweepAngle - rel) % 360; if(diff>180) diff = 360-diff;
      if(diff < 5) bright = 1;
      else bright = Math.max(0, bright - 0.028);
      RADAR_BLIP_STATE[key] = bright;
      if(bright <= 0.02) return;
      const d = haversine(MY_POS.lat, MY_POS.lon, m.lat, m.lon);
      const r = Math.min(1, d/MAXM) * (R-16);
      const rad = toRad(rel - 90);
      const x = cx + r*Math.cos(rad), y = cy + r*Math.sin(rad);
      ctx.shadowColor = "rgba(255,50,50,.95)"; ctx.shadowBlur = 12*bright;
      ctx.fillStyle = `rgba(255,60,60,${bright})`;
      ctx.beginPath(); ctx.arc(x,y, 5+3*bright, 0, Math.PI*2); ctx.fill();
      ctx.shadowBlur = 0;
      if(bright > 0.25){
        ctx.fillStyle = `rgba(255,220,220,${bright})`;
        ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "left"; ctx.textBaseline="middle";
        ctx.fillText(`M${m.id} · ${distLabel(d)}`, x+9, y);
      }
    } else {
      RADAR_BLIP_STATE[key] = Math.max(0, bright - 0.028);
    }
  });
  ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(cx,cy,4,0,Math.PI*2); ctx.fill();
}
function drawCompassCanvas(){
  const cv = document.getElementById("compasscv");
  const readout = document.getElementById("compassreadout");
  if(!cv) return;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, cx = W/2, cy = H/2, R = W/2 - 8;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle = "#f7f1e0"; ctx.strokeStyle = "#6b5327"; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.fill(); ctx.stroke();

  const noSignal = !COMPASS_LAST_EVENT || (Date.now() - COMPASS_LAST_EVENT > 4000);
  if(noSignal){
    ctx.fillStyle = "#a11616"; ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("kompas signali", cx, cy-6);
    ctx.fillText("topilmadi", cx, cy+8);
    if(readout) readout.textContent = "kompas mavjud emas";
    return;
  }
  const heading = RADAR_HEADING || 0;
  ["N","E","S","W"].forEach((lbl,i)=>{  // Shimol(N)/Sharq(E)/Janub(S)/G'arb(W) — xalqaro belgilar
    const dirDeg = i*90;
    const screenDeg = dirDeg - heading - 90;
    const rad = toRad(screenDeg);
    const lx = cx + (R-16)*Math.cos(rad), ly = cy + (R-16)*Math.sin(rad);
    ctx.fillStyle = lbl==="N" ? "#a11616" : "#6b5327";
    ctx.font = "bold 13px ui-monospace, monospace"; ctx.textAlign="center"; ctx.textBaseline="middle";
    ctx.fillText(lbl, lx, ly);
  });
  // markazdagi kichik chiziqlar (har 30° bir belgi)
  for(let d=0; d<360; d+=30){
    const rad = toRad(d - heading - 90);
    const x1 = cx + (R-6)*Math.cos(rad), y1 = cy + (R-6)*Math.sin(rad);
    const x2 = cx + (R-1)*Math.cos(rad), y2 = cy + (R-1)*Math.sin(rad);
    ctx.strokeStyle = "rgba(107,83,39,.5)"; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
  }
  // qat'iy tepadagi ko'rsatkich — sizning yo'nalishingiz
  ctx.fillStyle = "#a11616";
  ctx.beginPath(); ctx.moveTo(cx, cy-R+3); ctx.lineTo(cx-6, cy-R+16); ctx.lineTo(cx+6, cy-R+16);
  ctx.closePath(); ctx.fill();
  if(readout) readout.textContent = Math.round(heading) + "° " + cardinal(heading);
}
function mentorListFallback(mentors){
  if(!mentors.length) return "";
  const rows = mentors.map(m=>{
    let extra = "";
    if(MY_POS) extra = distLabel(haversine(MY_POS.lat, MY_POS.lon, m.lat, m.lon));
    const acc = m.acc!=null ? ` (±${Math.round(m.acc)}m)` : "";
    return `<div class="it"><span>🟢 Mentor ${m.id}${acc}</span><span>${extra}</span></div>`;
  }).join("");
  return `<div class="radarlist">${rows}</div>`;
}

// ============ MENTOR: onlayn bo'lish + yordam so'rovlari ============
let MENTOR_ONLINE = false, MENTOR_WATCH = null;
function toggleMentorOnline(){
  if(MENTOR_ONLINE) mentorGoOffline(); else mentorGoOnline();
}
let MENTOR_LAST_SENT = 0;
const smoothMentorPos = makeGeoSmoother();
function mentorGoOnline(){
  if(!navigator.geolocation){ alert("Bu qurilmada GPS qo'llab-quvvatlanmaydi."); return; }
  MENTOR_ONLINE = true;
  MENTOR_LAST_SENT = 0;
  MENTOR_WATCH = navigator.geolocation.watchPosition(
    pos => {
      const now = Date.now();
      if(now - MENTOR_LAST_SENT < 4000) return;  // serverni haddan tashqari so'rov bilan bosib qolmaslik uchun
      MENTOR_LAST_SENT = now;
      const acc = pos.coords.accuracy;
      const sm = smoothMentorPos(pos.coords.latitude, pos.coords.longitude, acc);
      api("/api/mentor/beacon", {lat:sm.lat, lon:sm.lon, acc}).catch(()=>{});
    },
    err => { alert("GPS xatosi: " + err.message + " (HTTPS talab qilinishi mumkin)"); mentorGoOffline(); },
    {enableHighAccuracy:true, maximumAge:4000, timeout:15000});
  render();
}
function mentorGoOffline(){
  MENTOR_ONLINE = false;
  if(MENTOR_WATCH!=null && navigator.geolocation) navigator.geolocation.clearWatch(MENTOR_WATCH);
  MENTOR_WATCH = null;
  api("/api/mentor/beacon", {online:false}).catch(()=>{});
  render();
}
function mentorPanel(){
  const helpEvents = (STATE.log||[]).filter(e=>e.type==="help_request").slice(0,10);
  const feed = helpEvents.length
    ? `<div class="helpfeed">${helpEvents.map(e=>{
        const d = new Date(e.ts*1000);
        const hh = String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0");
        return `<div class="row">🆘 [${hh}] <b>${e.tname}</b> yordam so'radi</div>`;
      }).join("")}</div>`
    : "";
  return `<div class="onlinebar">
    <span>${MENTOR_ONLINE ? "🟢 Siz onlaynsiz — lokatsiyangiz radar orqali ko'rinmoqda" : "⚪ Siz oflaynsiz"}</span>
    <button class="${MENTOR_ONLINE?"bad":"good"}" onclick="toggleMentorOnline()">${MENTOR_ONLINE?"Oflayn bo'lish":"🟢 Onlayn bo'lish"}</button>
  </div>${feed}`;
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
function usersView(){
  const mentors = (MENTORS && MENTORS.mentors) || [];
  const mrows = mentors.length
    ? mentors.map(m=>`<div class="it" style="max-width:none"><span>🟢 Mentor ${m.id}</span>
        <span>lat ${m.lat.toFixed(5)}, lon ${m.lon.toFixed(5)} · ${agoLabel(m.last_seen)}</span></div>`).join("")
    : `<div class="card" style="padding:14px; font-family:ui-monospace,monospace">Hozircha onlayn mentor yo'q.</div>`;
  // ---- bitta foydalanuvchi kartasi (aniq lokatsiyasi bilan) ----
  function userCard(u){
    const opts = USERS.teams.map(t=>
      `<option value="${t.id}" ${t.id===u.team_id?"selected":""}>«${t.name}»</option>`).join("");
    // lokatsiya satri — kalibrlash SHART EMAS, jonli GPS to'g'ridan-to'g'ri ko'rinadi
    let locLine;
    if(u.has_gps){
      const acc = (u.acc!=null) ? ` <span style="opacity:.6">±${Math.round(u.acc)}m</span>` : "";
      const nm = u.near ? ` · ${u.near.replace("FINISH · ","🏁 ")} yaqinida` : "";
      const coords = (u.lat!=null&&u.lon!=null)
        ? `<div style="font-family:ui-monospace,monospace; font-size:10px; opacity:.55; margin-top:2px">${u.lat.toFixed(5)}, ${u.lon.toFixed(5)}</div>` : "";
      const gmap = (u.lat!=null&&u.lon!=null)
        ? `<a href="https://www.google.com/maps?q=${u.lat},${u.lon}" target="_blank" rel="noopener"
             style="display:inline-block; margin-top:4px; font-size:12px; font-weight:bold; color:var(--blue,#1f3a6e)">🗺 Xaritada ochish →</a>` : "";
      locLine = `<div style="margin-top:6px; font-weight:bold; color:var(--green)">📍 Jonli GPS${acc}${nm}</div>${coords}${gmap}`;
    } else if(u.online){
      locLine = `<div style="margin-top:6px; opacity:.7">📡 Onlayn — telefonida lokatsiyaga ruxsat (allow) berilishi kutilmoqda…</div>`;
    } else {
      locLine = `<div style="margin-top:6px; opacity:.5">⚪ Oflayn — oxirgi lokatsiya yo'q</div>`;
    }
    return `<div class="card" style="padding:12px">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px">
        <div><b style="color:${u.team_color||'inherit'}">${u.team_name?"«"+u.team_name+"»":"— tanlanmagan"}</b>
          <span class="pill">${u.online?"🟢 onlayn":"⚪ "+agoLabel(u.last_seen)}</span></div>
        <div style="display:flex; gap:6px; flex-wrap:wrap">
          <select id="usel_${u.token}" style="font-family:ui-monospace,monospace; padding:6px; border:2px solid var(--ink)">${opts}</select>
          <button class="small" onclick="reassignUser('${u.token}')">O'zgartirish</button>
        </div>
      </div>
      ${locLine}
      <div style="font-family:ui-monospace,monospace; font-size:9.5px; opacity:.5; margin-top:6px">ID: ${u.token.slice(0,8)}</div>
    </div>`;
  }

  let ublock = `<div class="card" style="padding:18px; font-family:ui-monospace,monospace">
    Hozircha hech bir foydalanuvchi tizimga kirmagan.</div>`;
  if(USERS && USERS.users.length){
    // JAMOA bo'yicha guruhlab ko'rsatamiz
    const groups = [];
    USERS.teams.forEach(t=>{
      const mem = USERS.users.filter(u=>u.team_id===t.id);
      if(mem.length) groups.push({name:"«"+t.name+"»", color:t.color, mem});
    });
    const noteam = USERS.users.filter(u=>!u.team_id);
    if(noteam.length) groups.push({name:"— jamoa tanlanmagan", color:"#888", mem:noteam});
    ublock = groups.map(g=>{
      const online = g.mem.filter(u=>u.online).length;
      return `<div style="margin-top:14px">
        <div class="sect" style="border-left:4px solid ${g.color}; padding-left:8px; display:flex; justify-content:space-between; align-items:center">
          <span style="color:${g.color}; font-weight:bold">${g.name}</span>
          <span class="pill">👥 ${g.mem.length} · 🟢 ${online}</span></div>
        ${g.mem.map(userCard).join("")}
      </div>`;
    }).join("");
  }
  const mapNote = `<div class="card" style="padding:10px 14px; margin-top:8px; font-size:12px; opacity:.85">
     🗺 Hammani bitta jonli xaritada ko'rish uchun: <b>XARITA → REAL (GPS)</b> bo'limi — kalibrlash shart emas.</div>`;
  return `<h2 class="sec">📡 Onlayn mentorlar (radarda ko'rinadigan)</h2><div class="radarlist" style="max-width:none">${mrows}</div>
  <h2 class="sec" style="margin-top:18px">Foydalanuvchilar — jamoa va jonli lokatsiya bo'yicha</h2>${mapNote}${ublock}`;
}
async function reassignUser(token){
  const sel = document.getElementById("usel_"+token);
  try{ await api("/api/admin/user/setteam", {token, team_id: sel.value}); await refresh(); }
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
  let banner = "";
  if(ROLE==="user" && MYTEAM){
    const pos = STATE.board.indexOf(MYTEAM) + 1;
    const mine = STATE.teams.find(t=>t.id===MYTEAM);
    if(pos>0 && mine) banner = `<div class="rankbanner">
      <div class="big">${medals[pos-1]||("#"+pos)} — ${pos}-O'RIN</div>
      <div class="sm">«${mine.name}» · 🪙 ${mine.coins} coin · ${STATE.teams.length} jamoadan</div>
      </div>`;
  }
  const rows = order.map((t,i)=>{
    const mine = ROLE==="user" && t.id===MYTEAM;
    return `<tr class="${mine?"mine":""}"><td>${medals[i]||i+1}</td>
      <td style="color:${t.color};font-weight:bold">«${t.name}»${mine?" 👉":""}</td>
      <td>🪙 ${t.coins}</td><td>${fmt(liveTotal(t))}</td><td>${t.status}</td></tr>`;
  }).join("");
  return `${banner}<h2 class="sec">Reyting — coin ko'p, vaqt kam</h2>
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
      <button class="${TAB==="map"?"on":""}" onclick="switchTab('map')">🗺 XARITA</button>
      <button class="${TAB==="teams"?"on":""}" onclick="switchTab('teams')">JAMOALAR</button>
      <button class="${TAB==="board"?"on":""}" onclick="switchTab('board')">REYTING</button>
      <button class="${TAB==="log"?"on":""}" onclick="switchTab('log')">JURNAL</button>
      <button class="${TAB==="coords"?"on":""}" onclick="switchTab('coords')">KOORDINATORLAR</button>
      <button class="${TAB==="users"?"on":""}" onclick="switchTab('users')">FOYDALANUVCHILAR</button>
      <button class="small" onclick="exportReport()">⬇ Hisobot</button>
      <button class="bad" onclick="resetAll()">⟲</button>
    </div>`;
  } else if(ROLE==="user"){
    tabs = `<div class="tabs">
      <button class="${TAB==="teams"?"on":""}" onclick="switchTab('teams')">JAMOAM</button>
      <button class="${TAB==="board"?"on":""}" onclick="switchTab('board')">REYTING</button>
      <button class="${TAB==="radar"?"on":""}" onclick="switchTab('radar')">📡 MENTOR RADARI</button>
    </div>`;
  } else if(ROLE==="mentor"){
    tabs = `<div class="tabs">
      <button class="${TAB==="teams"?"on":""}" onclick="switchTab('teams')">JAMOALAR</button>
      <button class="${TAB==="board"?"on":""}" onclick="switchTab('board')">REYTING</button>
    </div>`;
  } else {
    tabs = `<div class="tabs">
      <button class="${TAB==="teams"?"on":""}" onclick="switchTab('teams')">JAMOALAR</button>
      <button class="${TAB==="board"?"on":""}" onclick="switchTab('board')">REYTING</button>
    </div>`;
  }
  let body = "";
  if(TAB==="map") body = mapView();
  else if(TAB==="teams"){
    if(ROLE==="koordinator") body = koordView();
    else if(ROLE==="user") body = userHomeView();
    else if(ROLE==="mentor") body = mentorPanel() + STATE.teams.map(t=>teamCard(t)).join("");
    else body = STATE.teams.map(t=>teamCard(t)).join("");
  }
  else if(TAB==="board") body = boardView();
  else if(TAB==="log") body = logView();
  else if(TAB==="coords") body = coordsView();
  else if(TAB==="users") body = usersView();
  else if(TAB==="radar") body = radarView();
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
  const ROLE_LABEL = {admin:"admin", koordinator:"koordinator", mentor:"mentor", user:"ishtirokchi"}[ROLE] || ROLE;
  $("#app").innerHTML = `
  <header><div class="t">☰ «SOYA»</div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      ${locBadge}<span class="role">${ROLE_LABEL}</span>
      <button class="small" onclick="logout()">chiqish</button>
    </div></header>
  <main>${masterBar}${tabs}${body}</main>
  <footer><span>Antinarko shtabi · jonli panel</span><span id="clock"></span></footer>`;
  $("#clock").textContent = new Date().toLocaleTimeString("uz-UZ");
  // rAF sikli o'z-o'zidan davom etadi (har freymda canvas'ni ID orqali qayta topadi);
  // faqat u qandaydir sababga ko'ra to'xtab qolgan bo'lsa qayta ishga tushiramiz.
  if(TAB==="radar" && RADAR_ON && !RADAR_RAF){ RADAR_T0 = 0; RADAR_RAF = requestAnimationFrame(radarLoop); }
  // Leaflet xaritasi #app innerHTML bilan birga o'chib ketmasligi uchun — bir marta
  // yaratilgan xarita DOM tugunini (REAL_MAP_EL) har render()da joriy joyiga qayta ilamiz
  // (qayta yaratmaymiz — aks holda zoom/pan holati va tayl keshi har safar yo'qolib qoladi).
  if(TAB==="map" && MAP_MODE==="real"){
    const host = document.getElementById("realmapwrap");
    if(host && REAL_MAP_EL){
      host.appendChild(REAL_MAP_EL);
      if(REAL_MAP) REAL_MAP.invalidateSize();
    }
    updateRealMarkers();
  }
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
  // REAL (Leaflet) xarita ochiq bo'lsa har soniya to'liq qayta chizmaymiz — u faqat
  // refresh() davridan (4s) yangilanadi, aks holda xarita har soniya "sakraydi".
  TICK = setInterval(()=>{
    if(STATE && TAB!=="log" && TAB!=="radar" && !(TAB==="map" && MAP_MODE==="real")) render();
  }, 1000);
  setInterval(refresh, 4000);
  if(ROLE==="user") startUserLocationSharing();  // admin REAL xaritasi uchun jonli GPS
  if(ROLE==="koordinator") startCoordLocationSharing();  // admin REAL xaritasida aniq ko'rinishi uchun
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
  } else if(ROLE==="user"){
    api("/api/user/state").then(us=>{
      if(us.team_id){ MYTEAM=us.team_id; sessionStorage.setItem("soya_team", MYTEAM); boot(); }
      else if(MYTEAM){ boot(); }
      else fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({pin:PIN})}).then(r=>r.json()).then(d=>teamPicker(d.teams));
    }).catch(()=>{
      if(MYTEAM) boot(); else loginView();
    });
  } else boot();
} else loginView();
</script>
</body>
</html>"""