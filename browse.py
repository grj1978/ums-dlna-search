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
Index-backed Browse() handler for UMS.

Handles UPnP ContentDirectory Browse() requests intercepted by PythonBridge,
returning virtual container trees (artists → albums → tracks) backed by the
SQLite index built by index_media.py.

Called by BrowseRequestHandler with:
  argv[1]: objectID   — the container/item ID being browsed
  argv[2]: browseFlag — "BrowseDirectChildren" or "BrowseMetadata"
  argv[3]: filter     — requested metadata fields (may be "*")
  argv[4]: start      — startingIndex (integer)
  argv[5]: count      — requestedCount (integer; 0 = all)
  argv[6]: sortCriteria — sort string (may be empty)
  argv[7]: rendererName — renderer display name (optional context)

Config (environment variables):
- MEDIA_ROOTS / MEDIA_ROOT / MEDIA_INDEX_DB: same as search.py / index_media.py
- UMS_MEDIA_HOST / UMS_MEDIA_PORT: used to build resource URLs
- SEARCH_ACCENT_ALIAS: see dlna_tools.py
"""

import sys
import os
import mimetypes
import random
from dlna_tools import (
    fold_accents, _accent_alias_containers, _accent_alias_items,
    _album_sort_key, file_class_for_mime, make_url, find_cover_url,
    query_files_by_artist, query_files_by_album, query_files_by_subpath,
    query_playlist_by_path, open_db,
    build_didl_tracks, build_didl_containers, emit, emit_index_not_ready,
    _BROWSE_ARTIST, _BROWSE_ALBUM, _BROWSE_ALL_TRACKS, _BROWSE_PLAYLIST,
    _DIDL_OPEN, _DIDL_CLOSE,
)

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
object_id     = sys.argv[1] if len(sys.argv) > 1 else "0"
browse_flag   = sys.argv[2] if len(sys.argv) > 2 else "BrowseDirectChildren"
filter_str    = sys.argv[3] if len(sys.argv) > 3 else "*"
start         = int(sys.argv[4]) if len(sys.argv) > 4 else 0
count         = int(sys.argv[5]) if len(sys.argv) > 5 else 50
sort_criteria = sys.argv[6] if len(sys.argv) > 6 else ""
renderer      = sys.argv[7] if len(sys.argv) > 7 else ""

# ---------------------------------------------------------------------------
# TODO: implement Browse() virtual container tree
# ---------------------------------------------------------------------------
# Planned container hierarchy:
#   "0"              → root  (not yet implemented — UMS handles root browsing)
#   "artists"        → list all artists (object.container.person.musicArtist)
#   "artist:<name>"  → albums for artist + "All Tracks" synthetic container
#   "album:<a>/<al>" → tracks in album
#   "allartisttracks:<name>" → all tracks by artist, sorted album/track
#   "playlist:<path>"        → tracks from .m3u playlist
# ---------------------------------------------------------------------------

emit_index_not_ready()
