/**
 * Terminal Page Generator
 * Creates a full-featured browser terminal using xterm.js (CDN)
 * connected via SSE (server→browser) + POST (browser→server).
 */
export interface TerminalPageConfig {
    xtermVersion: string;
    fitAddonVersion: string;
    webLinksAddonVersion: string;
}
export declare function generateTerminalPage(config?: Partial<TerminalPageConfig>): string;
//# sourceMappingURL=terminal-page.d.ts.map