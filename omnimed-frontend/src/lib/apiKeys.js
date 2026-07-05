const STORAGE_KEY = 'omnimed_user_api_keys'

export const KEY_CONFIGS = {
  openrouter: {
    label: 'OpenRouter',
    description: 'One key for Claude, GPT-4o, and all other models',
    models: ['claude', 'gpt4', 'deepseek', 'groq', 'qwen', 'cohere'],
    url: 'https://openrouter.ai/keys',
    steps: [
      'Go to openrouter.ai and sign up',
      'Click your avatar → Keys',
      'Click Create Key, give it a name',
      'Copy the key starting with sk-or-v1-…',
    ],
    placeholder: 'sk-or-v1-…',
  },
  claude: {
    label: 'Anthropic (Claude)',
    description: 'Direct Anthropic API key for Claude only',
    models: ['claude'],
    url: 'https://console.anthropic.com/settings/keys',
    steps: [
      'Go to console.anthropic.com and sign up',
      'Go to Settings → API Keys',
      'Click Create Key',
      'Copy the key starting with sk-ant-…',
    ],
    placeholder: 'sk-ant-…',
  },
  gpt4: {
    label: 'OpenAI (GPT-4o)',
    description: 'Direct OpenAI API key for GPT-4o only',
    models: ['gpt4'],
    url: 'https://platform.openai.com/api-keys',
    steps: [
      'Go to platform.openai.com and sign up',
      'Click your avatar → API Keys',
      'Click Create new secret key',
      'Copy the key starting with sk-…',
    ],
    placeholder: 'sk-…',
  },
  deepseek: {
    label: 'DeepSeek',
    description: 'Your own DeepSeek key for higher limits',
    models: ['deepseek'],
    url: 'https://platform.deepseek.com/api_keys',
    steps: [
      'Go to platform.deepseek.com and sign up',
      'Go to API Keys in the dashboard',
      'Click Create API Key',
      'Copy the key',
    ],
    placeholder: 'sk-…',
  },
  groq: {
    label: 'Groq',
    description: 'Your own Groq key for higher limits',
    models: ['groq'],
    url: 'https://console.groq.com/keys',
    steps: [
      'Go to console.groq.com and sign up',
      'Click API Keys in the left sidebar',
      'Click Create API Key',
      'Copy the key starting with gsk_…',
    ],
    placeholder: 'gsk_…',
  },
  cohere: {
    label: 'Cohere',
    description: 'Your own Cohere key for higher limits',
    models: ['cohere'],
    url: 'https://dashboard.cohere.com/api-keys',
    steps: [
      'Go to dashboard.cohere.com and sign up',
      'API key is shown on the home page',
      'Copy the key',
    ],
    placeholder: '…',
  },
  gemini: {
    label: 'Google (Gemini)',
    description: 'Your own Gemini key for higher limits',
    models: ['gemini'],
    url: 'https://aistudio.google.com/apikey',
    steps: [
      'Go to aistudio.google.com/apikey and sign in',
      'Click Create API Key',
      'Copy the key starting with AIza…',
    ],
    placeholder: 'AIza…',
  },
  qwen: {
    label: 'Qwen (OpenRouter)',
    description: 'Your own OpenRouter key for higher Qwen limits',
    models: ['qwen'],
    url: 'https://openrouter.ai/keys',
    steps: [
      'Go to openrouter.ai and sign up',
      'Click your avatar → Keys',
      'Click Create Key',
      'Copy the key starting with sk-or-v1-…',
    ],
    placeholder: 'sk-or-v1-…',
  },
}

export function getAllKeys() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
}

export function getKey(provider) {
  return getAllKeys()[provider] || null
}

export function setKey(provider, value) {
  const all = getAllKeys()
  if (value) all[provider] = value
  else delete all[provider]
  localStorage.setItem(STORAGE_KEY, JSON.stringify(all))
}

export function removeKey(provider) {
  setKey(provider, null)
}

/** Build the user_keys payload to send with each chat request */
export function buildUserKeys() {
  return getAllKeys()
}

/** Returns true if the model has a usable key (own key or openrouter) */
export function modelHasKey(modelKey) {
  const keys = getAllKeys()
  return !!(keys[modelKey] || keys.openrouter)
}
