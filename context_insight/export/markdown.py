from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from context_insight.db import repository


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "root"


def _fmt_calls(calls: list[sqlite3.Row]) -> list[str]:
    lines = []
    for c in calls:
        data_needed = ", ".join(json.loads(c["data_needed"] or "[]"))
        line = f"- **{c['to_service_name']}** ({c['call_kind']}, {c['purpose_kind']}): {c['reason']}"
        if data_needed:
            line += f" — precisa de: {data_needed}"
        lines.append(line)
    return lines or ["- (nenhuma dependência detectada)"]


def export_markdown(conn: sqlite3.Connection, out_dir: Path, service_filter: str | None = None) -> list[Path]:
    written: list[Path] = []
    for svc in repository.list_services(conn):
        if service_filter and svc["name"] != service_filter:
            continue
        service_row = repository.get_service_by_name(conn, svc["name"])
        service_dir = out_dir / svc["name"]
        service_dir.mkdir(parents=True, exist_ok=True)

        calls = repository.list_calls_for_service(conn, svc["id"])
        apis = repository.list_apis(conn, svc["id"])
        persistence = repository.list_persistence(conn, svc["id"])
        messages = repository.list_messages(conn, svc["id"])

        lines = [
            f"# {svc['name']}",
            "",
            service_row["short_desc"] or "",
            "",
            service_row["long_desc"] or "",
            "",
            f"**Stack:** {svc['stack'] or '?'}",
            "",
            "## Depende de",
            *_fmt_calls(calls),
            "",
            "## APIs",
        ]
        if apis:
            for a in apis:
                slug = _slug(f"{a['method']}-{a['path']}")
                lines.append(f"- `{a['method']} {a['path']}` — {a['summary'] or ''} ([detalhe](apis/{slug}.md))")
        else:
            lines.append("- (nenhuma API detectada)")

        lines += ["", "## Persistência"]
        lines += [f"- **{p['name']}** ({p['kind']})" for p in persistence] or ["- (nada detectado)"]

        lines += ["", "## Mensageria"]
        if messages:
            lines += [f"- **{m['channel']}** ({m['direction']}): {m['description'] or ''}" for m in messages]
        else:
            lines.append("- (nada detectado)")

        index_path = service_dir / "index.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(index_path)

        if apis:
            apis_dir = service_dir / "apis"
            apis_dir.mkdir(parents=True, exist_ok=True)
            for a in apis:
                api_row = repository.get_api_by_key(conn, svc["id"], a["method"], a["path"])
                api_calls = repository.list_calls_for_api(conn, api_row["id"])
                validations = repository.list_validations_for_api(conn, api_row["id"])
                response_shape = json.loads(api_row["response_shape"] or "[]")

                api_lines = [f"# {a['method']} {a['path']}", "", api_row["description"] or "", "", "## Resposta"]
                api_lines += [f"- `{f['field']}`: {f['type_desc']}" for f in response_shape] or ["(não detectado)"]
                api_lines += ["", "## Chamadas", *_fmt_calls(api_calls)]
                api_lines += ["", "## Validações / Restrições"]
                if validations:
                    api_lines += [f"- [{v['kind']}] {v['description']}" for v in validations]
                else:
                    api_lines.append("(nenhuma detectada)")

                slug = _slug(f"{a['method']}-{a['path']}")
                api_path = apis_dir / f"{slug}.md"
                api_path.write_text("\n".join(api_lines) + "\n", encoding="utf-8")
                written.append(api_path)

    return written
