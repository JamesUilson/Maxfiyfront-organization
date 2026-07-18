# OPERATSIYA «SOYA» — Shtab boshqaruv paneli

Jonli baholash tizimi: FastAPI + SQLite, bitta faylda. Telefonlardan bir vaqtda
kirish mumkin (admin, koordinatorlar, mentorlar).

## Rollar va PINlar (main.py boshida o'zgartiriladi)
| Rol | PIN | Nima qila oladi |
|---|---|---|
| ADMIN (shtab) | `2026` | 🗺 Jonli xarita, hamma amallar, ±1 tuzatish, Undo, Reset, koordinatorlar nazorati (lokatsiyasini o'zgartirish), ⬇ natija/jurnal hisobotini ZIP qilib yuklab olish |
| KOORDINATOR | `1122` | Nuqtasini FAQAT birinchi kirishda tanlaydi (keyin faqat admin o'zgartiradi); ▶ START (jamoa 1-nuqtaga kelganda), 📍 Yetib keldi, 🧩 Kazus, 🏃 Yo'lga chiqdi, 🎭 Xoin hukmi, 🏁 Yakunlash — FAQAT o'z nuqtasidagi jamoa uchun |
| MENTOR | `3344` | 💡 Ishora (−1 coin) |

## Coin tizimi
- Hududga yetib keldi: **+1**
- Kazus to'liq to'g'ri: **+1** (1–3-bosqichlar, har birida 1 marta)
- Mentor ishorasi: **−1**
- Xoin to'g'ri topildi: **+3**, topilmadi: **−1**
- Reyting: coin ko'p → vaqt kam (har jamoaning O'Z taymeri, START bosilgandan boshlanadi)

## Vaqt hisobi
- **Umumiy vaqt** — har jamoa uchun alohida: START → 🏁 Yakunlash oralig'i.
- **Joriy bosqich taymeri** — «yo'lda» yoki «nuqtada» rejimda jonli sanaydi.
- Har bosqich uchun 🏃 yo'l vaqti va 📍 nuqtadagi vaqt avtomatik qayd etiladi.

## Ish tartibi (jonli)
0. Koordinator kirgach O'Z NUQTASINI tanlaydi — bu FAQAT bir marta bo'ladi, keyin
   o'zi o'zgartira olmaydi (kerak bo'lsa faqat ADMIN, "Koordinatorlar" bo'limidan o'zgartiradi).
1. Jamoa o'zining **1-nuqtasiga yetib kelganda** o'sha yerdagi koordinator **▶ START** bosadi —
   taymer shu ondan yuradi va +1 coin (hudud topildi) yoziladi. Admin aralashmaydi.
2. Keyingi nuqtalarda koordinator **📍 Yetib keldi** bosadi → yo'l vaqti muzlaydi, +1 coin.
3. Kazus to'g'ri himoya qilinsa **🧩 Kazus to'g'ri** → +1 coin.
4. Jamoa ketayotganda **🏃 Yo'lga chiqdi** → keyingi bosqich taymeri start oladi.
5. Mentor yordam berdimi — **💡 Ishora** → −1 coin (jurnalda kim, qachon — hammasi qoladi).
6. Finalda: **📍 Yetib keldi** → xoin hukmi (🎭 +3 / ✖ −1) → **🏁 Yakunlash**.

Xato bosildimi — Admin **↩ Undo** bilan jamoaning oxirgi amalini bekor qiladi.

## Lokal ishga tushirish
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8100
```
Telefonlar shu Wi-Fi/hotspotdagi `http://KOMPYUTER_IP:8100` manziliga kiradi.

## AlwaysData'ga deploy
1. Fayllarni yuklang (`main.py`, `requirements.txt`).
2. `pip install --user -r requirements.txt`
3. Site turi: **ASGI** → command: `uvicorn main:app`
   (yoki Custom: `~/.local/bin/uvicorn main:app --host :: --port $PORT`)
4. SQLite (`soya.db`) avtomatik yaratiladi. Xotira sarfi juda kichik (aiogram emas 😄).

## Xavfsizlik eslatmalari
- Tadbirdan oldin PINlarni ALBATTA o'zgartiring.
- `⟲ RESET` faqat adminda, ikki bosqichli tasdiq bilan — mashqdan keyin bazani tozalash uchun.

## v2 yangiliklari
- 🗺 **Admin xaritasi**: Eco Park sxemasi, 5 jamoa jonli markerlari (rang + bosh harf),
  harakatdagi jamoa pulsatsiya qiladi, yo'nalish chizig'i o'z rangida, legendada coin/vaqt/holat.
- 📍 **Koordinator nuqtaga biriktirilgan**: kirishda lokatsiya tanlaydi; faqat o'z nuqtasiga
  tegishli jamoalar bilan ishlaydi (server ham tekshiradi — boshqa nuqta jamoasiga bosolmaydi).
- ▶ **START koordinatordan**: jamoa 1-nuqtaga kelgan zahoti o'sha koordinator boshlaydi.
- Koordinator ekrani: «📍 Hozir nuqtamda» / «➜ Menga kelmoqda» / boshqalar xira kuzatuvda.
- Jurnal endi amalni KIM va QAYSI NUQTADA bajarganini yozadi (masalan: koordinator @ Pirs).

## v3 yangiliklari — REAL XARITA
- 🗺 Admin xaritasi Eco Parkning HAQIQIY interaktiv rasmida. Jamoa markerlari
  (rangli doira + nom yorlig'i) rasm ustida jonli yuradi.
- ⏳ START kutayotgan jamoa markeri O'ZINING 1-nuqtasida turadi (jamoalar tarqalib ketishiga mos).
- ⛶ TABLO tugmasi — xaritani to'liq ekranga chiqaradi (proyektor/katta ekran uchun).
- 🎯 Kalibrlash: rasm ustiga bosilsa % koordinata ko'rinadi.

## v4 yangiliklari
- 🗺 **Yangi xarita rasmi**: `xarita.jpg` o'rniga `PIRS.png` ishlatiladi. Server birinchi
  ishga tushganda uni avtomatik kichraytirib/siqib (`pirs_web.jpg`, ~1600px, JPEG q78)
  keshlaydi — telefonlar og'ir asl faylni emas, shu optimallashtirilgan nusxani yuklaydi
  (buning uchun `Pillow` kerak, `requirements.txt` ga qo'shilgan; Pillow bo'lmasa asl
  `PIRS.png` to'g'ridan-to'g'ri xizmat qiladi).
  ⚠️ Rasm rakursi eskisidan farq qiladi — `main.py` boshidagi `MAP_XY` qiymatlari
  TAXMINIY qo'yilgan, tadbirdan oldin ALBATTA admin XARITA bo'limida rasm ustiga
  bosib chiqqan % koordinatalar bilan har bir nuqtani moslang.
- 📍 **Lokatsiya qulfi**: koordinator nuqtasini FAQAT birinchi kirishda tanlaydi —
  keyin o'zi o'zgartira olmaydi (server buni qurilma ID orqali ta'minlaydi, hatto
  so'rov tanasidagi qiymatga ham ishonmaydi). O'zgartirish kerak bo'lsa — faqat
  ADMIN, yangi **KOORDINATORLAR** bo'limidan.
- 👀 **Admin → Koordinatorlar nazorati**: har bir koordinator qurilmasining joriy
  nuqtasi, oxirgi faollik vaqti (yoki 🟢 onlayn belgisi) ko'rinadi, shu yerdan
  lokatsiyasini o'zgartirish mumkin.
- ⬇ **Hisobotni yuklab olish**: admin panelidagi "⬇ Hisobot" tugmasi ZIP fayl
  yuklaydi — `natijalar.csv` (jamoalar bo'yicha yakuniy natija: coin, vaqt, bosqichlar,
  xoin hukmi) va `jurnal.csv` (to'liq harakatlar jurnali, ekrandagi so'nggi 50 tadan
  farqli — cheklovsiz).
