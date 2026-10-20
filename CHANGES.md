# Mercury fixes: what to copy where

Copy every file in this folder into the same path in your mercury folder,
replacing the existing file. Then do the one deletion below.
(If you use git, `git apply mercury-fixes.patch` from the repo root does all of it at once.)

## Delete

- `frontend/src/components/Databloom.jsx` — a duplicate. The app now uses `frontend/src/pages/Databloom.jsx`, which you already have and which doesn't change.

## New files

| File | Why |
|---|---|
| `docker-compose.yml` | backend, worker, redis and frontend services with a shared data volume |
| `backend/Dockerfile` | one image for the API and the RQ worker (CPU PyTorch, git, embedding model baked in) |
| `backend/.dockerignore` | keeps venv, .env and local data out of the image |
| `frontend/.dockerignore` | keeps your local node_modules (wrong-OS binaries) out of the image |
| `README.md` | project overview, Docker and local setup, tests |

## Changed files

| File | Change |
|---|---|
| `requirements.txt` (root) | was a UTF-16 freeze of a whole Windows Python environment; now just points to `backend/requirements.txt` |
| `backend/requirements.txt` | pinned to the versions from your working environment; adds pytest and httpx |
| `backend/.env.example` | lists JWT_SECRET (required: the app won't start without it), COOKIE_SECURE, SMTP and the other settings |
| `backend/app/rag/chat.py` | an LLM failure raises `LLMUnavailable` instead of crashing |
| `backend/app/main.py` | turns `LLMUnavailable` into a 502 with a readable "try again" message |
| `backend/app/rag/__init__.py` | exports `LLMUnavailable` |
| `backend/app/rag/search.py` | `matches()` also understands Chroma filter syntax ($and, $or, $in, $nin, $eq, $ne) |
| `backend/app/routes/docsense.py` | upload no longer blocks the whole server (plain `def`), streams to disk with a 20 MB limit, rejects empty files (400) and files with no text (422), and returns `department`; `/docsense/files` is keyed by `doc_id` and returns filename, department and uploaded_by; asking about a document you can't see returns 404 |
| `backend/app/routes/databloom.py` | adds `GET /databloom/{doc_id}/chunks` and accepts `doc_id` in `POST /databloom` (what the page calls); the old `?source=` route still works |
| `backend/tests/conftest.py`, `backend/tests/test_docsense.py` | updated to the current API; all 16 tests pass |
| `frontend/src/lib/api.js` | adds the missing `documents()` and `uploadDocument()`; `chunks()` and `summarize()` use doc_id and return what the Databloom page expects |
| `frontend/src/App.jsx` | imports Databloom from `pages/` |

## After copying

```bash
cd backend
pip install -r requirements.txt
pytest -q                 # expect: 16 passed
```

For Docker: `cp backend/.env.example backend/.env`, fill in GROQ_API_KEY and JWT_SECRET, then from the repo root run `docker compose up --build`, and create your admin with
`docker compose exec backend python create_admin.py --email you@company.com --name "You"`.
