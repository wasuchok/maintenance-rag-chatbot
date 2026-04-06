# SQL Server Setup

โปรเจกต์นี้รองรับการเชื่อมต่อ SQL Server เป็นฐานข้อมูลภายนอกสำหรับดึงข้อมูลเคส/อาการ โดยยังใช้ `SQLite` เดิมของ Django ต่อไปตามปกติ

## Environment

เพิ่มค่าพวกนี้ใน `.env`

```env
SQLSERVER_HOST=192.168.1.10
SQLSERVER_PORT=1433
SQLSERVER_DATABASE=YourDatabase
SQLSERVER_USERNAME=sa
SQLSERVER_PASSWORD=your-password
SQLSERVER_CLIENT=pytds
SQLSERVER_DRIVER=ODBC Driver 18 for SQL Server
SQLSERVER_ENCRYPT=no
SQLSERVER_TRUST_SERVER_CERTIFICATE=yes
SQLSERVER_TRUSTED_CONNECTION=no
SQLSERVER_CONNECTION_TIMEOUT=30
SQLSERVER_CASES_SCHEMA=dbo
SQLSERVER_CASES_TABLE=YourCasesTable
```

## Commands

ทดสอบการเชื่อมต่อ

```bash
python manage.py test_sqlserver_connection
```

ดูตัวอย่างข้อมูลจาก table

```bash
python manage.py preview_sqlserver_table --table YourCasesTable --limit 5
```

ถ้าตั้ง `SQLSERVER_CASES_TABLE` ไว้แล้ว จะรันสั้น ๆ ได้เลย

```bash
python manage.py preview_sqlserver_table --limit 5
```

import เคสจาก SQL Server เข้า knowledge base และ index เข้า RAG

```bash
python manage.py import_sqlserver_cases
```

ถ้าต้องการลองบางส่วนก่อน

```bash
python manage.py import_sqlserver_cases --limit 20
```

import ข้อมูลจาก view `v_MT_JOB_CARD` ผ่าน API เพื่อเข้า knowledge base

```http
POST /api/knowledge/import/mt-job-cards/
Content-Type: application/json
X-API-Key: your-import-key

{
  "schema": "dbo",
  "view_name": "v_MT_JOB_CARD",
  "days": 30,
  "limit": 500
}
```

ถ้าไม่ได้ตั้ง `IMPORT_API_KEY` ไว้ API นี้จะใช้ได้เฉพาะตอน request นั้นเป็น admin ของ Django เท่านั้น

sync แบบ automation + checkpoint สำหรับ `v_MT_JOB_CARD`

```bash
python manage.py sync_mt_job_cards
```

พฤติกรรม:

- รอบแรก ถ้ายังไม่มี checkpoint จะดึงทั้งหมด
- รอบถัดไปจะดึงต่อจาก `J_CREATE_DATE` ล่าสุดที่เคย sync สำเร็จ
- มี overlap ย้อนหลังเป็นนาทีเพื่อกันข้อมูลตกหล่น และระบบจะ upsert/skip ให้เอง

ถ้าต้องการตั้ง bootstrap ให้รอบแรกเอาเฉพาะช่วงล่าสุดก่อน

```bash
python manage.py sync_mt_job_cards --bootstrap-days 7
```

ถ้าต้องการบังคับ full sync ใหม่

```bash
python manage.py sync_mt_job_cards --full
```

ถ้าต้องการเปลี่ยน overlap นาที

```bash
python manage.py sync_mt_job_cards --overlap-minutes 120
```

ตัวอย่าง cron sync ทุกวันตอนตีสอง

```cron
0 2 * * * cd /Users/mac_it/Desktop/django-local-chatbot && /Users/mac_it/Desktop/django-local-chatbot/.venv312/bin/python manage.py sync_mt_job_cards
```

ถ้าต้องการยิงผ่าน API แบบใช้ checkpoint

```http
POST /api/knowledge/sync/mt-job-cards/
Content-Type: application/json
X-API-Key: your-import-key

{
  "schema": "dbo",
  "view_name": "v_MT_JOB_CARD",
  "bootstrap_days": 7,
  "overlap_minutes": 120
}
```

ถ้าต้องการ sync เฉพาะข้อมูลที่อยู่ในช่วง N วันล่าสุด

```bash
python manage.py import_sqlserver_cases --days 7
python manage.py sync_sqlserver_cases --days 7
```

สั่ง sync ล่าสุดด้วยชื่อคำสั่งที่ใช้กับ cron ได้ง่ายขึ้น

```bash
python manage.py sync_sqlserver_cases
```

ตัวอย่าง cron sync ทุกวันตอนตีสอง

```cron
0 2 * * * cd /Users/mac_it/Desktop/django-local-chatbot && /Users/mac_it/Desktop/django-local-chatbot/.venv312/bin/python manage.py sync_sqlserver_cases
```

ตัวอย่าง cron sync เฉพาะ 7 วันล่าสุดทุกวันตอนตีสอง

```cron
0 2 * * * cd /Users/mac_it/Desktop/django-local-chatbot && /Users/mac_it/Desktop/django-local-chatbot/.venv312/bin/python manage.py sync_sqlserver_cases --days 7
```

## Notes

- ค่าเริ่มต้นในโปรเจกต์นี้ใช้ `python-tds` (`SQLSERVER_CLIENT=pytds`) เพราะตั้งต้นง่ายกว่าและไม่ต้องพึ่ง ODBC driver ของระบบ
- ถ้าต้องการใช้ ODBC ภายหลัง ค่อยเปลี่ยนเป็น `SQLSERVER_CLIENT=pyodbc`
- ถ้าใช้ `pyodbc` บน macOS ต้องมี ODBC driver ของ SQL Server ให้พร้อมก่อน เช่น Microsoft ODBC Driver 18
- คำสั่ง import ใช้ `CARD_ID` เป็นคีย์อ้างอิงคงที่ ถ้ารันซ้ำจะแก้เฉพาะแถวที่เปลี่ยนและข้ามแถวที่เหมือนเดิม
- เอกสารที่ import จาก SQL Server จะถูกเก็บเป็น knowledge แบบ `shared`
- โหมด `--days N` ในเวอร์ชันนี้ใช้คอลัมน์ `Create_date` เป็นตัวกรองข้อมูลล่าสุด
- API import `mt-job-cards` ใช้คอลัมน์ `J_CREATE_DATE` เป็นตัวกรองข้อมูลล่าสุด
- ถ้าต้องการเปิดให้เว็บภายนอกยิง API import ได้ แนะนำตั้ง `IMPORT_API_KEY=your-secret-key` ใน `.env`
- คำสั่ง `sync_mt_job_cards` และ API `/api/knowledge/sync/mt-job-cards/` ใช้ checkpoint เก็บ cursor ล่าสุดไว้ในฐานข้อมูลของ Django
- ค่า overlap สำหรับ checkpoint ปรับได้จาก `.env` ด้วย `SQLSERVER_JOB_CARD_SYNC_OVERLAP_MINUTES`
