import { readdir, readFile, writeFile, unlink, stat, mkdir } from "node:fs/promises";
import { join, relative, resolve, dirname, isAbsolute } from "node:path";
const DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
/**
 * Resolve a requested path to a path inside the workspace.
 * Accepts:
 * - workspace-relative paths (e.g. "projects/foo")
 * - paths prefixed with "workspace/" (e.g. "workspace/projects/foo")
 * - absolute paths inside workspaceDir (e.g. "/root/.openclaw/workspace/projects/foo")
 */
function resolveWorkspacePath(workspaceDir, requestedPath) {
    if (requestedPath.includes("\0")) {
        return null;
    }
    let normalized = requestedPath.replace(/\\/g, "/").trim();
    if (!normalized || normalized === "/" || normalized === ".") {
        normalized = ".";
    }
    if (isAbsolute(normalized)) {
        const abs = resolve(normalized);
        const rel = relative(workspaceDir, abs);
        if (!rel || rel === ".") {
            return workspaceDir;
        }
        if (rel.startsWith("..") || isAbsolute(rel)) {
            return null;
        }
        return abs;
    }
    normalized = normalized.replace(/^\/+/, "").replace(/\/+$/, "");
    if (normalized === "workspace" || normalized.startsWith("workspace/")) {
        normalized = normalized.slice("workspace".length).replace(/^\/+/, "");
    }
    if (!normalized || normalized === ".") {
        normalized = ".";
    }
    const resolved = resolve(workspaceDir, normalized);
    const rel = relative(workspaceDir, resolved);
    if (rel.startsWith("..") || isAbsolute(rel)) {
        return null;
    }
    return resolved;
}
/**
 * Validates that a path is within the workspace (prevents directory traversal)
 */
function isPathSafe(workspaceDir, requestedPath) {
    return resolveWorkspacePath(workspaceDir, requestedPath) !== null;
}
/**
 * Parse request body as JSON
 */
async function parseJsonBody(req) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        req.on("data", (chunk) => chunks.push(chunk));
        req.on("end", () => {
            try {
                const body = Buffer.concat(chunks).toString("utf-8");
                resolve(body ? JSON.parse(body) : {});
            }
            catch (e) {
                reject(new Error("Invalid JSON body"));
            }
        });
        req.on("error", reject);
    });
}
/**
 * Send JSON response
 */
function sendJson(res, status, data) {
    const body = JSON.stringify(data);
    res.writeHead(status, {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
    });
    res.end(body);
}
/**
 * Send error response
 */
function sendError(res, status, message) {
    sendJson(res, status, { error: message });
}
/**
 * Normalize a path for API operations to workspace-relative form.
 */
function normalizePath(workspaceDir, dirPath) {
    const resolved = resolveWorkspacePath(workspaceDir, dirPath);
    if (!resolved) {
        return dirPath;
    }
    const rel = relative(workspaceDir, resolved);
    return rel && rel !== "" ? rel : ".";
}
/**
 * List directory contents
 */
async function listDirectory(workspaceDir, dirPath, recursive = false) {
    const normalizedPath = normalizePath(workspaceDir, dirPath);
    const fullPath = resolve(workspaceDir, normalizedPath);
    const entries = await readdir(fullPath, { withFileTypes: true });
    const results = [];
    for (const entry of entries) {
        // Skip hidden files and node_modules
        if (entry.name.startsWith(".") || entry.name === "node_modules") {
            continue;
        }
        const entryPath = join(dirPath, entry.name);
        const fullEntryPath = join(fullPath, entry.name);
        if (entry.isDirectory()) {
            results.push({
                name: entry.name,
                path: entryPath,
                type: "directory",
            });
            if (recursive) {
                const subEntries = await listDirectory(workspaceDir, entryPath, true);
                results.push(...subEntries);
            }
        }
        else if (entry.isFile()) {
            try {
                const stats = await stat(fullEntryPath);
                results.push({
                    name: entry.name,
                    path: entryPath,
                    type: "file",
                    size: stats.size,
                    modified: stats.mtime.toISOString(),
                });
            }
            catch {
                // Skip files we can't stat
            }
        }
    }
    return results;
}
/**
 * Create file API handler
 */
export function createFileApiHandler(config) {
    const { workspaceDir, maxFileSize } = config;
    return async function handleFileApi(req, res, pathname) {
        const url = new URL(req.url || "/", `http://${req.headers.host}`);
        const method = req.method || "GET";
        // CORS headers for potential cross-origin requests
        res.setHeader("Access-Control-Allow-Origin", "*");
        res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
        res.setHeader("Access-Control-Allow-Headers", "Content-Type");
        if (method === "OPTIONS") {
            res.writeHead(204);
            res.end();
            return true;
        }
        try {
            // GET /api/files - List directory
            if (pathname === "/better-gateway/api/files" && method === "GET") {
                const dirPath = normalizePath(workspaceDir, url.searchParams.get("path") || "/");
                const recursive = url.searchParams.get("recursive") === "true";
                if (!isPathSafe(workspaceDir, dirPath)) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                const files = await listDirectory(workspaceDir, dirPath, recursive);
                sendJson(res, 200, { files, workspaceDir });
                return true;
            }
            // GET /api/files/read - Read file content
            if (pathname === "/better-gateway/api/files/read" && method === "GET") {
                const filePath = url.searchParams.get("path");
                if (!filePath) {
                    sendError(res, 400, "Missing path parameter");
                    return true;
                }
                if (!isPathSafe(workspaceDir, filePath)) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                const fullPath = resolveWorkspacePath(workspaceDir, filePath);
                if (!fullPath) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                const stats = await stat(fullPath);
                if (stats.size > maxFileSize) {
                    sendError(res, 413, `File too large (max ${maxFileSize / 1024 / 1024}MB)`);
                    return true;
                }
                const content = await readFile(fullPath, "utf-8");
                sendJson(res, 200, {
                    path: normalizePath(workspaceDir, filePath),
                    content,
                    size: stats.size,
                    modified: stats.mtime.toISOString(),
                });
                return true;
            }
            // POST /api/files/write - Write file content
            if (pathname === "/better-gateway/api/files/write" && method === "POST") {
                const body = await parseJsonBody(req);
                const filePath = body.path;
                const content = body.content;
                if (!filePath || content === undefined) {
                    sendError(res, 400, "Missing path or content");
                    return true;
                }
                if (!isPathSafe(workspaceDir, filePath)) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                const fullPath = resolveWorkspacePath(workspaceDir, filePath);
                if (!fullPath) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                // Ensure directory exists
                await mkdir(dirname(fullPath), { recursive: true });
                await writeFile(fullPath, content, "utf-8");
                sendJson(res, 200, { ok: true, path: normalizePath(workspaceDir, filePath) });
                return true;
            }
            // DELETE /api/files - Delete file
            if (pathname === "/better-gateway/api/files" && method === "DELETE") {
                const filePath = url.searchParams.get("path");
                if (!filePath) {
                    sendError(res, 400, "Missing path parameter");
                    return true;
                }
                if (!isPathSafe(workspaceDir, filePath)) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                const fullPath = resolveWorkspacePath(workspaceDir, filePath);
                if (!fullPath) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                await unlink(fullPath);
                sendJson(res, 200, { ok: true, path: normalizePath(workspaceDir, filePath) });
                return true;
            }
            // POST /api/files/mkdir - Create directory
            if (pathname === "/better-gateway/api/files/mkdir" && method === "POST") {
                const body = await parseJsonBody(req);
                const dirPath = body.path;
                if (!dirPath) {
                    sendError(res, 400, "Missing path");
                    return true;
                }
                if (!isPathSafe(workspaceDir, dirPath)) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                const fullPath = resolveWorkspacePath(workspaceDir, dirPath);
                if (!fullPath) {
                    sendError(res, 403, "Access denied: path outside workspace");
                    return true;
                }
                await mkdir(fullPath, { recursive: true });
                sendJson(res, 200, { ok: true, path: normalizePath(workspaceDir, dirPath) });
                return true;
            }
            return false;
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "Unknown error";
            if (message.includes("ENOENT")) {
                sendError(res, 404, "File or directory not found");
            }
            else if (message.includes("EACCES")) {
                sendError(res, 403, "Permission denied");
            }
            else if (message.includes("EISDIR")) {
                sendError(res, 400, "Path is a directory, not a file");
            }
            else {
                sendError(res, 500, message);
            }
            return true;
        }
    };
}
export { DEFAULT_MAX_FILE_SIZE };
//# sourceMappingURL=file-api.js.map