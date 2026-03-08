# Client

`client/` is the React frontend for Enterprise RAG. It handles authentication with Supabase, workspace onboarding, document upload UX, document-aware chat, and observability views.

## Responsibilities

- sign in and sign up users with Supabase Auth
- keep the current session and bearer token in client state
- route users through workspace creation before entering the app shell
- upload PDFs through the API + Supabase signed URL flow
- display ingestion progress and document status
- run streaming grounded chat against the selected document
- show citations, sources, usage, and observability metrics

## Tech Stack

- React 18
- TypeScript
- Vite
- React Router
- Supabase JS
- Tailwind CSS utilities
- Axios and fetch-based API calls

## App Structure

```text
client/
├── src/
│   ├── App.tsx                   # top-level routes
│   ├── context/AuthContext.tsx   # session lifecycle and auth actions
│   ├── lib/api.ts                # typed API client
│   ├── lib/supabase.ts           # Supabase client
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── Signup.tsx
│   │   ├── WorkspaceGate.tsx
│   │   ├── UploadPage.tsx
│   │   ├── ChatPage.tsx
│   │   ├── ObservabilityPage.tsx
│   │   └── WorkspaceInfoPage.tsx
│   ├── components/
│   │   ├── layout/
│   │   ├── upload/
│   │   ├── chat/
│   │   └── documents/
│   └── styles/
├── package.json
└── vite.config.ts
```

## Route Map

Public routes:
- `/login`
- `/signup`

Authenticated routes:
- `/workspace`
- `/app/upload`
- `/app/chat`
- `/app/observability`
- `/app/workspace`

Top-level flow in `src/App.tsx`:
1. unauthenticated users are redirected to `/login`
2. authenticated users are redirected to `/workspace`
3. workspace setup routes the user into `/app/*`
4. protected app routes render inside the shared app shell

## UX Flows

### Authentication

`AuthContext` is the session source of truth.

Current behavior:
- reads the initial Supabase session on load
- subscribes to auth state changes
- exposes `signIn`, `signUp`, and `signOut`
- registers a global unauthorized handler so API `401` responses log the user out and redirect to `/login`

Primary files:
- `src/context/AuthContext.tsx`
- `src/lib/supabase.ts`
- `src/lib/api.ts`

### Document Upload

Upload UX is centered in `UploadPage` and `components/upload/UploadPanel`.

Flow:
1. request `POST /documents/upload-prepare`
2. upload the PDF directly to the signed URL
3. notify the backend with `POST /documents/upload-complete`
4. poll the document list while processing is active
5. let the user delete documents from the table

Current behavior:
- shows live document status in a table
- refreshes every 4 seconds while documents are processing
- treats `ready` and `indexed` as successful ingest states

### Chat and Streaming Query

`ChatPage` drives the main RAG interaction.

Current behavior:
- binds the active document from the app shell context
- streams answer deltas from `POST /query/stream`
- loads citations and source text on demand
- persists and restores draft transcript state locally
- stores chat sessions with `/chats/sessions`
- updates usage metrics in the app shell after responses

Primary files:
- `src/pages/ChatPage.tsx`
- `src/components/chat/*`
- `src/lib/api.ts`

### Observability

`ObservabilityPage` renders a workspace-level operational summary using `GET /usage/observability`.

Displayed metrics:
- total queries
- 24-hour queries and errors
- error rate
- average latency
- p95 latency
- tokens used and remaining
- document pipeline health
- top queried documents
- recent query errors

## API Contracts Used

The client currently uses these backend APIs:

- `GET /auth/me`
- `POST /workspaces`
- `GET /workspaces/me`
- `GET /documents`
- `GET /documents/{document_id}`
- `GET /documents/{document_id}/pages/{page_number}`
- `POST /documents/upload-prepare`
- `POST /documents/upload-complete`
- `DELETE /documents/{document_id}`
- `POST /query/stream`
- `GET /citations/{chunk_id}`
- `GET /queries`
- `GET /queries/{query_id}`
- `POST /chats/sessions`
- `PATCH /chats/sessions/{session_id}`
- `GET /chats/sessions`
- `GET /chats/sessions/{session_id}`
- `GET /usage/today`
- `GET /usage/observability`

All authenticated requests send:

```http
Authorization: Bearer <supabase_access_token>
```

## Environment

Create `client/.env` with:

```bash
VITE_API_URL=http://localhost:8000
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-public-anon-key
```

## Run

```bash
cd client
npm install
npm run dev -- --host 0.0.0.0
```

Build for production:

```bash
npm run build
```

Preview the built app:

```bash
npm run preview
```

## Development Commands

```bash
cd client
npm run dev
npm run build
npm run lint
npm run test
```

## Notes for Contributors

- `src/lib/api.ts` is the contract layer; keep request and response types aligned with the backend
- auth redirects are centralized through the unauthorized handler, so avoid duplicating logout-on-401 logic elsewhere
- chat is document-scoped in the current UI model
- upload and polling behavior assume asynchronous backend processing states
- preserve the existing visual language unless the task is explicitly a redesign
