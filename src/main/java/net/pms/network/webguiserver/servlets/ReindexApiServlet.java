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
package net.pms.network.webguiserver.servlets;

import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import net.pms.network.webguiserver.GuiHttpServlet;
import net.pms.store.MediaScanner;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * POST /v1/api/reindex
 *
 * Triggers a full UMS media rescan, which on completion automatically invokes
 * PythonBridge.triggerReindex() to rebuild the media index.
 *
 * No authentication required. Intended for use by trusted local services
 * (e.g. a playlist exporter container on the same host/LAN).
 */
@WebServlet(name = "ReindexApiServlet", urlPatterns = {"/v1/api/reindex"}, displayName = "Reindex Api Servlet")
public class ReindexApiServlet extends GuiHttpServlet {

	private static final Logger LOGGER = LoggerFactory.getLogger(ReindexApiServlet.class);

	@Override
	protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
		String remoteAddr = req.getRemoteAddr();
		if (MediaScanner.isMediaScanRunning()) {
			LOGGER.debug("Reindex requested but media scan already running — ignoring");
			respond(req, resp, "{\"status\":\"already_running\"}", 200, "application/json");
			return;
		}
		LOGGER.info("Reindex triggered via HTTP from {}", remoteAddr);
		MediaScanner.startMediaScan();
		respond(req, resp, "{\"status\":\"started\"}", 200, "application/json");
	}
}
