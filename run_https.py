# -*- coding: utf-8 -*-
"""
HTTPS orqali ishga tushirish — GPS (Geolocation) va kompas (DeviceOrientation)
ko'pchilik telefon brauzerlarida FAQAT xavfsiz kontekst (https:// yoki localhost)da
ishlaydi. Oddiy `uvicorn main:app` (http://) bilan RADAR, kompas va mentor/ishtirokchi
jonli GPS ulashishi telefonlarda ishlamasligi mumkin.

Ishlatish (internetsiz lokal Wi-Fi/hotspotda ham ishlaydi):
    python3 run_https.py

Birinchi marta ishga tushganda `main.py` avtomatik o'z-o'zini imzolagan sertifikat
(cert.pem, key.pem) yaratadi (agar `openssl` o'rnatilgan bo'lsa). Telefon brauzeri
ochilganda "ulanish xavfsiz emas" deb bir marta ogohlantiradi — "Qo'shimcha"
(Advanced) -> "Baribir davom etish" (Proceed) tugmasini bosish kifoya, shundan
keyin GPS/kompasga to'liq ruxsat beriladi.
"""
import os
import uvicorn
import main  # import qilish paytida main.py o'zi cert.pem/key.pem borligini tekshiradi/yaratadi

PORT = int(os.environ.get("PORT", "8100"))

if __name__ == "__main__":
    if os.path.exists(main.CERT_FILE) and os.path.exists(main.KEY_FILE):
        print(f"HTTPS bilan ishga tushmoqda: https://<KOMPYUTER_IP>:{PORT}")
        uvicorn.run("main:app", host="0.0.0.0", port=PORT,
                    ssl_keyfile=main.KEY_FILE, ssl_certfile=main.CERT_FILE)
    else:
        print("OGOHLANTIRISH: sertifikat topilmadi (openssl o'rnatilmagan bo'lishi mumkin) — "
              "oddiy HTTP bilan ishga tushmoqda. GPS/kompas telefonlarda ishlamasligi mumkin.")
        uvicorn.run("main:app", host="0.0.0.0", port=PORT)
