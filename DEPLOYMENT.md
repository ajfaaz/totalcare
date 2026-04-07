# TotalCare deployment guide

This project now supports a safer workflow:

1. Edit locally
2. Test locally
3. Push to GitHub
4. Deploy to cPanel production

## Local workflow

Run locally with:

`.\.venv\Scripts\python.exe manage.py runserver`

Before pushing:

`.\.venv\Scripts\python.exe manage.py check`

## Production reality on cPanel

This project is currently deployed with Passenger and WSGI:

- `.htaccess` points to Passenger Python
- `passenger_wsgi.py` loads `totalcare.settings`
- cPanel is not using the `Procfile`
- cPanel is not using `ASGI` for this app

That means normal Django pages will work, but Django Channels WebSockets are not served by this setup.

## Important limitation

The project contains WebSocket routing in `totalcare/asgi.py`, `billing/routing.py`, and `messaging/routing.py`.

Because production is using WSGI on cPanel:

- normal HTTP views should work
- WebSocket features may not work in production
- the `Procfile` with `gunicorn` is not used by cPanel Passenger

If you need real-time messaging over WebSockets in production, you will need an ASGI-capable deployment target such as:

- a VPS with `daphne` or `uvicorn`
- Render, Railway, Fly.io, or similar ASGI-friendly hosting
- a reverse-proxy setup that supports ASGI

## Production environment settings

Do not store production secrets in git. Keep them in production environment variables or a server-only `.env`.

Recommended production values:

- `DEBUG=False`
- `SECRET_KEY=<real secret>`
- `ALLOWED_HOSTS=totalcare.arewanetventures.com,.totalcare.arewanetventures.com`
- `CSRF_TRUSTED_ORIGINS=https://totalcare.arewanetventures.com,https://*.totalcare.arewanetventures.com`
- `DATABASE_URL=<production postgres url>` or `DB_*` values
- `SESSION_COOKIE_DOMAIN=.totalcare.arewanetventures.com`
- `CSRF_COOKIE_DOMAIN=.totalcare.arewanetventures.com`
- `EMAIL_HOST_USER=<production email>`
- `EMAIL_HOST_PASSWORD=<production app password>`

## Recommended cPanel deploy steps

### Option 1: cPanel Git Version Control

If your hosting supports cPanel Git deployment:

1. Create or connect the repo in cPanel Git Version Control
2. Set the branch to `main`
3. Pull latest changes from GitHub
4. Activate the server virtualenv
5. Install/update dependencies:
   `pip install -r requirements.txt`
6. Run migrations:
   `python manage.py migrate`
7. Collect static files if needed:
   `python manage.py collectstatic --noinput`
8. Restart the Passenger app

### Option 2: manual pull on server

If you deploy from SSH:

1. SSH into the server
2. Go to the app folder:
   `cd /home/jvlbvywb/totalcare.arewanetventures.com`
3. Pull changes:
   `git pull origin main`
4. Activate the cPanel virtualenv
5. Run:
   `pip install -r requirements.txt`
6. Run:
   `python manage.py migrate`
7. Run:
   `python manage.py collectstatic --noinput`
8. Restart Passenger:
   `touch tmp/restart.txt`

## One-command deployment script

This repo includes `deploy.sh` for cPanel servers that do not have `rsync`.

Run:

`bash /home/jvlbvywb/repositories/totalcare/deploy.sh`

It will:

1. pull latest code from GitHub
2. copy tracked app files into the live app folder
3. skip server-only files like `.env`, `env`, `db.sqlite3`, `public`, `staticfiles`, and logs
4. run `migrate`
5. run `collectstatic`
6. restart Passenger

## Safe release checklist

For each release:

1. Test locally first
2. Commit and push to GitHub
3. Pull on production
4. Install any new dependencies
5. Run migrations
6. Collect static files
7. Restart the app
8. Open the live site and test login, dashboard, billing flow, and messaging flow

## What to watch after deployment

- login failures caused by wrong cookie or host settings
- missing CSS or images caused by static file issues
- migration errors caused by schema mismatch
- messaging pages that load but real-time updates do not work because WebSockets are not active on Passenger WSGI


## For your next commit, use:

git commit -m "Set up local and production deployment workflow for TotalCare"
git push origin main


## For production server commands, use:

cd /home/jvlbvywb/totalcare.arewanetventures.com
git pull origin main
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
touch tmp/restart.txt

## Perfect. Your cPanel Git repo is here:

/home/jvlbvywb/repositories/totalcare

Your live app is here:

/home/jvlbvywb/totalcare.arewanetventures.com

So the next step is to deploy from the repo folder into the live app folder.

Run this carefully:

cd /home/jvlbvywb/repositories/totalcare
git status
git pull origin main
If that works, copy the updated code into the live app folder:

rsync -av --delete --exclude '.git' --exclude '.env' /home/jvlbvywb/repositories/totalcare/ /home/jvlbvywb/totalcare.arewanetventures.com/
Then go to the live app folder and run Django deploy steps:

cd /home/jvlbvywb/totalcare.arewanetventures.com
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
touch tmp/restart.txt
After that:

open the site
test login
test dashboard
test one billing flow
Important:

this rsync --delete is correct only if /home/jvlbvywb/repositories/totalcare is now your full source of truth
it will remove files in the live folder that are not in GitHub, except .env because we excluded it
Before you run the rsync, I want you to quickly confirm one thing for safety. Run:

ls /home/jvlbvywb/totalcare.arewanetventures.com
ls /home/jvlbvywb/repositories/totalcare
Send me that, and I’ll confirm it’s safe to run the sync command exactly as written.
