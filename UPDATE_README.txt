เฝ้าน้องฝน v8.1 — ไฟล์ที่ต้องอัปเดตเท่านั้น

อัปทับใน GitHub:
1) index.html
2) sw.js
3) README.md
4) scripts/radar_intelligence.py

ไม่ต้องแก้ rain-watch.yml
ไม่ต้องลบ PWA ที่ติดตั้งแล้ว

หลัง GitHub Pages deploy:
- ผู้ใช้เว็บ: Refresh
- ผู้ใช้ PWA: ปิดแอปให้สนิทแล้วเปิดใหม่; ถ้ายังเก่าให้ Refresh 1 ครั้ง
- หลัง v8.1 นี้ การอัปเดตเวอร์ชันถัดไปจะง่ายขึ้นเพราะหน้าเว็บใช้ network-first และบังคับตรวจ Service Worker update
