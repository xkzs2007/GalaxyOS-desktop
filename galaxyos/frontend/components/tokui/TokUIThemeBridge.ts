interface MUITheme {
  palette?: {
    mode?: 'light' | 'dark';
    primary?: { main?: string };
    secondary?: { main?: string };
    error?: { main?: string };
    warning?: { main?: string };
    info?: { main?: string };
    success?: { main?: string };
    background?: { default?: string; paper?: string };
    text?: { primary?: string; secondary?: string };
    divider?: string;
  };
  shape?: { borderRadius?: number };
  typography?: { fontFamily?: string; fontSize?: number };
}

const MUI_TOKUI_MAPPING: Record<string, string> = {
  'palette.primary.main': '--tokui-color-primary',
  'palette.secondary.main': '--tokui-color-secondary',
  'palette.error.main': '--tokui-color-error',
  'palette.warning.main': '--tokui-color-warning',
  'palette.info.main': '--tokui-color-info',
  'palette.success.main': '--tokui-color-success',
  'palette.background.default': '--tokui-color-bg',
  'palette.background.paper': '--tokui-color-surface',
  'palette.text.primary': '--tokui-color-text',
  'palette.text.secondary': '--tokui-color-text-secondary',
  'palette.divider': '--tokui-color-border',
  'shape.borderRadius': '--tokui-radius',
  'typography.fontFamily': '--tokui-font-family',
  'typography.fontSize': '--tokui-font-size',
};

function getNestedValue(obj: Record<string, unknown>, path: string): string | undefined {
  const keys = path.split('.');
  let current: unknown = obj;
  for (const key of keys) {
    if (current && typeof current === 'object' && key in current) {
      current = (current as Record<string, unknown>)[key];
    } else {
      return undefined;
    }
  }
  return typeof current === 'string' || typeof current === 'number' ? String(current) : undefined;
}

function hexToHSL(hex: string): { h: number; s: number; l: number } | null {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return null;

  let r = parseInt(result[1], 16) / 255;
  let g = parseInt(result[2], 16) / 255;
  let b = parseInt(result[3], 16) / 255;

  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;

  if (max === min) return { h: 0, s: 0, l: Math.round(l * 100) };

  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);

  let h = 0;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;

  return { h: Math.round(h * 360), s: Math.round(s * 100), l: Math.round(l * 100) };
}

export function generateHSBScale(baseColor: string, steps: number = 10): Record<string, string> {
  const hsl = hexToHSL(baseColor);
  if (!hsl) return {};

  const tokens: Record<string, string> = {};
  for (let i = 1; i <= steps; i++) {
    const lightness = 97 - (i - 1) * (72 / (steps - 1));
    tokens[`--tokui-color-scale-${i}`] = `hsl(${hsl.h}, ${hsl.s}%, ${Math.round(lightness)}%)`;
  }
  return tokens;
}

export class TokUIThemeBridge {
  private _observer: MutationObserver | null = null;
  private _syncInterval: ReturnType<typeof setInterval> | null = null;
  private _lastTheme: string = '';

  syncFromMUI(muiTheme: MUITheme, rootElement?: HTMLElement): void {
    const root = rootElement || document.documentElement;
    try {
      for (const [muiPath, tokuiVar] of Object.entries(MUI_TOKUI_MAPPING)) {
        const value = getNestedValue(muiTheme as unknown as Record<string, unknown>, muiPath);
        if (value) {
          root.style.setProperty(tokuiVar, value);
        }
      }

      if (muiTheme.palette?.primary?.main) {
        const scale = generateHSBScale(muiTheme.palette.primary.main);
        for (const [varName, value] of Object.entries(scale)) {
          root.style.setProperty(varName, value);
        }
      }

      this._lastTheme = JSON.stringify(muiTheme);
    } catch (e) {
      console.warn('[GalaxyOS] TokUI theme sync failed:', e);
    }
  }

  enableDarkModeSync(rootElement?: HTMLElement): void {
    const root = rootElement || document.documentElement;

    const updateTheme = () => {
      const isDark = root.classList.contains('Mui-theme-dark') ||
        root.getAttribute('data-mui-color-scheme') === 'dark' ||
        window.matchMedia('(prefers-color-scheme: dark)').matches;

      root.setAttribute('data-tokui-theme', isDark ? 'dark' : 'default');
    };

    updateTheme();

    this._observer = new MutationObserver(() => updateTheme());
    this._observer.observe(root, {
      attributes: true,
      attributeFilter: ['class', 'data-mui-color-scheme'],
    });

    this._syncInterval = setInterval(updateTheme, 5000);
  }

  disableDarkModeSync(): void {
    if (this._observer) {
      this._observer.disconnect();
      this._observer = null;
    }
    if (this._syncInterval) {
      clearInterval(this._syncInterval);
      this._syncInterval = null;
    }
  }
}

export default TokUIThemeBridge;