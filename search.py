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
"""

import sys
import json
import os
import re
import html
import sqlite3
import mimetypes
import random
from urllib.parse import quote

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
HOST = os.environ.get('UMS_MEDIA_HOST', os.environ.get('UMS_HOST', '127.0.0.1'))
PORT = os.environ.get('UMS_MEDIA_PORT', os.environ.get('UMS_PORT', '9002'))

AUDIO_EXTS = {'.mp3', '.m4a', '.flac', '.aac', '.ogg', '.wav', '.wma', '.opus'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v'}
ALL_EXTS   = AUDIO_EXTS | IMAGE_EXTS | VIDEO_EXTS

def _default_db_path():
    xdg = os.environ.get('XDG_CONFIG_HOME', '')
    base = xdg if xdg else os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'UMS', 'database', 'media_index.db')

DB_PATH     = os.environ.get('MEDIA_INDEX_DB', _default_db_path())
COVER_CACHE = os.environ.get('COVER_CACHE_DIR', '')

# When True, honor the renderer's SearchCriteria exactly — all conditions and
# OR/AND logic are passed through unchanged.  When False (default), the WiiM
# field-narrowing rules are applied: each search class is restricted to only
# the fields that make sense for that result type.
# Set SEARCH_STRICT_CRITERIA=1 to enable; 0 or unset = WiiM filtering (default).
STRICT_SEARCH = os.environ.get('SEARCH_STRICT_CRITERIA', '0').strip() not in ('', '0', 'false', 'no')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def file_class_for_mime(mime):
    if not mime:
        return 'object.item'
    if mime.startswith('audio/'):
        return 'object.item.audioItem.musicTrack'
    if mime.startswith('video/'):
        return 'object.item.videoItem.movie'
    if mime.startswith('image/'):
        return 'object.item.imageItem.photo'
    return 'object.item'

def make_url(relpath):
    return f'http://{HOST}:{PORT}/media/{quote(relpath)}'

def make_cover_url(cache_path):
    """Convert an absolute cache file path to a /cover/<filename> URL."""
    if not cache_path:
        return None
    filename = os.path.basename(cache_path)
    return f'http://{HOST}:{PORT}/cover/{quote(filename)}'

def find_cover_url(track_relpath, db_cover_art=''):
    """Return the best available cover art URL for a track.
    Priority: 1) embedded art already extracted to cache (db_cover_art column)
              2) folder image file in the same directory
    """
    # 1. Use cached embedded art if available
    if db_cover_art:
        url = make_cover_url(db_cover_art)
        if url:
            return url
    # 2. Fall back to folder image file
    if not track_relpath:
        return None
    parts = track_relpath.split('/')
    if len(parts) < 2:
        return None
    dir_prefix = '/'.join(parts[:-1]) + '/'
    conn = open_db()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT relpath FROM files WHERE relpath LIKE ? AND mime LIKE 'image/%' LIMIT 1",
            (dir_prefix + '%',)
        ).fetchone()
        return make_url(row['relpath']) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

def path_parts(relpath):
    """Split relpath into (artist, album, filename) components (may be empty)."""
    parts = relpath.split('/')
    artist   = parts[0] if len(parts) > 1 else ''
    album    = parts[1] if len(parts) > 2 else ''
    filename = parts[-1]
    return artist, album, filename

# ---------------------------------------------------------------------------
# Index access
# ---------------------------------------------------------------------------
def open_db():
    """Open the SQLite index read-only.  Returns None if the DB doesn't exist."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _condition_sql(field, value):
    """Return (sql_fragment, value) for a single field/value condition."""
    val = f'%{value}%'
    if field == 'upnp:artist':
        return ("(lower(f.artist) LIKE lower(?) OR lower(f.album_artist) LIKE lower(?))", [val, val])
    elif field == 'upnp:albumartist':
        return ("lower(f.album_artist) LIKE lower(?)", val)
    elif field == 'dc:creator':
        # dc:creator is used for both artist and composer depending on renderer
        return ("(lower(f.artist) LIKE lower(?) OR lower(f.composer) LIKE lower(?))", [val, val])
    elif field == 'upnp:album':
        return ("lower(f.album) LIKE lower(?)", val)
    elif field == 'dc:title':
        return ("lower(f.title) LIKE lower(?)", val)
    elif field == 'upnp:genre':
        return ("lower(f.genre) LIKE lower(?)", val)
    else:
        return ("lower(f.title) LIKE lower(?)", val)


def query_files(conditions, use_or):
    """
    Query the index and return a list of sqlite3.Row objects.
    If the index is not available, returns None (caller should emit empty result).
    """
    conn = open_db()
    if conn is None:
        return None

    try:
        if not conditions:
            rows = conn.execute(
                "SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
                "FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album"
            ).fetchall()
        else:
            clauses = []
            params  = []
            for field, value in conditions:
                sql_frag, val = _condition_sql(field, value)
                clauses.append(sql_frag)
                if isinstance(val, list):
                    params.extend(val)
                elif val:
                    params.append(val)

            joiner  = " OR " if use_or else " AND "
            where   = joiner.join(clauses)
            rows    = conn.execute(
                f"SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
                f"FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album "
                f"WHERE {where}",
                params
            ).fetchall()
        return rows
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def query_files_by_subpath(subpath):
    """
    Return all files whose relpath starts with subpath (used for __browse__).
    Returns None if index not available.
    """
    conn = open_db()
    if conn is None:
        return None
    try:
        prefix = subpath.rstrip('/') + '/'
        rows = conn.execute(
            "SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
            "FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album "
            "WHERE f.relpath = ? OR f.relpath LIKE ?",
            (subpath, prefix + '%')
        ).fetchall()
        return rows
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def query_playlists(name_fragment):
    """Search the playlists table by name (case-insensitive LIKE). Returns None if DB unavailable."""
    conn = open_db()
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT * FROM playlists WHERE lower(name) LIKE lower(?)",
            (f'%{name_fragment}%',)
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def query_playlist_by_path(path):
    """Return a single playlist row by exact path, or None."""
    conn = open_db()
    if conn is None:
        return None
    try:
        return conn.execute("SELECT * FROM playlists WHERE path = ?", (path,)).fetchone()
    except Exception:
        return None
    finally:
        conn.close()


def query_files_by_artist(artist):
    """Return all files tagged with the given artist (matches artist OR album_artist)."""
    conn = open_db()
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
            "FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album "
            "WHERE f.artist = ? OR f.album_artist = ?",
            (artist, artist)
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def query_files_by_album(artist, album):
    """Return all files tagged with the given artist and album."""
    conn = open_db()
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
            "FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album "
            "WHERE f.artist = ? AND f.album = ?",
            (artist, album)
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# DIDL builders
# ---------------------------------------------------------------------------
_DIDL_OPEN = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
)
_DIDL_CLOSE = '</DIDL-Lite>'

def build_didl_tracks(items):
    out = []
    for item in items:
        mime, _ = mimetypes.guess_type(item['id'])
        protocol = mime if mime else 'application/octet-stream'
        artist_xml = f'  <upnp:artist>{html.escape(item["artist"])}</upnp:artist>\n  <dc:creator>{html.escape(item["artist"])}</dc:creator>\n' if item.get('artist') else ''
        album_xml  = f'  <upnp:album>{html.escape(item["album"])}</upnp:album>\n' if item.get('album') else ''
        track_xml  = f'  <upnp:originalTrackNumber>{item["track_number"]}</upnp:originalTrackNumber>\n' if item.get('track_number') else ''
        art_xml    = f'  <upnp:albumArtURI>{html.escape(item["cover_art_url"])}</upnp:albumArtURI>\n' if item.get('cover_art_url') else ''
        out.append(
            f'<item id="{html.escape(item["id"])}" parentID="{html.escape(item.get("parent_id","0"))}" restricted="1">\n'
            f'  <dc:title>{html.escape(item["title"])}</dc:title>\n'
            f'{artist_xml}'
            f'{album_xml}'
            f'{track_xml}'
            f'{art_xml}'
            f'  <upnp:class>{html.escape(item["class"])}</upnp:class>\n'
            f'  <res protocolInfo="http-get:*:{html.escape(protocol)}:*">{html.escape(item["url"])}</res>\n'
            f'</item>'
        )
    return _DIDL_OPEN + '\n'.join(out) + _DIDL_CLOSE

def build_didl_containers(containers):
    out = []
    for c in containers:
        artist_xml = ''
        if c.get('artist'):
            a = html.escape(c['artist'])
            artist_xml = f'  <upnp:artist>{a}</upnp:artist>\n  <upnp:albumArtist>{a}</upnp:albumArtist>\n'
        art_xml = ''
        if c.get('cover_art_url'):
            art_xml = f'  <upnp:albumArtURI>{html.escape(c["cover_art_url"])}</upnp:albumArtURI>\n'
        out.append(
            f'<container id="{html.escape(c["id"])}" parentID="{html.escape(c.get("parent_id","0"))}" '
            f'restricted="1" childCount="{c.get("child_count",0)}" searchable="0">\n'
            f'  <dc:title>{html.escape(c["title"])}</dc:title>\n'
            f'{artist_xml}'
            f'{art_xml}'
            f'  <upnp:class>{html.escape(c["class"])}</upnp:class>\n'
            f'</container>'
        )
    return _DIDL_OPEN + '\n'.join(out) + _DIDL_CLOSE

def emit(total, items_or_containers, didl_fn):
    page = items_or_containers[start:start+count] if count > 0 else items_or_containers[start:]
    print(json.dumps({
        'totalMatches': total,
        'returned': len(page),
        'didl': didl_fn(page),
    }))
    sys.exit(0)

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
_BROWSE_ARTIST        = '__browse__ artist:'
_BROWSE_ALBUM         = '__browse__ album:'
_BROWSE_ALL_TRACKS    = '__browse__ allartisttracks:'

def emit_index_not_ready():
    message_item = (
        '<container id="__index_building__" parentID="0" restricted="1" childCount="0" searchable="0">\n'
        '  <dc:title>Index is building \u2014 please search again in a moment</dc:title>\n'
        '  <upnp:class>object.container</upnp:class>\n'
        '</container>'
    )
    print(json.dumps({
        'totalMatches': 1,
        'returned': 1,
        'didl': _DIDL_OPEN + message_item + _DIDL_CLOSE,
        'error': 'index_not_ready',
    }))
    sys.exit(0)

_BROWSE_PLAYLIST = '__browse__ playlist:'

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
                'cover_art_url': find_cover_url(rel, cover_art),
            })
        items_out.sort(key=lambda x: (
            x['album'].lower(),
            x['track_number'] is None,
            x['track_number'] or 0,
            x['title'].lower(),
        ))
        emit(len(items_out), items_out, build_didl_tracks)

    if is_playlist:
        # Parse the .m3u file and return tracks as items
        pl_row = query_playlist_by_path(subpath)
        if pl_row is None:
            emit(0, [], build_didl_tracks)
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
        emit(len(items_out), items_out, build_didl_tracks)
    elif is_artist:
        # Return album containers for this artist (tag-based, not path-based)
        # First entry is always a synthetic "All Tracks" container.
        rows = query_files_by_artist(subpath)
        if rows is None:
            emit_index_not_ready()
        album_map = {}
        album_sample_relpath = {}
        album_sample_cover = {}
        total_audio = 0
        audio_relpaths_with_cover = []  # (relpath, cover_art) for random art pick
        fallback_relpath = ''
        for row in rows:
            mime = row['mime'] or None
            if mime and mime.startswith('audio/'):
                total_audio += 1
                if not fallback_relpath:
                    fallback_relpath = row['relpath']
                if row['cover_art']:
                    audio_relpaths_with_cover.append((row['relpath'], row['cover_art']))
            alb = row['album']
            if not alb:
                continue
            album_map[alb] = album_map.get(alb, 0) + 1
            if alb not in album_sample_relpath:
                album_sample_relpath[alb] = row['relpath']
            if not album_sample_cover.get(alb):
                album_sample_cover[alb] = row['cover_art']
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
            'artist': subpath,
            'child_count': total_audio,
            'cover_art_url': find_cover_url(any_relpath, any_cover),
        }
        containers = [all_tracks_container] + [
            {'id': f'album:{subpath}/{alb}', 'title': alb,
             'class': 'object.container.album.musicAlbum',
             'parent_id': f'artist:{subpath}', 'artist': subpath, 'child_count': cnt,
             'cover_art_url': find_cover_url(album_sample_relpath.get(alb, ''), album_sample_cover.get(alb, ''))}
            for alb, cnt in sorted(album_map.items())
        ]
        emit(len(containers), containers, build_didl_containers)
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
            cover_art    = row['cover_art']
            items_out.append({
                'id': rel, 'title': title, 'class': cls, 'url': make_url(rel),
                'artist': artist, 'album': album,
                'parent_id': f'album:{subpath}',
                'track_number': track_number,
                'cover_art_url': find_cover_url(rel, cover_art),
            })
        items_out.sort(key=lambda x: (x['track_number'] is None, x['track_number'] or 0, x['title']))
        emit(len(items_out), items_out, build_didl_tracks)

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
    containers.sort(key=lambda c: c['title'].lower())
    emit(len(containers), containers, build_didl_containers)

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
        if artist_conds:
            rows = query_files(artist_conds, True)  # OR across all artist fields
            if rows is None:
                emit_index_not_ready()
    artist_map = {}
    for row in rows:
        # Surface both artist and album_artist as separate artist entries
        for a in filter(None, {row['artist'], row['album_artist']}):
            artist_map[a] = artist_map.get(a, 0) + 1

    containers = [
        {'id': f'artist:{a}', 'title': a, 'class': 'object.container.person.musicArtist',
         'parent_id': '0', 'child_count': cnt}
        for a, cnt in sorted(artist_map.items())
    ]
    emit(len(containers), containers, build_didl_containers)

# ---------------------------------------------------------------------------
# Album container search
# ---------------------------------------------------------------------------
if requested_class and 'musicalbum' in requested_class.lower():
    if not STRICT_SEARCH:
        # Restrict to album-field conditions only — ignore artist/title OR conditions.
        album_conds = [(f, v) for f, v in conditions if f == 'upnp:album']
        if album_conds:
            rows = query_files(album_conds, False)
            if rows is None:
                emit_index_not_ready()
    album_map = {}
    album_sample_relpath = {}
    album_sample_cover = {}
    for row in rows:
        artist = row['artist']
        album  = row['album']
        if not artist or not album:
            continue
        key = (artist, album)
        album_map[key] = album_map.get(key, 0) + 1
        if key not in album_sample_relpath:
            album_sample_relpath[key] = row['relpath']
        if not album_sample_cover.get(key):
            album_sample_cover[key] = row['cover_art']

    containers = [
        {'id': f'album:{a}/{alb}', 'title': alb,
         'class': 'object.container.album.musicAlbum',
         'parent_id': f'artist:{a}', 'artist': a, 'child_count': cnt,
         'cover_art_url': find_cover_url(album_sample_relpath.get((a, alb), ''), album_sample_cover.get((a, alb), ''))}
        for (a, alb), cnt in sorted(album_map.items())
    ]
    emit(len(containers), containers, build_didl_containers)

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

emit(len(items_out), items_out, build_didl_tracks)
