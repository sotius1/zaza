import { pick } from '../lib/text.js'

export const PLACEHOLDERS = [
  'What shall we build?',
  'Try \"explain this codebase\"',
  'Try \"write a test for…\"',
  'Try \"refactor the auth module\"',
  'Try \"/help\" for commands',
  'Try \"fix the lint errors\"',
  'Try \"how does the config loader work?\"',
  'Try \"review my changes\"',
  'Try \"deploy to production\"'
]

export const PLACEHOLDER = pick(PLACEHOLDERS)
