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
import java.io.OutputStream;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import net.pms.configuration.UmsConfiguration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Serves extracted album art from the cover cache directory at /cover/{filename}.
 *
 * The cover cache directory is set via the COVER_CACHE_DIR environment variable,
 * injected by PythonBridge. Files are written there by index_media.py when it
 * extracts embedded art from audio tracks.
 */
public class CoverCacheServlet extends HttpServlet {

	private static final Logger LOGGER = LoggerFactory.getLogger(CoverCacheServlet.class);
	private static final long serialVersionUID = 1L;
	private static final int BUFFER_SIZE = 32 * 1024;

	@Override
	protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
		String coverCacheDir = UmsConfiguration.getProfileDirectory()
			+ File.separator + "cache" + File.separator + "covers";


		String pathInfo = req.getPathInfo();
		if (pathInfo == null || pathInfo.isEmpty() || pathInfo.equals("/")) {
			resp.sendError(HttpServletResponse.SC_BAD_REQUEST);
			return;
		}

		String filename = URLDecoder.decode(
			pathInfo.startsWith("/") ? pathInfo.substring(1) : pathInfo,
			StandardCharsets.UTF_8
		);

		// Security: reject path traversal and directory separators
		if (filename.contains("..") || filename.contains("/") || filename.contains("\\")) {
			resp.sendError(HttpServletResponse.SC_FORBIDDEN);
			return;
		}

		File cacheDir = new File(coverCacheDir);
		File coverFile = new File(cacheDir, filename);

		// Canonical path check to prevent symlink traversal
		try {
			String cacheDirCanonical = cacheDir.getCanonicalPath();
			String coverFileCanonical = coverFile.getCanonicalPath();
			if (!coverFileCanonical.startsWith(cacheDirCanonical)) {
				resp.sendError(HttpServletResponse.SC_FORBIDDEN);
				return;
			}
		} catch (IOException e) {
			resp.sendError(HttpServletResponse.SC_FORBIDDEN);
			return;
		}

		if (!coverFile.isFile()) {
			LOGGER.debug("CoverCacheServlet: 404 for {}", filename);
			resp.sendError(HttpServletResponse.SC_NOT_FOUND);
			return;
		}

		String mimeType = Files.probeContentType(coverFile.toPath());
		if (mimeType == null) {
			mimeType = "image/jpeg";
		}
		resp.setContentType(mimeType);
		resp.setContentLengthLong(coverFile.length());
		resp.setHeader("Cache-Control", "public, max-age=86400");

		try (OutputStream out = resp.getOutputStream()) {
			byte[] buf = new byte[BUFFER_SIZE];
			try (var in = Files.newInputStream(coverFile.toPath())) {
				int n;
				while ((n = in.read(buf)) != -1) {
					out.write(buf, 0, n);
				}
			}
		} catch (IOException e) {
			LOGGER.trace("CoverCacheServlet: client disconnected while streaming {}: {}", filename, e.getMessage());
		}
	}
}
