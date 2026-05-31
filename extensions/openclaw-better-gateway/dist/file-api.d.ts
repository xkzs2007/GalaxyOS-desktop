import { IncomingMessage, ServerResponse } from "node:http";
interface FileEntry {
    name: string;
    path: string;
    type: "file" | "directory";
    size?: number;
    modified?: string;
}
interface FileApiConfig {
    workspaceDir: string;
    maxFileSize: number;
}
declare const DEFAULT_MAX_FILE_SIZE: number;
/**
 * Create file API handler
 */
export declare function createFileApiHandler(config: FileApiConfig): (req: IncomingMessage, res: ServerResponse, pathname: string) => Promise<boolean>;
export { DEFAULT_MAX_FILE_SIZE };
export type { FileApiConfig, FileEntry };
//# sourceMappingURL=file-api.d.ts.map