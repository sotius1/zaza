import type { ThemeColors } from './theme.js'

const RICH_RE = /\[(?:bold\s+)?(?:dim\s+)?(#(?:[0-9a-fA-F]{3,8}))\]([\s\S]*?)(\[\/\])/g

export function parseRichMarkup(markup: string): Line[] {
  const lines: Line[] = []

  for (const raw of markup.split('\n')) {
    const trimmed = raw.trimEnd()

    if (!trimmed) {
      lines.push(['', ' '])
      continue
    }

    const matches = [...trimmed.matchAll(RICH_RE)]

    if (!matches.length) {
      lines.push(['', trimmed])
      continue
    }

    let cursor = 0

    for (const m of matches) {
      const before = trimmed.slice(cursor, m.index)

      if (before) {
        lines.push(['', before])
      }

      lines.push([m[1]!, m[2]!])
      cursor = m.index! + m[0].length
    }

    if (cursor < trimmed.length) {
      lines.push(['', trimmed.slice(cursor)])
    }
  }

  return lines
}

// ═══════════════════════════════════════════════════════════════════════════
// ZAZA COSMOS — kosmiczny banner ASCII
// ═══════════════════════════════════════════════════════════════════════════

const LOGO_ART = [
  '  ███████╗ █████╗ ███████╗ █████╗     ██████╗ ██████╗ ███████╗██╗    ██╗',
  '  ╚══███╔╝██╔══██╗╚══███╔╝██╔══██╗    ██╔══██╗██╔══██╗██╔════╝██║    ██║',
  '    ███╔╝ ███████║  ███╔╝ ███████║    ██████╔╝██████╔╝█████╗  ██║ █╗ ██║',
  '   ███╔╝  ██╔══██║ ███╔╝  ██╔══██║    ██╔═══╝ ██╔══██╗██╔══╝  ██║███╗██║',
  '  ███████╗██║  ██║███████╗██║  ██║    ██║     ██║  ██║███████╗╚███╔███╔╝',
  '  ╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝    ╚═╝     ╚═╝  ╚═╝╚══════╝ ╚══╝╚══╝ ',
]

const COSMOS_ART = [
  '      ·  ✦  ·    ˚  ✧     ·    ˚    ·  ✦    ·',
  '  ✧     ·    ˚    ·     ✦    ·    ˚    ·     ✧',
  '      ·    ˚   ✦     ·     ˚  ✧    ·    ˚    ·',
  '  ˚    ·     ✧    ·    ˚    ·     ✦    ·    ˚   ',
  '    ✦     ·    ˚    ·  ✧     ·    ˚    ·     ✦ ',
]

const COMPACT_LOGO = [
  ' ██████╗ ██████╗ ███████╗██╗    ██╗',
  ' ██╔══██╗██╔══██╗██╔════╝██║    ██║',
  ' ██████╔╝██████╔╝█████╗  ██║ █╗ ██║',
  ' ██╔═══╝ ██╔══██╗██╔══╝  ██║███╗██║',
  ' ██║     ██║  ██║███████╗╚███╔███╔╝',
  ' ╚═╝     ╚═╝  ╚═╝╚══════╝ ╚══╝╚══╝ ',
]

const TINY_LOGO = [
  '  ╔═╗╔═╗╔═╗╔═╗  ╦═╗╔═╗╦ ╦',
  '  ╔═╝╠═╣╔═╝╠═╣  ╠╦╝║ ║╚╦╝',
  '  ╚═╝╩ ╩╚═╝╩ ╩  ╩╚═╚═╝ ╩ ',
]

// Nebula-style hero art for wide terminals
const NEBULA_ART = [
  '            ·  ⋆  ·    ˚  ✦  ·    ˚    ·  ⋆  ·',
  '        ✦     ·    ˚    ·     ⋆    ·    ˚    ·     ✦',
  '    ⋆     ·    ˚    ·  ✦     ·    ˚    ·     ⋆    ·    ˚',
  '       ·    ˚   ⋆     ·     ˚  ✦    ·    ˚    ·',
  '   ✧     ·     ⋆    ·    ˚    ·     ✦    ·    ˚    ·     ✧',
  '    ˚    ·     ✦    ·    ˚    ·     ⋆    ·    ˚    ·    ⋆',
  '       ·    ˚    ·  ✧     ·    ˚    ·     ✦    ·    ˚',
  '   ⋆     ·     ✦    ·    ˚    ·     ⋆    ·    ˚    ·     ⋆',
]

// ── Gradient maps ───────────────────────────────────────────────────────

// Cyan → Magenta → Gold → White gradient for the main logo
const LOGO_GRADIENT = [0, 0, 1, 1, 2, 2] as const
const COMPACT_GRADIENT = [0, 0, 1, 1, 2, 2] as const
const TINY_GRADIENT = [0, 1, 2, 2] as const

// Nebula: subtle color shifts
const NEBULA_GRADIENT = [3, 3, 3, 3, 3, 3, 3, 3] as const
const COSMOS_GRADIENT = [3, 3, 3, 3, 3] as const

const colorize = (art: string[], gradient: readonly number[], c: ThemeColors): Line[] => {
  const p = [c.primary, c.accent, c.border, c.muted]

  return art.map((text, i) => [p[gradient[i]!] ?? c.muted, text])
}

// ── Exports ─────────────────────────────────────────────────────────────

export const LOGO_WIDTH = 69
export const COMPACT_WIDTH = 36
export const TINY_WIDTH = 28
export const NEBULA_WIDTH = 52
export const COSMOS_WIDTH = 49

export const logo = (c: ThemeColors, customLogo?: string): Line[] =>
  customLogo ? parseRichMarkup(customLogo) : colorize(LOGO_ART, LOGO_GRADIENT, c)

export const compactLogo = (c: ThemeColors): Line[] =>
  colorize(COMPACT_LOGO, COMPACT_GRADIENT, c)

export const tinyLogo = (c: ThemeColors): Line[] =>
  colorize(TINY_LOGO, TINY_GRADIENT, c)

export const nebula = (c: ThemeColors, customHero?: string): Line[] =>
  customHero ? parseRichMarkup(customHero) : colorize(NEBULA_ART, NEBULA_GRADIENT, c)

export const cosmos = (c: ThemeColors): Line[] =>
  colorize(COSMOS_ART, COSMOS_GRADIENT, c)

export const artWidth = (lines: Line[]) => lines.reduce((m, [, t]) => Math.max(m, t.length), 0)

export type Line = [string, string]
