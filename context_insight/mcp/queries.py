"""Read helpers backing the MCP tools. Thin JSON-shaping wrappers over db.repository."""
from __future__ import annotations

import json
import sqlite3

from context_insight.db import repository


def _fmt_call(c: sqlite3.Row) -> dict:
    return {
        "to_service_name": c["to_service_name"],
        "call_kind": c["call_kind"],
        "reason": c["reason"],
        "data_needed": json.loads(c["data_needed"] or "[]"),
        "purpose_kind": c["purpose_kind"],
    }


def list_services(conn: sqlite3.Connection) -> dict:
    rows = repository.list_services(conn)
    return {
        "services": [
            {"name": r["name"], "short_desc": r["short_desc"], "stack": r["stack"], "api_count": r["api_count"]}
            for r in rows
        ]
    }


def describe_service(conn: sqlite3.Connection, service: str) -> dict:
    row = repository.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    calls = repository.list_calls_for_service(conn, row["id"])
    apis = repository.list_apis(conn, row["id"])
    persistence = repository.list_persistence(conn, row["id"])
    messages = repository.list_messages(conn, row["id"])
    return {
        "name": row["name"],
        "short_desc": row["short_desc"],
        "long_desc": row["long_desc"],
        "stack": row["stack"],
        "calls": [_fmt_call(c) for c in calls],
        "apis": [{"method": a["method"], "path": a["path"], "summary": a["summary"]} for a in apis],
        "persists": [{"name": p["name"], "kind": p["kind"]} for p in persistence],
        "messages": [
            {"direction": m["direction"], "channel": m["channel"], "description": m["description"]} for m in messages
        ],
    }


def list_apis(conn: sqlite3.Connection, service: str) -> dict:
    row = repository.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    apis = repository.list_apis(conn, row["id"])
    return {"apis": [{"method": a["method"], "path": a["path"], "summary": a["summary"]} for a in apis]}


def describe_api(conn: sqlite3.Connection, service: str, method: str, path: str) -> dict:
    row = repository.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    api = repository.get_api_by_key(conn, row["id"], method.upper(), path)
    if api is None:
        return {"error": f"unknown api: {method} {path} on {service}"}
    calls = repository.list_calls_for_api(conn, api["id"])
    validations = repository.list_validations_for_api(conn, api["id"])
    return {
        "method": api["method"],
        "path": api["path"],
        "summary": api["summary"],
        "description": api["description"],
        "response_shape": json.loads(api["response_shape"] or "[]"),
        "calls": [_fmt_call(c) for c in calls],
        "validations": [{"kind": v["kind"], "description": v["description"]} for v in validations],
    }


def describe_persistence(conn: sqlite3.Connection, service: str) -> dict:
    row = repository.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    entities = repository.list_persistence(conn, row["id"])
    return {
        "entities": [
            {"name": e["name"], "kind": e["kind"], "schema_json": json.loads(e["schema_json"] or "[]")}
            for e in entities
        ]
    }


def describe_messages(conn: sqlite3.Connection, service: str) -> dict:
    row = repository.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    messages = repository.list_messages(conn, row["id"])
    return {
        "messages": [
            {
                "direction": m["direction"],
                "channel": m["channel"],
                "shape_json": json.loads(m["shape_json"] or "[]"),
                "description": m["description"],
            }
            for m in messages
        ]
    }


def search(conn: sqlite3.Connection, query: str) -> dict:
    return {"results": repository.search(conn, query)}
