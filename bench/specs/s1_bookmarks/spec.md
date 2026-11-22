# Dev Spec S1: Bookmark Manager with Tags, Search & User Auth

Build a full-stack multi-user Bookmark Manager application using the `fastapi-react-postgres` stack pack.

## Features (3)
1. **Auth (`backend/app/features/auth/`)**
   - `POST /api/auth/register`: accepts `email`, `password`, `name`; returns HTTP `201` with `id`, `email`, `name`. Rejects duplicate email (`400` or `409`).
   - `POST /api/auth/login`: accepts `email`, `password`; returns HTTP `200` with `{"access_token": "...", "token_type": "bearer"}`. Returns `401` on invalid credentials.
   - `GET /api/auth/me`: requires bearer token; returns HTTP `200` with `id`, `email`, `name`, or `401` if unauthenticated.
2. **Bookmarks (`backend/app/features/bookmarks/` & `frontend/src/features/bookmarks/`)**
   - `POST /api/bookmarks`: authenticated; accepts `url` (must start with `http://` or `https://`, otherwise `422`), `title`, optional `description`, optional `tags: list[str]`. Returns HTTP `201`.
   - `GET /api/bookmarks`: authenticated; supports `?q=<search>` (matches title, url, or description case-insensitively), `?tag=<tag>`, `?page=1&size=20`. Returns HTTP `200` `Page<Bookmark>` (`items`, `total`, `page`, `size`) strictly scoped to the current user.
   - `GET /api/bookmarks/{bookmark_id}`: authenticated; returns bookmark (`200`) or `404` if missing or owned by another user.
   - `PATCH /api/bookmarks/{bookmark_id}`: authenticated; updates `title`, `url`, `description`, or `tags` (`200`) or `404` if missing/not owned.
   - `DELETE /api/bookmarks/{bookmark_id}`: authenticated; returns `204` or `404` if missing/not owned.
3. **Tags (`backend/app/features/tags/`)**
   - `GET /api/tags`: authenticated; returns HTTP `200` `{"items": [{"name": "...", "count": N}]}` sorted alphabetically by `name`, computed strictly over the current user's bookmarks.

## Frontend UI (`frontend/src/features/bookmarks/`)
- Provide `feature.ts` exporting `navItems` with link to `/bookmarks` and a `BookmarkPage` React component exposing all required `data-testid` attributes in `pinned_testids.yaml`.
