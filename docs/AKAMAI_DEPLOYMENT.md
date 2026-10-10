# Deploy the MVP on Akamai Cloud

One Compute Instance runs FastAPI/Strands, React/Nginx, and a named SQLite volume.
NWS, Open-Meteo and the model API are upstream services. Email delivery is absent.

SQLite runs inside the Python backend and stores its file on the instance's
persistent Docker volume. You do not need to create an Akamai Managed Database.
Akamai also offers [managed MySQL](https://techdocs.akamai.com/cloud-computing/docs/database-engines-plans),
but this MVP follows the blueprint's SQLite requirement. See Akamai's
[persistent Docker volume guide](https://www.akamai.com/cloud/guides/understanding-docker-volumes/).

## Current deployment

- App: **https://172.236.119.150**
- Akamai instance: `107866813`, label `hackmidwest-facility-operations`, Chicago
  (`us-ord`), Ubuntu 24.04, 2 GB RAM / 1 vCPU / 50 GB disk.
- Approved base price: $0.018/hour, capped at $12/month, before applicable
  taxes/overages. Paid instance backups are disabled.
- Cloud Firewall: `201494741`; public HTTP/HTTPS, SSH restricted to the deployment
  computer's public IPv4. Update that rule in Akamai if your public IP changes.
- Project directory: `/root/facility-operations`; Compose project:
  `facility-operations`; database volume: `facility-operations_facility_data`.
- Manager IDs: `1001` and `2001`. A private copy of the generated production
  password is on the deployment computer at
  `artifacts/akamai-manager-password.txt` (mode 600, excluded from Git).
  Production configuration is `/root/facility-operations/.env` (mode 600).
  The local app's `.env` and password are unchanged.
- Dedicated SSH key on the deployment computer:
  `~/.ssh/hackmidwest_akamai_ed25519`. Password SSH login is disabled.
- The migrated database initially contained 7 facilities, 26 users, 3 shifts and
  91 decisions. Existing data was imported before startup, without re-seeding.
- Daily facility checks run every three hours; the seven-day plan is refreshed
  from the manager UI. Added facilities are automatically included in scheduled
  checks. Messages remain pending for manager review; email delivery is absent.

Connect and inspect:

```sh
ssh -i ~/.ssh/hackmidwest_akamai_ed25519 root@172.236.119.150
cd /root/facility-operations
docker compose -f docker-compose.yml -f docker-compose.akamai.yml ps
docker compose -f docker-compose.yml -f docker-compose.akamai.yml logs --tail=50 api
systemctl list-timers facility-cert-renew.timer
curl --fail https://172.236.119.150/api/health
```

The IP certificate uses `/opt/certbot/bin/certbot` and the host
`facility-cert-renew.timer`, as documented below. Renewal briefly stops and
restarts the web container; the API scheduler continues running.

## 1. Prepare the instance

Create an Ubuntu 24.04 LTS Compute Instance with SSH-key access. A small instance
with 2 GB RAM is a practical starting point for this MVP and its image builds.
If using a domain, point its DNS A record at the instance. For an IP-only
deployment, follow the IP certificate section below. In an Akamai Cloud Firewall,
allow SSH only from your IP, and HTTP/HTTPS (80/443). Keep port 8000 closed.
Allow outbound HTTPS for source/model APIs and package/image downloads.

Follow [Akamai's instance guide](https://techdocs.akamai.com/cloud-computing/docs/create-a-compute-instance)
and [Docker's Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/).
On a clean Ubuntu instance, run over SSH as a sudo-capable user:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl certbot
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Commands below use `sudo docker`; Docker group membership is optional.

## 2. Copy the project

On your local computer, replace the host/user placeholders:

```sh
ssh DEPLOY_USER@SERVER_IP 'mkdir -p ~/facility-operations'
rsync -av --exclude='.env' --exclude='.git' --exclude='.venv' \
  --exclude='node_modules' --exclude='dist' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='data' --exclude='artifacts' \
  /Users/ayusharyal/Desktop/hack_final/ DEPLOY_USER@SERVER_IP:~/facility-operations/
```

This copies application code and lockfiles, without local secrets/SQLite data.
Cloning your Git repository is an alternative.

Alternatively, copy the prepared source archive from your local computer:

```sh
scp artifacts/facility-operations-akamai.tar.gz DEPLOY_USER@SERVER_IP:~/
ssh DEPLOY_USER@SERVER_IP
mkdir -p ~/facility-operations
tar -xzf ~/facility-operations-akamai.tar.gz -C ~/facility-operations
```

The archive contains source, lockfiles and deployment instructions. It excludes
`.env`, local databases, virtual environments and frontend build dependencies.

## 3. Configure secrets on the instance

```sh
cd ~/facility-operations
cp .env.example .env
chmod 600 .env
nano .env
```

Set these values (keep passwords/keys out of chat and version control):

```dotenv
DEMO_LOGIN=false
MANAGER_PASSWORD=<a unique password with at least 12 characters>
SESSION_SECRET=<at least 32 random characters>
OPENAI_API_KEY=<your model-provider credential>
MODEL_ID=hackathon-chat
OPENAI_BASE_URL=https://ws.akamai-workshops.com/v1
NWS_USER_AGENT=FacilityOperationsAI/0.1 (your-real-contact-address)
SCHEDULER_ENABLED=true
EVALUATION_INTERVAL_HOURS=3
EVALUATE_ON_STARTUP=false
WEB_BIND=0.0.0.0
WEB_PORT=80
```

Generate the session secret with `openssl rand -hex 32` on the instance. Set a
real password yourself. Both seeded managers share this MVP password; employees
cannot log in. The deployment override forces password mode even if the base
`.env` still enables selector login.

The example follows the supplied [Akamai workshop inference reference](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/e362ae25ea54a5ee33fa11f6265d15bc1705dca3/docs/inference.md):
use your workshop team key, the `hackathon-chat` alias and the base URL above.
This is the workshop service's configuration; other Akamai inference deployments
can have different endpoints, model names and limits. The agent uses the documented
Chat Completions interface, JSON schema output and a maximum of 4,096 output tokens.
Workshop availability and quotas are shared with your team's other applications.

For direct OpenAI, leave `OPENAI_BASE_URL` blank and select an available model such
as `gpt-4.1-mini` with its matching provider key. Credentials stay in the backend.
References: [Strands provider configuration](https://strandsagents.com/docs/user-guide/sdk/model-providers/openai/)
and [OpenAI configuration](https://developers.openai.com/api/docs/quickstart).

## 4. Issue HTTPS certificate and start both services

With DNS pointing at the instance and port 80 free, substitute your domain/contact:

```sh
sudo certbot certonly --standalone --cert-name facility \
  -d YOUR_DOMAIN --email YOUR_CONTACT_EMAIL --agree-tos --no-eff-email
sudo docker compose -f docker-compose.yml -f docker-compose.akamai.yml config --quiet
sudo docker compose -f docker-compose.yml -f docker-compose.akamai.yml build
sudo docker compose -f docker-compose.yml -f docker-compose.akamai.yml run --rm api python seed.py
sudo docker compose -f docker-compose.yml -f docker-compose.akamai.yml up -d
sudo docker compose ps
curl --fail https://YOUR_DOMAIN/api/health
```

Runtime services remain `api` and `web`. Nginx terminates TLS from the read-only
host certificate mount and redirects HTTP to HTTPS. No backend port is published.
Use HTTPS before entering the manager password over the public internet. Local
Compose binds only to localhost by default.

For standalone certificate renewal, create these root-owned executable hooks,
replacing the project path with its real absolute directory:

`/etc/letsencrypt/renewal-hooks/pre/facility-stop-web.sh`:

```sh
#!/bin/sh
cd /home/DEPLOY_USER/facility-operations || exit 1
docker compose -f docker-compose.yml -f docker-compose.akamai.yml stop web
```

`/etc/letsencrypt/renewal-hooks/post/facility-start-web.sh`:

```sh
#!/bin/sh
cd /home/DEPLOY_USER/facility-operations || exit 1
docker compose -f docker-compose.yml -f docker-compose.akamai.yml up -d web
```

```sh
sudo chmod 700 /etc/letsencrypt/renewal-hooks/pre/facility-stop-web.sh
sudo chmod 700 /etc/letsencrypt/renewal-hooks/post/facility-start-web.sh
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run --run-deploy-hooks
```

Renewal briefly stops the web container; the backend continues. See the
[Certbot renewal guide](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates).

## HTTPS when using an IP address without a domain

[Let's Encrypt supports IP address certificates](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html).
They use the `shortlived` profile and last about six days, so automatic renewal
is required. Use this section instead of the domain-specific certificate and
`certbot.timer` commands above; keep the same Compose build/start steps and hooks. Ubuntu's bundled Certbot may be too old; use Certbot 5.4 or newer
in an isolated host environment (the Docker stack still has only two services):

```sh
sudo apt-get install -y python3-venv
sudo python3 -m venv /opt/certbot
sudo /opt/certbot/bin/pip install 'certbot>=5.4,<6'
sudo /opt/certbot/bin/certbot certonly --standalone --non-interactive \
  --agree-tos --register-unsafely-without-email --cert-name facility \
  --preferred-profile shortlived --ip-address SERVER_IP
```

Use the same `docker-compose.akamai.yml` override and certificate paths above.
Nginx serves `https://SERVER_IP` and redirects HTTP to HTTPS. Keep inbound port
80 available for certificate validation. The certificate covers the IP address,
so no DNS record or self-signed certificate exception is needed.

Use the pre/post renewal hooks above with the actual deployment path. Configure
these host systemd units (using `/opt/certbot/bin/certbot` rather than an older
system package):

`/etc/systemd/system/facility-cert-renew.service`:

```ini
[Unit]
Description=Renew facility HTTPS certificate
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/certbot/bin/certbot renew --quiet
```

`/etc/systemd/system/facility-cert-renew.timer`:

```ini
[Unit]
Description=Check facility certificate renewal twice daily

[Timer]
OnBootSec=10min
OnUnitActiveSec=12h
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now facility-cert-renew.timer
sudo /opt/certbot/bin/certbot renew --dry-run --run-deploy-hooks
sudo systemctl list-timers facility-cert-renew.timer
```

Renewal briefly restarts only the web container. App evaluations keep their
three-hour cadence inside the API container; this host timer only manages TLS.

## 5. Verify the live demo

1. Open `https://YOUR_DOMAIN` or `https://172.236.119.150`, select manager
   1001/2001, and enter the password.
2. Edit location ID, name, coordinates or hours; run **Check next 7 days**.
   Open each affected date/shift draft and review its recipients and text. Use
   **Evaluate today** for the single-day facility status.
3. Inspect real source URLs/timestamps, modeled AQI labeling and policy rules.
4. Review every inclusion/exclusion; try facility-wide scope for remote staff.
5. Edit subject/body, confirm essential-staff instructions, and approve.
6. Download the saved message. No mail or external notifications are sent.
7. Restart `api` and confirm persisted decision history.

```sh
sudo docker compose restart api
sudo docker compose logs --tail=100 api web
```

`INSUFFICIENT_DATA` preserves the last known recommendation. Check model access,
key/base URL, US coordinates, source connectivity and NWS user agent. A 409 means
the single-worker operation lock is busy; retry after the running evaluation/edit.
The scheduler reloads all saved facilities every three hours and updates each
facility's current posture, reason, confidence, affected window and last-checked
time, saving a pending decision snapshot in the same SQLite database. Newly
added facilities are included automatically on the next run. Source/model
failures preserve the last known recommendation. The seven-day plan is refreshed
with the manager's **Check next 7 days** button; scheduled checks update daily status.

The first scheduled check runs three hours after the backend starts unless
`EVALUATE_ON_STARTUP=true`. This is an interval from process startup, not fixed
UTC clock times. Restarting resets the interval; keep the instance and containers
running. Set `EVALUATION_INTERVAL_HOURS` to another positive whole number if
needed. No host cron task or extra service is required.

## Persistence, backup and updates

The named `facility_data` volume contains `/data/facility.db`. Keep the project
directory/Compose project name stable; changing it selects another volume.
`docker compose down` preserves the volume. **Avoid `down -v`** when retaining
history. Never scale `api` or add workers with the embedded scheduler.

Create a consistent backup while running:

```sh
sudo docker compose exec -T api python -c "import sqlite3; source=sqlite3.connect('/data/facility.db'); target=sqlite3.connect('/data/backup.db'); source.backup(target); target.close(); source.close()"
sudo docker compose cp api:/data/backup.db ./facility-backup.db
```

After copying updated source, repeat the two-file `build` and `up -d` commands.
Seeding is idempotent and never resets edited facilities or history.

After changing keys, password, model or other `.env` settings, recreate the backend:

```sh
sudo docker compose -f docker-compose.yml -f docker-compose.akamai.yml up -d --force-recreate api
sudo docker compose -f docker-compose.yml -f docker-compose.akamai.yml ps
```

A restart alone retains the container's previous environment. Preserve the same
`SESSION_SECRET` to keep existing manager sessions valid across recreation.
