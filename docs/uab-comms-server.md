# UAB Comms Server Notes

Last inspected: 2026-05-14  
Host alias: `uab`  
Purpose: UAB division app hosting via Docker

## Access

Local SSH config contains:

```sshconfig
Host uab
    HostName 138.26.49.108
    User ccampos
    IdentityFile ~/.ssh/id_ed25519_uab
    IdentitiesOnly yes
```

The host accepted SSH key authentication during inspection. The server hostname is `comms`.

## Host Summary

- OS: Ubuntu 24.04.3 LTS
- User inspected: `ccampos`
- Home directory: `/home/ccampos`
- Docker: `29.0.1`
- Docker Compose: `v2.40.3`
- Docker storage driver: `overlayfs`
- Docker root dir: `/var/lib/docker`
- Docker cgroup driver: `systemd`

## Critical Capacity Note

The root filesystem was critically full at inspection time:

```text
/dev/vda1  77G total  76G used  1.2G available  99%
```

Docker reported substantial reclaimable image space:

```text
Images          21 total, 19 active, 39.82GB size, 39.12GB reclaimable
Containers      22 total, 22 active, 1.008GB size, 0B reclaimable
Local Volumes   11 total, 2 active, 394.9MB size, 311.8MB reclaimable
Build Cache     0B
```

Do not deploy additional large images without first freeing disk space or expanding the disk. Prefer a reviewed cleanup such as pruning unused Docker images only after confirming no rollback images are needed.

Follow-up cleanup on 2026-05-15 found the immediate root cause was Docker JSON log growth, especially `surfsense-pgadmin-1`:

```text
surfsense-pgadmin-1 Docker JSON log: 31.55 GB
Docker containers directory before truncation: 33 GB
Root filesystem before truncation: 77G total, 76G used, 948M available, 99%
Root filesystem after truncation: 77G total, 44G used, 33G available, 57%
```

Action taken:

```bash
sudo find /var/lib/docker/containers -name "*-json.log" -exec truncate -s 0 {} \;
```

All containers remained running afterward, and no unhealthy containers were reported. Docker is using the `json-file` logging driver without an observed global rotation policy, so log growth can recur. Add log rotation for future deployments and consider a maintenance-window Docker daemon logging policy.

## Required Docker Network

All UAB division app services should use only the shared external Docker network:

```text
shared_new_docker_network
```

Network details:

```text
Driver: bridge
Subnet: 10.10.0.0/16
Gateway: 10.10.0.1
```

Compose files for new services should use this pattern:

```yaml
services:
  app:
    networks:
      - shared

networks:
  shared:
    external: true
    name: shared_new_docker_network
```

Avoid creating a project-specific default network for division apps unless there is a deliberate isolation requirement. If a service must be reachable by Nginx Proxy Manager, it must be attached to `shared_new_docker_network`.

## Main Deployment Root

The primary deployment tree appears to be:

```text
/mnt/data/dockerapps
```

It is a Git repository:

```text
remote: git@bitbucket.org:uabshp/selfhost_stack.git
branch: main
```

Git reports several app directories as untracked in that repo, including:

```text
coolify/
division-portal/
grantvis/
staff-directory/
surfsense/
uab-scholars-api/
```

The repository README describes this stack as the reproducible self-host setup for UAB services and explicitly documents the shared network requirement.

## App Directories

Observed directories under `/mnt/data/dockerapps`:

```text
/mnt/data/dockerapps/appsmith
/mnt/data/dockerapps/coolify
/mnt/data/dockerapps/division-portal
/mnt/data/dockerapps/easyappointments
/mnt/data/dockerapps/grantvis
/mnt/data/dockerapps/n8n
/mnt/data/dockerapps/nginx-proxy-manager
/mnt/data/dockerapps/staff-directory
/mnt/data/dockerapps/staticsite
/mnt/data/dockerapps/supabase
/mnt/data/dockerapps/surfsense
/mnt/data/dockerapps/uab-scholars-api
/mnt/data/dockerapps/watchtower
```

Additional working or older copies exist under `/home/ccampos`:

```text
/home/ccampos/coolify
/home/ccampos/division-portal
/home/ccampos/language-rewriter-deployment
/home/ccampos/surfsense
```

Prefer `/mnt/data/dockerapps` for active deployment work unless a specific app is known to be managed elsewhere.

## Compose Projects

`docker compose ls --all` reported:

```text
ckrmk748ezxdiq7eyzhxspme       running   /artifacts/zvp4tjemyxxwicpxg7x3n97s/docker-compose.yaml
coolify-proxy                  running   /data/coolify/proxy/docker-compose.yml
division-portal                running   /mnt/data/dockerapps/division-portal/docker-compose.yml
grantvis                       running   /mnt/data/dockerapps/grantvis/docker-compose.yml
gz8w6nmlk02l9rhyqspkdtr7       running   /artifacts/b3px2g4oswq31fw822ysg6fm/docker-compose.yaml
language-rewriter-deployment   running   /home/ccampos/language-rewriter-deployment/docker-compose.yml
nginx-proxy-manager            running   /mnt/data/dockerapps/nginx-proxy-manager/docker-compose.yml
source                         running   /mnt/data/dockerapps/coolify/source/docker-compose.yml
staff-directory                running   /mnt/data/dockerapps/staff-directory/docker-compose.yml
surfsense                      running   /mnt/data/dockerapps/surfsense/docker-compose.yml
uab-scholars-api               running   /mnt/data/dockerapps/uab-scholars-api/docker-compose.yml
```

## Running Containers

Division-facing or infrastructure containers observed:

```text
nginx-proxy-manager-app-1
coolify
coolify-proxy
scholars-api
staff-directory-frontend
staff-directory-backend
n8n
language-rewriter-deployment-language-rewriter-1
grantvis-frontend
division-portal-frontend-1
division-portal-backend-1
surfsense-frontend-1
surfsense-backend-1
surfsense-pgadmin-1
surfsense-redis-1
surfsense-db-1
```

Coolify internal containers observed:

```text
coolify-sentinel
coolify-db
coolify-redis
coolify-realtime
```

Coolify-managed application containers observed with generated names:

```text
gz8w6nmlk02l9rhyqspkdtr7-151141558100
ckrmk748ezxdiq7eyzhxspme-182614908890
```

## Network Attachment Observations

Containers attached to `shared_new_docker_network` include:

```text
nginx-proxy-manager-app-1
coolify
coolify-proxy
scholars-api
staff-directory-frontend
staff-directory-backend
n8n
language-rewriter-deployment-language-rewriter-1
grantvis-frontend
division-portal-frontend-1
division-portal-backend-1
surfsense-frontend-1
surfsense-backend-1
surfsense-pgadmin-1
surfsense-redis-1
surfsense-db-1
```

Some containers are not attached to `shared_new_docker_network`, including Coolify internals and generated Coolify app containers:

```text
coolify-sentinel
coolify-db
coolify-redis
coolify-realtime
gz8w6nmlk02l9rhyqspkdtr7-151141558100
ckrmk748ezxdiq7eyzhxspme-182614908890
```

That may be intentional for Coolify internals. For UAB division apps, standardize on `shared_new_docker_network`.

Some compose files attach services to both a project `default` network and the shared network. Examples:

```text
/mnt/data/dockerapps/uab-scholars-api/docker-compose.yml
/home/ccampos/language-rewriter-deployment/docker-compose.yml
```

For new deployments, use the shared network only unless an app-specific private network is required.

## Exposed Host Ports

Listening ports observed:

```text
22        SSH
80        HTTP / proxy
443       HTTPS / proxy
81        Nginx Proxy Manager admin
8000      scholars-api
8001      staff-directory backend
8081      staff-directory frontend
6001-6002 Coolify realtime
```

For new apps, prefer internal container ports on `shared_new_docker_network` with routing through Nginx Proxy Manager or Coolify. Avoid exposing additional host ports unless explicitly required.

## Existing Compose Network Patterns

Most app compose files under `/mnt/data/dockerapps` already declare the shared network as external:

```yaml
networks:
  shared_new_docker_network:
    external: true
```

Some use an alias:

```yaml
networks:
  shared:
    external: true
    name: shared_new_docker_network
```

Either is fine, but use a consistent pattern within each compose file.

## Deployment Guidance For New Apps

Recommended location:

```text
/mnt/data/dockerapps/<app-name>
```

Recommended compose principles:

- Attach every service that needs proxy access to `shared_new_docker_network`.
- Do not publish new host ports unless necessary.
- Let Nginx Proxy Manager or Coolify handle external routing and TLS.
- Keep secrets in `.env` on the server; do not commit `.env`.
- Avoid committing generated output, cache directories, uploads, logs, and database volumes.
- Check disk space before build/deploy because the server was at 99% disk usage.

Minimal compose sketch:

```yaml
services:
  app:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    expose:
      - "8501"
    networks:
      - shared

networks:
  shared:
    external: true
    name: shared_new_docker_network
```

## Notes From Inspection

- No infographic app deployment was found under `/mnt/data/dockerapps` or `/home/ccampos` during this inspection.
- `.env` files were located but not read, to avoid exposing secrets.
- The remote `/mnt/data/dockerapps` repo triggered Git's dubious ownership warning for the current user; using `git -c safe.directory=/mnt/data/dockerapps ...` allowed read-only inspection.
- The server has no swap configured.
- Active uptime during inspection was about 7 days and 20 hours.
