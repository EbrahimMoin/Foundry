"""Stack Pack #1: FastAPI + React (Vite/TS) + Postgres monorepo skeleton & conventions."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from foundry.sandbox.protocol import PackageInstallPolicy

_SAFE_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

_PYTHON_ALLOWLIST = frozenset(
    {
        "email-validator",
        "faker",
        "httpx",
        "pydantic",
        "pydantic-settings",
        "pytest-mock",
        "python-dateutil",
        "python-multipart",
        "python-slugify",
        "slugify",
        "structlog",
        "tenacity",
    }
)

_NPM_ALLOWLIST = frozenset(
    {
        "@hookform/resolvers",
        "clsx",
        "date-fns",
        "lucide-react",
        "react-hook-form",
        "recharts",
        "tailwind-merge",
        "zod",
    }
)

_PACKAGE_DENYLIST = frozenset({"ctx", "event-stream", "ua-parser-js-malware"})


def _slugify(name: str) -> str:
    cleaned = _SAFE_SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return cleaned or "foundry-app"


class FastAPIReactPostgresPack:
    """Reference StackPack implementing Foundry's Wave 0 FastAPI + React + Postgres layout."""

    name = "fastapi-react-postgres"
    sandbox_image = "foundry-sandbox-fastapi-react:local"

    def default_write_set(
        self,
        domain: str,
        feature: str | None = None,
        kind: str = "build",
    ) -> list[str]:
        feat = _slugify(feature) if feature else None
        if kind == "schema":
            return ["backend/app/models/", "backend/migrations/"]
        if kind == "integration":
            if not feat:
                raise ValueError("integration tasks require a feature name")
            return [
                f"backend/app/features/{feat}/",
                f"frontend/src/features/{feat}/",
            ]
        if domain == "backend":
            if not feat:
                return ["backend/app/core/", "backend/tests/"]
            return [f"backend/app/features/{feat}/"]
        if domain == "frontend":
            if not feat:
                return ["frontend/src/app/", "frontend/src/components/ui/"]
            return [f"frontend/src/features/{feat}/"]
        if domain == "infra":
            return ["infra/", ".github/workflows/"]
        if domain == "data":
            return ["backend/app/models/", "backend/migrations/"]
        raise ValueError(f"Unsupported domain {domain!r} for {self.name}")

    def check_commands(
        self,
        domain: str,
        write_set: Sequence[str] = (),
    ) -> list[list[str]]:
        targets = [p for p in write_set if p.startswith("backend/")]
        scope_args = targets if targets else ["backend/"]
        if domain in {"backend", "data"}:
            return [
                ["ruff", "check", *scope_args],
                ["mypy", *scope_args],
                ["pytest", "-q", "backend/tests"],
            ]
        if domain == "frontend":
            return [
                ["pnpm", "--dir", "frontend", "exec", "tsc", "--noEmit"],
                ["pnpm", "--dir", "frontend", "test", "--run"],
            ]
        if domain == "integration":
            return [
                ["ruff", "check", "backend/"],
                ["mypy", "backend/app"],
                ["pytest", "-q", "backend/tests"],
                ["pnpm", "--dir", "frontend", "exec", "tsc", "--noEmit"],
                ["pnpm", "--dir", "frontend", "test", "--run"],
            ]
        return []

    def curated_package_policy(self) -> PackageInstallPolicy:
        return PackageInstallPolicy(
            curated_python_allowlist=_PYTHON_ALLOWLIST,
            curated_npm_allowlist=_NPM_ALLOWLIST,
            denylist=_PACKAGE_DENYLIST,
        )

    def scaffold(self, target_dir: Path, app_name: str) -> list[Path]:
        """Materialize the Wave 0 monorepo skeleton at target_dir."""
        slug = _slugify(app_name)
        files: dict[str, str] = {
            "backend/pyproject.toml": self._backend_pyproject(slug),
            "backend/Dockerfile": _BACKEND_DOCKERFILE,
            "backend/alembic.ini": _ALEMBIC_INI,
            "backend/app/__init__.py": "",
            "backend/app/main.py": self._backend_main(app_name),
            "backend/app/core/__init__.py": "",
            "backend/app/core/config.py": _CORE_CONFIG_PY,
            "backend/app/core/db.py": _CORE_DB_PY,
            "backend/app/core/security.py": _CORE_SECURITY_PY,
            "backend/app/models/__init__.py": (
                "from app.models.base import Base\n\n__all__ = ['Base']\n"
            ),
            "backend/app/models/base.py": _MODELS_BASE_PY,
            "backend/app/features/__init__.py": "",
            "backend/migrations/env.py": _ALEMBIC_ENV_PY,
            "backend/migrations/script.py.mako": _ALEMBIC_SCRIPT_MAKO,
            "backend/migrations/versions/.gitkeep": "",
            "backend/tests/__init__.py": "",
            "backend/tests/conftest.py": _CONFTEST_PY,
            "frontend/package.json": self._frontend_package_json(slug),
            "frontend/tsconfig.json": _FRONTEND_TSCONFIG,
            "frontend/vite.config.ts": _VITE_CONFIG_TS,
            "frontend/index.html": self._frontend_index_html(app_name),
            "frontend/src/main.tsx": _FRONTEND_MAIN_TSX,
            "frontend/src/app/AppLayout.tsx": _APP_LAYOUT_TSX,
            "frontend/src/app/AuthProvider.tsx": _AUTH_PROVIDER_TSX,
            "frontend/src/app/router.tsx": _ROUTER_TSX,
            "frontend/src/components/ui/.gitkeep": "",
            "frontend/src/features/.gitkeep": "",
            "frontend/src/api/generated/.gitkeep": "",
            "contracts/openapi.yaml": self._starter_openapi(app_name),
            "contracts/schema.yaml": "version: 1\ntables: {}\n",
            "contracts/components.yaml": "version: 1\nroutes: []\ncomponents: {}\n",
            ".spectral.yaml": _SPECTRAL_YAML,
            "e2e/.gitkeep": "",
            "infra/compose.local.yml": self._compose_local_yml(slug),
            ".github/workflows/ci.yml": _CI_YML,
        }

        written: list[Path] = []
        for rel_path, content in files.items():
            dest = target_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written.append(dest)
        return written

    @staticmethod
    def _backend_pyproject(slug: str) -> str:
        return (
            "[project]\n"
            f'name = "{slug}-backend"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.12"\n'
            "dependencies = [\n"
            '  "alembic>=1.13",\n'
            '  "asyncpg>=0.30",\n'
            '  "fastapi>=0.115",\n'
            '  "pydantic>=2.7",\n'
            '  "pydantic-settings>=2.4",\n'
            '  "pyjwt>=2.9",\n'
            '  "sqlalchemy[asyncio]>=2.0",\n'
            '  "uvicorn[standard]>=0.30",\n'
            "]\n\n"
            "[tool.pytest.ini_options]\n"
            'pythonpath = ["."]\n'
            'asyncio_mode = "auto"\n\n'
            "[tool.ruff]\n"
            'target-version = "py312"\n'
            "line-length = 100\n"
            'src = ["."]\n\n'
            "[tool.ruff.lint]\n"
            'select = ["E", "F", "W", "I", "B"]\n\n'
            "[tool.mypy]\n"
            'python_version = "3.12"\n'
            "ignore_missing_imports = true\n"
        )

    @staticmethod
    def _backend_main(app_name: str) -> str:
        return f'''"""Auto-discovering FastAPI application entrypoint (frozen after Wave 1)."""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import FastAPI

app = FastAPI(title={app_name!r})


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {{"status": "ok"}}


def _register_feature_routers(application: FastAPI) -> None:
    features_pkg = importlib.import_module("app.features")
    for mod_info in pkgutil.iter_modules(features_pkg.__path__, "app.features."):
        router_mod_name = f"{{mod_info.name}}.router"
        try:
            mod = importlib.import_module(router_mod_name)
        except ModuleNotFoundError:
            continue
        router = getattr(mod, "router", None)
        if router is not None:
            application.include_router(router, prefix="/api")


_register_feature_routers(app)
'''

    @staticmethod
    def _frontend_package_json(slug: str) -> str:
        pkg = {
            "name": f"{slug}-frontend",
            "private": True,
            "version": "0.1.0",
            "type": "module",
            "scripts": {
                "dev": "vite",
                "build": "tsc && vite build",
                "test": "vitest",
            },
            "dependencies": {
                "react": "^18.3.1",
                "react-dom": "^18.3.1",
            },
            "devDependencies": {
                "@testing-library/react": "^16.0.0",
                "typescript": "^5.6.0",
                "vite": "^5.4.0",
                "vitest": "^2.1.0",
            },
        }
        return json.dumps(pkg, indent=2) + "\n"

    @staticmethod
    def _frontend_index_html(app_name: str) -> str:
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "  <head>\n"
            '    <meta charset="UTF-8" />\n'
            '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
            f"    <title>{app_name}</title>\n"
            "  </head>\n"
            "  <body>\n"
            '    <div id="root"></div>\n'
            '    <script type="module" src="/src/main.tsx"></script>\n'
            "  </body>\n"
            "</html>\n"
        )

    @staticmethod
    def _starter_openapi(app_name: str) -> str:
        return (
            "openapi: 3.1.0\n"
            "info:\n"
            f"  title: {app_name} API\n"
            '  version: "0.1.0"\n'
            "components:\n"
            "  securitySchemes:\n"
            "    bearerAuth:\n"
            "      type: http\n"
            "      scheme: bearer\n"
            "  schemas:\n"
            "    Error:\n"
            "      type: object\n"
            "      required: [detail]\n"
            "      properties:\n"
            "        detail:\n"
            "          type: string\n"
            "paths:\n"
            "  /api/health:\n"
            "    get:\n"
            "      operationId: healthCheck\n"
            "      security: []\n"
            "      x-foundry-rules: Always returns HTTP 200 when app process is healthy.\n"
            "      responses:\n"
            '        "200":\n'
            "          description: Service healthy\n"
        )

    @staticmethod
    def _compose_local_yml(slug: str) -> str:
        return (
            "services:\n"
            "  backend:\n"
            "    build: ../backend\n"
            "    environment:\n"
            f"      DATABASE_URL: postgresql+asyncpg://postgres:postgres@db:5432/{slug}\n"
            "    ports:\n"
            '      - "8000:8000"\n'
            "    depends_on:\n"
            "      - db\n"
            "  db:\n"
            "    image: postgres:16-alpine\n"
            "    environment:\n"
            "      POSTGRES_PASSWORD: postgres\n"
            f"      POSTGRES_DB: {slug}\n"
        )


_BACKEND_DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

_ALEMBIC_INI = """[alembic]
script_location = migrations
prepend_sys_path = .
"""

_CORE_CONFIG_PY = """from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/app"
    jwt_secret: str = "dev-only-secret"


settings = Settings()
"""

_CORE_DB_PY = """from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
"""

_CORE_SECURITY_PY = """from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict[str, str]:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return {"id": creds.credentials, "role": "member"}
"""

_MODELS_BASE_PY = """from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
"""

_ALEMBIC_ENV_PY = """from alembic import context

from app.models.base import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url="sqlite://", target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_offline()
"""

_ALEMBIC_SCRIPT_MAKO = """\"\"\"${message}\"\"\"

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
"""

_CONFTEST_PY = r'''"""Shared G3 fixtures with post-test OpenAPI request telemetry (A7-7)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from app.main import app

_TRACE_FILE = Path(".foundry_tmp/g3_request_trace.jsonl")
_LOCKED_OPENAPI_FILE = Path(".foundry_tmp/locked_openapi.json")


@dataclass
class RequestValidationRecord:
    method: str
    path: str
    valid: bool
    expect_invalid: bool = False
    error: str | None = None
    status_code: int | None = None
    test_nodeid: str = ""


@dataclass
class RequestValidationRecorder:
    records: list[RequestValidationRecord] = field(default_factory=list)

    def record(self, item: RequestValidationRecord) -> None:
        self.records.append(item)


def _load_openapi_spec() -> dict[str, Any]:
    if _LOCKED_OPENAPI_FILE.is_file():
        loaded = json.loads(_LOCKED_OPENAPI_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return app.openapi()


def _resolve_ref(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur: Any = spec
        for part in ref[2:].split("/"):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return {}
        if isinstance(cur, dict):
            return cur
    return schema


def _match_operation(
    spec: dict[str, Any], method: str, path: str
) -> tuple[str, dict[str, Any]] | None:
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return None
    m = method.lower()
    if path in paths and isinstance(paths[path], dict) and m in paths[path]:
        op = paths[path][m]
        return (path, op) if isinstance(op, dict) else None
    for tmpl, methods in paths.items():
        if not isinstance(methods, dict) or m not in methods:
            continue
        seg_parts = [
            "[^/]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg)
            for seg in str(tmpl).strip("/").split("/")
        ]
        regex = "^/" + "/".join(seg_parts) + "$"
        if str(tmpl) == "/":
            regex = "^/$"
        if re.match(regex, path):
            op = methods[m]
            if isinstance(op, dict):
                return str(tmpl), op
    return None


def _check_type(value: Any, expected_type: str | None) -> bool:
    if expected_type is None:
        return True
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _validate_outbound_request(
    spec: dict[str, Any],
    *,
    method: str,
    url: str,
    json_body: Any,
    params: Any,
) -> tuple[bool, str | None]:
    parsed = urlsplit(str(url))
    path = parsed.path or "/"
    matched = _match_operation(spec, method, path)
    if matched is None:
        return False, f"Undocumented operation {method.upper()} {path}"

    _, op = matched
    # Query parameters
    param_defs = op.get("parameters") or []
    if isinstance(param_defs, list):
        provided_params = dict(params) if isinstance(params, dict) else {}
        if parsed.query:
            for item in parsed.query.split("&"):
                if "=" in item:
                    k, _ = item.split("=", 1)
                    provided_params.setdefault(k, "")
        for pdef in param_defs:
            if not isinstance(pdef, dict):
                continue
            if (
                pdef.get("in") == "query"
                and pdef.get("required")
                and pdef.get("name") not in provided_params
            ):
                return False, f"Missing required query parameter '{pdef.get('name')}'"

    # JSON Request Body
    req_body = op.get("requestBody")
    if isinstance(req_body, dict):
        content = (req_body.get("content") or {}).get("application/json")
        if req_body.get("required") and json_body is None and isinstance(content, dict):
            return False, "Missing required JSON requestBody"
        if json_body is not None and isinstance(content, dict):
            schema = _resolve_ref(spec, content.get("schema"))
            if isinstance(json_body, dict):
                for req_field in schema.get("required") or []:
                    if req_field not in json_body:
                        return False, f"Missing required body field '{req_field}'"
                props = schema.get("properties") or {}
                if isinstance(props, dict):
                    for key, val in json_body.items():
                        if key in props and val is not None:
                            prop_schema = _resolve_ref(spec, props[key])
                            if not _check_type(val, prop_schema.get("type")):
                                return (
                                    False,
                                    f"Field '{key}' expected type {prop_schema.get('type')}",
                                )
    return True, None


class _InstrumentedAsyncClient(httpx.AsyncClient):
    """AsyncClient that records per-call OpenAPI validation traces without short-circuiting."""

    def __init__(
        self,
        *args: Any,
        recorder: RequestValidationRecorder,
        spec: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._recorder = recorder
        self._spec = spec

    async def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        expect_invalid = bool(kwargs.pop("expect_invalid", False))
        headers = dict(kwargs.get("headers") or {})
        for hk in list(headers):
            if hk.lower() == "x-foundry-expect-invalid":
                expect_invalid = str(headers.pop(hk)).lower() in {"1", "true", "yes"}
                kwargs["headers"] = headers

        valid, error = _validate_outbound_request(
            self._spec,
            method=method,
            url=str(url),
            json_body=kwargs.get("json"),
            params=kwargs.get("params"),
        )
        response = await super().request(method, url, *args, **kwargs)
        nodeid = os.environ.get("PYTEST_CURRENT_TEST", "").split(" ", 1)[0]
        path = urlsplit(str(url)).path or "/"
        rec = RequestValidationRecord(
            method=method.upper(),
            path=path,
            valid=valid,
            expect_invalid=expect_invalid,
            error=error,
            status_code=response.status_code,
            test_nodeid=nodeid,
        )
        self._recorder.record(rec)
        _TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "test_nodeid": nodeid,
            "method": rec.method,
            "path": path,
            "status_code": response.status_code,
            "request_valid": valid,
            "expect_invalid": expect_invalid,
            "validation_error": error,
            "response_body_excerpt": response.text[:300],
        }
        with _TRACE_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
        return response

    async def get(
        self, url: Any, *args: Any, expect_invalid: bool = False, **kwargs: Any
    ) -> httpx.Response:
        return await self.request("GET", url, *args, expect_invalid=expect_invalid, **kwargs)

    async def post(
        self, url: Any, *args: Any, expect_invalid: bool = False, **kwargs: Any
    ) -> httpx.Response:
        return await self.request("POST", url, *args, expect_invalid=expect_invalid, **kwargs)

    async def put(
        self, url: Any, *args: Any, expect_invalid: bool = False, **kwargs: Any
    ) -> httpx.Response:
        return await self.request("PUT", url, *args, expect_invalid=expect_invalid, **kwargs)

    async def patch(
        self, url: Any, *args: Any, expect_invalid: bool = False, **kwargs: Any
    ) -> httpx.Response:
        return await self.request("PATCH", url, *args, expect_invalid=expect_invalid, **kwargs)

    async def delete(
        self, url: Any, *args: Any, expect_invalid: bool = False, **kwargs: Any
    ) -> httpx.Response:
        return await self.request("DELETE", url, *args, expect_invalid=expect_invalid, **kwargs)


@pytest.fixture
def validation_recorder() -> RequestValidationRecorder:
    return RequestValidationRecorder()


@pytest.fixture
def seed_user():
    """Contract-defined helper allowing black-box G3 tests to create users per role."""

    def _seed(role: str = "member", email: str | None = None) -> dict[str, str]:
        return {"id": f"user_{role}", "role": role, "email": email or f"{role}@example.com"}

    return _seed


@pytest.fixture
def auth_headers():
    def _headers(user: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {user['id']}"}

    return _headers


@pytest.fixture
async def client(validation_recorder: RequestValidationRecorder) -> Any:
    spec = _load_openapi_spec()
    transport = httpx.ASGITransport(app=app)
    async with _InstrumentedAsyncClient(
        transport=transport,
        base_url="http://test",
        recorder=validation_recorder,
        spec=spec,
    ) as async_client:
        yield async_client
'''

_FRONTEND_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "react-jsx",
    "noEmit": true
  },
  "include": ["src"]
}
"""

_VITE_CONFIG_TS = """import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
"""

_FRONTEND_MAIN_TSX = """import React from "react";
import ReactDOM from "react-dom/client";
import { AppLayout } from "./app/AppLayout";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppLayout />
  </React.StrictMode>
);
"""

_APP_LAYOUT_TSX = """import React from "react";

interface FeatureRegistryModule {
  navItems?: Array<{ label: string; path: string; testId?: string }>;
}

const featureModules = import.meta.glob<FeatureRegistryModule>("../features/*/feature.ts", {
  eager: true,
});

export function AppLayout() {
  const navItems = Object.values(featureModules).flatMap((m) => m.navItems ?? []);
  return (
    <div data-testid="app-shell">
      <nav data-testid="main-nav">
        {navItems.map((item) => (
          <a key={item.path} href={item.path} data-testid={item.testId}>
            {item.label}
          </a>
        ))}
      </nav>
      <main data-testid="main-content" />
    </div>
  );
}
"""

_AUTH_PROVIDER_TSX = """import React, { createContext, useContext, useState } from "react";

interface AuthContextValue {
  token: string | null;
  setToken: (tok: string | null) => void;
}

const AuthContext = createContext<AuthContextValue>({
  token: null,
  setToken: () => undefined,
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  return <AuthContext.Provider value={{ token, setToken }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
"""

_ROUTER_TSX = """import React from "react";

export function AppRouter() {
  return <div data-testid="router-outlet" />;
}
"""

_SPECTRAL_YAML = """extends: ["spectral:oas"]
rules:
  paths-kebab-api-prefix:
    description: All API paths must live under /api
    given: "$.paths[*]~"
    severity: error
    then:
      function: pattern
      functionOptions:
        match: "^/api/"
  operation-security-required:
    description: Every operation must declare security (or security: [])
    given: "$.paths[*][get,post,put,patch,delete]"
    severity: error
    then:
      field: security
      function: defined
"""

_CI_YML = """name: ci
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "Run backend & frontend gates"
"""
