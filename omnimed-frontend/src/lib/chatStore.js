// Server-side chat persistence (Supabase = source of truth, localStorage = cache).
// A conversation is a `sessions` row (+ title); each turn is a `messages` row
// whose `content` jsonb is the full UI message object. RLS scopes to the user.

import { supabase } from './supabase'

const isUuid = (s) => typeof s === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)

// Drop volatile UI-only flags before persisting a message.
function stripTransient(m) {
  const { conclusionLoading, ...rest } = m
  return rest
}

/** Conversations for a user, newest first. Only those with a title (real content). */
export async function listConversations(userId) {
  if (!userId) return []
  const { data, error } = await supabase
    .from('sessions')
    .select('session_id, title, updated_at')
    .eq('user_id', userId)
    .not('title', 'is', null)
    .order('updated_at', { ascending: false })
  if (error) throw error
  return (data || []).map(r => ({
    id: r.session_id,
    title: r.title || 'Untitled',
    updatedAt: r.updated_at ? new Date(r.updated_at).getTime() : 0,
  }))
}

/** Full message list for a conversation, in order. Returns null for non-uuid ids. */
export async function loadMessages(sessionId) {
  if (!isUuid(sessionId)) return null
  const { data, error } = await supabase
    .from('messages')
    .select('content, seq')
    .eq('session_id', sessionId)
    .order('seq', { ascending: true })
  if (error) throw error
  return (data || []).map(r => r.content)
}

/**
 * Upsert one turn's messages and bump the conversation. Pass `title` (string)
 * to set the conversation title, or null to leave it unchanged. No-ops for
 * local-only (non-uuid) session ids.
 */
export async function saveTurn(sessionId, userId, title, msgs) {
  if (!isUuid(sessionId) || !msgs?.length) return
  const rows = msgs.map(m => ({
    session_id: sessionId,
    user_id: userId || null,
    seq: m.id,
    role: m.role,
    content: stripTransient(m),
  }))
  await supabase.from('messages').upsert(rows, { onConflict: 'session_id,seq' })

  const update = { updated_at: new Date().toISOString() }
  if (title != null) update.title = title
  await supabase.from('sessions').update(update).eq('session_id', sessionId)
}

export async function renameConversation(sessionId, title) {
  if (!isUuid(sessionId)) return
  await supabase.from('sessions').update({ title }).eq('session_id', sessionId)
}

export async function deleteConversation(sessionId) {
  if (!isUuid(sessionId)) return
  await supabase.from('sessions').delete().eq('session_id', sessionId)  // cascades to messages
}
