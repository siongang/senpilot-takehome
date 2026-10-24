# DigitalOcean deployment

Use one Ubuntu 24.04 LTS Basic Droplet with Regular CPU, 1 vCPU, and 2 GB RAM. The web receiver and browser worker run as separate Docker Compose services on the same server. They share the `senpilot_runtime` volume for SQLite and job files. A domain is optional for development; the temporary tunnel below provides a public HTTPS URL.

Compose uses **webhooks**, not inbox polling. The receiver validates AgentMail's Svix signature, saves the message identity, and returns 204 only after SQLite commits. Duplicate deliveries create no additional job. The worker fetches the full email, filters automatic/outgoing messages, parses with OpenAI, and processes the durable queue.

## 1. Prepare the Droplet

Create the Droplet with SSH-key authentication. Install Docker Engine and the Compose plugin using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/), and install Git. Enable Docker at boot:

```sh
sudo systemctl enable --now docker
```

Use a DigitalOcean Cloud Firewall: allow SSH (TCP 22) from your own IP. The development tunnel needs no public inbound web ports. For stable HTTPS, also allow TCP 80 and 443. Do not expose port 8000; Compose binds it only on the Droplet's loopback interface.

Examples below run on the Droplet with permission to use Docker. Never copy private SSH keys into the repository or chat.

## 2. Get code and configure secrets

Deploy a Git revision containing these files; an older revision will not contain the webhook changes.

```sh
git clone https://github.com/siongang/senpilot-takehome.git
cd senpilot-takehome
test -f .env || cp .env.example .env
chmod 600 .env
```

Edit `.env` on the server or transfer it securely with SCP. Set `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX`, and `OPENAI_API_KEY`. The inbox identifier must match webhook `message.inbox_id`. Obtain `AGENTMAIL_WEBHOOK_SECRET` in the next step.

Compose sets the runtime path to `/app/runtime`. Keep `.env` out of the image. Do not print expanded `docker compose config` without `--quiet`, because it contains secrets.

## 3. Register the webhook and start development hosting

Start only the temporary tunnel, before starting the receiver. It can create a public address while its origin is offline:

```sh
docker compose --env-file .env -f deploy/compose.yaml --profile dev-tunnel up -d --no-deps tunnel
docker compose --env-file .env -f deploy/compose.yaml logs tunnel
```

The logs contain an HTTPS address ending in `trycloudflare.com`. In AgentMail's console, create an inbox-scoped webhook subscribed to **only `message.received`**, using `https://YOUR-TUNNEL.trycloudflare.com/webhooks/agentmail`. Copy its signing secret into `AGENTMAIL_WEBHOOK_SECRET` in `.env`. The endpoint will return an origin error until the receiver starts; AgentMail retries delivery.

Validate and start the app:

```sh
docker compose --env-file .env -f deploy/compose.yaml config --quiet
docker compose --env-file .env -f deploy/compose.yaml --profile dev-tunnel up -d --build
```

Confirm the webhook is enabled. Redelivery is safe: jobs are unique by inbox and incoming message ID, even across multiple webhook registrations. Changing a secret requires recreating the affected services:

```sh
docker compose --env-file .env -f deploy/compose.yaml up -d --force-recreate web worker
```

[Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) are for development, need no domain/account, and provide a temporary address. A restart can change the URL; update AgentMail if it does. Do not use this temporary address as the final evaluator endpoint.

## 4. Switch to stable HTTPS

Point a domain/subdomain's DNS A record to the Droplet's IPv4 address. Set `SITE_HOSTNAME=agent.example.com` in `.env` (no scheme/path). Open TCP 80 and 443 in the firewall. Remove incorrect AAAA records unless IPv6 is configured too.

```sh
docker compose --env-file .env -f deploy/compose.yaml --profile https up -d --build
docker compose --env-file .env -f deploy/compose.yaml --profile dev-tunnel stop tunnel
```

Caddy provisions and renews a public TLS certificate. Update AgentMail to `https://agent.example.com/webhooks/agentmail`. A raw IP with Caddy's default local certificate does not provide a publicly trusted webhook URL.

## 5. Acceptance checks

```sh
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
docker compose --env-file .env -f deploy/compose.yaml ps
docker compose --env-file .env -f deploy/compose.yaml logs --tail 100 web worker
docker compose --env-file .env -f deploy/compose.yaml exec worker senpilot-agent status
```

Check the public HTTPS `/healthz` too. The HTTP health check confirms the receiver/database; it does **not** prove the browser worker can finish a job.

Send an email from an independent mailbox, receive the threaded reply, and open its ZIP. Retry the same webhook and confirm no duplicate job/result. Restart the app services and reboot the Droplet; confirm stored job state/files survive. Test with the laptop worker stopped. Observe memory during a ten-file job before declaring the 2 GB size sufficient.

Docker restarts services after process exit or reboot. An unhealthy-but-running service is visible in `compose ps`; health checks alone do not restart it. Investigate logs and restart the affected service if necessary. Killed jobs recover after their lease expires. Manual polling remains available through `senpilot-agent once`/`run`, but is not in Compose startup.

## Updates, backup, and rollback

### Automatic deployment from GitHub

The [Test and deploy workflow](../.github/workflows/deploy.yml) runs Python tests and Compose validation for pull requests and pushes to `main`. A successful push to `main` uploads the exact tested Git revision and deploys it to the existing Droplet. It can also be run manually from GitHub Actions on `main`. Pull requests never receive deployment credentials or deploy code.

Repository Actions secrets:

| Name | Value |
| --- | --- |
| `DEPLOY_HOST` | Droplet IPv4 address. |
| `DEPLOY_SSH_KEY` | Dedicated Ed25519 deployment private key, never your personal SSH key. |
| `DEPLOY_KNOWN_HOSTS` | Pinned host public key obtained through an already trusted SSH connection. |

Application API keys stay in `/opt/senpilot/.env`; they are not needed in Actions. The deployment key's `authorized_keys` entry uses `restrict,command="/usr/local/sbin/senpilot-actions-ssh"` and only permits `upload <40-character SHA>` and `deploy <40-character SHA>`. No general SSH shell, port forwarding, or SCP is allowed with this key. Deployments can still run the repository's Dockerfile with Docker privileges, so only trusted changes should reach `main`.

One-time server bootstrap installs `deploy/ssh-command.sh` as `/usr/local/sbin/senpilot-actions-ssh` and `deploy/release.sh` as `/usr/local/sbin/senpilot-release`, both owned by root with mode 700, then adds the restricted public key. Updating these installed bootstrap scripts requires the operator's SSH access; updating application code requires only a push to `main`.

The server serializes deployments with `flock`. Each revision has a source directory at `/opt/senpilot/releases/<SHA>` and an image tagged `senpilot-agent:<SHA>`. It builds and runs tests in that image before stopping the current services. It then backs up the stopped runtime volume, starts the new web/worker services, checks readiness and SQLite integrity, and records the revision in `/opt/senpilot/deployed-revision`. `/opt/senpilot/current` points to the latest successful source directory.

The Compose project and `senpilot_runtime` volume retain their existing names. The tunnel and Caddy services are not restarted by deployments. A worker interrupted by a deployment recovers its job after the existing lease expires; send retries retain their existing idempotency protection.

If startup or readiness fails, the script attempts to restart the previous image with its previous Compose file and reports failure to Actions. It preserves runtime data and does not automatically restore an old database or reverse schema migrations. An incompatible database migration requires manual recovery using the pre-deployment backup. Build and test failures leave the running app untouched.

Release directories, images, and runtime backups are retained for recovery. Monitor disk space and remove older releases/images/backups when they are no longer needed; keep the current and previous release, and copy important backups off the Droplet. This workflow does not add a hosting service or a paid deployment subscription.

### Manual update

For a Git-checkout deployment without Actions, after reviewing and publishing a new revision:

```sh
git pull --ff-only
docker compose --env-file .env -f deploy/compose.yaml build
docker compose --env-file .env -f deploy/compose.yaml up -d web worker
```

Back up before upgrades with `bash deploy/backup.sh`. It briefly stops intake/processing, copies SQLite (including WAL) and files from a consistent stopped state, and restarts both services. AgentMail retries delivery while intake is down. Backups are under ignored `backups/`; download them to another machine. Keep secrets separately. Do not run `docker compose down -v`: `-v` deletes the database/files.

For rollback, retain the previous Git revision and image, return to that revision, rebuild, and recreate the app services. An incompatible migration requires a matching DB backup. Restore a runtime backup only with web/worker stopped, into the `senpilot_runtime` volume. Verify status before enabling intake. Database recovery can replay work; reconcile provider threads before resending uncertain deliveries.

## Current verification

Verified on October 24, 2026: Docker installed on the Ubuntu Droplet; image built; all 66 tests passed inside that image; webhook registered; public HTTPS health and signature rejection passed; hosted OpenAI extraction passed; a ten-file Other Documents scrape produced a valid ZIP; an external Key Documents email completed in about 12 seconds with six valid PDFs and recipient-confirmed delivery/opening. Signed duplicate delivery created no additional job or reply. Files survived container replacement, completed job state survived an app restart, SQLite integrity passed, and stopped-state backups were copied off the server.

The first deployment transferred source directly to `/opt/senpilot`; that directory is not a Git checkout. Actions now deploys Git archives into revision-specific release directories instead of running `git pull` there. Use `/opt/senpilot/current/deploy/compose.yaml` and `/opt/senpilot/.env` for manual operations after the first Actions deployment. For manual recreation, set `SENPILOT_IMAGE=senpilot-agent:$(cat /opt/senpilot/deployed-revision)` so Compose uses the deployed revision's image. Keep runtime data and secrets outside release archives and images.

The current URL is a development Quick Tunnel. A stable HTTPS address, whole-host reboot verification, and live hosted fault cases remain open. Docker's restart policy preserves processes, but does not repair AgentMail's URL if a Quick Tunnel address changes after restart. Do not treat that temporary URL as the final submission endpoint.

Sources: [AgentMail verification](https://www.agentmail.to/docs/webhook-verification), [Docker Compose](https://docs.docker.com/compose/how-tos/production/), [Caddy HTTPS](https://caddyserver.com/docs/automatic-https).
