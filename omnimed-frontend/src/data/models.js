// Every model the site offers, in one place. The chat boxes, the API-key
// settings and the admin pages all read from here; it mirrors MODELS in
// omnimed-backend/routes/chat.py, which decides how each one is called.
//
// key     — permanent id, stored in validations.model and usage_logs. Never
//           renamed or pointed at a different model; a new model gets a new key.
// userKey — which saved API key pays for it (lib/apiKeys.js KEY_CONFIGS)
// gated   — metered at 5/day on the project's keys (backend GATED_MODELS)
export const MODELS = [
  // Anthropic
  { key: 'opus55',   label: 'Claude Opus 5.5',   group: 'Anthropic', userKey: 'claude', gated: true, color: '#6d28d9', bg: '#ede9fe' },
  { key: 'sonnet55', label: 'Claude Sonnet 5.5', group: 'Anthropic', userKey: 'claude', gated: true, color: '#7c3aed', bg: '#f5f3ff' },
  { key: 'haiku55',  label: 'Claude Haiku 5.5',  group: 'Anthropic', userKey: 'claude', gated: true, color: '#9333ea', bg: '#faf5ff' },
  { key: 'fable51',  label: 'Claude Fable 5.1',  group: 'Anthropic', userKey: 'claude', gated: true, color: '#7e22ce', bg: '#f3e8ff' },
  { key: 'opus5',    label: 'Claude Opus 5',     group: 'Anthropic', userKey: 'claude', gated: true, color: '#6d28d9', bg: '#ede9fe' },
  { key: 'sonnet5',  label: 'Claude Sonnet 5',   group: 'Anthropic', userKey: 'claude', gated: true, color: '#7c3aed', bg: '#f5f3ff' },
  // OpenAI
  { key: 'gpt61sol',  label: 'GPT-6.1 Sol',  group: 'OpenAI', userKey: 'gpt4', gated: true, color: '#15803d', bg: '#dcfce7' },
  { key: 'gpt6astra', label: 'GPT-6 Astra',  group: 'OpenAI', userKey: 'gpt4', gated: true, color: '#15803d', bg: '#dcfce7' },
  { key: 'gpt6luna',  label: 'GPT-6 Luna',   group: 'OpenAI', userKey: 'gpt4', gated: true, color: '#16a34a', bg: '#dcfce7' },
  { key: 'gpt55',     label: 'GPT-5.5',      group: 'OpenAI', userKey: 'gpt4', gated: true, color: '#166534', bg: '#dcfce7' },
  { key: 'gpt54mini', label: 'GPT-5.4 mini', group: 'OpenAI', userKey: 'gpt4', gated: true, color: '#047857', bg: '#d1fae5' },
  // DeepSeek
  { key: 'deepseek_pro', label: 'DeepSeek V4 Pro', group: 'DeepSeek', userKey: 'deepseek', color: '#0369a1', bg: '#e0f2fe' },
  // Google
  { key: 'gemini38', label: 'Gemini 3.8 Flash', group: 'Google', userKey: 'gemini', color: '#b45309', bg: '#fef3c7' },
  { key: 'gemini37', label: 'Gemini 3.7 Flash', group: 'Google', userKey: 'gemini', color: '#b45309', bg: '#fef3c7' },
  { key: 'gemini36', label: 'Gemini 3.6 Flash', group: 'Google', userKey: 'gemini', color: '#b45309', bg: '#fef3c7' },
  { key: 'gemini',   label: 'Gemini 2.5 Flash', group: 'Google', userKey: 'gemini', color: '#ca8a04', bg: '#fef3c7' },
  // Qwen — through OpenRouter (paid credit), or Groq (free tier, rate-limited)
  { key: 'qwen38flash',   label: 'Qwen 3.8 Flash',      group: 'Qwen', userKey: 'qwen', color: '#be185d', bg: '#fce7f3' },
  { key: 'qwen38max0902', label: 'Qwen 3.8 Max',        group: 'Qwen', userKey: 'qwen', color: '#be185d', bg: '#fce7f3' },
  { key: 'qwen37plus',    label: 'Qwen 3.7 Plus',       group: 'Qwen', userKey: 'qwen', color: '#db2777', bg: '#fce7f3' },
  { key: 'qwen36flash',   label: 'Qwen 3.6 Flash',      group: 'Qwen', userKey: 'qwen', color: '#9d174d', bg: '#fce7f3' },
  { key: 'groq_qwen38',   label: 'Qwen 3.8 27B (Groq)', group: 'Qwen', userKey: 'groq', color: '#a21caf', bg: '#fae8ff' },
  // Other providers
  { key: 'groq',   label: 'Groq GPT-OSS 120B', group: 'Other', userKey: 'groq',   color: '#c2410c', bg: '#ffedd5' },
  { key: 'cohere', label: 'Cohere Command A+', group: 'Other', userKey: 'cohere', color: '#0f766e', bg: '#ccfbf1' },
]

// No longer offered, but named in old chats and in the statistics.
export const RETIRED_MODELS = [
  { key: 'claude',         label: 'Claude Sonnet 4.6', group: 'Anthropic', color: '#8b5cf6', bg: '#f5f3ff' },
  { key: 'opus48',         label: 'Claude Opus 4.8',   group: 'Anthropic', color: '#5b21b6', bg: '#ede9fe' },
  { key: 'fable5',         label: 'Claude Fable 5',    group: 'Anthropic', color: '#7e22ce', bg: '#f3e8ff' },
  { key: 'gpt4',           label: 'GPT-4o',            group: 'OpenAI',    color: '#15803d', bg: '#dcfce7' },
  { key: 'gpt56sol',       label: 'GPT-5.6 Sol',       group: 'OpenAI',    color: '#166534', bg: '#dcfce7' },
  { key: 'deepseek',       label: 'DeepSeek Chat',     group: 'DeepSeek',  color: '#0369a1', bg: '#e0f2fe' },
  { key: 'deepseek_flash', label: 'DeepSeek Flash',    group: 'DeepSeek',  color: '#0369a1', bg: '#e0f2fe' },
  { key: 'gemini35',       label: 'Gemini 3.5 Flash',  group: 'Google',    color: '#a16207', bg: '#fef9c3' },
  { key: 'qwen',           label: 'Qwen 2.5 72B',      group: 'Qwen',      color: '#a21caf', bg: '#fae8ff' },
  { key: 'qwen38max',      label: 'Qwen 3.8 Max',      group: 'Qwen',      color: '#be185d', bg: '#fce7f3' },
]

export const MODEL_GROUPS = ['Anthropic', 'OpenAI', 'DeepSeek', 'Google', 'Qwen', 'Other']

export const DEFAULT_BOXES = ['gemini38', 'deepseek_pro', 'groq', 'qwen38flash', 'cohere']
export const MAX_BOXES = 5

const UNKNOWN = { label: null, group: 'Other', color: '#475569', bg: '#f1f5f9' }

/** A model's display details, current or retired; unknown keys show as-is. */
export function modelInfo(key) {
  return MODELS.find(m => m.key === key)
    || RETIRED_MODELS.find(m => m.key === key)
    || { ...UNKNOWN, key, label: key }
}

export const modelLabel = (key) => modelInfo(key).label
