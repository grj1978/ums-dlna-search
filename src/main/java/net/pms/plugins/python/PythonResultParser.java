package net.pms.plugins.python;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Parses JSON returned by the Python search script.
 *
 * Expected JSON format:
 * {
 *   "totalMatches": 123,
 *   "returned": 10,
 *   "didl": "<item>...</item>"
 * }
 */
public class PythonResultParser {

    private static final ObjectMapper mapper = new ObjectMapper();

    private static JsonNode parse(String json) throws Exception {
        return mapper.readTree(json);
    }

    /** Extracts the DIDL-Lite XML payload returned by Python. */
    public static String getDidl(String json) throws Exception {
        JsonNode root = parse(json);
        JsonNode didl = root.get("didl");
        return didl != null ? didl.asText() : "";
    }

    /** Number of items returned in this page of results. */
    public static int getReturned(String json) throws Exception {
        JsonNode root = parse(json);
        JsonNode returned = root.get("returned");
        return returned != null ? returned.asInt() : 0;
    }

    /** Total number of matches across the entire search. */
    public static int getTotal(String json) throws Exception {
        JsonNode root = parse(json);
        JsonNode total = root.get("totalMatches");
        return total != null ? total.asInt() : 0;
    }
}
