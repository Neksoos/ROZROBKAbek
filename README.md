# Kyhranu Backend (FastAPI)

## Запуск локально
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Потрібно налаштувати `DATABASE_URL` у `.env` (див. `.env.example`).
