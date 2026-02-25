# rythmx

Automated music discovery and acquisition. Reads your SoulSync library database,
cross-references Last.fm listening history, scores new releases, queues downloads
via SoulSync, and publishes playlists to Plex.

## Pages
- **Discovery** — scored track recommendations; download or publish to Plex
- **Cruise Control** — automated cycle: poll Last.fm → find releases → check library → queue downloads → publish playlist
- **Stats** — Last.fm top artists/tracks, acquisition history
- **Settings** — configure all service connections

## Setup

1. Copy `.env.example` to `.env` and fill in your credentials
2. Confirm your SoulSync Docker volume name: `docker volume ls`
3. Update `docker-compose.yml` with the correct volume name
4. Start: `docker-compose up -d`
5. Open: http://localhost:8009

## Configuration

All secrets via `.env` — never hardcoded. See `.env.example` for all options.

## Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your values
python -m app.main
```
