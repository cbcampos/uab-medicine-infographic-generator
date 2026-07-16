# UAB Comms Infographic Deployment Plan

Last updated: 2026-05-14  
Target host: `uab` / `comms`  
Target app: UAB Medicine Infographic Generator  
Proposed subdomain: `graphicgen.comms.gimpop.org`

## Goals

- Deploy the infographic generator as a new Docker container on the UAB `comms` server.
- Use only the existing shared Docker network: `shared_new_docker_network`.
- Route public traffic manually through Nginx Proxy Manager.
- Keep Azure OpenAI credentials and deployment names server-side only.
- Do not expose OpenAI, Gemini, Azure endpoint, API key, or model configuration controls to end users.
- Keep the production app Azure-only.

## Server Constraint

The server was at 99% root disk usage during inspection:

```text
/dev/vda1  77G total  76G used  1.2G available  99%
```

Docker reported approximately 39 GB of reclaimable image space, but images should not be pruned blindly because currently running apps may rely on rollback images.

## Disk Cleanup Plan

### 1. Confirm Current State

Run these before deleting anything:

```bash
df -h
docker system df
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}'
```

### 2. Identify Safe Cleanup Candidates

Prefer these checks:

```bash
docker image ls --filter dangling=true
docker volume ls --filter dangling=true
docker builder du
```

Also inspect large non-Docker files if disk pressure remains:

```bash
sudo du -xhd1 / | sort -h
sudo du -xhd1 /var | sort -h
sudo du -xhd1 /mnt/data | sort -h
```

### 3. Low-Risk Cleanup

Recommended first pass:

```bash
docker image prune
docker builder prune
```

This removes dangling image layers and build cache, not named images.

### 4. Medium-Risk Cleanup

Only after confirming rollback images are not needed:

```bash
docker image prune -a
```

This can remove unused named images. It should be done during a maintenance window or after exporting the image list for rollback reference.

### 5. Avoid Unless Explicitly Reviewed

Do not run these without reviewing app data and volume use:

```bash
docker system prune --volumes
docker volume prune
```

Volumes may contain app databases, uploads, or persistent state.

## Production App Changes Needed

The current app supports Basic and Advanced modes. Advanced mode exposes provider choice and credential/model fields for OpenAI, Azure, and Gemini. Production should hide those controls.

Recommended implementation:

- Add an environment flag such as `UAB_INFOGRAPHIC_PRODUCTION=true`.
- When production mode is enabled:
  - Force `experience_mode = "basic"`.
  - Force `provider = "azure"`.
  - Hide the Basic/Advanced selector.
  - Hide provider/model/API credential controls.
  - Hide or disable Gemini and OpenAI provider paths in the UI.
  - Read Azure settings only from environment variables.
  - Keep all generation features that end users need: PDF upload, source text, audience, style, contact sheet if desired, download buttons.

Required production environment variables:

```env
UAB_INFOGRAPHIC_PRODUCTION=true
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://...
AZURE_OPENAI_IMAGE_MODEL=gpt-image-2
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_VISION_DEPLOYMENT=gpt-4o
UAB_MEDICINE_LOGO_PATH=/app/assets/uab-medicine-logo.jpg
```

Do not set `OPENAI_API_KEY` or `GEMINI_API_KEY` in the production container.

## Docker Image Plan

Production deployment files in this repo:

```text
Dockerfile
.dockerignore
docker-compose.prod.yml
.env.production.example
```

Recommended container name:

```text
uab-infographic-generator
```

Recommended internal port:

```text
8501
```

Recommended compose service:

```yaml
services:
  uab-infographic-generator:
    container_name: uab-infographic-generator
    build:
      context: .
    restart: unless-stopped
    env_file:
      - .env
    environment:
      UAB_INFOGRAPHIC_PRODUCTION: "true"
      UAB_MEDICINE_LOGO_PATH: /app/assets/uab-medicine-logo.jpg
      STREAMLIT_SERVER_ENABLE_CORS: "false"
      STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION: "false"
    expose:
      - "8501"
    networks:
      - shared
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8501/_stcore/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

networks:
  shared:
    external: true
    name: shared_new_docker_network
```

The service should not publish a host port. Nginx Proxy Manager can reach it by container name on `shared_new_docker_network`.

## Server Deployment Location

Recommended app directory:

```text
/mnt/data/dockerapps/uab-infographic-generator
```

Suggested structure:

```text
/mnt/data/dockerapps/uab-infographic-generator/
  docker-compose.yml
  .env
  app/
```

If deploying from GitHub, either clone the repo into the app directory or use a compose file that builds from a checked-out repository path. Keep `.env` on the server only.

## Nginx Proxy Manager Manual Configuration

Create a new proxy host:

```text
Domain Names: graphicgen.comms.gimpop.org
Scheme: http
Forward Hostname / IP: uab-infographic-generator
Forward Port: 8501
```

Recommended Nginx Proxy Manager options:

```text
Block Common Exploits: enabled
Websockets Support: enabled
Access List: choose based on intended audience
SSL: request or attach Let's Encrypt certificate
Force SSL: enabled after certificate is issued
HTTP/2 Support: enabled
```

Streamlit uses websocket-style connections, so websocket support should be enabled.

## Deployment Status

Deployed on 2026-05-15.

```text
Remote path: /mnt/data/dockerapps/uab-infographic-generator
Compose file: /mnt/data/dockerapps/uab-infographic-generator/docker-compose.prod.yml
Container: uab-infographic-generator
Image: uab-infographic-generator-uab-infographic-generator:latest
Network: shared_new_docker_network
Container IP at deployment: 10.10.0.18
Internal port: 8501
Host port bindings: none
Health: healthy
```

The app was deployed with `UAB_INFOGRAPHIC_PRODUCTION=true`, Azure-only generation, server-side `.env`, reverse-proxy Streamlit settings, and per-container Docker JSON log rotation.

Reverse-proxy note from initial test: Nginx Proxy Manager served the `comms.gimpop.org` certificate for `graphicgen.comms.gimpop.org`. The proxy host needs a Let's Encrypt certificate whose SAN includes `graphicgen.comms.gimpop.org`; otherwise browser websocket handling can fail even when the page shell loads.

## Production Secrets Handling

- Store Azure values in `/mnt/data/dockerapps/uab-infographic-generator/.env`.
- Set file permissions restrictively:

```bash
chmod 600 /mnt/data/dockerapps/uab-infographic-generator/.env
```

- Do not commit `.env`.
- Do not paste `.env` values into Streamlit UI fields.
- Do not set Gemini or OpenAI keys in production.
- Do not log API keys. Current debug logging prints endpoint and model/deployment names, but not the key.

## Deployment Steps

1. Free enough disk space for at least one image build plus rollback room.
2. Implement production-mode UI guard locally.
3. Add Dockerfile, `.dockerignore`, and production compose.
4. Test locally with environment variables and no exposed provider controls.
5. Push code to GitHub.
6. SSH to `uab`.
7. Create `/mnt/data/dockerapps/uab-infographic-generator`.
8. Pull/clone the repo or copy deployment files.
9. Create server-side `.env` with Azure settings.
10. Run:

```bash
docker compose up -d --build
docker compose ps
docker logs --tail=100 uab-infographic-generator
```

11. Confirm the container is attached only to `shared_new_docker_network`.
12. Configure Nginx Proxy Manager manually using the values above.
13. Visit `https://graphicgen.comms.gimpop.org`.
14. Generate one small test infographic and verify logo/footer behavior.

## Rollback Plan

Before deployment:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
docker images > /home/ccampos/docker-images-before-infographic-deploy.txt
```

If the new app fails:

```bash
cd /mnt/data/dockerapps/uab-infographic-generator
docker compose down
```

This should remove only the infographic container if the compose project contains only that service.

## Open Decisions

- Whether `graphicgen.comms.gimpop.org` already has DNS pointed at the `comms` host.
- Whether the app should be public, VPN-only, or protected by an Nginx Proxy Manager access list.
- Whether image generation should be rate-limited at the proxy or app level.
- Whether generated images should be retained server-side or only held in Streamlit session memory.
