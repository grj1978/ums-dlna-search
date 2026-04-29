# Fork Changelog

All notable changes to this UMS fork's custom search engine and deployment
additions are documented here.  Upstream UMS changes are tracked in
[CHANGELOG.md](CHANGELOG.md).  The technical inventory of every added or
modified file lives in [CHANGES.md](CHANGES.md).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [1.1.0] – 2026-04-29

### Added
- **Release year sorting** — albums are now ordered by release year (ascending) in all contexts
  where album order is determined: artist browse album list, "All Tracks" container, and album
  search results.  Albums with no year tag are sorted alphabetically and placed after all
  dated albums.  Within a year, albums are sorted alphabetically as a tiebreaker.
- **Accent alias entries** (`SEARCH_ACCENT_ALIAS`, default disabled) — when enabled, a second
  accent-stripped alias entry (suffixed `[*]`) is created for any artist, album, or track whose
  name contains accented/special characters (e.g. *Jóhann Jóhannsson* → *Johann Johannsson [*]*).
  The alias shares the same container/item ID so drilling in or playing it resolves to the real
  content.  Covers artist browse, album browse under an artist, album search results, track search
  results, and the All Tracks container.  Enable with `SEARCH_ACCENT_ALIAS=1` in your compose
  environment.  Only needed for renderers that genuinely cannot render Unicode.
- `year` column added to `files` and `albums` tables (schema v7); read from the `date` mutagen
  tag (4-digit year extracted).  Schema bump triggers a full index rebuild on next start.
  - **Schema v8** supersedes this: `year` renamed to `release_date` (YYYYMMDD integer),
    `disc_number` column added — see v1.1.0 final schema notes below.

### Fixed
- **Accent-insensitive artist/album browse** — browsing into an artist or album whose name
  contains accented characters (e.g. *Jóhann Jóhannsson*, *Sigur Rós*) now works correctly
  when the renderer strips accents from container IDs before sending the Browse request (as WiiM
  does).  `query_files_by_artist` and `query_files_by_album` now use the registered `fold()` SQL
  function for comparison instead of exact-string equality, so `"Johann Johannsson"` correctly
  resolves to the accented artist in the DB.
- **Canonical artist name in browse results** — when browsing an artist via an unaccented ID,
  album containers and the "All Tracks" container now display the correct accented artist name
  (resolved from the DB rows) rather than the unaccented ID string.
- **Accented cover art files not served** — `CoverCacheServlet` returned 404 for any cover art
  file whose name contained non-ASCII characters (e.g. `Sigur_Rós_Takk_.jpg`).  Root cause:
  the JVM's native filesystem encoding (`sun.jnu.encoding`) defaulted to ASCII in the container
  because the OS locale was unset (POSIX/ASCII).  Fixed by setting `ENV LANG=C.UTF-8` in the
  Dockerfile — this sets the OS locale before the JVM initialises, which is what Java uses to
  determine `sun.jnu.encoding`.  The `-Dsun.jnu.encoding` JVM flag is silently ignored on
  Java 17+ Linux and cannot be used as a substitute.
- **Artist/album search with `dc:title` criteria** — when a renderer sends `dc:title contains
  "X"` as the only condition for a `musicArtist` or `musicAlbum` search (rather than
  `upnp:artist` or `upnp:album`), the value is now correctly treated as an artist/album name
  fragment rather than a track title fragment.  Resolves empty search results on renderers that
  use `dc:title` to express container-name searches.
- NFS/network I/O errors (`OSError: [Errno 5]`) on a single file during full rebuild no longer
  crash the entire indexer.  The offending file is skipped with a WARNING log entry and will be
  picked up automatically on the next incremental scan once the mount is healthy.
- `search.py` now checks the DB schema version before querying; if the DB is outdated (e.g. a
  failed rebuild left a v6 DB in place) it returns "index not ready" instead of crashing with a
  `KeyError` on the missing `year` column.

### Changed
- **Dockerfile moved to project root** — the `Dockerfile` previously lived outside the project
  in `host_service/ums/`.  It now lives at the project root (standard convention), so
  `docker build .` works with no `-f` flag.  The `docker-compose.yml` `dockerfile:` reference
  has been updated accordingly.  The `host_service/ums/` directory (which contained only
  now-redundant copies of `entrypoint.sh` and `seed/`) has been removed; the canonical copies
  in `src/main/external-resources/docker/` are the sole source of truth.

### Schema (v8)
- `year` column renamed to `release_date INTEGER` — stores full date as `YYYYMMDD` integer
  (e.g. `2003-05-12` → `20030512`, `2003-05` → `20030500`, `2003` → `20030000`).  Handles all
  ISO 8601 / ID3 date formats up to day resolution; sub-day fields (time) are ignored.
- `disc_number INTEGER` added to `files` table; read from the `discnumber` mutagen tag.
  Track listings now sort by disc → track → title; "All Tracks" sorts album (by date) → disc → track → title.
  Tracks with no disc tag are treated as disc 1 (not sorted last).

---

## [1.0.2] – 2026-04-27

### Added
- Incremental scan now prunes albums whose last audio track was deleted: removes the `albums`
  table row and deletes the cover art JPEG from `/profile/cache/covers/` on disk.  Affected
  albums are identified before the `DELETE FROM files` batch runs, so the cover path is still
  readable at cleanup time.  Albums with remaining tracks are unaffected.

### Documentation
- Added **Upgrade Notes** section to README advising a full index rebuild when upgrading from
  v1.0.1 or earlier — stale phantom entries from prior indexer bugs are not corrected by
  incremental scans and require a fresh rebuild.

---

## [1.0.1] – 2026-04-26

### Added
- **"All Tracks" container** in artist browse view (`allartisttracks:<artist>` container ID).
  Appears as the first entry when drilling into any artist, before the individual album list.
  Selecting it returns all audio tracks for that artist sorted by album → track number → title,
  making it easy to queue an entire discography in one action.  Cover art is randomly chosen
  from the set of tracks that have embedded art, so the thumbnail varies across sessions.

### Fixed
- Phantom album and artist entries appearing in search results caused by
  folder names being used as metadata fallbacks for non-audio files (images,
  video).  Path-based fallbacks for `artist`, `album`, and `title` are now
  applied **only when the file mime type is `audio/*`**.  Untagged audio files
  continue to use folder/filename as a fallback; images and video receive empty
  metadata fields and no longer pollute the music index.

---

## [1.0.0] – 2026-04-26

Initial versioned baseline.  Covers all fork work prior to this date.

### Added

#### Python search engine (`search.py`)
- SQLite index-backed DLNA `Search()` handler invoked via `PythonBridge`
- UPnP class-scoped search returning correctly typed DIDL results:
  - `musicArtist` → artist/album_artist/composer field matching, returns artist containers
  - `musicAlbum` → album field matching, returns album containers
  - `audioItem`/`musicTrack` → `dc:title` field matching, returns track items
  - `playlistContainer` → name matching against `playlists` table, returns playlist containers
- Browse mode for synthetic container IDs:
  - `artist:<name>` → returns album containers (tag-based)
  - `album:<artist>/<album>` → returns tracks sorted by track number
  - `playlist:<path>` → parses `.m3u`, returns tracks with DB metadata lookup
- Cover art URLs added as `<upnp:albumArtURI>` in all DIDL track and album responses
- `find_cover_url()` resolves cover cache path to HTTP URL via `CoverCacheServlet`, falling
  back to folder image scan
- WiiM field-narrowing: each search class restricted to only semantically relevant fields,
  preventing false positives from renderer OR conditions
- `SEARCH_STRICT_CRITERIA` env var to bypass WiiM narrowing for other renderers
- Playlist lstrip fix: leading path separators stripped from `.m3u` entry paths before
  filesystem lookup, fixing silently missing playlist tracks
- All text matching is case-insensitive

#### Python media indexer (`index_media.py`)
- Walks `MEDIA_ROOTS` and writes a SQLite database (schema v6) used by `search.py`
- Schema: `metadata`, `files`, `albums`, `playlists` tables with full set of music tag columns
- Tag reading via `mutagen`: `artist`, `album_artist`, `composer`, `album`, `title`, `genre`,
  `track_number`; path-based fallbacks for untagged audio files
- Full rebuild writes to a temp DB and atomically renames to live path — searches never blocked
- Incremental update: re-reads tags only for files whose `mtime` has changed
- Playlist indexing: all `.m3u`/`.m3u8` files under `MEDIA_ROOTS`
- Embedded cover art extraction once per album during full rebuild; cached as
  `<artist>_<album>.jpg` under `$COVER_CACHE_DIR` (`/profile/cache/covers/`)
- Genre stored in `albums` table; never downgraded to empty by a later track
- Incremental cover/genre upsert preserves best existing values via `ON CONFLICT DO UPDATE SET`
- Batch writes (500-row transactions) during incremental update to keep searches responsive
- `FOLDER_NAMES_IGNORED` env var support (comma-separated folder names to skip)
- Periodic rebuild scheduled by `PythonBridge` on startup

#### Java additions
- `SearchRequestHandler` — bridges DLNA `Search()` SOAP requests and Browse delegation to
  `search.py` via `PythonBridge`
- `SearchRequest` — POJO carrying search parameters between `UmsContentDirectoryService` and
  `SearchRequestHandler`
- `PythonBridge` — synchronous Python script invocation; debounced and one-shot reindex
  triggers; periodic rebuild timer (`python_index_refresh_minutes`, default 1440 min)
- `CoverCacheServlet` — serves `GET /cover/<filename>` from `/profile/cache/covers/`
- `ReindexApiServlet` — `POST /v1/api/reindex` triggers UMS media rescan + Python reindex
- `UmsContentDirectoryService` — Browse delegation for `artist:`, `album:`, `playlist:`
  container IDs; Search delegation to `SearchRequestHandler`
- `WebGuiServerJetty` — registered `ReindexApiServlet` at `/v1/api/reindex`

#### Renderer profiles
- `Linkplay-WiiM-ProPlus.conf` — dedicated profile (`LoadingPriority = 2`), no transcoding limits
- `Linkplay-WiiM.conf` — updated with `LoadingPriority = 1`, `TranscodeAudio = LPCM`, FLAC/ALAC
  `Supported` caps for older firmware

#### Docker deployment (`host_service`)
- `Dockerfile` — custom image on `ubuntu:22.04`; installs JRE, mediainfo, python3, mutagen;
  copies `ums.jar`, `search.py`, `index_media.py` directly into image. Located in project root.
- `src/main/external-resources/docker/entrypoint.sh` — seeds `/profile` on first run; resolves
  `UMS_HOSTNAME` to LAN IP and injects it as `hostname =` in `UMS.conf` at startup
- `src/main/external-resources/docker/seed/UMS.conf` — minimal UMS config with blank `hostname =`
  line for entrypoint injection
- `src/main/external-resources/docker/seed/SHARED.conf` — shares `/media` as the monitored folder

#### Configuration keys
| Key | Default | Description |
|---|---|---|
| `python_index_refresh_minutes` | `1440` | Auto-rebuild interval in minutes (0 = disabled) |

#### HTTP endpoints
| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/api/reindex` | Trigger UMS rescan + Python index rebuild |
| `GET` | `/cover/<filename>` | Serve cover art JPEG from `/profile/cache/covers/` |

---

[Unreleased]: https://github.com/grj1978/ums-dlna-search/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/grj1978/ums-dlna-search/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/UniversalMediaServer/UniversalMediaServer/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/UniversalMediaServer/UniversalMediaServer/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/UniversalMediaServer/UniversalMediaServer/releases/tag/v1.0.0
