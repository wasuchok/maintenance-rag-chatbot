# MT RAG Assistant

โปรเจกต์นี้คือระบบ `Django + Chainlit + Ollama + Chroma RAG` สำหรับทำ AI Assistant ที่ตอบจากฐานความรู้ภายในเป็นหลัก โดยรองรับทั้ง

- หน้าแชตผ่าน `Chainlit`
- REST API สำหรับเว็บภายนอก
- การ import ข้อมูลจาก `SQL Server`
- การ import ไฟล์ `xlsx` แบบ `1 row = 1 document`
- การ sync ข้อมูลอัตโนมัติพร้อม `checkpoint`

เหมาะกับ use case เช่น

- ถามตอบข้อมูล policy / knowledge ภายใน
- ค้นหาเคสซ่อมเก่าจากอาการเครื่องจักร
- ดึงข้อมูลจาก SQL Server เข้า RAG เพื่อให้ AI ค้นและสรุปคำตอบ

## Features

- คุยกับ local LLM ผ่าน Ollama
- ใช้ `RAG` จาก `Chroma`
- รองรับคำตอบภาษาไทย อังกฤษ และญี่ปุ่น
- Chainlit login ด้วยบัญชี Django
- แยก chat history ตามผู้ใช้
- มี sidebar ประวัติห้องสนทนา
- รองรับ upload เอกสารเข้า knowledge base
- รองรับ `xlsx` ตระกูล `History-*` และ import แบบ `1 row = 1 document`
- เชื่อม `SQL Server`
- import ข้อมูลจาก `TB_MT_JOB_DETAIL`
- import / sync ข้อมูลจาก `v_MT_JOB_CARD`
- analytics สำหรับคำถามเชิงสถิติ เช่น `เกิดกี่ครั้ง`, `ต่อเดือน`, `ต่อปี`, `บ่อยไหม`
- มี `checkpoint` สำหรับ sync รอบถัดไปอัตโนมัติ
- มี CORS สำหรับเรียก API จากเว็บภายนอก

## Tech Stack

- Python / Django / Django REST Framework
- Chainlit
- Ollama
- ChromaDB
- SQLite
- SQL Server (`python-tds` หรือ `pyodbc`)

## Project Structure

- `chainlit_app.py` : หน้าแชต Chainlit
- `chatbot/views.py` : Django API endpoints
- `chatbot/services/ollama_service.py` : prompt / generation / RAG orchestration
- `chatbot/services/rag_service.py` : embedding / indexing / retrieval
- `chatbot/services/sqlserver_service.py` : SQL Server connection layer
- `chatbot/services/sqlserver_case_ingestion_service.py` : import จาก `TB_MT_JOB_DETAIL`
- `chatbot/services/sqlserver_job_card_ingestion_service.py` : import จาก `v_MT_JOB_CARD`
- `chatbot/services/sqlserver_job_card_sync_service.py` : sync + checkpoint
- `chatbot/services/xlsx_history_ingestion_service.py` : import ไฟล์ `xlsx` แบบ `History-*`
- `run_chainlit.sh` : script สำหรับรัน Chainlit

## Environment

ตัวอย่าง `.env`

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:14b
OLLAMA_THINK=false
OLLAMA_KEEP_ALIVE=15m
OLLAMA_NUM_PREDICT=384
OLLAMA_EMBED_MODEL=nomic-embed-text-v2-moe
RAG_ONLY_MODE=false
RAG_INCLUDE_CHAT_HISTORY=true
RAG_SEARCH_TOP_K=20

SQLSERVER_HOST=192.168.1.10
SQLSERVER_PORT=1433
SQLSERVER_DATABASE=YourDatabase
SQLSERVER_USERNAME=sa
SQLSERVER_PASSWORD=your-password
SQLSERVER_CLIENT=pytds
SQLSERVER_CASES_SCHEMA=dbo
SQLSERVER_CASES_TABLE=TB_MT_JOB_DETAIL
SQLSERVER_JOB_CARD_SCHEMA=dbo
SQLSERVER_JOB_CARD_VIEW=v_MT_JOB_CARD
SQLSERVER_JOB_CARD_SYNC_OVERLAP_MINUTES=60

IMPORT_API_KEY=your-secret-key

CORS_ALLOW_ALL_ORIGINS=true
# CORS_ALLOW_ALL_ORIGINS=false
# CORS_ALLOWED_ORIGINS=http://localhost:3000,http://192.168.1.50:3000
# CORS_ALLOW_CREDENTIALS=true
```

## Setup

ติดตั้ง dependency

```bash
python -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
```

apply migrations

```bash
.venv312/bin/python manage.py migrate
```

สร้าง user สำหรับ login ใน Chainlit

```bash
.venv312/bin/python manage.py createsuperuser
```

โหลด model ที่ใช้

```bash
ollama pull qwen3:14b
ollama pull nomic-embed-text-v2-moe
```

## Run

เปิด Ollama

```bash
ollama serve
```

เปิด Chainlit

```bash
./run_chainlit.sh
```

ค่า default:

- host: `0.0.0.0`
- port: `8100`

เปิด Django API

```bash
.venv312/bin/python manage.py runserver 0.0.0.0:8000
```

## Chainlit

สิ่งที่ทำได้จากหน้าแชต:

- ถามตอบจาก knowledge base
- login ด้วยบัญชี Django
- ดูประวัติห้องจาก sidebar
- เปลี่ยนชื่อ / ลบห้องของตัวเอง
- admin upload / ลบเอกสาร shared
- admin sync SQL ล่าสุด
- admin import ไฟล์ `xlsx`

รายละเอียดเพิ่มเติมดูที่ `chainlit.md`

## REST API

Base URL

```text
http://127.0.0.1:8000/api
```

### Health Check

```http
GET /api/health/
```

### Chat

```http
POST /api/chat/
Content-Type: application/json

{
  "conversation_id": "web-user-001-room-1",
  "message": "Sensor ชำรุด แก้ยังไง"
}
```

ตัวอย่าง response

```json
{
  "conversation_id": "web-user-001-room-1",
  "reply": "จากเคสที่ใกล้เคียง ...",
  "sources": [
    {
      "title": "2024-07-19 10:29:00 | G-46 | Sensor ชำรุด",
      "source": "xlsx-history:History-2024:xxxxxxxxxxxxxxxx",
      "chunk_index": 0,
      "document_id": 1234,
      "distance": 0.12
    }
  ],
  "saved": {
    "user_message_id": 10,
    "assistant_message_id": 11
  }
}
```

### Chat History

```http
GET /api/chat/<conversation_id>/history/
```

### Knowledge

```http
GET /api/knowledge/
POST /api/knowledge/
GET /api/knowledge/<document_id>/
PUT /api/knowledge/<document_id>/
DELETE /api/knowledge/<document_id>/
```

หมายเหตุ:

- ตอนนี้ endpoint แก้ไข knowledge ใช้ได้เฉพาะ admin
- ถ้าเรียกจากเว็บภายนอก ให้ตั้ง CORS ใน `.env`

## Import XLSX

รองรับไฟล์ `xlsx` แบบชีตตระกูล `History-*` เช่น

- `History-2024`
- `History-2023`
- `History-2022`

และจะ import เป็น `1 row = 1 document`

จาก terminal:

```bash
.venv312/bin/python manage.py import_history_xlsx "/path/to/file.xlsx"
```

ถ้าจะระบุชีตเอง:

```bash
.venv312/bin/python manage.py import_history_xlsx "/path/to/file.xlsx" --sheet "History-2024"
```

ถ้าจะเปลี่ยนชื่อที่แสดง:

```bash
.venv312/bin/python manage.py import_history_xlsx "/path/to/file.xlsx" --name "Main Card 2024"
```

## SQL Server

ทดสอบการเชื่อมต่อ

```bash
.venv312/bin/python manage.py test_sqlserver_connection
```

ดูตัวอย่างข้อมูลจาก table

```bash
.venv312/bin/python manage.py preview_sqlserver_table --limit 5
```

### Import จาก `TB_MT_JOB_DETAIL`

```bash
.venv312/bin/python manage.py import_sqlserver_cases
```

แบบจำกัดจำนวน

```bash
.venv312/bin/python manage.py import_sqlserver_cases --limit 20
```

แบบช่วงวันล่าสุด

```bash
.venv312/bin/python manage.py import_sqlserver_cases --days 7
```

### Import จาก `v_MT_JOB_CARD` ผ่าน API

```bash
curl -X POST "http://127.0.0.1:8000/api/knowledge/import/mt-job-cards/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "schema": "dbo",
    "view_name": "v_MT_JOB_CARD",
    "days": 30,
    "limit": 500
  }'
```

## Automation + Checkpoint

ระบบ `sync_mt_job_cards` จะเก็บ checkpoint ไว้ใน SQLite ของ Django เพื่อจำว่า sync ถึง `J_CREATE_DATE` ไหนแล้ว

รัน sync ปกติ:

```bash
.venv312/bin/python manage.py sync_mt_job_cards
```

รอบแรกแบบดึงเฉพาะช่วงล่าสุด:

```bash
.venv312/bin/python manage.py sync_mt_job_cards --bootstrap-days 7
```

บังคับ full sync:

```bash
.venv312/bin/python manage.py sync_mt_job_cards --full
```

ปรับ overlap:

```bash
.venv312/bin/python manage.py sync_mt_job_cards --overlap-minutes 120
```

ตัวอย่าง cron:

```cron
0 2 * * * cd /Users/mac_it/Desktop/django-local-chatbot && /Users/mac_it/Desktop/django-local-chatbot/.venv312/bin/python manage.py sync_mt_job_cards
```

API สำหรับ sync แบบใช้ checkpoint:

```bash
curl -X POST "http://127.0.0.1:8000/api/knowledge/sync/mt-job-cards/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "schema": "dbo",
    "view_name": "v_MT_JOB_CARD",
    "bootstrap_days": 7,
    "overlap_minutes": 120
  }'
```

## Problem Analytics API

ใช้สำหรับถามเชิงสถิติจาก `v_MT_JOB_CARD` เช่น

- ปัญหานี้เกิดกี่ครั้ง
- ต่อเดือนเป็นยังไง
- ต่อปีเป็นยังไง
- เกิดบ่อยไหม

ตัวอย่าง:

```bash
curl -X POST "http://127.0.0.1:8000/api/analytics/mt-job-cards/problem-stats/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "query": "Sensor ชำรุด",
    "schema": "dbo",
    "view_name": "v_MT_JOB_CARD",
    "top_cases": 5,
    "top_groups": 5,
    "monthly_limit": 24,
    "response_language": "th"
  }'
```

response จะคืนทั้ง

- `summary`
- `analytics.total_count`
- `analytics.yearly_counts`
- `analytics.monthly_counts`
- `analytics.top_machines`
- `analytics.top_positions`
- `analytics.top_teams`
- `analytics.recent_cases`

## Data Storage

- `db.sqlite3` : Django data
  - users
  - chat history
  - knowledge document metadata
  - sync checkpoints
- `chroma_data/` : vector store ของ RAG

## Notes

- โปรเจกต์นี้ใช้ `RAG / re-index / sync` เป็นหลัก ไม่ได้ fine-tune model ตรง ๆ
- ถ้าข้อมูลต้นทางมี `updated_at` ในอนาคต ควรเปลี่ยน checkpoint ไปอิง field นั้น จะแม่นกว่า `J_CREATE_DATE`
- Chainlit ในโปรเจกต์นี้มีปัญหาบน Python `3.14` จึงแนะนำใช้ `.venv312`
- ถ้าข้อมูล knowledge เยอะมาก ระบบค้นหา RAG ถูกปรับให้ query แบบแบ่ง batch แล้ว เพื่อหลบปัญหา `too many SQL variables`

## Related Docs

- `chainlit.md`
- `sqlserver.md`
- `api_chat.txt`
