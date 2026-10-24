# The Kavery Engineering College AI Helpdesk

A lightweight FAQ chatbot for **The Kavery Engineering College (TKEC)**, Mecheri, Mettur, Salem. It answers common questions about admissions, courses, fees, hostels, transport, placements, academics, and contacts using the supplied college FAQ data. The ChatGPT-inspired interface includes a searchable, category-filtered FAQ sidebar and quick-start chat.

## Objective

Provide students and visitors with fast, friendly answers from a curated college FAQ database. The chatbot uses retrieval-based text similarity; it does not generate answers with a language model.

## Tech stack

- Python and Flask for the backend
- `difflib.SequenceMatcher` for NLP-style text similarity
- Supabase (PostgreSQL) for hosted FAQ and site settings storage, with local JSON fallback
- HTML, CSS, and JavaScript for the responsive chat interface

## Run locally

Requires Python 3.8 or later.

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in your browser. The Flask app listens on port 5000.

## Connect Supabase

1. Create a Supabase project and open **Project Settings → API**. Copy the project URL and the **service_role** key. The service-role key is private server-side only; never put it in browser JavaScript or commit it.
2. Open the Supabase **SQL Editor** and run (or rerun) the SQL in `supabase_schema.sql`. It creates the public image bucket, read policy, and FAQ image column as well as the tables.

   ```dotenv
   SUPABASE_URL=https://your-project-id.supabase.co
   SUPABASE_KEY=your-private-service-role-key
   FLASK_SECRET_KEY=replace-with-a-long-random-secret
   ```

3. On the first app request, the existing `kavery_faq.json` is copied to the Supabase `faqs` table if it is empty. Website settings are created in `app_settings`; subsequent admin edits save directly to Supabase. Admins can add a JPG, PNG, or WebP image (up to 5 MB) to an FAQ. Images are stored in the `helpdesk-images` Supabase Storage bucket and shown with that FAQ's chat answer.
4. Install the dependencies and restart the app:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe app.py
   ```

If Supabase credentials are left blank, the app continues using local JSON files. The `.env` file is excluded by `.gitignore`.

## Admin page

Open **http://127.0.0.1:5000/admin/login** to sign in. Set the admin password in the same PowerShell window before starting the app:

```powershell
$env:TKEC_ADMIN_USERNAME = "admin"
$env:TKEC_ADMIN_PASSWORD = "choose-a-long-unique-password"
python app.py
```

Use the configured username and password at sign-in. The admin page at `/admin` lets you add, search, edit, and delete FAQ entries, update the public helpdesk text, quick-reply topics, fallback response, and contact details, and change the admin password. With Supabase connected, FAQs and website settings are saved in Supabase; without it, they use `kavery_faq.json` and `site_settings.json`. Admin credentials are saved locally as a salted password hash in `admin_credentials.json`.

On first startup, `TKEC_ADMIN_USERNAME` defaults to `admin`; `TKEC_ADMIN_PASSWORD` must be set to initialize admin sign-in. After initialization, password changes are saved in `admin_credentials.json` and persist across restarts. For a deployed site, use HTTPS, set a persistent random `FLASK_SECRET_KEY`, and set `COOKIE_SECURE=true`; do not use the built-in Flask development server in production.

## How answers are selected

The backend compares a normalized question with the FAQ questions and keywords, then adds a small keyword-match bonus. A result is returned only when its score is greater than `0.4`; otherwise the chatbot gives a contact fallback. FAQ matching is local and lightweight, so responses are typically immediate and do not require an external AI service.

## Why retrieval-based FAQ matching instead of a pure LLM?

This is a small retrieval-only, RAG-style FAQ system—not a generative RAG pipeline. It returns answers already written in `kavery_faq.json`; it does not ask an LLM to compose facts. That keeps responses grounded in the curated college information, avoids generative hallucinations, and makes replies fast without external model calls. If no FAQ is a sufficiently close match, it directs the user to the college contact details rather than guessing.

## College details represented

- **The Kavery Engineering College**, Mecheri, Mettur, Salem - 636453, Tamil Nadu
- College code **2625**; established **2006**; autonomous from **2024**
- **NAAC A+**, **AICTE approved**, and **Anna University affiliated**
- Website: [tkec.edu.in](https://tkec.edu.in); email: tkec@rediffmail.com
- Phone: 04298-279158, 9842350156, 9524982156

The FAQ database is in `kavery_faq.json` and currently contains 628 FAQ entries across categorized college topics. It includes branch-specific questions for all listed BE/BTech and PG programmes. Each entry includes keywords used by the chatbot matcher and searchable from the sidebar. Add or update entries with verified information when college details change.
