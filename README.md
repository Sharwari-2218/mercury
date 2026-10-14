# Mercury AI

**A company knowledge assistant built on retrieval-augmented generation (RAG).**
Ask questions across your company's documents, wiki pages and GitHub repositories, and get answers with inline source citations.

Repository: https://github.com/Sharwari-2218/mercury

---

## Features

| Feature | What it does |
|---|---|
| **Ask (All)** | One question across every document and wiki page you're allowed to see |
| **Docsense** | Upload PDF, DOCX, TXT or MD files and ask questions about one document or all of them |
| **GitTalk** | Index a public GitHub repo and ask about its code; answers name the exact file paths |
| **Sharebase** | Add a wiki or web page by URL and ask questions across pages; re-adding a page replaces the old version |
| **Databloom** | Browse a document chunk by chunk and get a short markdown summary of any chunk |
| **Contribute** | Submit a file, repo or wiki URL; a background worker ingests it while you track the job's status |
| **Feed** | Share useful answers, upvote or downvote them, sort by newest or trending, and follow tags to get email alerts |
| **Admin** | Approve or reject sign-up requests, manage users, and set which content types each department may search |

Every answer comes with numbered sources, the search queries that were used, and three suggested follow-up questions. Answers are short by default and switch to a detailed format when the question asks to "explain", "describe" or "give an overview".

## How the search works

Mercury uses a **3-layer hybrid search**:

1. **LLM query rewriting:** the question is rewritten into up to three alternative phrasings (synonyms, expanded acronyms).
2. **Keyword search (BM25)** and **vector search (Chroma + all-MiniLM-L6-v2 embeddings)** run for the original question and each rewrite.
3. **Reciprocal Rank Fusion (RRF)** merges all the result lists by rank, so BM25 scores and cosine similarities never need to be normalised against each other:

   ```
   score(chunk) = Σ  1 / (60 + rank)
   ```

The top chunks are sent to the LLM (Llama 3.3 70B on Groq) with a prompt that only allows answers from the given context and requires `[1]`, `[2]` citations.

**Other retrieval details**
- Documents are split into 800-character chunks with 100-character overlap.
- Code is split along function and class boundaries for 20+ languages, and every code chunk starts with its file path, so both search layers know where it came from.
- The BM25 tokenizer splits `snake_case` and `camelCase`, so code identifiers match plain-English questions.
- If a question names a file (for example "explain the README" or "what does signer.py do?"), that whole file is read in order instead of only the chunks search picks.

## Access control

- Users request access through a sign-up form; an admin approves them into a department (and can restrict sign-ups to company email domains).
- Each department has a policy listing the content types it may search: `code`, `doc` and/or `wiki`.
- The same department and type filter is applied to **both** the keyword and the vector layer before fusion, so nothing outside a user's scope can reach the LLM.
- Login uses a short-lived JWT access token kept in memory plus a rotating httpOnly refresh cookie. Passwords are hashed with Argon2. Logging out, or an admin disabling an account, invalidates all of that user's tokens immediately.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, SQLite, Pydantic |
| AI / RAG | LangChain, ChromaDB, Sentence-Transformers (all-MiniLM-L6-v2), rank-bm25, Groq (Llama 3.3 70B) |
| Ingestion | PyPDF, python-docx, BeautifulSoup, GitPython |
| Background jobs | Redis, RQ |
| Auth | PyJWT, pwdlib (Argon2) |
| Email | SMTP (Gmail app password) |
| Frontend | React 19, Vite, Tailwind CSS, React Router, react-markdown |
| Testing | Pytest |

## Project structure

```
mercury/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, routers, CORS
│   │   ├── auth.py            # JWT, password hashing, department filters
│   │   ├── models.py          # users, policies, jobs, posts, votes, subscriptions
│   │   ├── database.py        # SQLAlchemy engine and session
│   │   ├── notify.py          # email alerts
│   │   ├── worker_tasks.py    # RQ ingestion jobs
│   │   ├── rag/
│   │   │   ├── ingest.py      # load -> split -> embed -> store
│   │   │   ├── search.py      # BM25 + vector + query rewrites + RRF
│   │   │   ├── chat.py        # prompts, grounded answers, follow-ups
│   │   │   └── config.py      # paths, models, clients
│   │   └── routes/            # ask, docsense, gittalk, sharebase, databloom,
│   │                          # contribute, feed, auth
│   ├── tests/                 # pytest suite
│   ├── create_admin.py        # first admin + starter department policies
│   └── requirements.txt
└── frontend/
    └── src/
        ├── pages/             # Ask, Contribute, Feed, Databloom, Admin, Login, Signup
        ├── components/
        ├── lib/api.js         # every backend call
        └── api.js             # auth-aware fetch with token refresh
```

## Getting started

**Prerequisites:** Python 3.12, Node.js 20+, Git, Redis, and a free [Groq API key](https://console.groq.com).

### 1. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate

# CPU-only PyTorch (skip this line if you have an NVIDIA GPU)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

cp .env.example .env                  # Windows: copy .env.example .env
```

Fill in `.env`. Two values are required:

```env
GROQ_API_KEY=your_groq_key
JWT_SECRET=                           # python -c "import secrets; print(secrets.token_hex(32))"
COOKIE_SECURE=false                   # for local http
```

Create the first admin (this also adds the starter departments `engineering`, `hr` and `sales`), then start the API:

```bash
python create_admin.py
uvicorn app.main:app --reload         # http://localhost:8000, API docs at /docs
```

### 2. Worker

In a second terminal, with Redis running and the virtual environment active:

```bash
cd backend
rq worker ingest                      # Windows: rq worker ingest --worker-class rq.worker.SimpleWorker
```

### 3. Frontend

In a third terminal:

```bash
cd frontend
npm install
npm run dev                           # http://localhost:5173
```

Vite forwards `/api` requests to the backend, so the login cookie works without any CORS setup. Sign in with the admin account you created.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | (required) | LLM access |
| `JWT_SECRET` | (required) | Signs login tokens; at least 32 characters |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `COOKIE_SECURE` | `true` | Set to `false` for local http |
| `ALLOWED_EMAIL_DOMAINS` | (any) | Comma-separated domains allowed to sign up |
| `DATA_DIR` | `backend/data` | Chroma vectors, chunk files, uploads |
| `DATABASE_URL` | `sqlite:///./mercury.db` | App database |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | Job queue |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | (off) | Email alerts; if unset, emails are only logged |
| `APP_URL` | `http://localhost:5173` | Links in emails |

## API overview

Interactive docs are at `http://localhost:8000/docs` while the backend is running.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/signup`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `GET /auth/me` |
| Admin | `GET /auth/requests`, `POST /auth/requests/{id}/approve` · `/reject`, `GET/POST /auth/users`, `GET/PUT /auth/policies` |
| Ask | `POST /ask` |
| Docsense | `POST /docsense/upload`, `POST /docsense/ask`, `GET /docsense/files` |
| GitTalk | `POST /gittalk/index`, `POST /gittalk/ask`, `GET /gittalk/repos` |
| Sharebase | `POST /sharebase/add`, `POST /sharebase/ask`, `GET /sharebase/pages`, `DELETE /sharebase/pages/{id}` |
| Databloom | `GET /databloom/{doc_id}/chunks`, `POST /databloom` |
| Contribute | `POST /contribute`, `GET /contribute/status/{id}`, `GET /contribute/jobs` |
| Feed | `GET /feed`, `GET /feed/trending`, `POST /posts`, `DELETE /posts/{id}`, `POST /posts/{id}/vote`, `GET/POST/DELETE /subscriptions` |

## Running tests

```bash
cd backend
pytest -q
```

The tests replace Chroma and the Groq LLM with fast, deterministic fakes. Everything else is the real code: routes, login, department rules, chunking, BM25 search and RRF.

## Roadmap

- [ ] Docker Compose setup (backend, worker, Redis, frontend)
- [ ] Limit feed posts to the departments allowed to see their sources
- [ ] Block private and internal addresses when fetching wiki URLs
- [ ] Stream answers token by token
- [ ] Include indexed GitHub repos in "All" search
