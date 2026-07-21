# OPERATSIYA «SOYA» — Shtab boshqaruv paneli

Jonli baholash tizimi: FastAPI + SQLite, bitta faylda. Telefonlardan bir vaqtda
kirish mumkin (admin, koordinatorlar, mentorlar).

## Rollar va PINlar (main.py boshida o'zgartiriladi)
| Rol | PIN | Nima qila oladi |
|---|---|---|
| ADMIN (shtab) | `2026` | 🗺 Jonli xarita, hamma amallar, ±1 tuzatish, Undo, Reset, koordinatorlar nazorati (lokatsiyasini o'zgartirish), ⬇ natija/jurnal hisobotini ZIP qilib yuklab olish |
| KOORDINATOR | `1122` | Nuqtasini FAQAT birinchi kirishda tanlaydi (keyin faqat admin o'zgartiradi); ▶ START (jamoa 1-nuqtaga kelganda), 📍 Yetib keldi, 🧩 Kazus, 🏃 Yo'lga chiqdi, 🎭 Xoin hukmi, 🏁 Yakunlash — FAQAT o'z nuqtasidagi jamoa uchun |
| MENTOR | `3344` | 💡 Ishora (−1 coin), 🟢 Onlayn bo'lish (radarda ko'rinish uchun lokatsiya ulashadi), 🆘 yordam so'rovlari jurnali |
| ISHTIROKCHI (USER) | `7788` | Kirganda o'z jamoasini FAQAT bir marta tanlaydi; 🏆 Reytingda o'z o'rnini ko'radi, 🆘 Mentordan yordam so'raydi, 📡 Radar orqali onlayn mentorlarni GPS+kompas bilan topadi |

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

⚠️ **Bu oddiy HTTP rejimi — GPS (radar) va kompas ISHLAMAYDI**, chunki
brauzerlar bu ikkalasini faqat xavfsiz (HTTPS) kontekstda beradi. Agar
ISHTIROKCHI radari, kompas yoki MENTOR/REAL xarita kerak bo'lsa, pastdagi
HTTPS rejimidan foydalaning.

## HTTPS bilan ishga tushirish (RADAR/KOMPAS uchun TAVSIYA ETILADI)
```bash
pip install -r requirements.txt
python3 run_https.py
```
Birinchi marta ishga tushganda avtomatik o'z-o'zini imzolagan sertifikat
(`cert.pem`, `key.pem`) yaratiladi (`openssl` kerak, ko'pchilik Mac/Linux'da
allaqachon o'rnatilgan). Bu **internetga ehtiyoj sezmaydi** — oddiy lokal
Wi-Fi/hotspotda ham ishlaydi. Telefonlar endi `https://KOMPYUTER_IP:8100`
manziliga kiradi. Birinchi ochilishda brauzer "ulanish xavfsiz emas" deb
ogohlantiradi (sertifikat o'z-o'zidan imzolangani uchun, bu normal) —
**"Qo'shimcha" (Advanced) → "Baribir davom etish" (Proceed)** tugmasini bir
marta bosish kifoya, shundan keyin GPS/kompasga to'liq ruxsat beriladi va
sayt odatdagidek ishlayveradi.

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

## v5 yangiliklari — ISHTIROKCHI, REYTING VA MENTOR RADARI
- 👤 **Yangi rol: ISHTIROKCHI** (`USER_PIN`, `main.py` boshida). Kirganda o'z jamoasini
  bir marta tanlaydi (qurilmaga qulflanadi — koordinator nuqtasi kabi); admin
  "FOYDALANUVCHILAR" bo'limidan xato tanlovni tuzatadi.
- 🏆 **Reyting bannerida o'z o'rni**: ishtirokchi "JAMOAM" va "REYTING" bo'limlarida
  o'zining N-o'rinini va reytingdagi qatorini ajratilgan holda ko'radi.
- 🆘 **"Mentordan yordam so'rash" tugmasi**: ishtirokchi bosganda jamoa nomi bilan
  signal ketadi — mentor ekranida va jurnalda ko'rinadi.
- 📡 **Mentor radari**: mentor "🟢 Onlayn bo'lish" tugmasini bossa, telefon GPS
  lokatsiyasini serverga yuboradi. Ishtirokchi "RADAR" bo'limida o'z GPS+kompas
  (device orientation) yordamida onlayn mentor(lar)gacha bo'lgan MASOFA va
  YO'NALISHNI (samolyot radari kabi aylanuvchi nur va yorqin nuqta) ko'radi,
  shu bilan birga kompas ko'rsatkichi ham beriladi.
  ⚠️ **MUHIM**: GPS (Geolocation) va kompas (DeviceOrientation) ko'pchilik
  brauzerlarda faqat **HTTPS** (yoki `localhost`) ustida ishlaydi. Oddiy
  lokal Wi-Fi/hotspot orqali `http://KOMPYUTER_IP:8100` bilan kirilganda bu
  ruxsat berilmasligi mumkin — radar shunda ham ochiladi, lekin GPS xatosi
  haqida ogohlantiradi va faqat "onlayn mentorlar" ro'yxatini (yo'nalishsiz)
  ko'rsatadi. To'liq radar/kompas uchun tadbirni HTTPS orqali (masalan
  AlwaysData deploy, yoki lokal tarmoq uchun mkcert/ngrok/Tailscale kabi
  vositalar bilan) joylashtirish tavsiya etiladi.

## v6 yangiliklari — RADAR/KOMPAS TUZATISH VA REAL GPS XARITASI
- 🎯 **Radar qayta ishlab chiqildi (canvas asosida)**: endi signal chizig'i QIZIL
  rangda aylanadi va mentor nuqtasi FAQAT signal ustidan o'tayotganda yonadi,
  o'tib ketgach so'nib yo'qoladi (haqiqiy samolyot radari kabi) — avvalgi
  versiyada nuqta doim ko'rinib turardi va dizayn sxematik edi.
- 📍 **GPS aniqligi shaffof ko'rsatiladi**: radar ekranida "±N m aniqlik" belgisi
  chiqadi; aniqlik >50m bo'lsa telefon sozlamalarida "Aniq lokatsiya" (Precise
  Location, iOS/Android'da alohida yoqiladigan sozlama) yoqilganini tekshirish
  tavsiya etiladi — masofa xatosining eng keng tarqalgan sababi shu (taxminiy
  lokatsiya rejimida xato 100+ metrgacha yetishi mumkin). Shuningdek so'nggi
  o'lchovlar aniqlikka qarab og'irlashtirilgan o'rtacha bilan silliqlanadi
  (GPS "sakrashi"ni kamaytiradi), past aniqlikdagi keskin xato o'lchovlar
  chetlab o'tiladi.
- 🧭 **Kompas tuzatildi**: signal topilmasa (qurilma qo'llab-quvvatlamasa yoki
  ruxsat berilmagan bo'lsa) endi aniq "kompas topilmadi" deb yozadi — avval
  sukut bo'yicha 0° da "muzlab qolgandek" ko'rinib, ishlamayotgandek tuyular edi.
  Kompas endi N/E/S/W belgilari va gradus ko'rsatkichi bilan chiroyli chizilgan.
- 🗺 **Admin: INTERAKTIV / REAL (GPS) xarita almashtirgichi**. "XARITA"
  bo'limida endi ikkita rejim bor:
  - **INTERAKTIV** — avvalgidek, o'yin holatiga asoslangan taxminiy pozitsiya.
  - **REAL (GPS)** — ishtirokchilar, mentorlar va koordinatorlarning HAQIQIY
    jonli GPS pozitsiyalari **xuddi shu pirs.jpg rasmiga** proyeksiya qilinadi
    (internet/xarita-plitkalarisiz — tadbir odatda internetsiz lokal Wi-Fi'da
    o'tishi uchun eng amaliy yechim). Buning uchun admin kamida 3 ta ma'lum
    nuqtada (masalan Administratsiya, FINISH va Pirs) jismonan turib "📍 Shu
    yerda GPS olish" tugmasini bosib GPS "kalibrlash" qiladi — shundan keyin
    tizim avtomatik proyeksiya (eng kichik kvadratlar affin transformatsiyasi)
    hisoblaydi. Koordinatorlar allaqachon MA'LUM nuqtasiga biriktirilgani
    uchun ularga alohida GPS kerak emas — nuqtasi asosida avtomatik ko'rinadi.
  - Ishtirokchi jamoasini tanlagach, uning telefoni ILOVA ochiq turgan
    davomida (RADAR bo'limida bo'lmasa ham) fonda jonli GPS'ini serverga
    yuboradi — shu orqali admin REAL xaritada uni ko'ra oladi.

## v7 yangiliklari — HTTPS (GPS/KOMPAS UCHUN) VA MENTOR XARITADA KO'RINISHI
- 🔐 **`python3 run_https.py`** — internetsiz ham ishlaydigan, o'z-o'zini
  imzolagan HTTPS ishga tushirish skripti (batafsili yuqorida). "Kompas
  topilmadi" muammosining asosiy sababi ko'pincha HTTP orqali kirilishi edi —
  GPS/DeviceOrientation ko'pchilik brauzerlarda faqat xavfsiz kontekstda ishlaydi.
- 🗺 **Mentor endi INTERAKTIV xaritada ham ko'rinadi** (avval faqat REAL
  rejimda ko'rinardi) — GPS kalibrlash tayyor bo'lsa, onlayn mentor(lar)
  qaysi xarita rejimida turishidan qat'i nazar 🟤 "M" nishoni bilan chiqadi.
- 🎯 Xarita nishonlari endi rasm chegarasidan (0–100%) chiqib "yo'qolib
  qolmasligi" uchun 2–98% oralig'ida ushlab turiladi (kalibrlash nuqtalari
  chekka bo'lganda ba'zi GPS proyeksiyalari chegaradan chiqib, ko'rinmas
  bo'lib qolishi mumkin edi).
