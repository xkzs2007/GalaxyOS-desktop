export function start(workspaceDir: string): Promise<void>;
export function stop(): Promise<void>;
export function execute(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<any>;
