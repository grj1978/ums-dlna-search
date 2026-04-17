# ums-dlna-search

**GitHub:** https://github.com/grj1978/ums-dlna-search | **Docker Hub:** https://hub.docker.com/r/grj1978/ums-dlna-search

A fork of [Universal Media Server][10] with a Python-based DLNA search engine designed for **music libraries**.

This fork replaces UMS's built-in search with a fast, SQLite-backed search engine (`search.py` + `index_media.py`) that is purpose-built for music. It was developed specifically for the **WiiM** family of music streamers, but should work with any DLNA renderer that uses `Search()` requests.

> **Music-only scope:** The Python search engine handles audio classes only. DLNA `Search()` requests for video or other non-audio media are passed through to UMS's built-in browse-based fallback rather than the Python engine — results will be functional but basic (folder-name matching, no metadata search). UMS's original SQL-backed search for non-audio has been removed as part of replacing `SearchRequestHandler`. Normal DLNA *browsing* and streaming of all file types is handled by upstream UMS and is completely unaffected.

## What this fork adds

- **Python search engine** — replaces the upstream H2/SQL search with a lightweight SQLite index built by `index_media.py`. Handles artist, album, track, and playlist search tabs.
- **Album art in search results** — embedded cover art is extracted once per album at index time and served via a dedicated `/cover/*` HTTP endpoint, so album thumbnails appear correctly in search results and playlists.
- **Playlist support** — `.m3u`/`.m3u8` playlists are indexed and browsable as playlist containers with full track metadata.
- **WiiM search fix** — the WiiM sends noisy cross-field OR queries (e.g. artist search also matches track titles). This fork restricts each search tab to only the field that makes sense. See the [DLNA Search Behavior](#-dlna-search-behavior-wiim--renderer-override) section below.
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
#     dockerfile: /path/to/host_service/ums/Dockerfile
#     additional_contexts:
#       dockerconfig: /path/to/host_service/ums
docker compose build ums && docker compose up -d ums
```

### First-run indexing

On first start (or after deleting `/profile/database/media_index.db`), `index_media.py` performs a full rebuild:
- Walks all audio files under `/media`
- Reads ID3/FLAC/MP4 tags via `mutagen`
- Extracts one cover image per album into `/profile/cache/covers/`
- Writes the SQLite index to `/profile/database/media_index.db`

Subsequent starts do a fast incremental scan — only files with changed modification times are re-read. Monitor progress with:

```bash
docker logs -f ums 2>&1 | grep -i "python\|index\|scanned"
```

---

## 🔍 DLNA Search Behavior (WiiM / Renderer Override)

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
| Artists | `artist`, `album_artist`, and `composer` DB columns (all three creator fields) |
| Albums | `upnp:album` only |
| Tracks | `dc:title` only |

The `upnp:class` value still controls what *type* of result is returned (artist containers, album containers, or track items). Only the field-matching logic is overridden.

> **Note for other users:** This behavior is an intentional departure from strict DLNA compliance. If your renderer sends well-formed, single-field search criteria you may want to remove these restrictions in `search.py`.

---

## Upstream: Universal Media Server

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
