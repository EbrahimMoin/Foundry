"""Pydantic v2 contract models for DbSchema, OpenAPI, Components, and Deps."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from foundry.verification.models import ContractViolation


class ColumnSpec(BaseModel):
    name: str
    type: str
    nullable: bool = False
    primary_key: bool = False
    unique: bool = False
    references: str | None = None


class TableSpec(BaseModel):
    columns: list[ColumnSpec] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)


class DbSchemaContract(BaseModel):
    """Data Lead `db_schema` contract."""

    version: int = 1
    tables: dict[str, TableSpec] = Field(default_factory=dict)


class OpenAPIContract(BaseModel):
    """Backend Lead `api` contract (validated with `x-foundry-rules` & pinned pointers)."""

    openapi: str = "3.1.0"
    info: dict[str, Any] = Field(default_factory=lambda: {"title": "API", "version": "0.1.0"})
    components: dict[str, Any] = Field(default_factory=dict)
    paths: dict[str, Any] = Field(default_factory=dict)


class RouteSpec(BaseModel):
    path: str
    component: str
    label: str = ""
    nav: bool = False
    test_id: str | None = None


class ComponentSpec(BaseModel):
    export_path: str
    props: dict[str, str] = Field(default_factory=dict)
    emits: list[str] = Field(default_factory=list)
    testids: list[str] = Field(default_factory=list)


class ComponentContract(BaseModel):
    """Frontend Lead `ui_components` contract."""

    version: int = 1
    routes: list[RouteSpec] = Field(default_factory=list)
    components: dict[str, ComponentSpec] = Field(default_factory=dict)


class DepsContract(BaseModel):
    """Infra Lead `deps` + CI contract."""

    python_packages: list[str] = Field(default_factory=list)
    npm_packages: list[str] = Field(default_factory=list)
    ci_workflow: str = ".github/workflows/ci.yml"
    deploy_target: str = "local_compose"


class ContractValidationResult(BaseModel):
    """Deterministic Stage-4 staged-contract validation report."""

    valid: bool
    violations: list[ContractViolation] = Field(default_factory=list)
