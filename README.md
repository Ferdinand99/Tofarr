# Tofa Collection Creator

Docker app that creates Tofa custom collections from external sources and keeps them up to date.

## Sources
- TMDB collection, TMDB list, TMDB discover rules (actor, genre, studio, ...)
- Trakt public list
- Manual list of TMDB ids (one per line, optional `tv`, `# comment`)

A seeded **MCU Timeline** (manual list, disabled) is created on first start. Check the ids in the preview before enabling.

## Configuration
Open **Settings** in the web UI and enter the Tofa URL, admin API key and optional TMDB / Trakt keys. Saved values live in `/config` and override the environment variables below, which only act as defaults.

## Environment
| Variable | Required | Notes |
|---|---|---|
| `TOFA_URL` | no (or set in UI) | Direct address, e.g. `http://192.168.1.10:33333`. Not the relay. |
| `TOFA_API_KEY` | no (or set in UI) | Admin key from Tofa Server > Settings > API keys |
| `TMDB_API_KEY` | for TMDB sources | Free at themoviedb.org/settings/api |
| `TRAKT_CLIENT_ID` | for Trakt sources | trakt.tv/oauth/applications |
| `SEERR_URL` / `SEERR_API_KEY` | no (or set in UI) | Seerr, to request titles you do not have |
| `TZ` | no | Default `Europe/Oslo` |

Port `8080`, volume `/config`.

## Discover
The Discover page lists Tofa shelves and TMDB, Trakt and IMDb charts. Titles you have show an In library badge. One click creates a collection from a shelf, and with Seerr configured titles you lack get a Request button. Nothing is requested automatically.

## Behavior
- Always preview first: dry run shows what would be added, removed and which titles are not in your library.
- Titles missing from the Tofa library are reported, not errors. They are added on a later run once scanned.
- An empty source result never wipes a collection.
- A collection deleted in Tofa is recreated on the next run.
- Deleting a definition never deletes the Tofa collection unless you tick the box.

## Limitations
- Tofa has no reorder endpoint. Items are added in source order only.
- The collection item endpoints (`PUT/DELETE /collections/custom/{id}/items/{media_id}`) are not in Tofa's public API spec (v0.10.0). They are listed on a server's Settings > API page. Tofa is in beta, so they can change.
- Needs a direct connection to the server; API keys do not work through the relay.

## Unraid
Add `https://github.com/Ferdinand99/unraid-templates` under Docker > Template repositories, or copy `unraid/tofa-collection-creator.xml` into that repo's `templates/`.

## Develop
```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pytest
```
