#!/usr/bin/env python3
# This file is part of Universal Media Server, based on PS3 Media Server.
#
# This program is a free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; version 2 of the License only.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""
Index-backed search/browse script for UMS.

Handles UPnP ContentDirectory Search() requests from renderers like WiiM,
returning properly typed results (containers for artist/album searches,
items for track searches). Also handles Browse() delegation for synthetic
container IDs (artist:<name>, album:<artist>/<album>).

Queries the SQLite index built by index_media.py instead of live os.walk.

Config (environment variables):
- MEDIA_ROOTS: colon-separated list of root dirs (set by PythonBridge)
- MEDIA_ROOT: single root dir fallback for standalone testing
- MEDIA_INDEX_DB: path to SQLite index (default: ~/.config/UMS/media_index.db)
- UMS_MEDIA_HOST / UMS_MEDIA_PORT: used to build resource URLs
- SEARCH_STRICT_CRITERIA: set to "true"/"1"/"yes" to honor renderer criteria as-is;
  when unset or "false" the WiiM field-narrowing rules are applied (default)
- SEARCH_ACCENT_ALIAS: set to "0"/"false"/"no" to disable accent-alias entries;
  when unset or "1" (default), a second entry with accent-stripped title (+ ' [*]')
  is created for any artist/album/track whose name contains accented characters
"""

import sys
import os
import re
import sqlite3
import mimetypes
import random
from dlna_tools import (
    fold_accents, _accent_alias_containers, _accent_alias_items,
    _album_sort_key, file_class_for_mime, make_url, find_cover_url,
    query_files, query_files_by_subpath, query_playlists, query_playlist_by_path,
    query_files_by_artist, query_files_by_album, open_db,
    build_didl_tracks, build_didl_containers, emit, emit_index_not_ready,
    _BROWSE_ARTIST, _BROWSE_ALBUM, _BROWSE_ALL_TRACKS, _BROWSE_PLAYLIST,
)

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
criteria    = sys.argv[1] if len(sys.argv) > 1 else ""
filter_str  = sys.argv[2] if len(sys.argv) > 2 else ""
start       = int(sys.argv[3]) if len(sys.argv) > 3 else 0
count       = int(sys.argv[4]) if len(sys.argv) > 4 else 50
renderer    = sys.argv[5] if len(sys.argv) > 5 else ""
container_id = sys.argv[6] if len(sys.argv) > 6 else "0"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# When True, honor the renderer's SearchCriteria exactly — all conditions and
# OR/AND logic are passed through unchanged.  When False (default), the WiiM
# field-narrowing rules are applied: each search class is restricted to only
# the fields that make sense for that result type.
# Set SEARCH_STRICT_CRITERIA=1 to enable; 0 or unset = WiiM filtering (default).
STRICT_SEARCH = os.environ.get('SEARCH_STRICT_CRITERIA', '0').strip() not in ('', '0', 'false', 'no')

# ---------------------------------------------------------------------------
# Parse the search criteria
# ---------------------------------------------------------------------------
# Extract upnp:class = "..." if present
_class_m = re.search(r'upnp:class\s*(?:=|derivedfrom)\s*"([^"]+)"', criteria, re.IGNORECASE)
requested_class = _class_m.group(1) if _class_m else None

# Extract all field contains "value" conditions (dc:* and upnp:*)
conditions = []
for m in re.finditer(r'((?:dc|upnp):[a-zA-Z]+)\s+contains\s+"([^"]+)"', criteria, re.IGNORECASE):
    conditions.append((m.group(1).lower(), m.group(2).strip().lower()))

# WiiM groups contains conditions with OR inside parentheses:
#   upnp:class = "..." and (field1 contains "x" or field2 contains "x")
# Detect whether the conditions body uses 'or' connectors.
_criteria_body = re.sub(r'upnp:class\s*(?:=|derivedfrom)\s*"[^"]*"\s*(and\s*)?', '', criteria, flags=re.IGNORECASE).strip().strip('()')
use_or_conditions = bool(re.search(r'\bor\b', _criteria_body, re.IGNORECASE))

# ---------------------------------------------------------------------------
# __browse__ mode: Browse into a synthetic artist: or album: container
# Called from UmsContentDirectoryService when objectID starts with artist:/album:
# ---------------------------------------------------------------------------
if criteria.startswith(_BROWSE_ARTIST) or criteria.startswith(_BROWSE_ALBUM) or criteria.startswith(_BROWSE_PLAYLIST) or criteria.startswith(_BROWSE_ALL_TRACKS):
    is_artist     = criteria.startswith(_BROWSE_ARTIST)
    is_playlist   = criteria.startswith(_BROWSE_PLAYLIST)
    is_all_tracks = criteria.startswith(_BROWSE_ALL_TRACKS)
    if is_playlist:
        subpath = criteria[len(_BROWSE_PLAYLIST):]
    elif is_artist:
        subpath = criteria[len(_BROWSE_ARTIST):]
    elif is_all_tracks:
        subpath = criteria[len(_BROWSE_ALL_TRACKS):]
    else:
        subpath = criteria[len(_BROWSE_ALBUM):]
    # Sanitize path traversal. Playlists use absolute filesystem paths so
    # preserve the leading slash; artist/album IDs are synthetic and have none.
    subpath = subpath.replace('..', '')
    if not is_playlist:
        subpath = subpath.lstrip('/')

    if is_all_tracks:
        # Return all audio tracks for this artist, sorted by album then track number then title
        rows = query_files_by_artist(subpath)
        if rows is None:
            emit_index_not_ready()
        items_out = []
        for row in rows:
            mime = row['mime'] or None
            if not (mime and mime.startswith('audio/')):
                continue
            rel          = row['relpath']
            fn           = row['filename']
            title        = row['title'] or os.path.splitext(fn)[0]
            cls          = file_class_for_mime(mime)
            cover_art    = row['cover_art']
            items_out.append({
                'id': rel, 'title': title, 'class': cls, 'url': make_url(rel),
                'artist': row['artist'], 'album': row['album'],
                'parent_id': f'allartisttracks:{subpath}',
                'track_number': row['track_number'],
                'disc_number': row['disc_number'],
                'release_date': row['release_date'],
                'cover_art_url': find_cover_url(rel, cover_art),
            })
        items_out.sort(key=lambda x: (
            *_album_sort_key(x['release_date'], x['album']),
            x['disc_number'] or 1,
            x['track_number'] is None,
            x['track_number'] or 0,
            x['title'].lower(),
        ))
        items_out = _accent_alias_items(items_out)
        emit(len(items_out), items_out, build_didl_tracks, start, count)

    if is_playlist:
        # Parse the .m3u file and return tracks as items
        pl_row = query_playlist_by_path(subpath)
        if pl_row is None:
            emit(0, [], build_didl_tracks, start, count)
        items_out = []
        try:
            with open(subpath, encoding='utf-8', errors='replace') as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # Lines are absolute paths like /media/<relpath>
                    if line.startswith('/media/'):
                        relpath = line[len('/media/'):]
                    else:
                        relpath = line.lstrip('/')
                    relpath = relpath.replace('..', '')
                    # Look up metadata from DB
                    conn = open_db()
                    row = None
                    if conn:
                        try:
                            row = conn.execute(
                                "SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
                                "FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album "
                                "WHERE f.relpath = ?",
                                (relpath,)
                            ).fetchone()
                        except sqlite3.OperationalError:
                            row = None
                        finally:
                            conn.close()
                    if row:
                        title = row['title'] or os.path.splitext(row['filename'])[0]
                        mime  = row['mime'] or None
                        cls   = file_class_for_mime(mime)
                        cover_art = row['cover_art']
                        items_out.append({
                            'id': relpath, 'title': title, 'class': cls,
                            'url': make_url(relpath),
                            'artist': row['artist'], 'album': row['album'],
                            'parent_id': f'playlist:{subpath}',
                            'track_number': row['track_number'],
                            'cover_art_url': find_cover_url(relpath, cover_art),
                        })
                    else:
                        # File not in index — build minimal item from path
                        fn    = os.path.basename(relpath)
                        title = os.path.splitext(fn)[0]
                        mime, _ = mimetypes.guess_type(fn)
                        cls   = file_class_for_mime(mime)
                        items_out.append({
                            'id': relpath, 'title': title, 'class': cls,
                            'url': make_url(relpath),
                            'artist': '', 'album': '',
                            'parent_id': f'playlist:{subpath}',
                            'track_number': None,
                        })
        except OSError:
            pass
        emit(len(items_out), items_out, build_didl_tracks, start, count)
    elif is_artist:
        # Return album containers for this artist (tag-based, not path-based)
        # First entry is always a synthetic "All Tracks" container.
        rows = query_files_by_artist(subpath)
        if rows is None:
            emit_index_not_ready()
        # Resolve the canonical (accented) artist name from the matched rows.
        # The WiiM may strip accents from container IDs so subpath may be unaccented.
        canonical_artist = subpath
        for _r in rows:
            for _field in ('album_artist', 'artist'):
                _val = _r[_field]
                if _val and fold_accents(_val) == fold_accents(subpath):
                    canonical_artist = _val
                    break
            else:
                continue
            break
        album_map = {}
        album_sample_relpath = {}
        album_sample_cover = {}
        album_year = {}
        total_audio = 0
        audio_relpaths_with_cover = []  # (relpath, cover_art) for random art pick
        fallback_relpath = ''
        for row in rows:
            mime = row['mime'] or None
            is_audio = mime and mime.startswith('audio/')
            if is_audio:
                total_audio += 1
                if not fallback_relpath:
                    fallback_relpath = row['relpath']
                if row['cover_art']:
                    audio_relpaths_with_cover.append((row['relpath'], row['cover_art']))
            alb = row['album']
            if not alb or not is_audio:
                continue
            album_map[alb] = album_map.get(alb, 0) + 1
            if alb not in album_sample_relpath:
                album_sample_relpath[alb] = row['relpath']
            if not album_sample_cover.get(alb):
                album_sample_cover[alb] = row['cover_art']
            if alb not in album_year and row['release_date']:
                album_year[alb] = row['release_date']
        if audio_relpaths_with_cover:
            _pick = random.choice(audio_relpaths_with_cover)
            any_relpath, any_cover = _pick[0], _pick[1]
        else:
            any_relpath, any_cover = fallback_relpath, ''
        all_tracks_container = {
            'id': f'allartisttracks:{subpath}',
            'title': 'All Tracks',
            'class': 'object.container.album.musicAlbum',
            'parent_id': f'artist:{subpath}',
            'artist': canonical_artist,
            'child_count': total_audio,
            'cover_art_url': find_cover_url(any_relpath, any_cover),
        }
        containers = [all_tracks_container] + [
            {'id': f'album:{subpath}/{alb}', 'title': alb,
             'class': 'object.container.album.musicAlbum',
             'parent_id': f'artist:{subpath}', 'artist': canonical_artist, 'child_count': cnt,
             'cover_art_url': find_cover_url(album_sample_relpath.get(alb, ''), album_sample_cover.get(alb, ''))}
            for alb, cnt in sorted(album_map.items(), key=lambda x: _album_sort_key(album_year.get(x[0]), x[0]))
        ]
        containers = _accent_alias_containers(containers)
        emit(len(containers), containers, build_didl_containers, start, count)
    else:
        # Return tracks for this album (artist/album both from tag)
        parts = subpath.split('/', 1)
        if len(parts) == 2:
            rows = query_files_by_album(parts[0], parts[1])
        else:
            rows = query_files_by_subpath(subpath)
        if rows is None:
            emit_index_not_ready()
        items_out = []
        for row in rows:
            rel          = row['relpath']
            artist       = row['artist']
            album        = row['album']
            fn           = row['filename']
            title        = row['title'] or os.path.splitext(fn)[0]
            mime         = row['mime'] or None
            cls          = file_class_for_mime(mime)
            track_number = row['track_number']
            disc_number  = row['disc_number']
            cover_art    = row['cover_art']
            items_out.append({
                'id': rel, 'title': title, 'class': cls, 'url': make_url(rel),
                'artist': artist, 'album': album,
                'parent_id': f'album:{subpath}',
                'track_number': track_number,
                'disc_number': disc_number,
                'cover_art_url': find_cover_url(rel, cover_art),
            })
        items_out.sort(key=lambda x: (
            x['disc_number'] or 1,
            x['track_number'] is None, x['track_number'] or 0,
            x['title'],
        ))
        items_out = _accent_alias_items(items_out)
        emit(len(items_out), items_out, build_didl_tracks, start, count)

# ---------------------------------------------------------------------------
# Normal search — query index
# ---------------------------------------------------------------------------
rows = query_files(conditions, use_or_conditions)
if rows is None:
    emit_index_not_ready()

# ---------------------------------------------------------------------------
# Playlist container search
# ---------------------------------------------------------------------------
if requested_class and 'playlistcontainer' in requested_class.lower():
    searched_values = [v for _, v in conditions]
    matched = []
    for v in searched_values:
        rows = query_playlists(v)
        if rows is None:
            emit_index_not_ready()
        matched.extend(rows)
    # Deduplicate by path
    seen_paths = set()
    containers = []
    for row in matched:
        if row['path'] not in seen_paths:
            seen_paths.add(row['path'])
            containers.append({
                'id': f'playlist:{row["path"]}',
                'title': row['name'],
                'class': 'object.container.playlistContainer',
                'parent_id': '0',
                'child_count': 0,
            })
    containers.sort(key=lambda c: fold_accents(c['title']))
    emit(len(containers), containers, build_didl_containers, start, count)

# ---------------------------------------------------------------------------
# Artist container search
# ---------------------------------------------------------------------------
if requested_class and 'musicartist' in requested_class.lower():
    if not STRICT_SEARCH:
        # Restrict to artist-field conditions only — match artist, album_artist, or composer.
        # WiiM often sends OR conditions like (upnp:artist contains "x" or dc:title contains "x")
        # which would otherwise match tracks by title and surface their artists as false positives.
        artist_conds = [(f, v) for f, v in conditions
                        if f in ('upnp:artist', 'upnp:albumartist', 'dc:creator')]
        if not artist_conds:
            # Renderer sent only dc:title (artist container name search) — re-map to artist fields.
            artist_conds = [('upnp:artist', v) for f, v in conditions if f == 'dc:title']
        if artist_conds:
            rows = query_files(artist_conds, True)  # OR across all artist fields
            if rows is None:
                emit_index_not_ready()
    artist_map = {}
    artist_sample = {}  # artist → (relpath, cover_art); prefer rows with embedded art
    for row in rows:
        # Use album_artist when present (avoids polluting the list with per-track
        # guest artists on compilations/collaborations); fall back to artist.
        a = row['album_artist'] or row['artist']
        if a:
            artist_map[a] = artist_map.get(a, 0) + 1
            # Keep the first row with embedded cover art, or any row if none yet
            if a not in artist_sample or (not artist_sample[a][1] and row['cover_art']):
                artist_sample[a] = (row['relpath'], row['cover_art'])

    # Fold-merge: collapse variants that differ only by accents/casing
    # (e.g. 'Jóhann Jóhannsson' and 'Johann Johannsson' from mis-tagged files)
    # Keep the variant with the most tracks; sum the counts.
    fold_groups = {}
    for a, cnt in artist_map.items():
        fold_groups.setdefault(fold_accents(a), []).append((a, cnt))
    artist_map = {}
    artist_cover = {}  # canonical → (relpath, cover_art)
    for variants in fold_groups.values():
        canonical = max(variants, key=lambda x: x[1])[0]
        artist_map[canonical] = sum(c for _, c in variants)
        # Pick embedded cover from any variant; fall back to any relpath for folder art
        for a, _ in variants:
            relpath, cover = artist_sample.get(a, ('', ''))
            if cover:
                artist_cover[canonical] = (relpath, cover)
                break
        if canonical not in artist_cover:
            relpath, cover = artist_sample.get(canonical, ('', ''))
            artist_cover[canonical] = (relpath, cover)

    containers = [
        {'id': f'artist:{a}', 'title': a, 'class': 'object.container.person.musicArtist',
         'parent_id': '0', 'child_count': cnt,
         'cover_art_url': find_cover_url(*artist_cover.get(a, ('', '')))}
        for a, cnt in sorted(artist_map.items(), key=lambda x: fold_accents(x[0]))
    ]
    containers = _accent_alias_containers(containers, lambda c: fold_accents(c['title']))
    emit(len(containers), containers, build_didl_containers, start, count)

# ---------------------------------------------------------------------------
# Album container search
# ---------------------------------------------------------------------------
if requested_class and 'musicalbum' in requested_class.lower():
    if not STRICT_SEARCH:
        # Restrict to album-field conditions only — ignore artist/title OR conditions.
        album_conds = [(f, v) for f, v in conditions if f == 'upnp:album']
        if not album_conds:
            # Renderer sent only dc:title (album container name search) — re-map to album field.
            album_conds = [('upnp:album', v) for f, v in conditions if f == 'dc:title']
        if album_conds:
            rows = query_files(album_conds, False)
            if rows is None:
                emit_index_not_ready()
    album_map = {}
    album_sample_relpath = {}
    album_sample_cover = {}
    album_year = {}
    for row in rows:
        # Use album_artist when present so albums group under the same artist
        # as the artist search does (avoids per-track guest credits splitting albums).
        artist = row['album_artist'] or row['artist']
        album  = row['album']
        if not artist or not album:
            continue
        key = (artist, album)
        album_map[key] = album_map.get(key, 0) + 1
        if key not in album_sample_relpath:
            album_sample_relpath[key] = row['relpath']
        if not album_sample_cover.get(key):
            album_sample_cover[key] = row['cover_art']
        if key not in album_year and row['release_date']:
            album_year[key] = row['release_date']

    containers = [
        {'id': f'album:{a}/{alb}', 'title': alb,
         'class': 'object.container.album.musicAlbum',
         'parent_id': f'artist:{a}', 'artist': a, 'child_count': cnt,
         'cover_art_url': find_cover_url(album_sample_relpath.get((a, alb), ''), album_sample_cover.get((a, alb), ''))}
        for (a, alb), cnt in sorted(album_map.items(), key=lambda x: _album_sort_key(album_year.get(x[0]), x[0][1]))
    ]
    containers = _accent_alias_containers(containers)
    emit(len(containers), containers, build_didl_containers, start, count)

# ---------------------------------------------------------------------------
# Track / item search (default)
# ---------------------------------------------------------------------------
# When the renderer explicitly requests audio tracks, restrict to title-field
# conditions only — ignore artist/album OR conditions sent by the WiiM.
_track_class = requested_class and (
    'audioitem' in requested_class.lower() or
    'musictrack' in requested_class.lower()
)
if _track_class and not STRICT_SEARCH:
    title_conds = [(f, v) for f, v in conditions if f == 'dc:title']
    if title_conds:
        rows = query_files(title_conds, False)
        if rows is None:
            emit_index_not_ready()

items_out = []
for row in rows:
    rel    = row['relpath']
    artist = row['artist']
    album  = row['album']
    fn     = row['filename']
    title  = row['title'] or os.path.splitext(fn)[0]
    mime   = row['mime'] or None
    cls    = file_class_for_mime(mime)
    cover_art = row['cover_art']
    items_out.append({
        'id': rel, 'title': title, 'class': cls, 'url': make_url(rel),
        'artist': artist, 'album': album, 'parent_id': '0',
        'cover_art_url': find_cover_url(rel, cover_art),
    })

items_out = _accent_alias_items(items_out)
emit(len(items_out), items_out, build_didl_tracks, start, count)
