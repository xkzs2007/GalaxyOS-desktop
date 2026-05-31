import { IncomingMessage, ServerResponse } from "node:http";
interface PluginConfig {
    reconnectIntervalMs: number;
    maxReconnectAttempts: number;
    maxFileSize: number;
}
interface PluginApi {
    registerHttpRoute: (params: {
        path: string;
        match?: "exact" | "prefix";
        auth: "gateway" | "plugin";
        handler: (req: IncomingMessage, res: ServerResponse) => Promise<boolean | void> | boolean | void;
        replaceExisting?: boolean;
    }) => void;
    logger: {
        info: (msg: string) => void;
        warn: (msg: string) => void;
        error: (msg: string) => void;
        debug: (msg: string) => void;
    };
    pluginConfig?: Record<string, unknown>;
    resolvePath: (input: string) => string;
}
declare const _default: {
    id: string;
    name: string;
    configSchema: {
        parse(raw: unknown): PluginConfig;
        uiHints: {
            reconnectIntervalMs: {
                label: string;
                placeholder: string;
            };
            maxReconnectAttempts: {
                label: string;
                placeholder: string;
            };
            maxFileSize: {
                label: string;
                placeholder: string;
                advanced: boolean;
            };
        };
    };
    register(api: PluginApi): void;
};
export default _default;
//# sourceMappingURL=index.d.ts.map