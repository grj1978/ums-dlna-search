/*
 * This file is part of Universal Media Server, based on PS3 Media Server.
 *
 * This program is a free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation; version 2 of the License only.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
 * details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program; if not, write to the Free Software Foundation, Inc., 51
 * Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
 */
package net.pms.network.mediaserver.servlets;

import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import net.pms.configuration.sharedcontent.SharedContentConfiguration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Serves raw media files from configured shared folders at /media/{relpath}.
 *
 * This is used by search.py-generated resource URLs so that renderers (e.g.
 * WiiM) can stream audio files found via DLNA Search/Browse.
 */
public class MediaFileServlet extends HttpServlet {

	private static final Logger LOGGER = LoggerFactory.getLogger(MediaFileServlet.class);
	private static final long serialVersionUID = 1L;
	private static final int BUFFER_SIZE = 64 * 1024;

	@Override
	protected void doHead(HttpServletRequest req, HttpServletResponse resp) throws IOException {
		serveFile(req, resp, false);
	}

	@Override
	protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
		serveFile(req, resp, true);
	}

	private void serveFile(HttpServletRequest req, HttpServletResponse resp, boolean sendBody) throws IOException {
		String pathInfo = req.getPathInfo();
		if (pathInfo == null || pathInfo.isEmpty() || pathInfo.equals("/")) {
			resp.sendError(HttpServletResponse.SC_BAD_REQUEST);
			return;
		}

		// Strip leading slash and decode
		String relPath = URLDecoder.decode(pathInfo.startsWith("/") ? pathInfo.substring(1) : pathInfo, StandardCharsets.UTF_8);

		// Security: reject path traversal
		if (relPath.contains("..")) {
			resp.sendError(HttpServletResponse.SC_FORBIDDEN);
			return;
		}

		File file = resolveFile(relPath);
		if (file == null || !file.isFile()) {
			LOGGER.debug("MediaFileServlet: 404 for relPath={}", relPath);
			resp.sendError(HttpServletResponse.SC_NOT_FOUND);
			return;
		}

		String mimeType = Files.probeContentType(file.toPath());
		if (mimeType == null) {
			mimeType = "application/octet-stream";
		}

		long fileLength = file.length();
		resp.setHeader("Accept-Ranges", "bytes");
		resp.setContentType(mimeType);

		// Parse Range header
		String rangeHeader = req.getHeader("Range");
		long start = 0;
		long end = fileLength - 1;
		boolean ranged = false;

		if (rangeHeader != null && rangeHeader.startsWith("bytes=")) {
			String rangeSpec = rangeHeader.substring(6);
			String[] parts = rangeSpec.split("-", 2);
			try {
				if (!parts[0].isEmpty()) {
					start = Long.parseLong(parts[0].trim());
				}
				if (parts.length > 1 && !parts[1].isEmpty()) {
					end = Long.parseLong(parts[1].trim());
				}
			} catch (NumberFormatException e) {
				resp.sendError(HttpServletResponse.SC_REQUESTED_RANGE_NOT_SATISFIABLE);
				return;
			}
			if (start > end || start >= fileLength || end >= fileLength) {
				resp.setHeader("Content-Range", "bytes */" + fileLength);
				resp.sendError(HttpServletResponse.SC_REQUESTED_RANGE_NOT_SATISFIABLE);
				return;
			}
			ranged = true;
		}

		long contentLength = end - start + 1;
		resp.setHeader("Content-Length", Long.toString(contentLength));

		if (ranged) {
			resp.setStatus(HttpServletResponse.SC_PARTIAL_CONTENT);
			resp.setHeader("Content-Range", "bytes " + start + "-" + end + "/" + fileLength);
		} else {
			resp.setStatus(HttpServletResponse.SC_OK);
		}

		if (!sendBody) {
			return;
		}

		try (RandomAccessFile raf = new RandomAccessFile(file, "r");
			 OutputStream out = resp.getOutputStream()) {
			raf.seek(start);
			byte[] buf = new byte[BUFFER_SIZE];
			long remaining = contentLength;
			while (remaining > 0) {
				int toRead = (int) Math.min(buf.length, remaining);
				int read = raf.read(buf, 0, toRead);
				if (read < 0) {
					break;
				}
				out.write(buf, 0, read);
				remaining -= read;
			}
		} catch (IOException e) {
			// Client disconnected mid-stream — normal, not an error
			LOGGER.trace("MediaFileServlet: client disconnected while streaming {}: {}", relPath, e.getMessage());
		}
	}

	private static File resolveFile(String relPath) {
		try {
			List<File> sharedFolders = SharedContentConfiguration.getSharedFolders();
			for (File root : sharedFolders) {
				File candidate = new File(root, relPath);
				// Canonical path check to prevent symlink traversal
				String rootCanonical = root.getCanonicalPath();
				String candidateCanonical = candidate.getCanonicalPath();
				if (candidateCanonical.startsWith(rootCanonical) && candidate.isFile()) {
					return candidate;
				}
			}
		} catch (Exception e) {
			LOGGER.warn("MediaFileServlet: error resolving file {}: {}", relPath, e.getMessage());
		}
		return null;
	}
}
