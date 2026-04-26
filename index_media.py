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
Media index builder for UMS DLNA Search.

Walks MEDIA_ROOTS and writes a SQLite database used by search.py so that
searches query the index instead of doing a live os.walk on every request.

Environment variables (set by PythonBridge, or manually for testing):
  MEDIA_ROOTS            colon-separated list of root directories to scan
  MEDIA_ROOT             single root fallback for standalone testing
  MEDIA_INDEX_DB         path to the SQLite DB (default: ~/.config/UMS/media_index.db)
  FOLDER_NAMES_IGNORED   comma-separated folder names to skip (set in UMS.conf)

Usage:
  python3 index_media.py            # normal run
  python3 index_media.py --verbose  # print progress
"""

import os
import sys
import sqlite3
import mimetypes
import re
import time

try:
    from mutagen import File as MutagenFile
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "6"

PLAYLIST_EXTS = {'.m3u', '.m3u8'}

AUDIO_EXTS = {'.mp3', '.m4a', '.flac', '.aac', '.ogg', '.wav', '.wma', '.opus'}
IMAGE_EXTS  = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
VIDEO_EXTS  = {'.mp4', '.mkv', '.avi', '.mov', '.m4v'}
ALL_EXTS    = AUDIO_EXTS | IMAGE_EXTS | VIDEO_EXTS

verbose = '--verbose' in sys.argv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_roots_env = os.environ.get('MEDIA_ROOTS', '')
if _roots_env:
    MEDIA_ROOTS = [r for r in _roots_env.split(':') if r]
else:
    MEDIA_ROOTS = [r for r in [os.environ.get('MEDIA_ROOT', '')] if r]

_ignored_env = os.environ.get('FOLDER_NAMES_IGNORED', '')
IGNORED_FOLDERS = set(n.strip() for n in _ignored_env.split(',') if n.strip()) if _ignored_env else set()

def _default_db_path():
    xdg = os.environ.get('XDG_CONFIG_HOME', '')
    base = xdg if xdg else os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'UMS', 'database', 'media_index.db')

DB_PATH      = os.environ.get('MEDIA_INDEX_DB', _default_db_path())
COVER_CACHE  = os.environ.get('COVER_CACHE_DIR', os.path.join(os.path.dirname(DB_PATH), '..', 'cache', 'covers'))

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY,
    artist       TEXT NOT NULL DEFAULT '',
    album_artist TEXT NOT NULL DEFAULT '',
    composer     TEXT NOT NULL DEFAULT '',
    album        TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    genre        TEXT NOT NULL DEFAULT '',
    filename     TEXT NOT NULL DEFAULT '',
    relpath      TEXT NOT NULL,
    fullpath     TEXT NOT NULL,
    media_root   TEXT NOT NULL,
    mime         TEXT NOT NULL DEFAULT '',
    track_number INTEGER,
    mtime        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_artist       ON files (artist       COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_album_artist ON files (album_artist COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_composer     ON files (composer     COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_album        ON files (album        COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_title        ON files (title        COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_genre        ON files (genre        COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_relpath      ON files (relpath);
CREATE TABLE IF NOT EXISTS albums (
    artist    TEXT NOT NULL DEFAULT '',
    album     TEXT NOT NULL DEFAULT '',
    cover_art TEXT NOT NULL DEFAULT '',
    genre     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (artist, album)
);
CREATE TABLE IF NOT EXISTS playlists (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    path     TEXT NOT NULL UNIQUE,
    mtime    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_playlist_name ON playlists (name COLLATE NOCASE);
"""

def open_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def needs_rebuild(conn):
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        return row is None or row[0] != SCHEMA_VERSION
    except sqlite3.OperationalError:
        return True


def _extract_embedded_cover(fullpath, artist, album):
    """Extract embedded album art from fullpath into COVER_CACHE.
    Returns the cache file path (str) if successful, else empty string."""
    if not MUTAGEN_AVAILABLE:
        return ''
    try:
        from mutagen import File as _MF
        audio = _MF(fullpath)
        if audio is None:
            return ''
        pic_data = None
        # ID3 (MP3, etc.) — APIC frames
        if hasattr(audio, 'tags') and audio.tags:
            for key in audio.tags.keys():
                if key.startswith('APIC'):
                    pic_data = audio.tags[key].data
                    break
        # FLAC / Vorbis — PICTURE block via mutagen.flac
        if pic_data is None and hasattr(audio, 'pictures') and audio.pictures:
            pic_data = audio.pictures[0].data
        # MP4 — covr atom
        if pic_data is None:
            covr = audio.tags.get('covr') if audio.tags else None
            if covr:
                pic_data = bytes(covr[0])
        if not pic_data:
            return ''
        # Build a stable filename from artist+album
        safe = re.sub(r'[^\w\-]', '_', f'{artist}_{album}')[:120]
        cache_path = os.path.join(COVER_CACHE, f'{safe}.jpg')
        os.makedirs(COVER_CACHE, exist_ok=True)
        with open(cache_path, 'wb') as fh:
            fh.write(pic_data)
        return cache_path
    except Exception:
        return ''


def read_tags(fullpath, path_artist, path_album, path_title):
    """Read ID3/FLAC/etc tags via mutagen; return (artist, album_artist, composer, album, title, genre, track_number)."""
    artist, album_artist, composer, album, title, genre, track_number = (
        path_artist, '', '', path_album, path_title, '', None
    )
    if MUTAGEN_AVAILABLE:
        try:
            tags = MutagenFile(fullpath, easy=True)
            if tags is not None:
                artist       = (tags.get('artist')      or [''])[0] or path_artist
                album_artist = (tags.get('albumartist') or [''])[0]
                composer     = (tags.get('composer')    or [''])[0]
                album        = (tags.get('album')       or [''])[0] or path_album
                title        = (tags.get('title')       or [''])[0] or path_title
                genre        = (tags.get('genre')       or [''])[0]
                tn_raw = (tags.get('tracknumber') or [''])[0]
                if tn_raw:
                    m = re.match(r'(\d+)', str(tn_raw))
                    if m:
                        track_number = int(m.group(1))
        except Exception:
            pass  # corrupt/unreadable file — use path fallbacks
    return artist, album_artist, composer, album, title, genre, track_number


def _walk_media_roots():
    """
    Yield (fullpath, relpath, mr, filename, mtime, path_artist, path_album, path_title)
    for every eligible file under all MEDIA_ROOTS.
    """
    for mr in MEDIA_ROOTS:
        if not os.path.isdir(mr):
            print(f"WARNING: MEDIA_ROOT not found: {mr}", flush=True)
            continue
        for dirpath, dirs, filenames in os.walk(mr):
            dirs[:] = [d for d in dirs if d not in IGNORED_FOLDERS]
            for fn in filenames:
                _, ext = os.path.splitext(fn)
                if ext.lower() not in ALL_EXTS:
                    continue
                fullpath = os.path.join(dirpath, fn)
                relpath  = os.path.relpath(fullpath, mr).replace(os.sep, '/')
                parts    = relpath.split('/')
                if len(parts) > 2:
                    path_artist, path_album = parts[0], parts[1]
                elif len(parts) == 2:
                    path_artist, path_album = parts[0], ''
                else:
                    path_artist, path_album = '', ''
                path_title = os.path.splitext(fn)[0]
                mtime = int(os.stat(fullpath).st_mtime)
                yield fullpath, relpath, mr, fn, mtime, path_artist, path_album, path_title


def _walk_playlists():
    """Yield (name, path, mtime) for every .m3u/.m3u8 under MEDIA_ROOTS."""
    for mr in MEDIA_ROOTS:
        if not os.path.isdir(mr):
            continue
        for dirpath, dirs, filenames in os.walk(mr):
            dirs[:] = [d for d in dirs if d not in IGNORED_FOLDERS]
            for fn in filenames:
                _, ext = os.path.splitext(fn)
                if ext.lower() not in PLAYLIST_EXTS:
                    continue
                fullpath = os.path.join(dirpath, fn)
                name = os.path.splitext(fn)[0]
                mtime = int(os.stat(fullpath).st_mtime)
                yield name, fullpath, mtime


def rebuild_playlists(conn):
    """Resync the playlists table (incremental — insert/update/delete)."""
    existing = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT path, id, mtime FROM playlists").fetchall()
    }
    seen = set()
    to_insert = []
    to_update = []  # (name, mtime, id)
    for name, path, mtime in _walk_playlists():
        seen.add(path)
        if path in existing:
            ex_id, ex_mtime = existing[path]
            if mtime != ex_mtime:
                to_update.append((name, mtime, ex_id))
        else:
            to_insert.append((name, path, mtime))
    deleted_ids = [existing[p][0] for p in existing if p not in seen]
    if to_insert:
        conn.executemany("INSERT INTO playlists (name, path, mtime) VALUES (?, ?, ?)", to_insert)
    if to_update:
        conn.executemany("UPDATE playlists SET name=?, mtime=? WHERE id=?", to_update)
    if deleted_ids:
        conn.executemany("DELETE FROM playlists WHERE id=?", [(i,) for i in deleted_ids])
    return len(to_insert), len(to_update), len(deleted_ids)


def full_rebuild():
    """Build a fresh index into a temp DB file, then atomically replace the live DB.
    The live DB is never locked or modified until the instant of the rename,
    so searches continue uninterrupted throughout the entire scan."""
    tmp_path = DB_PATH + '.building'
    # Clean up any leftover temp files from a previous interrupted build
    for suffix in ('', '-wal', '-shm'):
        try:
            os.remove(tmp_path + suffix)
        except FileNotFoundError:
            pass

    tmp_conn = sqlite3.connect(tmp_path)
    tmp_conn.execute("PRAGMA journal_mode=DELETE")  # no WAL needed; single writer, temp file
    tmp_conn.execute("PRAGMA synchronous=OFF")       # faster bulk load; file is throwaway if crash
    tmp_conn.executescript(CREATE_SCHEMA)

    # Track which (artist, album) pairs have already had cover art extracted
    # so we only extract once per album during a full rebuild.
    covered_albums = {}  # album_key → (cover_art, genre)

    rows = []
    total = 0
    started = time.time()
    for fullpath, relpath, mr, fn, mtime, path_artist, path_album, path_title in _walk_media_roots():
        mime, _ = mimetypes.guess_type(fn)
        _is_audio = mime and mime.startswith('audio/')
        artist, album_artist, composer, album, title, genre, track_number = read_tags(
            fullpath,
            path_artist if _is_audio else '',
            path_album  if _is_audio else '',
            path_title  if _is_audio else '',
        )
        if _is_audio:
            album_key = (artist or path_artist, album or path_album)
            if album_key not in covered_albums:
                cover_art = _extract_embedded_cover(fullpath, album_key[0], album_key[1])
                covered_albums[album_key] = (cover_art, genre)  # store even if empty so we don't retry
            elif not covered_albums[album_key][1] and genre:
                covered_albums[album_key] = (covered_albums[album_key][0], genre)
        rows.append((artist, album_artist, composer, album, title, genre, fn, relpath, fullpath, mr, mime or '', track_number, mtime))
        total += 1
        if verbose and total % 1000 == 0:
            print(f"Scanned {total} files in {time.time()-started:.1f}s ...", flush=True)

    tmp_conn.executemany(
        "INSERT INTO files (artist, album_artist, composer, album, title, genre, filename, relpath, fullpath, media_root, mime, track_number, mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    tmp_conn.executemany(
        "INSERT OR REPLACE INTO albums (artist, album, cover_art, genre) VALUES (?, ?, ?, ?)",
        [(k[0], k[1], v[0], v[1]) for k, v in covered_albums.items()]
    )
    tmp_conn.execute("INSERT OR REPLACE INTO metadata VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    tmp_conn.execute("INSERT OR REPLACE INTO metadata VALUES ('indexed_at',      ?)", (str(int(time.time())),))
    rebuild_playlists(tmp_conn)
    tmp_conn.commit()
    tmp_conn.close()

    # Atomically replace the live DB — searches using the old file via open
    # inodes continue unaffected; new open_db() calls see the fresh file.
    os.replace(tmp_path, DB_PATH)
    # Remove stale WAL/SHM from the old DB so SQLite doesn't misread them.
    for suffix in ('-wal', '-shm'):
        try:
            os.remove(DB_PATH + suffix)
        except FileNotFoundError:
            pass

    return total, 0, 0


def incremental_update(conn):
    """Update only files that have changed, add new files, remove deleted files.
    The existing rows stay visible to searches throughout — no downtime."""
    started = time.time()

    # Load the current index: fullpath → (id, mtime)
    existing = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT fullpath, id, mtime FROM files").fetchall()
    }

    seen_fullpaths = set()
    to_insert = []
    to_update = []
    album_covers = {}  # album_key → (cover_art, genre) for albums with new/changed tracks
    scanned = 0

    for fullpath, relpath, mr, fn, mtime, path_artist, path_album, path_title in _walk_media_roots():
        seen_fullpaths.add(fullpath)
        scanned += 1
        if verbose and scanned % 1000 == 0:
            print(f"Checked {scanned} files in {time.time()-started:.1f}s ...", flush=True)

        if fullpath in existing:
            existing_id, existing_mtime = existing[fullpath]
            if mtime == existing_mtime:
                continue  # unchanged — skip tag reading entirely
            # File changed — re-read tags and update
            mime, _ = mimetypes.guess_type(fn)
            _is_audio = mime and mime.startswith('audio/')
            artist, album_artist, composer, album, title, genre, track_number = read_tags(
                fullpath,
                path_artist if _is_audio else '',
                path_album  if _is_audio else '',
                path_title  if _is_audio else '',
            )
            if _is_audio:
                cover_art = _extract_embedded_cover(fullpath, artist or path_artist, album or path_album)
                album_key = (artist or path_artist, album or path_album)
                ex_ca, ex_g = album_covers.get(album_key, ('', ''))
                album_covers[album_key] = (cover_art if cover_art else ex_ca, genre if genre else ex_g)
            to_update.append((artist, album_artist, composer, album, title, genre, fn, relpath, mr, mime or '', track_number, mtime, existing_id))
        else:
            # New file
            mime, _ = mimetypes.guess_type(fn)
            _is_audio = mime and mime.startswith('audio/')
            artist, album_artist, composer, album, title, genre, track_number = read_tags(
                fullpath,
                path_artist if _is_audio else '',
                path_album  if _is_audio else '',
                path_title  if _is_audio else '',
            )
            if _is_audio:
                cover_art = _extract_embedded_cover(fullpath, artist or path_artist, album or path_album)
                album_key = (artist or path_artist, album or path_album)
                ex_ca, ex_g = album_covers.get(album_key, ('', ''))
                album_covers[album_key] = (cover_art if cover_art else ex_ca, genre if genre else ex_g)
            to_insert.append((artist, album_artist, composer, album, title, genre, fn, relpath, fullpath, mr, mime or '', track_number, mtime))

    # Files in DB that no longer exist on disk
    deleted_ids = [existing[fp][0] for fp in existing if fp not in seen_fullpaths]

    # Commit in small batches so the write lock window per transaction is short,
    # keeping searches responsive throughout the incremental scan.
    BATCH = 500
    for i in range(0, len(to_insert), BATCH):
        conn.executemany(
            "INSERT INTO files (artist, album_artist, composer, album, title, genre, filename, relpath, fullpath, media_root, mime, track_number, mtime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            to_insert[i:i + BATCH]
        )
        conn.commit()
    for i in range(0, len(to_update), BATCH):
        conn.executemany(
            "UPDATE files SET artist=?, album_artist=?, composer=?, album=?, title=?, genre=?, "
            "filename=?, relpath=?, media_root=?, mime=?, track_number=?, mtime=? WHERE id=?",
            to_update[i:i + BATCH]
        )
        conn.commit()
    if album_covers:
        conn.executemany(
            "INSERT INTO albums (artist, album, cover_art, genre) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(artist, album) DO UPDATE SET"
            " cover_art = CASE WHEN excluded.cover_art != '' THEN excluded.cover_art ELSE cover_art END,"
            " genre = CASE WHEN excluded.genre != '' THEN excluded.genre ELSE genre END",
            [(k[0], k[1], v[0], v[1]) for k, v in album_covers.items()]
        )
        conn.commit()
    for i in range(0, len(deleted_ids), BATCH):
        conn.executemany("DELETE FROM files WHERE id=?", [(x,) for x in deleted_ids[i:i + BATCH]])
        conn.commit()

    pl_added, pl_updated, pl_removed = rebuild_playlists(conn)
    changed_count = len(to_insert) + len(to_update) + len(deleted_ids) + pl_added + pl_updated + pl_removed
    if changed_count > 0:
        conn.execute("INSERT OR REPLACE INTO metadata VALUES ('indexed_at', ?)", (str(int(time.time())),))
        conn.commit()
    return scanned, len(to_insert) + len(to_update), len(deleted_ids)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if not MEDIA_ROOTS:
    print("ERROR: MEDIA_ROOTS (or MEDIA_ROOT) is not set; nothing to index.", flush=True)
    sys.exit(1)

t0 = time.time()
conn = open_db()
try:
    do_full = needs_rebuild(conn)
finally:
    conn.close()

if do_full:
    if verbose:
        print(f"Schema mismatch or first run — full rebuild at {DB_PATH}", flush=True)
    total, added, removed = full_rebuild()
    elapsed = time.time() - t0
    print(f"Full rebuild: {total} files in {elapsed:.1f}s → {DB_PATH}", flush=True)
else:
    conn = open_db()
    try:
        if verbose:
            print(f"Incremental update at {DB_PATH}", flush=True)
        scanned, changed, removed = incremental_update(conn)
    finally:
        conn.close()
    elapsed = time.time() - t0
    print(f"Incremental update: scanned {scanned}, changed {changed}, removed {removed} in {elapsed:.1f}s → {DB_PATH}", flush=True)
