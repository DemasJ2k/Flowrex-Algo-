# AI Chat Persistence & Memory — Design Doc

_Created 2026-04-16._

---

## Current State

- Messages stored in `LLMSupervisor._sessions[user_id].conversation` (Python list in memory)
- Lost on backend restart
- Lost on page refresh (frontend doesn't reload history)
- No concept of "chat sessions" — just one continuous conversation per user

## What's Needed

1. **Chat messages persist to DB** — survive restarts
2. **Frontend loads chat history** on page mount
3. **Multiple chat sessions** — user can create new chat, view old ones, delete them
4. **AI Supervisor remembers context** — loads last N messages from DB into the Claude prompt

---

## Database Model

```python
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(200), default="New Chat")  # auto-generated from first message
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)  # false = archived/deleted

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    model = Column(String(50), nullable=True)  # which Claude model responded
    tokens_used = Column(Integer, nullable=True)  # for cost tracking
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

## API Endpoints

```
GET  /api/llm/sessions              — list user's chat sessions
POST /api/llm/sessions              — create new session (returns session_id)
GET  /api/llm/sessions/{id}         — get messages for a session
DELETE /api/llm/sessions/{id}       — delete a session

POST /api/llm/chat                  — send message (add session_id param)
     body: { message: "...", session_id: 5 }
     → saves user message to DB
     → sends to Claude with last 20 messages as context
     → saves assistant reply to DB
     → returns reply + session_id

POST /api/llm/chat/clear            — clear current session (keep in DB but mark inactive)
```

## Frontend Changes

Left sidebar (or top bar) in the AI page:
- "New Chat" button → creates new session
- List of past sessions (title + date) → click to load
- Delete button per session
- Current session messages load from DB on page mount

## AI Supervisor Memory

On every chat message:
1. Load last 20 messages from the DB for this session
2. Include the trading context (agents, recent trades, P&L)
3. Send to Claude
4. Save the reply back to DB

On backend restart:
- The `_conversation` list is rebuilt from DB for active sessions
- No more data loss

## Cost Tracking

Each `ChatMessage` stores `tokens_used` from the Anthropic response headers.
New endpoint: `GET /api/llm/usage` → returns monthly token count + estimated cost.

---

## Migration 007

```sql
CREATE TABLE chat_sessions (...);
CREATE TABLE chat_messages (...);
CREATE INDEX ix_chat_sessions_user ON chat_sessions(user_id);
CREATE INDEX ix_chat_messages_session ON chat_messages(session_id);
```

## Estimated Effort: ~4h
- Migration + models: 30min
- Backend endpoints: 1.5h  
- Frontend chat UI with session list: 2h
