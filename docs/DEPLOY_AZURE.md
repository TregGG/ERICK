# Deploying Campus Rick on an Azure VM (trial deployment)

Step-by-step guide to put Campus Rick on a public HTTPS address using an Azure for Students VM.
Replace everything in `<angle brackets>` with your own values.

## Context (read this first, especially if you are an AI assistant helping with this)

- **Stack:** FastAPI backend (`backend/`) + PostgreSQL 16 with PostGIS + WebSockets (`/ws`). The same
  FastAPI process serves the passenger, driver and admin web apps (`backend/main.py`, the static mounts
  near the end of the file). The entry point is `run-local.py`, which reads `HOST`/`PORT` from `backend/.env`.
- **Why a VM and not Vercel/serverless:** `backend/main.py` starts an always-on dispatch loop
  (`dispatch.run_forever`) in its lifespan, and live tracking needs persistent WebSockets. Both need
  one process that is always running. Run **exactly one instance**: realtime state is in memory.
- **Login mode for this trial:** `APP_ENV=development` with `OTP_DEV_MODE=true`, so the OTP is shown on
  screen. Production mode (`APP_ENV=production`) refuses to start without a real SMS provider
  (`backend/config.py`, `validate_for_runtime`). **Dev mode means anyone can log in as any phone
  number, so share the link only with trusted testers.** Email/SMS OTP is a separate, later task.
- **Decisions already made:**
  - Azure for Students subscription. Central India was not allowed, so use the nearest allowed region
    (South India, or Southeast Asia).
  - Image: **Ubuntu Server 22.04 LTS – x64 Gen2** (24.04 x64 was not offered).
  - Size: **Standard_B2ats_v2** (x64/AMD, 2 vCPU, 1 GB RAM, free-services eligible). Do NOT use
    B2pts_v2: that one is ARM and won't run the x64 image.
  - PostgreSQL **16** from the official PGDG apt repo, to match local Docker (`postgis/postgis:16-3.4`).
  - Python **3.13** via `uv` (the project targets 3.13; Ubuntu 22.04 ships 3.10).
  - Caddy as reverse proxy for automatic HTTPS + WebSocket proxying.
  - No Docker on the server (decided against it for now).
- **VM details used below:** VM name `COMM-OUTREACH`, admin user `azureuser`, SSH key
  `COMM-OUTREACH_key.pem`, DNS label `campusrick`. Your address is
  `campusrick.centralindia.cloudapp.azure.com`.
  Below it is written as `<HOSTNAME>`. The database password is written as `<DB_PASSWORD>`; keep the
  real one in a password manager, never in this file (the repository is public).

---

## Part 1: Create the VM (Azure Portal)

1. Portal search → **Virtual machines** → **+ Create** → **Azure virtual machine**.
2. **Basics** tab:
   - Subscription: **Azure for Students**
   - Resource group: **Create new** → `campusrick-rg`
   - Virtual machine name: `COMM-OUTREACH`
   - Region: an allowed region near India (**South India** or **Southeast Asia**). If the image shows
     "not valid for the specified location", change the region, then **re-select the image**.
     The allowed regions are listed in Portal → **Policy** → **Assignments** →
     "Allowed resource deployment regions".
   - Availability options: **No infrastructure redundancy required**
   - Security type: **Standard**
   - Image: **Ubuntu Server 22.04 LTS – x64 Gen2**
   - Size: **Standard_B2ats_v2** (free services eligible)
   - Authentication type: **SSH public key**, username `azureuser`, **Generate new key pair**,
     **RSA SSH Format**, key pair name `COMM-OUTREACH_key`
   - Public inbound ports: **Allow selected ports** → tick **all three: SSH (22), HTTP (80), HTTPS (443)**.
     Without 80/443 the website is unreachable and Caddy cannot get a certificate.
     The yellow "all IP addresses" warning is expected for a public website.
3. **Disks** tab: keep the defaults.
4. **Networking** tab: keep the defaults, and make sure **"Delete public IP when VM is deleted" is unticked**.
5. **Review + create** → **Create** → **Download private key and create resource**. Save the `.pem`;
   it cannot be downloaded again.
6. **Go to resource** → **Overview** → next to **DNS name** click **Not configured**:
   - DNS name label: `campusrick` → **Save**
   - Check that IP **Assignment** is **Static** (the default for Standard SKU).
   - Note the full DNS name. That is your `<HOSTNAME>`.

## Part 2: Connect from your laptop (Linux)

```bash
mv ~/Downloads/COMM-OUTREACH_key.pem ~/.ssh/
chmod 400 ~/.ssh/COMM-OUTREACH_key.pem
ssh -i ~/.ssh/COMM-OUTREACH_key.pem azureuser@<HOSTNAME>
```

Type `yes` at the first prompt. **Everything below runs on the VM.**

## Part 3: Swap and system packages

```bash
# 2 GB swap (the VM has only 1 GB RAM)
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# System updates
sudo apt update && sudo apt upgrade -y

# PostgreSQL 16 + PostGIS from the official PostgreSQL repo (22.04's own Postgres is 14)
sudo apt install -y git curl postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-16 postgresql-16-postgis-3

# Caddy (official repo)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

If a purple "which services should be restarted" screen appears, press Enter.

Check: `psql --version` should print 16.x.

## Part 4: Database

Generate a password and keep it somewhere safe. Avoid `@`, `/` and `:`, because they break the URL in `.env`:

```bash
openssl rand -hex 24
```

Create the user, database and extensions (this mirrors `docker/postgres-init/01-extensions.sql`):

```bash
sudo -u postgres psql <<'SQL'
CREATE USER erick WITH PASSWORD '<DB_PASSWORD>';
CREATE DATABASE erickshaw OWNER erick;
\c erickshaw
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
SQL
```

Postgres listens only on localhost by default. Do **not** open port 5432 in Azure.

## Part 5: Get the code

```bash
sudo mkdir -p /opt/erick && sudo chown azureuser:azureuser /opt/erick
git clone https://github.com/TregGG/ERICK.git /opt/erick
```

If the repo is private: GitHub → Settings → Developer settings → **Fine-grained personal access token**,
with access to only the `ERICK` repo and **Contents: Read-only**. Paste the token when git asks for a password.

## Part 6: Python 3.13 and dependencies

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
cd /opt/erick
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r backend/requirements.txt
```

## Part 7: `backend/.env`

Generate a new JWT secret. **Do not reuse any old secret**, because an old `backend/.env` exists in git history:

```bash
/opt/erick/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run `nano /opt/erick/backend/.env` and paste:

```ini
APP_ENV=development
HOST=127.0.0.1
PORT=8091
DATABASE_URL=postgresql+asyncpg://erick:<DB_PASSWORD>@localhost:5432/erickshaw
JWT_SECRET=<GENERATED_SECRET>
JWT_EXPIRY_HOURS=24
OTP_DEV_MODE=true
SMS_PROVIDER=console
CORS_ORIGINS=[]
ROUTING_BASE_URL=https://router.project-osrm.org
SIMULATION_ENABLED=false
DISPATCH_ENABLED=true
```

Save with **Ctrl+O**, Enter, then exit with **Ctrl+X**. `HOST=127.0.0.1` is intentional: only Caddy is exposed to the internet.

## Part 8: Set up the database tables and your admin account

```bash
cd /opt/erick
.venv/bin/python -m backend.manage migrate
.venv/bin/python -m backend.manage load-campus
.venv/bin/python -m backend.manage create-admin --phone <YOUR_10_DIGIT_NUMBER> --name "Transport Office"
```

⚠️ **Do NOT run `seed-demo` or `bootstrap-dev`.** They create a demo admin with a publicly known
phone number (`+917000000001`), and in dev OTP mode anyone could log in as that admin.

## Part 9: Run the backend as a service (systemd)

Run `sudo nano /etc/systemd/system/erick.service` and paste:

```ini
[Unit]
Description=Campus Rick backend
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=azureuser
WorkingDirectory=/opt/erick
ExecStart=/opt/erick/.venv/bin/python run-local.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now erick
sudo systemctl status erick                  # expect "active (running)"
curl -I http://127.0.0.1:8091/passenger/     # expect HTTP 200
```

## Part 10: HTTPS with Caddy

Run `sudo nano /etc/caddy/Caddyfile`, **delete all existing content**, and paste (with your real hostname):

```
<HOSTNAME> {
    reverse_proxy 127.0.0.1:8091
}
```

Then:

```bash
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30 --no-pager   # look for "certificate obtained successfully"
```

Caddy gets and renews the Let's Encrypt certificate automatically, and proxies WebSockets as-is.
`run-local.py` already trusts forwarded headers from 127.0.0.1. The frontend sends a WebSocket ping
every 25s (`shared/js/realtime.js`), which keeps connections alive past Azure's 4-minute idle timeout.

Open `https://<HOSTNAME>/` on your phone. You should see the padlock and the landing page.

## Part 11: First real test

1. Open `https://<HOSTNAME>/admin/` and log in with your number. The OTP is shown on screen.
2. Phone A: open `/driver/` and sign up as a driver. Approve that driver in the admin app.
3. Phone B (on mobile data): open `/passenger/`, sign up, and book a ride.
4. Phone A gets the request. Accept it and move around. Phone B should see the rickshaw move live.
5. Turn on airplane mode on one phone for about 30s, then turn it off. The app should reconnect by itself.
6. `sudo reboot` the VM. The site should come back on its own after about a minute.

## Part 12: Daily operations

**Deploy new code:** push to `master`. GitHub Actions runs the tests and deploys (see Part 13).
To deploy by hand on the VM:
```bash
/opt/erick/deploy/deploy.sh
```
It pulls `origin/master`, installs dependencies if `backend/requirements.txt` changed, migrates,
restarts and health-checks; if the new version is unhealthy it rolls the code back.

**Logs:**
```bash
journalctl -u erick -f      # backend
journalctl -u caddy -f      # HTTPS / proxy
```

**Nightly backups**, kept for 7 days:
```bash
mkdir -p ~/backups
crontab -e
```
Add:
```
0 3 * * * sudo -u postgres pg_dump erickshaw | gzip > /home/azureuser/backups/erick-$(date +\%F).sql.gz && find /home/azureuser/backups -mtime +7 -delete
```

**Credit:** check Portal → **Cost Management** occasionally. The VM should be free; the public IP and
traffic may cost a few cents a month.

## Part 13: Automatic deploys and production mode

### Deploy on every push (one-time setup)

Every push to `master` runs the backend tests in GitHub Actions (`.github/workflows/deploy.yml`). If
they pass, Actions connects to the VM over SSH and runs `deploy/deploy.sh`.

1. **On your laptop**, create a key used only for deploys:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/erick_deploy -N "" -C "github-deploy"
   cat ~/.ssh/erick_deploy.pub
   ```
2. **On the VM**, run `nano ~/.ssh/authorized_keys` and add one new line (the existing key stays):
   ```
   command="/opt/erick/deploy/deploy.sh",no-port-forwarding,no-pty,no-agent-forwarding,no-X11-forwarding <contents of erick_deploy.pub>
   ```
   With `command=`, this key can only run the deploy script. It cannot open a shell.
3. **On your laptop**, get the server's host key:
   ```bash
   ssh-keyscan <HOSTNAME>
   ```
4. GitHub → repository → **Settings → Secrets and variables → Actions → New repository secret**. Add:
   | Name | Value |
   | --- | --- |
   | `DEPLOY_HOST` | `<HOSTNAME>` |
   | `DEPLOY_SSH_KEY` | the full contents of `~/.ssh/erick_deploy` (the private key) |
   | `DEPLOY_KNOWN_HOSTS` | the output of `ssh-keyscan` from step 3 |
5. Push any commit to `master` and open the **Actions** tab. Both **test** and **deploy** should go green.

A failed test means nothing is deployed. A failed deploy (unhealthy after restart) rolls back the code
automatically, but **not** database migrations.

### Switch between development and production (one-time setup)

```bash
sudo ln -sf /opt/erick/deploy/erick-mode.sh /usr/local/bin/erick-mode
```

Add the Twilio Verify values to `backend/.env` once (see `docs/OPERATIONS.md`, "SMS login with Twilio Verify"):
```ini
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_VERIFY_SERVICE_SID=VA...
```

Then:
```bash
erick-mode production    # real SMS login via Twilio Verify, no codes on screen
erick-mode development   # codes shown on screen, no SMS (trusted testers only)
erick-mode status        # current mode (secrets masked) and what the running server reports
```

`erick-mode` changes only `APP_ENV`, `OTP_DEV_MODE`, `SMS_PROVIDER` and (in production)
`SIMULATION_ENABLED`. It checks the new settings with the backend's own startup guard before
restarting. If anything is missing (for example a Twilio value, or a `JWT_SECRET` shorter than 32
characters), it says what and keeps the old settings. If the server does not come up healthy, it
restores the previous `backend/.env`.

Production mode needs the real admin account from Part 8. Demo accounts cannot be created in production.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Site doesn't load at all | Ports 80/443 not open: VM → **Networking** → add inbound rules for 80 and 443 |
| Caddy log shows certificate errors | Same as above, or the hostname in the Caddyfile doesn't match the DNS name exactly |
| `erick` service keeps restarting | `journalctl -u erick -n 50`. Usually a wrong `DATABASE_URL`/password, or migrations not run |
| `password authentication failed` | The password in `.env` doesn't match the one used in Part 4 |
| `type "geography" does not exist` | The PostGIS extension is missing: re-run the `CREATE EXTENSION` lines from Part 4 |
| GPS doesn't work on the phone | You must use the `https://` address; browsers block location on plain http |
| Driver location freezes | Driver's screen is off or the app is backgrounded (browser limit). Keep the app open |
| Out of memory / very slow | Check the swap is active (`free -h`); see Part 3 |

## Known limits of this trial setup

- **Dev OTP mode:** anyone can log in as any number. Trusted testers only; switch with `erick-mode production` (Part 13).
- **Driver GPS pauses in the background.** A native wrapper (e.g. Capacitor) with background location fixes this later.
- **Routing uses the public OSRM demo server** (~1 request/second). Fine for a pilot.
