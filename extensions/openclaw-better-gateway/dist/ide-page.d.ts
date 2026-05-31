/**
 * IDE Page Generator
 * Creates a full-featured code editor interface using Monaco Editor (CDN)
 */
export interface IdePageConfig {
    monacoVersion: string;
    theme: "vs-dark" | "vs" | "hc-black";
}
/**
 * Language detection from file extension
 */
declare const EXTENSION_TO_LANGUAGE: Record<string, string>;
/**
 * Generate the IDE page HTML
 */
export declare function generateIdePage(config?: Partial<IdePageConfig>): string;
export { EXTENSION_TO_LANGUAGE };
//# sourceMappingURL=ide-page.d.ts.map