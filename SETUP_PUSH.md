# เปิด Push แจ้งเตือนตอนปิดแอป (OneSignal)

Radar Intelligence และ Dashboard ใช้ได้ก่อน โดยไม่ต้องตั้ง OneSignal

ถ้าต้องการ Push ขณะปิดแอป:
1. สร้าง OneSignal App แบบ Web Push และใช้ GitHub Pages URL
2. GitHub → Settings → Secrets and variables → Actions
3. เพิ่ม `ONESIGNAL_APP_ID`
4. เพิ่ม `ONESIGNAL_REST_API_KEY`
5. Actions → NongFon Rain Watch → Run workflow
6. เปิดเว็บ แล้วกด “เปิดแจ้งเตือน” และอนุญาต

ห้ามใส่ REST API Key ลงใน index.html หรือ push-config.js
