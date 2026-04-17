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
package net.pms.network.mediaserver.handlers;

import net.pms.network.mediaserver.HTTPXMLHelper;
import net.pms.network.mediaserver.handlers.message.SearchRequest;
import net.pms.renderers.Renderer;
import net.pms.store.MediaStoreIds;
import net.pms.plugins.python.PythonBridge;
import net.pms.plugins.python.PythonResultParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.jupnp.support.model.SearchResult;

public class SearchRequestHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(SearchRequestHandler.class);
    private static final String CRLF = "\r\n";

    /**
     * Entry point for DLNA Search() requests.
     *
     * This implementation:
     *  - forwards the search to an external Python script
     *  - expects JSON back from Python
     *  - converts that JSON into DIDL-Lite inside PythonResultParser
     *  - wraps the DIDL in the standard SOAP SearchResponse envelope
     */
    public StringBuilder createSearchResponse(SearchRequest requestMessage, Renderer renderer) {
        try {
            String rendererName = renderer != null ? renderer.getRendererName() : "";

            // Call external Python search handler.
            // Arguments are intentionally simple and stable:
            //   0: searchCriteria (DLNA SearchCriteria string)
            //   1: filter
            //   2: startingIndex
            //   3: requestedCount
            //   4: rendererName (optional context)
            String json = PythonBridge.run(
                "search.py",
                requestMessage.getSearchCriteria(),
                requestMessage.getFilter(),
                Long.toString(requestMessage.getStartingIndex()),
                Long.toString(requestMessage.getRequestedCount()),
                rendererName,
                requestMessage.getContainerId() == null ? "0" : requestMessage.getContainerId()
            );

            // Let PythonResultParser interpret the JSON.
            String didl = PythonResultParser.getDidl(json);
            int numberReturned = PythonResultParser.getReturned(json);
            int totalMatches = PythonResultParser.getTotal(json);
            long updateID = MediaStoreIds.getSystemUpdateId().getValue();

            // Build full SOAP SearchResponse envelope.
            StringBuilder response = new StringBuilder();
            response.append(HTTPXMLHelper.XML_HEADER).append(CRLF);
            response.append(HTTPXMLHelper.SOAP_ENCODING_HEADER).append(CRLF);
            response.append(HTTPXMLHelper.SEARCHRESPONSE_HEADER).append(CRLF);

            response.append(HTTPXMLHelper.RESULT_HEADER);
            response.append(HTTPXMLHelper.DIDL_HEADER);
            response.append(didl);
            response.append(HTTPXMLHelper.DIDL_FOOTER);
            response.append(HTTPXMLHelper.RESULT_FOOTER);
            response.append(CRLF);

            response.append("<NumberReturned>").append(numberReturned).append("</NumberReturned>").append(CRLF);
            response.append("<TotalMatches>").append(totalMatches).append("</TotalMatches>").append(CRLF);
            response.append("<UpdateID>").append(updateID).append("</UpdateID>").append(CRLF);
            response.append(HTTPXMLHelper.SEARCHRESPONSE_FOOTER).append(CRLF);
            response.append(HTTPXMLHelper.SOAP_ENCODING_FOOTER).append(CRLF);

            return response;

        } catch (Exception e) {
            LOGGER.error("Python search plugin failed", e);
            return createEmptyErrorResponse();
        }
    }

    /**
     * Helper that performs the same work as createSearchResponse but returns
     * a jUPnP {@link SearchResult} so it can be used by the UPnP action
     * implementation.
     */
    public SearchResult createSearchResult(SearchRequest requestMessage, Renderer renderer) {
        try {
            String rendererName = renderer != null ? renderer.getRendererName() : "";
            LOGGER.debug("Search criteria: [{}] containerId: [{}] renderer: [{}]",
                requestMessage.getSearchCriteria(), requestMessage.getContainerId(), rendererName);

            String json = PythonBridge.run(
                "search.py",
                requestMessage.getSearchCriteria(),
                requestMessage.getFilter(),
                Long.toString(requestMessage.getStartingIndex()),
                Long.toString(requestMessage.getRequestedCount()),
                rendererName,
                requestMessage.getContainerId() == null ? "0" : requestMessage.getContainerId()
            );

            String didl = PythonResultParser.getDidl(json);
            int numberReturned = PythonResultParser.getReturned(json);
            int totalMatches = PythonResultParser.getTotal(json);
            long updateID = MediaStoreIds.getSystemUpdateId().getValue();

            return new SearchResult(didl, numberReturned, totalMatches, updateID);
        } catch (Exception e) {
            LOGGER.error("Python search plugin failed", e);
            return new SearchResult("", 0, 0, MediaStoreIds.getSystemUpdateId().getValue());
        }
    }

    /**
     * Fallback response when Python fails: empty result set.
     */
    private StringBuilder createEmptyErrorResponse() {
        StringBuilder response = new StringBuilder();
        response.append(HTTPXMLHelper.XML_HEADER).append(CRLF);
        response.append(HTTPXMLHelper.SOAP_ENCODING_HEADER).append(CRLF);
        response.append(HTTPXMLHelper.SEARCHRESPONSE_HEADER).append(CRLF);

        response.append(HTTPXMLHelper.RESULT_HEADER);
        response.append(HTTPXMLHelper.DIDL_HEADER);
        // no items
        response.append(HTTPXMLHelper.DIDL_FOOTER);
        response.append(HTTPXMLHelper.RESULT_FOOTER);
        response.append(CRLF);

        response.append("<NumberReturned>0</NumberReturned>").append(CRLF);
        response.append("<TotalMatches>0</TotalMatches>").append(CRLF);
        response.append("<UpdateID>").append(MediaStoreIds.getSystemUpdateId().getValue()).append("</UpdateID>").append(CRLF);
        response.append(HTTPXMLHelper.SEARCHRESPONSE_FOOTER).append(CRLF);
        response.append(HTTPXMLHelper.SOAP_ENCODING_FOOTER).append(CRLF);

        return response;
    }
}
