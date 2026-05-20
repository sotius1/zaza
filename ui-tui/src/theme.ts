export interface ThemeColors {
  primary: string
  accent: string
  border: string
  text: string
  muted: string
  completionBg: string
  completionCurrentBg: string

  label: string
  ok: string
  error: string
  warn: string

  prompt: string
  sessionLabel: string
  sessionBorder: string

  statusBg: string
  statusFg: string
  statusGood: string
  statusWarn: string
  statusBad: string
  statusCritical: string
  selectionBg: string

  diffAdded: string
  diffRemoved: string
  diffAddedWord: string
  diffRemovedWord: string

  shellDollar: string
}

export interface ThemeBrand {
  name: string
  icon: string
  prompt: string
  welcome: string
  goodbye: string
  tool: string
  helpHeader: string
}

export interface Theme {
  color: ThemeColors
  brand: ThemeBrand
  bannerLogo: string
  bannerHero: string
}

// ── Color math ───────────────────────────────────────────────────────

function parseHex(h: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(h)

  if (!m) {
    return null
  }

  const n = parseInt(m[1]!, 16)

  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]
}

function mix(a: string, b: string, t: number) {
  const pa = parseHex(a)
  const pb = parseHex(b)

  if (!pa || !pb) {
    return a
  }

  const lerp = (i: 0 | 1 | 2) => Math.round(pa[i] + (pb[i] - pa[i]) * t)

  return '#' + ((1 << 24) | (lerp(0) << 16) | (lerp(1) << 8) | lerp(2)).toString(16).slice(1)
}

// ── Brand ─────────────────────────────────────────────────────────────

const BRAND: ThemeBrand = {
  name: 'ZAZA',
  icon: '⬡',
  prompt: '›',
  welcome: 'What shall we build?',
  goodbye: 'Until next time ⬡',
  tool: '│',
  helpHeader: 'Commands'
}

const cleanPromptSymbol = (s: string | undefined, fallback: string) => {
  const cleaned = String(s ?? '')
    .replace(/\s+/g, ' ')
    .trim()

  return cleaned || fallback
}

// ═══════════════════════════════════════════════════════════════════════════
// DARK THEME — Cyberpunk / Neon Cosmos
// ═══════════════════════════════════════════════════════════════════════════
//
// Primary:    #00F0FF (electric cyan)
// Accent:     #FF006E (neon magenta/pink)
// Border:     #3A3A5C (muted purple)
// Text:       #E0E0FF (soft white with blue tint)
// Muted:      #6B6B9A (desaturated purple)

export const DARK_THEME: Theme = {
  color: {
    primary: '#00F0FF',
    accent: '#FF006E',
    border: '#3A3A5C',
    text: '#E0E0FF',
    muted: '#6B6B9A',

    completionBg: '#1A1A2E',
    completionCurrentBg: '#2A2A4E',

    label: '#A0A0D0',
    ok: '#00FF88',
    error: '#FF3366',
    warn: '#FFB800',

    prompt: '#00F0FF',
    sessionLabel: '#6B6B9A',
    sessionBorder: '#3A3A5C',

    statusBg: '#0D0D1A',
    statusFg: '#8888AA',
    statusGood: '#00FF88',
    statusWarn: '#FFB800',
    statusBad: '#FF6B35',
    statusCritical: '#FF0044',
    selectionBg: '#2A2A4E',

    diffAdded: 'rgb(0,255,136)',
    diffRemoved: 'rgb(255,51,102)',
    diffAddedWord: 'rgb(0,200,100)',
    diffRemovedWord: 'rgb(255,80,80)',

    shellDollar: '#FF006E'
  },

  brand: BRAND,

  bannerLogo: '',
  bannerHero: ''
}

// ═══════════════════════════════════════════════════════════════════════════
// LIGHT THEME — Clean / Minimal
// ═══════════════════════════════════════════════════════════════════════════

export const LIGHT_THEME: Theme = {
  color: {
    primary: '#0088CC',
    accent: '#CC0055',
    border: '#B0B0CC',
    text: '#1A1A2E',
    muted: '#666688',

    completionBg: '#F0F0F8',
    completionCurrentBg: '#E0E0F0',

    label: '#555577',
    ok: '#008844',
    error: '#CC0033',
    warn: '#CC8800',

    prompt: '#0088CC',
    sessionLabel: '#666688',
    sessionBorder: '#B0B0CC',

    statusBg: '#F5F5FA',
    statusFg: '#555566',
    statusGood: '#008844',
    statusWarn: '#CC8800',
    statusBad: '#CC4400',
    statusCritical: '#CC0000',
    selectionBg: '#D0D0E8',

    diffAdded: 'rgb(0,180,80)',
    diffRemoved: 'rgb(200,0,50)',
    diffAddedWord: 'rgb(0,130,60)',
    diffRemovedWord: 'rgb(180,0,40)',

    shellDollar: '#CC0055'
  },

  brand: BRAND,

  bannerLogo: '',
  bannerHero: ''
}

// ── Theme detection ────────────────────────────────────────────────────

const TRUE_RE = /^(?:1|true|yes|on)$/
const FALSE_RE = /^(?:0|false|no|off)$/

const LIGHT_DEFAULT_TERM_PROGRAMS = new Set<string>()

const LUMA_LIGHT_THRESHOLD = 0.6
const HEX_3_RE = /^[0-9a-f]{3}$/
const HEX_6_RE = /^[0-9a-f]{6}$/

function backgroundLuminance(raw: string): null | number {
  const v = raw.trim().toLowerCase()

  if (!v) {
    return null
  }

  const hex = v.startsWith('#') ? v.slice(1) : v

  const rgb = HEX_6_RE.test(hex)
    ? [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)]
    : HEX_3_RE.test(hex)
      ? [parseInt(hex[0]! + hex[0]!, 16), parseInt(hex[1]! + hex[1]!, 16), parseInt(hex[2]! + hex[2]!, 16)]
      : null

  if (!rgb) {
    return null
  }

  return (0.2126 * rgb[0]! + 0.7152 * rgb[1]! + 0.0722 * rgb[2]!) / 255
}

export function detectLightMode(
  env: NodeJS.ProcessEnv = process.env,
  lightDefaultTermPrograms: ReadonlySet<string> = LIGHT_DEFAULT_TERM_PROGRAMS
): boolean {
  const lightFlag = (env.ZAZA_TUI_LIGHT ?? '').trim().toLowerCase()

  if (TRUE_RE.test(lightFlag)) {
    return true
  }

  if (FALSE_RE.test(lightFlag)) {
    return false
  }

  const themeFlag = (env.ZAZA_TUI_THEME ?? '').trim().toLowerCase()

  if (themeFlag === 'light') {
    return true
  }

  if (themeFlag === 'dark') {
    return false
  }

  const bgHint = backgroundLuminance(env.ZAZA_TUI_BACKGROUND ?? '')

  if (bgHint !== null) {
    return bgHint >= LUMA_LIGHT_THRESHOLD
  }

  const colorfgbg = (env.COLORFGBG ?? '').trim()

  if (colorfgbg) {
    const lastField = colorfgbg.split(';').at(-1) ?? ''

    if (/^\d+$/.test(lastField)) {
      const bg = Number(lastField)

      if (bg === 7 || bg === 15) {
        return true
      }

      if (bg >= 0 && bg < 16) {
        return false
      }
    }
  }

  const termProgram = (env.TERM_PROGRAM ?? '').trim()

  return lightDefaultTermPrograms.has(termProgram)
}

export const DEFAULT_THEME: Theme = detectLightMode() ? LIGHT_THEME : DARK_THEME

// ── Skin → Theme ─────────────────────────────────────────────────────

export function fromSkin(
  colors: Record<string, string>,
  branding: Record<string, string>,
  bannerLogo = '',
  bannerHero = '',
  toolPrefix = '',
  helpHeader = ''
): Theme {
  const d = DEFAULT_THEME
  const c = (k: string) => colors[k]

  const accent = c('ui_accent') ?? c('banner_accent') ?? d.color.accent
  const bannerAccent = c('banner_accent') ?? c('banner_title') ?? d.color.accent
  const muted = c('banner_dim') ?? d.color.muted
  const completionBg = c('completion_menu_bg') ?? d.color.completionBg

  return {
    color: {
      primary: c('ui_primary') ?? c('banner_title') ?? d.color.primary,
      accent,
      border: c('ui_border') ?? c('banner_border') ?? d.color.border,
      text: c('ui_text') ?? c('banner_text') ?? d.color.text,
      muted,
      completionBg,
      completionCurrentBg: c('completion_menu_current_bg') ?? mix(completionBg, bannerAccent, 0.25),

      label: c('ui_label') ?? d.color.label,
      ok: c('ui_ok') ?? d.color.ok,
      error: c('ui_error') ?? d.color.error,
      warn: c('ui_warn') ?? d.color.warn,

      prompt: c('prompt') ?? c('banner_text') ?? d.color.prompt,
      sessionLabel: c('session_label') ?? muted,
      sessionBorder: c('session_border') ?? muted,

      statusBg: d.color.statusBg,
      statusFg: d.color.statusFg,
      statusGood: c('ui_ok') ?? d.color.statusGood,
      statusWarn: c('ui_warn') ?? d.color.statusWarn,
      statusBad: d.color.statusBad,
      statusCritical: d.color.statusCritical,
      selectionBg: c('selection_bg') ?? d.color.selectionBg,

      diffAdded: d.color.diffAdded,
      diffRemoved: d.color.diffRemoved,
      diffAddedWord: d.color.diffAddedWord,
      diffRemovedWord: d.color.diffRemovedWord,
      shellDollar: c('shell_dollar') ?? d.color.shellDollar
    },

    brand: {
      name: branding.agent_name ?? d.brand.name,
      icon: d.brand.icon,
      prompt: cleanPromptSymbol(branding.prompt_symbol, d.brand.prompt),
      welcome: branding.welcome ?? d.brand.welcome,
      goodbye: branding.goodbye ?? d.brand.goodbye,
      tool: toolPrefix || d.brand.tool,
      helpHeader: branding.help_header ?? (helpHeader || d.brand.helpHeader)
    },

    bannerLogo,
    bannerHero
  }
}
