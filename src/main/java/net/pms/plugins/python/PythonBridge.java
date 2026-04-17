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
package net.pms.plugins.python;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import net.pms.PMS;
import net.pms.configuration.UmsConfiguration;
import net.pms.configuration.sharedcontent.SharedContentConfiguration;
import net.pms.network.mediaserver.MediaServer;
import net.pms.network.mediaserver.jupnp.transport.impl.jetty.ee10.JettyServletContainer;
import net.pms.network.mediaserver.servlets.CoverCacheServlet;
import net.pms.network.mediaserver.servlets.MediaFileServlet;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Bridge to call external Python scripts and manage the media index.
 *
 * On first use this class:
 *  1. Registers the /media/* file-serving servlet.
 *  2. Launches index_media.py as a background process to build the SQLite index.
 *  3. Schedules periodic index rebuilds (configurable via python_index_refresh_minutes).
 */
public class PythonBridge {

    private static final Logger LOGGER = LoggerFactory.getLogger(PythonBridge.class);

    private static final AtomicBoolean SERVLET_REGISTERED = new AtomicBoolean(false);
    private static final AtomicBoolean COVER_SERVLET_REGISTERED = new AtomicBoolean(false);
    private static final AtomicBoolean INDEXER_STARTED    = new AtomicBoolean(false);

    /** Holds the pending debounced reindex task so it can be cancelled on re-trigger. */
    private static final AtomicReference<ScheduledFuture<?>> PENDING_DEBOUNCED = new AtomicReference<>();

    private static final ScheduledExecutorService SCHEDULER =
        Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "python-indexer");
            t.setDaemon(true);
            return t;
        });

    // ---------------------------------------------------------------------------
    // Media servlet registration
    // ---------------------------------------------------------------------------

    /**
     * Ensure the /media/* file-serving servlet is registered. Safe to call repeatedly.
     */
    public static void ensureMediaServletRegistered() {
        if (SERVLET_REGISTERED.compareAndSet(false, true)) {
            JettyServletContainer.INSTANCE.addPluginServlet(
                "Python Media File Server",
                new MediaFileServlet(),
                "/media/*"
            );
        }
        if (COVER_SERVLET_REGISTERED.compareAndSet(false, true)) {
            JettyServletContainer.INSTANCE.addPluginServlet(
                "Python Cover Cache Server",
                new CoverCacheServlet(),
                "/cover/*"
            );
        }
    }

    // ---------------------------------------------------------------------------
    // Indexer management
    // ---------------------------------------------------------------------------

    /**
     * Start the background media indexer if it hasn't been started yet.
     * Also schedules periodic rebuilds based on python_index_refresh_minutes config.
     */
    public static void startIndexerOnce() {
        if (!INDEXER_STARTED.compareAndSet(false, true)) {
            return;
        }
        // Run immediately in background
        SCHEDULER.execute(() -> runIndexer());

        // Schedule periodic refresh
        try {
            UmsConfiguration config = PMS.getConfiguration();
            int refreshMinutes = config.getPythonIndexRefreshMinutes();
            if (refreshMinutes > 0) {
                SCHEDULER.scheduleAtFixedRate(
                    () -> runIndexer(),
                    refreshMinutes, refreshMinutes, TimeUnit.MINUTES
                );
            }
        } catch (Exception e) {
            LOGGER.debug("Could not read python_index_refresh_minutes, periodic refresh not scheduled: {}", e.getMessage());
        }
    }

    /**
     * Trigger a one-shot index rebuild immediately (e.g. after a full media rescan).
     */
    public static void triggerReindex() {
        SCHEDULER.execute(() -> runIndexer());
    }

    /**
     * Schedule an index rebuild after a 30-second quiet period.
     * Multiple rapid calls (e.g. file-watcher events while copying an album)
     * collapse into a single rebuild run 30 seconds after the last event.
     * Safe to call from any thread.
     */
    public static void scheduleDebouncedReindex() {
        ScheduledFuture<?> existing = PENDING_DEBOUNCED.getAndSet(null);
        if (existing != null) {
            existing.cancel(false);
        }
        ScheduledFuture<?> pending = SCHEDULER.schedule(() -> runIndexer(), 30, TimeUnit.SECONDS);
        PENDING_DEBOUNCED.set(pending);
    }

    private static void runIndexer() {
        File workDir    = new File(System.getProperty("user.dir"));
        File scriptFile = new File(workDir, "index_media.py");
        if (!scriptFile.exists()) {
            LOGGER.debug("index_media.py not found at {}; skipping indexing", scriptFile.getAbsolutePath());
            return;
        }
        try {
            ProcessBuilder pb = new ProcessBuilder("python3", scriptFile.getAbsolutePath());
            pb.directory(workDir);
            buildEnv(pb.environment());
            pb.redirectErrorStream(true);

            LOGGER.info("Starting Python media indexer...");
            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    LOGGER.debug("indexer: {}", line);
                }
            }
            int exitCode = process.waitFor();
            if (exitCode == 0) {
                LOGGER.info("Python media indexer completed successfully.");
            } else {
                LOGGER.warn("Python media indexer exited with code {}", exitCode);
            }
        } catch (Exception e) {
            LOGGER.warn("Python media indexer failed: {}", e.getMessage());
        }
    }

    // ---------------------------------------------------------------------------
    // Environment helpers
    // ---------------------------------------------------------------------------

    private static void buildEnv(Map<String, String> env) {
        try {
            env.put("UMS_MEDIA_HOST", MediaServer.getHost());
            env.put("UMS_MEDIA_PORT", Integer.toString(MediaServer.getPort()));
        } catch (Exception e) {
            // MediaServer not yet available
        }
        try {
            List<File> sharedFolders = SharedContentConfiguration.getSharedFolders();
            if (!sharedFolders.isEmpty()) {
                env.put("MEDIA_ROOTS", sharedFolders.stream()
                    .map(File::getAbsolutePath)
                    .collect(Collectors.joining(":")));
            }
        } catch (Exception e) {
            // Shared folder config not yet available
        }
        try {
            UmsConfiguration config = PMS.getConfiguration();
            List<String> ignored = config.getIgnoredFolderNames();
            if (!ignored.isEmpty()) {
                env.put("FOLDER_NAMES_IGNORED", String.join(",", ignored));
            }
            // Point Python at the same profile directory so the DB lands in the database subdir
            String profileDir = UmsConfiguration.getProfileDirectory();
            env.put("MEDIA_INDEX_DB",
                profileDir + File.separator + "database" + File.separator + "media_index.db");
            env.put("COVER_CACHE_DIR",
                profileDir + File.separator + "cache" + File.separator + "covers");
        } catch (Exception e) {
            // Config not yet available
        }
    }

    // ---------------------------------------------------------------------------
    // Script runner
    // ---------------------------------------------------------------------------

    public static String run(String script, String... args) throws Exception {
        ensureMediaServletRegistered();
        startIndexerOnce();

        File workDir    = new File(System.getProperty("user.dir"));
        File scriptFile = new File(workDir, script);

        List<String> cmd = new ArrayList<>();
        cmd.add("python3");
        cmd.add(scriptFile.getAbsolutePath());
        Collections.addAll(cmd, args);

        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.directory(workDir);
        buildEnv(pb.environment());
        pb.redirectErrorStream(true);

        Process process = pb.start();

        StringBuilder output = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                output.append(line).append("\n");
            }
        }
        process.waitFor();
        return output.toString().trim();
    }
}
