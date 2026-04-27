# Fork Changelog

All notable changes to this UMS fork's custom search engine and deployment
additions are documented here.  Upstream UMS changes are tracked in
[CHANGELOG.md](CHANGELOG.md).  The technical inventory of every added or
modified file lives in [CHANGES.md](CHANGES.md).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [1.0.2] – Unreleased

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
- Cover art URLs emitted as `<upnp:albumArtURI>` in all DIDL track and album responses
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
  copies `ums.jar`, `search.py`, `index_media.py` directly into image
- `entrypoint.sh` — seeds `/profile` on first run; resolves `UMS_HOSTNAME` to LAN IP and
  injects it as `hostname =` in `UMS.conf` at startup
- `seed/UMS.conf` — minimal UMS config with blank `hostname =` line for entrypoint injection
- `seed/SHARED.conf` — shares `/media` as the monitored folder

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

[Unreleased]: https://github.com/UniversalMediaServer/UniversalMediaServer/compare/main...HEAD
[1.0.2]: https://github.com/UniversalMediaServer/UniversalMediaServer/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/UniversalMediaServer/UniversalMediaServer/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/UniversalMediaServer/UniversalMediaServer/releases/tag/v1.0.0
