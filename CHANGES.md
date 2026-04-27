# UMS DLNA Search — Changes from Upstream UMS

This document tracks all additions and modifications made to the original
[Universal Media Server](https://github.com/UniversalMediaServer/UniversalMediaServer)
project in this fork.

---

## New Files

### `search.py`
Python script invoked by `PythonBridge` to handle all DLNA `Search()` requests
and synthetic `Browse()` requests for generated container IDs.

- Queries the SQLite media index (`media_index.db`) built by `index_media.py`
- Handles UPnP class–scoped search tabs:
  - `musicArtist` → artist/album_artist/composer field matching, returns artist containers
  - `musicAlbum` → album field matching, returns album containers
  - `audioItem`/`musicTrack` → dc:title field matching only, returns track items
  - `playlistContainer` → name matching against `playlists` table, returns playlist containers
- Browse mode for synthetic container IDs:
  - `artist:<name>` → returns an "All Tracks" container (all audio tracks for the artist,
    sorted album → track number → title; cover art randomly selected from tracks with embedded
    art) followed by album containers (tag-based)
  - `allartisttracks:<name>` → returns all audio tracks for the artist, sorted album → track number → title
  - `album:<artist>/<album>` → returns tracks sorted by track number
  - `playlist:<path>` → parses `.m3u` file, returns tracks with DB metadata lookup
- All text matching is case-insensitive (`lower(field) LIKE lower(?)`)
- `upnp:artist` conditions match both `artist` and `album_artist` columns
- `dc:creator` conditions match both `artist` and `composer` columns
- All track/album queries use `LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album`
  with `COALESCE(a.cover_art, '') AS cover_art` — every result row has the correct album art path
- `build_didl_tracks()` emits `<upnp:albumArtURI>` for every track that has a resolved cover URL
- `find_cover_url(relpath, cover_art)` resolves a cover cache path to an HTTP URL via
  `CoverCacheServlet` (`/cover/<filename>`), falling back to a folder image scan
- Album container DIDL uses `album_sample_cover` dict populated from the LEFT JOIN — correct cover
  for all albums regardless of which track was indexed first
- **Playlist lstrip fix**: leading path separators stripped from `.m3u` entry paths before
  filesystem lookup, fixing tracks silently missing from playlist Browse results

### `index_media.py`
Python script that builds and incrementally maintains the SQLite media index.

**Schema v6** (`/profile/database/media_index.db`):

| Table | Columns |
|---|---|
| `metadata` | `key`, `value` |
| `files` | `id`, `artist`, `album_artist`, `composer`, `album`, `title`, `genre`, `filename`, `relpath`, `fullpath`, `media_root`, `mime`, `track_number`, `mtime` |
| `albums` | `artist`, `album`, `cover_art`, `genre` (PRIMARY KEY: `artist`, `album`) |
| `playlists` | `id`, `name`, `path`, `mtime` |

- Full rebuild triggered on schema version mismatch or first run
- Incremental updates: only re-reads tags for changed files (mtime check); new files are
  inserted, deleted files are removed from `files`
- Orphan pruning: after file deletions, any `albums` row whose last audio track was removed is
  deleted and its cover art JPEG is removed from `/profile/cache/covers/` on disk
- Playlists table: indexes all `.m3u`/`.m3u8` files found under `MEDIA_ROOTS`
- Tag reading via `mutagen` (artist, album_artist, composer, album, title, genre, track_number)
- Path-based fallbacks (folder/filename) used **only for audio files** — non-audio files (images,
  video) receive empty metadata fields so folder names are never surfaced as phantom album/artist
  entries in music search results
- Respects `FOLDER_NAMES_IGNORED` env var
- Invoked by `PythonBridge` on startup, after UMS media scan, and on a periodic timer
- **Cover art extraction**: embedded art extracted once per album during full rebuild via `mutagen`;
  cached as `<artist>_<album>.jpg` under `$COVER_CACHE_DIR` (`/profile/cache/covers/`);
  path stored in `albums.cover_art`; subsequent tracks of the same album skip extraction
- **Genre in albums table**: first non-empty genre tag seen across an album's tracks is stored in
  `albums.genre`; never downgraded to empty by later tracks (upsert uses `CASE WHEN` guard)
- **Incremental cover/genre upsert**: when a changed or new audio file has embedded art or a genre
  tag, `albums` is upserted preserving the best existing values via `ON CONFLICT DO UPDATE SET`

### `src/main/java/net/pms/network/webguiserver/servlets/ReindexApiServlet.java`
New servlet: `POST /v1/api/reindex`

- Triggers a full UMS media rescan (`MediaScanner.startMediaScan()`)
- UMS rescan completion automatically fires `PythonBridge.triggerReindex()` (existing wiring)
- No authentication required — accessible from any host on the local network
- Returns `{"status":"started"}` or `{"status":"already_running"}`
- Usage from a playlist exporter or other local script:
  ```bash
  curl -s -X POST http://ums.int:9001/v1/api/reindex
  ```

### `src/main/java/net/pms/network/mediaserver/handlers/SearchRequestHandler.java`
New handler: bridges DLNA `Search()` SOAP requests to `search.py` via `PythonBridge`.

- `createSearchResult()` — called by the UPnP action path in `UmsContentDirectoryService`
- `createSearchResponse()` — called by the legacy HTTP handler path
- Both delegate to `PythonBridge.run("search.py", ...)` and parse the JSON result

### `src/main/java/net/pms/network/mediaserver/handlers/message/SearchRequest.java`
Simple POJO carrying search parameters between `UmsContentDirectoryService` and
`SearchRequestHandler`.

### `src/main/java/net/pms/plugins/python/PythonBridge.java`
Utility for invoking Python scripts from Java.

- `run(script, args...)` — synchronous invocation, returns stdout as String
- `triggerReindex()` — one-shot immediate index rebuild (async)
- `scheduleDebouncedReindex()` — debounced 30-second rebuild (collapses rapid calls)
- Periodic rebuild scheduled on startup via `python_index_refresh_minutes` config key (default: 1440 min)
- Sets environment variables: `MEDIA_ROOTS`, `UMS_MEDIA_HOST`, `UMS_MEDIA_PORT`, `FOLDER_NAMES_IGNORED`,
  `COVER_CACHE_DIR` (set to `<profile>/cache/covers/`)
- Registers `CoverCacheServlet` at `/cover/*` on the UMS media server

### `src/main/java/net/pms/network/mediaserver/servlets/CoverCacheServlet.java`
New servlet: serves cover art images extracted by `index_media.py`.

- Serves `GET /cover/<filename>` — looks up file in `UmsConfiguration.getProfileDirectory()/cache/covers/`
- Returns the raw JPEG bytes with `Content-Type: image/jpeg`
- Returns 404 if the file does not exist or is outside the cache directory
- Used by `search.py`'s `find_cover_url()` to emit `albumArtURI` URLs in DLNA DIDL responses

### `src/main/external-resources/docker/profile/SHARED.conf`
Default shared folder configuration baked into the Docker image.
- Shares `/media` as a monitored folder with metadata enabled
- Only takes effect on fresh deployments (empty `/profile` volume)

---

## Docker Deployment (`host_service` stack)

### `host_service/ums/Dockerfile`
Custom image based on `ubuntu:22.04`. Replaces the upstream `universalmediaserver/ums:latest` image.

- Installs: `openjdk-17-jre-headless`, `mediainfo`, `fonts-dejavu`, `python3`, `mutagen`, `dnsutils`
- No `ffmpeg`/`mplayer` — transcoding not needed for WiiM devices serving native FLAC
- `search.py` and `index_media.py` are `COPY`'d directly into the image from the project root —
  a `docker compose build` is sufficient to pick up Python changes (no Maven rebuild needed)
- `ums.jar` is also `COPY`'d from `target/` — Maven rebuild required only for Java changes
- Python scripts run directly from `/ums/` (the working directory); `mutagen` must be installed
  in the image for `index_media.py` to read audio tags and extract cover art

**Volumes:**
- `/media` — media library mount
- `/profile` — persistent UMS profile; contains:
  - `/profile/database/media_index.db` — SQLite search index
  - `/profile/cache/covers/` — extracted album art JPEGs (`<artist>_<album>.jpg`)

### `host_service/ums/entrypoint.sh`
Runs before starting UMS to handle runtime configuration:

1. **Profile seeding** — on first run (empty `/profile` volume), copies `seed/UMS.conf` and `seed/SHARED.conf` into `/profile`
2. **Hostname injection** — resolves `UMS_HOSTNAME` env var to a LAN IP using `getent hosts`, then injects it as `hostname = <ip>` in `/profile/UMS.conf`

UMS must advertise a bare IP address (not a hostname) in SSDP/UPnP packets for DLNA clients to connect back correctly. Using a DNS name like `ums.int` in compose allows the IP to change without updating the compose file — the container resolves it fresh on every start.

### `host_service/ums/seed/UMS.conf`
Minimal UMS config seeded on first run. Contains a blank `hostname =` line that `entrypoint.sh` populates at startup.

### `host_service/ums/seed/SHARED.conf`
Shares `/media` as the single monitored folder. Mirrors `src/main/external-resources/docker/profile/SHARED.conf`.

### `src/main/external-resources/renderers/Linkplay-WiiM-ProPlus.conf`
Renderer profile for WiiM Pro Plus specifically.
- `LoadingPriority = 2` — wins over the generic WiiM profile
- `UpnpDetailsSearch = WiiM Pro Plus`
- No transcoding limits (firmware resolved hi-res FLAC issues)

---

## Modified Files

### `src/main/java/net/pms/network/mediaserver/jupnp/support/contentdirectory/UmsContentDirectoryService.java`
- **Browse delegation** (line ~769): objectIDs starting with `artist:`, `album:`, `playlist:`,
  or `allartisttracks:` are delegated to `SearchRequestHandler.createSearchResult()` via a
  synthetic `__browse__` criteria string, bypassing the default UMS browse tree entirely
- **Search delegation** (line ~913): all DLNA `Search()` SOAP calls delegate to
  `SearchRequestHandler.createSearchResult()` via `PythonBridge`

### `src/main/java/net/pms/network/webguiserver/WebGuiServerJetty.java`
- Registered `ReindexApiServlet` at `/v1/api/reindex`

### `src/main/external-resources/renderers/Linkplay-WiiM.conf`
- Added `LoadingPriority = 1` (generic fallback, loses to Pro Plus profile)
- Added `TranscodeAudio = LPCM` (transcodes hi-res FLAC for older firmware)
- Added `Supported` lines capping FLAC at 192kHz, ALAC at 96kHz

### `pom.xml`
- Added Ant copy step to include `docker/profile/SHARED.conf` in the Docker build target

---

## Configuration Keys Added

| Key | Default | Description |
|---|---|---|
| `python_index_refresh_minutes` | `1440` | How often to auto-rebuild the media index (0 = disabled). **Required for NFS mounts** — Java WatchService (inotify) does not fire on NFS, so this periodic refresh is the only way new NFS content is picked up by both the search index and the DLNA browse tree. |

---

## HTTP Endpoints Added

| Method | URL | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/api/reindex` | None | Trigger UMS media rescan + Python index rebuild |
| `GET` | `/cover/<filename>` | None | Serve cover art JPEG from `/profile/cache/covers/` |

---

## WiiM Search Behavior (Reference)

Observed DLNA search criteria sent by WiiM Pro Plus per search tab:

| WiiM Tab | `upnp:class` | Conditions |
|---|---|---|
| Track | `derivedfrom "object.item.audioItem"` | `dc:title OR upnp:artist OR dc:creator` |
| Artist | `= "object.container.person.musicArtist"` | `upnp:artist OR dc:title` |
| Album | `= "object.container.album.musicAlbum"` | `upnp:album OR dc:title OR upnp:artist` |
| Playlist | `= "object.container.playlistContainer"` | `dc:title` |

Our handling per tab:
- **Track**: restrict to `dc:title` only (ignore artist/creator OR conditions)
- **Artist**: restrict to `upnp:artist`/`upnp:albumartist`/`dc:creator` (matches `artist` + `album_artist` + `composer` columns)
- **Album**: restrict to `upnp:album` only
- **Playlist**: query `playlists` table by name
