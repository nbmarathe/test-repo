# TradingJournalApp

A production-oriented trading journal web application built with FastAPI, Jinja2 templates, Pandas, Tailwind CSS, and CSV storage.

## Features

- Tradebook import and validation (`/import`)
- Open trades dashboard with editable fields (`/open_trades`)
- Monthly and quarterly summary (`/summary`)
- Analytics with filters + cumulative PnL chart (`/analytics`)

## Windows Setup

1. Install Python 3.10+ from the official Python website and ensure **Add Python to PATH** is enabled.
2. Open Command Prompt and navigate to this project directory:
   ```bat
   cd TradingJournalApp
   ```
3. Create a virtual environment:
   ```bat
   python -m venv .venv
   ```
4. Activate virtual environment:
   ```bat
   .venv\Scripts\activate
   ```
5. Install dependencies:
   ```bat
   pip install -r requirements.txt
   ```
6. Run the server:
   ```bat
   uvicorn main:app --reload
   ```
7. Open the app:
   - http://127.0.0.1:8000

## Auto-start using Windows Task Scheduler

1. Open **Task Scheduler** → **Create Task**.
2. General tab:
   - Name: `TradingJournalApp`
   - Select **Run whether user is logged on or not**.
3. Triggers tab:
   - New → Begin task: **At startup** (or At log on).
4. Actions tab:
   - New → Action: **Start a program**
   - Program/script:
     ```
     C:\path\to\TradingJournalApp\.venv\Scripts\python.exe
     ```
   - Add arguments:
     ```
     -m uvicorn main:app --host 127.0.0.1 --port 8000
     ```
   - Start in:
     ```
     C:\path\to\TradingJournalApp
     ```
5. Save and test by right-clicking task → **Run**.

## Optional Windows Service (NSSM)

1. Download NSSM and extract it.
2. Open elevated Command Prompt and run:
   ```bat
   nssm install TradingJournalApp
   ```
3. Configure:
   - **Path**: `C:\path\to\TradingJournalApp\.venv\Scripts\python.exe`
   - **Startup directory**: `C:\path\to\TradingJournalApp`
   - **Arguments**: `-m uvicorn main:app --host 127.0.0.1 --port 8000`
4. Start service:
   ```bat
   nssm start TradingJournalApp
   ```
5. Set Startup type to Automatic in Services.
