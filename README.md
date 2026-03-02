# rythmx

Automated music discovery and acquisition.

## Pages
- **Discovery** — scored track recommendations; download or publish to Plex
- **Cruise Control** — automated cycle: poll Last.fm → find releases → check library → queue downloads → publish playlist
- **Stats** — Last.fm top artists/tracks, acquisition history
- **Settings** — configure all service connections


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
