# Changelog

Releases are tagged `vMAJOR.MINOR.PATCH`. Each tag publishes the Docker images `X.Y.Z`, `X.Y` and `latest`
and a GitHub release with generated notes. Pushes to `main` publish `edge`.

## 0.1.0

First release.

- Build Tofa collections from TMDB collections and lists, TMDB discover filters, Trakt lists,
  IMDb lists and charts, Tofa discovery shelves, or a pasted list of TMDB ids.
- Collections are kept in the order of the source, and updated on a schedule you choose.
- Preview before anything is written. Titles that are not in your library are listed and added later.
- Discover page with posters and an In library badge, and one-click collection creation.
- Optional Seerr integration to request titles you do not have.
- Server address and keys are set in the web UI.
