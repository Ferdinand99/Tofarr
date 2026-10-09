# Tofa Collection Creator – plan

## Context
Docker-app for Unraid som lager og holder Tofa-collections oppdatert (f.eks. MCU timeline) fra flere kilder. Installeres via Ferdinand99/unraid-templates (`templates/<app>.xml`, image på ghcr.io som `sylo-fluxer`).

## Funn om Tofa API (v0.10.0, https://docs.tofa.tv/api.md + api-spec.json)
- Auth: admin API-nøkkel (`Authorization: Bearer <key>`), kun direkte tilkobling til serveren (`http://<ip>:33333`). Passer for Unraid på samme nett. Device flow er fallback.
- Dokumentert for collections: `GET/POST /api/v1/collections/custom` (POST tar `name`, `overview`), `GET /collections/custom/{id}`, `DELETE /collections/{id}/artwork/{kind}`.
- Oppslag: `POST /api/v1/media/by-tmdb/batch` (maks 200, `{tmdb_id, media_type}` -> `media_id`), `GET /search`, `GET /system/info` (feature-detect via `capabilities`).
- **HULL:** ingen dokumentert endpoint for å legge til/fjerne/omorganisere filmer i en custom collection, endre eller slette den. Watchlist har add/delete (`/users/me/watchlist/{media_id}`).

## Steg 0 – løst
Serverens egen API-side (Settings > API) viser flere collections-endpoints enn offentlig dokumentasjon:
`PATCH /collections/custom/{id}` (rediger navn/overview), `DELETE /collections/custom/{id}`, `PUT` og `DELETE /collections/custom/{id}/items/{media_id}` (legg til/fjern), `PUT /collections/{id}/artwork/{kind}` (poster/backdrop).
Ingen reorder-endpoint -> rekkefølge støttes ikke via API. Timeline-rekkefølge kan bare ligge i collection-beskrivelsen eller bli håndtert av Tofa selv (sjekk om sortering skjer på release date). Første implementasjonsoppgave: hent request/response-skjema for disse fra serverens `/api-spec.json`-ekvivalent eller via curl-eksempelet på API-siden, og verifiser mot den ekte serveren (de er ikke i det offentlige subsettet, så feature-detect og fail gracefully).

## Design (arkitektonisk, forslag)
Én container, Python 3.12 + FastAPI + APScheduler + SQLite, port 8080, volum `/config`.

Enheter (hver med ett ansvar):
- `tofa/client.py` – all kommunikasjon med Tofa (collections CRUD, items, TMDB-oppslag, system/info). Eneste sted som kjenner endpoint-former.
- `sources/` – plugin per kilde, felles grensesnitt `fetch() -> ordered list[{tmdb_id, media_type, title}]`: `tmdb` (collections/lister, krever gratis TMDB-nøkkel), `trakt` (lister, krever client id), `yaml` (egne manuelle lister), `rules` (skuespiller/sjanger/studio via TMDB discover).
- `sync.py` – for hver definisjon: hent kilde -> løs TMDB-id mot Tofa-biblioteket (batch) -> diff mot gjeldende collection -> legg til/fjern/omordne. Filmer som mangler i biblioteket rapporteres som "missing" (ikke feil) og plukkes opp ved neste kjøring.
- `scheduler.py` – cron/intervall per collection + "kjør nå".
- `web/` – enkelt UI: koble til Tofa (URL + API-nøkkel), opprett/rediger collection-definisjoner, forhåndsvis diff, se status/logg og manglende filmer.
- Config/DB i `/config` (SQLite + valgfri `collections.yaml`); hemmeligheter via miljøvariabler eller UI.

Feilhåndtering: dry-run/preview før første skriving, ingen sletting av collections uten bekreftelse, retry med backoff, respekter rate limits, feature-detect capabilities.

## Leveranse
- `Dockerfile`, `docker-compose.yml`, GitHub Actions -> `ghcr.io/ferdinand99/tofa-collection-creator`.
- `tofa-collection-creator.xml` for `unraid-templates/templates/` (port, `/config`-path, TOFA_URL, TOFA_API_KEY, TMDB_API_KEY, TRAKT_CLIENT_ID, TZ), i samme stil som `sylo.xml`.
- Startpakke: ferdig MCU timeline-definisjon (TMDB-/Trakt-basert).

## Verifisering
- Enhetstester for diff-logikk og kilde-plugins (mock HTTP).
- Kjør container lokalt mot Tofa-serveren: opprett MCU-collection, kjør sync to ganger (andre kjøring = ingen endringer), fjern et element i kilden og bekreft at det oppdateres.
- Bygg image, installer via template på Unraid, bekreft at `/config` overlever restart.

## Åpne punkter
- Rekkefølge: ingen reorder i API. Bekreft med bruker at "MCU timeline" kan nøye seg med riktig innhold (evt. rekkefølge i beskrivelsen), eller undersøk om Tofa sorterer på add-rekkefølge (da kan sync legge til i riktig rekkefølge på en tom collection).
