# TotalCare local setup

This project is a Django application that can now run locally without cPanel.

## First-time setup

1. Create a virtual environment:
   `py -3.13 -m venv .venv`
2. Activate it:
   `.\.venv\Scripts\Activate.ps1`
3. Install dependencies:
   `pip install -r requirements.txt`
4. Create your local env file:
   `Copy-Item .env.example .env`
5. Run migrations:
   `python manage.py migrate`
6. Start the app:
   `python manage.py runserver`

Open `http://127.0.0.1:8000/`.

## Database behavior

- Local development defaults to `db.sqlite3`.
- Production can use PostgreSQL by setting either `DATABASE_URL` or the `DB_*` values in `.env`.

## Recommended workflow

1. Make changes locally.
2. Test locally.
3. Commit and push to GitHub.
4. Pull or deploy from GitHub to production.

## Important notes

- Do not commit `.env`.
- Do not commit your virtual environment folder.
- cPanel-specific files like `passenger_wsgi.py` can stay for production, but local development should use `python manage.py runserver`.
