export function logError(error: unknown): void {
  if (!process.env.ZAZA_INK_DEBUG_ERRORS) {
    return
  }

  console.error(error)
}
