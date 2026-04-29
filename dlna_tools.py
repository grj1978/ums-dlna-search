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
Shared DLNA utilities for search.py and browse.py.

Provides:
  - Media type constants and config (HOST, PORT, DB_PATH, etc.)
  - URL builders (make_url, make_cover_url, find_cover_url)
  - Text helpers (fold_accents, _accent_alias_containers, _accent_alias_items)
  - SQLite index access (open_db, query_files, query_files_by_artist, etc.)
  - DIDL-Lite builders (build_didl_tracks, build_didl_containers)
  - Output helpers (emit, emit_index_not_ready)
  - Browse container ID prefixes (_BROWSE_ARTIST, _BROWSE_ALBUM, etc.)
"""

import sys
import json
import os
import html
import sqlite3
import mimetypes
import unicodedata
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUDIO_EXTS = {'.mp3', '.m4a', '.flac', '.aac', '.ogg', '.wav', '.wma', '.opus'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v'}
ALL_EXTS   = AUDIO_EXTS | IMAGE_EXTS | VIDEO_EXTS

# ---------------------------------------------------------------------------
# Config  (read from environment; set by PythonBridge or manually for testing)
# ---------------------------------------------------------------------------
HOST = os.environ.get('UMS_MEDIA_HOST', os.environ.get('UMS_HOST', '127.0.0.1'))
PORT = os.environ.get('UMS_MEDIA_PORT', os.environ.get('UMS_PORT', '9002'))

def _default_db_path():
    xdg = os.environ.get('XDG_CONFIG_HOME', '')
    base = xdg if xdg else os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'UMS', 'database', 'media_index.db')

DB_PATH     = os.environ.get('MEDIA_INDEX_DB', _default_db_path())
COVER_CACHE = os.environ.get('COVER_CACHE_DIR', '')

# When True, creates an additional entry with accent-stripped title (suffixed ' [*]')
# for any artist, album, or track whose name contains accented/special characters.
# Only needed for renderers that cannot render Unicode.  Disabled by default.
# Enable with SEARCH_ACCENT_ALIAS=1.
ACCENT_ALIAS = os.environ.get('SEARCH_ACCENT_ALIAS', '0').strip() not in ('', '0', 'false', 'no')

# ---------------------------------------------------------------------------
# Browse container ID prefixes  (used by both search.py and browse.py)
# ---------------------------------------------------------------------------
_BROWSE_ARTIST     = '__browse__ artist:'
_BROWSE_ALBUM      = '__browse__ album:'
_BROWSE_ALL_TRACKS = '__browse__ allartisttracks:'
_BROWSE_PLAYLIST   = '__browse__ playlist:'

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


def fold_accents(s):
    """Normalize a string for accent-insensitive matching and sorting.
    Lowercases, then strips combining diacritical marks via NFD decomposition.
    Examples: 'Jóhann' → 'johann', 'Björk' → 'bjork', 'Ñoño' → 'nono'
    """
    if not s:
        return s or ''
    return ''.join(
        c for c in unicodedata.normalize('NFD', s.lower())
        if unicodedata.category(c) != 'Mn'
    )


def _accent_alias_containers(containers, sort_key=None):
    """If ACCENT_ALIAS is enabled, append a folded-title alias entry for every container
    whose title contains accented characters.  The alias shares the same ``id`` so
    drilling into it resolves to the same real content.  ``sort_key``, when supplied,
    is used to re-sort the combined list.
    """
    if not ACCENT_ALIAS:
        return containers
    aliases = [
        {**c, 'title': fold_accents(c['title']) + ' [*]'}
        for c in containers
        if fold_accents(c['title']) != c['title'].lower()
    ]
    if not aliases:
        return containers
    combined = containers + aliases
    if sort_key:
        combined.sort(key=sort_key)
    return combined


def _accent_alias_items(items_out):
    """If ACCENT_ALIAS is enabled, append a folded-title alias item for every track
    whose title contains accented characters.  The alias shares the same ``id``/``url``
    so it plays the correct file.
    """
    if not ACCENT_ALIAS:
        return items_out
    aliases = [
        {**item, 'title': fold_accents(item['title']) + ' [*]'}
        for item in items_out
        if fold_accents(item['title']) != item['title'].lower()
    ]
    return items_out + aliases


# ---------------------------------------------------------------------------
# Index access
# ---------------------------------------------------------------------------
SEARCH_SCHEMA_VERSION = "8"

def open_db():
    """Open the SQLite index read-only.  Returns None if the DB doesn't exist or schema is outdated."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.create_function('fold', 1, lambda s: fold_accents(s) if s else '')
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if row is None or row[0] != SEARCH_SCHEMA_VERSION:
            conn.close()
            return None
        return conn
    except sqlite3.OperationalError:
        return None


def _condition_sql(field, value):
    """Return (sql_fragment, value) for a single field/value condition."""
    val = f'%{fold_accents(value)}%'
    if field == 'upnp:artist':
        return ("(fold(f.artist) LIKE ? OR fold(f.album_artist) LIKE ?)", [val, val])
    elif field == 'upnp:albumartist':
        return ("fold(f.album_artist) LIKE ?", val)
    elif field == 'dc:creator':
        # dc:creator is used for both artist and composer depending on renderer
        return ("(fold(f.artist) LIKE ? OR fold(f.composer) LIKE ?)", [val, val])
    elif field == 'upnp:album':
        return ("fold(f.album) LIKE ?", val)
    elif field == 'dc:title':
        return ("fold(f.title) LIKE ?", val)
    elif field == 'upnp:genre':
        return ("fold(f.genre) LIKE ?", val)
    else:
        return ("fold(f.title) LIKE ?", val)


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
            "SELECT * FROM playlists WHERE fold(name) LIKE ?",
            (f'%{fold_accents(name_fragment)}%',)
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
            "WHERE fold(f.artist) = fold(?) OR fold(f.album_artist) = fold(?)",
            (artist, artist)
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def query_files_by_album(artist, album):
    """Return all files tagged with the given artist and album.
    Matches on album_artist OR artist so that tracks where the artist browsed
    is only the album_artist (not the per-track artist field) are included.
    """
    conn = open_db()
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT f.*, COALESCE(a.cover_art, '') AS cover_art "
            "FROM files f LEFT JOIN albums a ON f.artist = a.artist AND f.album = a.album "
            "WHERE (fold(f.artist) = fold(?) OR fold(f.album_artist) = fold(?)) AND fold(f.album) = fold(?)",
            (artist, artist, album)
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _album_sort_key(release_date, album):
    """Sort key for album ordering: release_date ascending (no date sorts last), then alphabetical.
    release_date is an integer in YYYYMMDD form (e.g. 20030512).
    """
    no_date = release_date is None or release_date == 0
    return (no_date, release_date or 0, fold_accents(album))


# ---------------------------------------------------------------------------
# DIDL-Lite builders
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


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def emit(total, items_or_containers, didl_fn, start, count):
    """Paginate items_or_containers and print the JSON result, then exit."""
    page = items_or_containers[start:start+count] if count > 0 else items_or_containers[start:]
    print(json.dumps({
        'totalMatches': total,
        'returned': len(page),
        'didl': didl_fn(page),
    }))
    sys.exit(0)

def emit_index_not_ready():
    """Print a single placeholder container indicating the index is still building, then exit."""
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
