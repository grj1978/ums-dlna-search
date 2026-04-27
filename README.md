# ums-dlna-search

**GitHub:** https://github.com/grj1978/ums-dlna-search | **Docker Hub:** https://hub.docker.com/r/grj1978/ums-dlna-search

A fork of [Universal Media Server][10] with a Python-based DLNA search engine designed for **music libraries**.

This fork replaces UMS's built-in search with a fast, SQLite-backed search engine (`search.py` + `index_media.py`) that is purpose-built for music. It was developed specifically for the **WiiM** family of music streamers, but should work with any DLNA renderer that uses `Search()` requests.

> **Music-only scope:** The Python search engine handles audio classes only. DLNA `Search()` requests for video or other non-audio media are passed through to UMS's built-in browse-based fallback rather than the Python engine — results will be functional but basic (folder-name matching, no metadata search). UMS's original SQL-backed search for non-audio has been removed as part of replacing `SearchRequestHandler`. Normal DLNA *browsing* and streaming of all file types is handled by upstream UMS and is completely unaffected.

## What this fork adds

- **Python search engine** — replaces the upstream H2/SQL search with a lightweight SQLite index built by `index_media.py`. Handles artist, album, track, and playlist search tabs.
- **Album art in search results** — embedded cover art is extracted once per album at index time and served via a dedicated `/cover/*` HTTP endpoint, so album thumbnails appear correctly in search results and playlists.
- **Playlist support** — `.m3u`/`.m3u8` playlists are indexed and browsable as playlist containers with full track metadata.
- **"All Tracks" artist container** — when you drill into an artist, an "All Tracks" entry appears at the top of the album list. Selecting it queues the artist's entire discography sorted by album → track number, making it easy to play a complete collection in one action.
- **WiiM search fix** — the WiiM sends noisy cross-field OR queries (e.g. artist search also matches track titles). By default, this fork restricts each search tab to only the field that makes sense for that result type. See the [DLNA Search Behavior](#-dlna-search-behavior-wiim--renderer-override) section below for details and configuration.
- **Docker-first deployment** — ships as a single Docker image with no external dependencies beyond a media volume.

## Docker deployment

The easiest way to deploy is to pull the pre-built image from Docker Hub and drop the compose block below into your stack.

### docker-compose.yml

```yaml
volumes:
  ums_profile:        # Persistent profile: SQLite index + extracted cover art

services:
  ums:
    image: grj1978/ums-dlna-search:latest
    container_name: ums
    network_mode: host   # Required for SSDP/UPnP multicast discovery
    # Ports are bound automatically via host networking — no explicit mapping needed.
    # Listed here for firewall/documentation purposes:
    #   9001/tcp  — UMS web UI
    #   5001/tcp  — DLNA HTTP media server
    #   9002/tcp  — Internal reindex API (loopback only)
    #   1900/udp  — SSDP/UPnP discovery (multicast)
    environment:
      # Hostname or IP this server advertises in SSDP/UPnP packets.
      # Must be an address reachable by your DLNA renderers.
      # The entrypoint resolves this name to an IP and injects it into UMS.conf.
      - UMS_HOSTNAME=your-server-hostname-or-ip  # REQUIRED

      # Friendly name shown to DLNA clients. Default: "Universal Media Server"
      # - UMS_SERVER_NAME=MyMusicServer

      # How often to re-scan the media index, in minutes. Default: 1440 (24 hours)
      # Set to 0 to disable periodic rescans entirely.
      # - UMS_INDEX_REFRESH_MINUTES=1440

      # Search field filtering mode. Default: 0 (WiiM-focused filtering)
      #   0 — WiiM-focused: each search tab is restricted to only the fields that
      #        make sense for that result type.
      #   1 — Strict mode: honor the renderer's SearchCriteria exactly as sent,
      #        with no field filtering applied.
      # - SEARCH_STRICT_CRITERIA=0

      # Override the profile directory inside the container. Default: /profile
      # Change this if you want to mount the profile at a different path.
      # - UMS_PROFILE=/profile
    volumes:
      - ums_profile:/profile          # Persistent profile (index DB + cover cache)
      - /path/to/your/music:/media:ro # Your music library (read-only recommended)
    restart: unless-stopped
```

Then start it:

```bash
docker compose pull ums && docker compose up -d ums
```

### Building from source

If you want to modify the code or build your own image:

```bash
# 1. Build the Java project (required after any Java change; skip for Python-only changes)
cd /path/to/ums-dlna-search
mvn clean package -Dmaven.test.skip=true

# 2. Build and start with a local image
# In your docker-compose.yml, replace the image line with:
#   image: ums-dlna-search:local
#   build:
#     context: /path/to/ums-dlna-search
#     dockerfile: src/main/external-resources/docker/Dockerfile
docker compose build ums && docker compose up -d ums
```

### First-run indexing

On first start (or after deleting `/profile/database/media_index.db`), `index_media.py` performs a full rebuild of the search index. This may take a minute or two for large libraries. Subsequent scans are incremental and only process changed files. See [How This Fork Works](#️-how-this-fork-works) for full details.

Monitor indexing progress with:

```bash
docker logs -f ums 2>&1 | grep -i "python\|index\|scanned"
```

---

## � Upgrade Notes

> ** Seeing phantom entries?  — delete your index after upgrading**
>
> v1.0.1+ introduced fixes to how the index is built and pruned. Any index built by an earlier version may contain phantom entries — albums or artists that appear in search results but have no playable tracks. These entries are not corrected by an incremental scan; a full rebuild is required.
>
> After pulling the new image and restarting the container, delete the index to force a clean rebuild:
> ```bash
> docker exec ums rm -f /profile/database/media_index.db
> docker restart ums
> ```
> The index will rebuild automatically on startup. For large libraries this takes a minute or two — monitor progress with `docker logs -f ums`.

---

## �🔍 DLNA Search Behavior (WiiM / Renderer Override)

This fork includes a custom Python-based DLNA search backend (`search.py`) that replaces UMS's built-in search engine. It is designed for music libraries and was specifically tuned for the **WiiM** music streamer, which sends DLNA `Search()` SOAP requests with cross-field OR conditions that produce noisy, unhelpful results with a standard DLNA server.

### What the WiiM actually asks for

On each search tab the WiiM sends an `upnp:class`-scoped query with an OR across multiple fields, for example:

| Tab | WiiM's SOAP `SearchCriteria` |
|-----|------------------------------|
| Artists | `upnp:class derivedfrom "object.person.musicArtist" and (upnp:artist contains "X" or dc:title contains "X")` |
| Albums | `upnp:class derivedfrom "object.container.album.musicAlbum" and (upnp:album contains "X" or dc:title contains "X" or upnp:artist contains "X")` |
| Tracks | `upnp:class derivedfrom "object.item.audioItem" and (dc:title contains "X" or upnp:artist contains "X" or upnp:album contains "X")` |

The OR conditions mean that, for example, an artist-tab search for "Love" would return artist containers for any artist whose *track title* also happens to contain "Love" — which is almost always useless noise.

### What this fork does instead

The cross-field OR conditions are intentionally ignored. Each tab is restricted to only the field that makes sense for that result type:

| Tab | Fields actually searched |
|-----|--------------------------|
| Artists | `artist` and `album_artist` DB columns |
| Albums | `upnp:album` only |
| Tracks | `dc:title` only |

The `upnp:class` value still controls what *type* of result is returned (artist containers, album containers, or track items). Only the field-matching logic is overridden.

> **Using a different renderer?** If your renderer sends well-formed, single-field search criteria, set `SEARCH_STRICT_CRITERIA=1` in your compose environment. All searches will still go through `search.py` — the SQLite index is always used — but the field-narrowing rules are skipped and the renderer's criteria are honored exactly as sent.

---

## ⚙️ How This Fork Works

### Index lifecycle

The Python search engine maintains a SQLite database (`media_index.db`) that is separate from UMS's own H2 browse database. Both are updated in sync — but they serve different purposes: UMS's database drives DLNA browsing and streaming, while the SQLite index drives `Search()` requests only.

**Full rebuild** (first start, or after a schema change):
- `index_media.py` builds a fresh index into a temporary file
- The temporary file is atomically renamed over the live database at the end — so searches are never interrupted and never see a partially-built index
- Cover art is extracted once per album during this phase

**Incremental scan** (every subsequent start, and on a configurable timer):
- Walks all files under `/media` and compares each file's mtime (modification timestamp — the filesystem's record of when a file was last changed) against what's stored in the index
- Only files with a changed mtime are re-read for tag changes
- If nothing has changed, **no writes are made to the database at all** — the scan is entirely read-only and has no impact on active searches
- If changes are found, they are written in small batches so individual write-lock windows are milliseconds, not seconds

**Automatic reindex on file changes:**
- While the container is running, UMS's Java file watcher (inotify) monitors `/media` for any create, modify, or delete events
- Any such event triggers a debounced reindex — `index_media.py` runs 30 seconds after the last event, collapsing rapid bursts (e.g. copying an album) into a single scan
- When UMS completes a full media rescan (triggered from the web UI), it also fires a Python reindex so both databases stay in sync

> **NFS/network mount note:** inotify does not fire events for changes made on NFS or SMB mounts by remote machines. If your `/media` is network storage, rely on `UMS_INDEX_REFRESH_MINUTES` as the mechanism for picking up new music — the file watcher will not see remote changes.

### Search and browse routing

- All DLNA `Search()` SOAP requests from renderers are intercepted by `SearchRequestHandler`, which calls `search.py` as a subprocess and expects JSON back
- `Browse()` requests for synthetic container IDs (e.g. `artist:The Beatles`, `album:The Beatles/Abbey Road`, `allartisttracks:The Beatles`, `playlist:/media/...`) are also delegated to `search.py` — this is what makes drill-down navigation work after clicking a search result. The `allartisttracks:` container returns all audio tracks for the artist sorted by album → track number → title
- All other `Browse()` requests (the normal folder tree) are handled entirely by upstream UMS and are unaffected by this fork

### Cover art

- Embedded cover art is extracted from audio files **once per album** at full-rebuild time and cached as JPEG files in `/profile/cache/covers/`
- The cache is served via a dedicated `/cover/*` HTTP endpoint on the same port as the DLNA media server
- Incremental scans extract cover art only for newly added albums — existing cached images are never re-extracted

---

## 🗂️ Profile Directory Layout

The `/profile` volume contains everything that persists across container restarts. It is safe to delete individual subdirectories to force a rebuild of that data:

| Path | Contents | Safe to delete? |
|------|----------|-----------------|
| `/profile/UMS.conf` | Main UMS configuration (hostname, server name, shared folders, etc.) | Deleting resets all settings to seed defaults on next start |
| `/profile/SHARED.conf` | Shared folder definitions | Deleting resets to seed defaults |
| `/profile/database/media_index.db` | Python SQLite search index | Yes — triggers full rebuild on next start |
| `/profile/database/` (other files) | UMS's own H2 browse database | Yes — UMS rebuilds it on next start |
| `/profile/cache/covers/` | Extracted album art cache | Yes — repopulated on next full rebuild |

---

## 🔧 Environment Variables

All variables are injected into `UMS.conf` by the container entrypoint on every start, so they take effect even if the profile volume already exists.

| Variable | Default | Description |
|----------|---------|-------------|
| `UMS_HOSTNAME` | *(required)* | Hostname or IP this server advertises in SSDP/UPnP multicast packets. Must be reachable by your DLNA renderers. The entrypoint resolves this to an IP address at startup. |
| `UMS_SERVER_NAME` | `Universal Media Server` | Friendly name shown to DLNA renderers and clients. |
| `UMS_INDEX_REFRESH_MINUTES` | `1440` | How often (in minutes) to run an incremental index scan. Set to `0` to disable periodic scans entirely and rely only on the file watcher. For NFS mounts, this is the only mechanism that detects remote changes. |
| `SEARCH_STRICT_CRITERIA` | `0` | `0` (default): WiiM field-narrowing rules are applied — each search tab is restricted to only the fields that make sense for that result type. `1`: honor the renderer's `SearchCriteria` exactly as sent, with no field filtering. See [DLNA Search Behavior](#-dlna-search-behavior-wiim--renderer-override). |
| `UMS_PROFILE` | `/profile` | Path inside the container where UMS stores its profile (config, databases, cover cache). Override if you want to mount the profile volume at a different path. |

---



This project is a fork of [Universal Media Server][10] by the UMS team.
All core DLNA/UPnP streaming, transcoding, renderer detection, and web UI are upstream UMS code — this fork only adds the search engine and Docker deployment layer.

**Upstream project members:** [ik666][32], [mik_s][7], [SubJunk][3], [SurfaceS][33], [valib][5] — and [many contributors][10].

---

## 🧩 Contributing

We welcome contributions from everyone!  
If you'd like to get started:

1. Fork the repository and clone your fork locally.  
2. Create a feature branch for your change (`git checkout -b feature-name`).  
3. Make your changes and test them.  
4. Push to your fork and open a Pull Request on GitHub.

See our [CONTRIBUTING.md](CONTRIBUTING.md) for detailed setup and coding guidelines.

---

## 📜 License

Universal Media Server is released under the **GNU General Public License (GPL)**.  
For more information, see the [LICENSE](LICENSE) file included in this repository.

---

  [1]: https://www.universalmediaserver.com
  [2]: https://www.universalmediaserver.com/comparison/
  [3]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=2
  [4]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=62
  [5]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=683
  [6]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=171
  [7]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=10450
  [8]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=1194
  [9]: https://www.universalmediaserver.com/forum
  [10]: https://github.com/UniversalMediaServer/UniversalMediaServer
  [11]: https://www.universalmediaserver.com/downloads/
  [12]: https://github.com/UniversalMediaServer/UniversalMediaServer/issues?state=open
  [13]: https://support.universalmediaserver.com
  [15]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=4025
  [16]: https://github.com/josepma
  [17]: https://github.com/kirvx
  [18]: https://github.com/ler0y
  [19]: https://github.com/AlfredoRamos
  [20]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=573
  [21]: https://github.com/squadjot
  [22]: https://crowdin.com/profile/OnarEngincan
  [23]: https://github.com/K4r0lSz
  [24]: https://github.com/prescott66
  [26]: http://www.mplayerhq.hu/
  [27]: https://www.ffmpeg.org/
  [28]: https://mediaarea.net/en/MediaInfo
  [29]: https://crowdin.com/
  [30]: https://www.universalmediaserver.com/forum/memberlist.php?mode=viewprofile&u=55
  [31]: https://github.com/js-kyle
  [32]: https://github.com/ik666
  [33]: https://github.com/SurfaceS
  [34]: https://github.com/threedguru
  [35]: https://architectureofsales.com
  [36]: https://www.patreon.com/universalmediaserver
  [37]: https://stats.uptimerobot.com/k0YIB5IOhL
