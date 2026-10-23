import { modelInfo } from '../data/models.js'

const STORAGE_KEY = 'omnimed_user_api_keys'

// Which saved key pays for a model: every Anthropic model uses the `claude`
// key, every OpenAI model `gpt4`, and so on (data/models.js, `userKey`).
// A hand-kept list here once covered 9 of the 25 models, so a saved Anthropic
// key did not count for Opus 5.5 and the 5/day badge stayed on.
function providerForModel(modelKey) {
  return modelInfo(modelKey).userKey || modelKey
}

// One entry per key a user can save, in the order the settings show them.
// There is no general "OpenRouter" key any more: the backend sends only the
// Qwen models through OpenRouter, so an OpenRouter key is the Qwen key. A key
// saved under the old "openrouter" name is moved there (see getAllKeys).
export const KEY_CONFIGS = {
  claude: {
    label: 'Anthropic (Claude)',
    description: 'Your Anthropic API key — covers every Claude model, with no daily limit',
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
    label: 'OpenAI (GPT)',
    description: 'Your OpenAI API key — covers every GPT model, with no daily limit',
    url: 'https://platform.openai.com/api-keys',
    steps: [
      'Go to platform.openai.com and sign up',
      'Click your avatar → API Keys',
      'Click Create new secret key',
      'Copy the key starting with sk-…',
    ],
    placeholder: 'sk-…',
  },
  gemini: {
    label: 'Google (Gemini)',
    description: 'Your own Gemini key for higher limits',
    url: 'https://aistudio.google.com/apikey',
    steps: [
      'Go to aistudio.google.com/apikey and sign in',
      'Click Create API Key',
      'Copy the key starting with AIza…',
    ],
    placeholder: 'AIza…',
  },
  deepseek: {
    label: 'DeepSeek',
    description: 'Your own DeepSeek key for higher limits',
    url: 'https://platform.deepseek.com/api_keys',
    steps: [
      'Go to platform.deepseek.com and sign up',
      'Go to API Keys in the dashboard',
      'Click Create API Key',
      'Copy the key',
    ],
    placeholder: 'sk-…',
  },
  qwen: {
    label: 'Qwen (OpenRouter)',
    description: 'An OpenRouter key, for the Qwen models served through OpenRouter',
    url: 'https://openrouter.ai/keys',
    steps: [
      'Go to openrouter.ai and sign up',
      'Click your avatar → Keys',
      'Click Create Key',
      'Copy the key starting with sk-or-v1-…',
    ],
    placeholder: 'sk-or-v1-…',
  },
  groq: {
    label: 'Groq',
    description: 'Your own Groq key — GPT-OSS and Qwen 3.8 27B, with higher rate limits',
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
    url: 'https://dashboard.cohere.com/api-keys',
    steps: [
      'Go to dashboard.cohere.com and sign up',
      'API key is shown on the home page',
      'Copy the key',
    ],
    placeholder: '…',
  },
}

function getAllKeys() {
  let all
  try {
    all = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
  if (all.openrouter) {
    // The general OpenRouter key was retired; the same key pays for Qwen.
    if (!all.qwen) all.qwen = all.openrouter
    delete all.openrouter
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(all)) } catch { /* read-only storage */ }
  }
  return all
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

/** True if the user saved the key that pays for this model */
export function modelHasKey(modelKey) {
  return !!getAllKeys()[providerForModel(modelKey)]
}
