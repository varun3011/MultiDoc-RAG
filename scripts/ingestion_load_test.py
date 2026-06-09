from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _validate_http_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"Refusing non-HTTP URL: {url}")


def _json_request(
    *,
    method: str,
    url: str,
    token: str,
    request_timeout_seconds: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_http_url(url)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=request_timeout_seconds) as response:  # nosec B310
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with {exc.code}: {detail}") from exc
    return json.loads(response_body) if response_body else {}


def _put_file(url: str, path: Path) -> None:
    _validate_http_url(url)
    request = urllib.request.Request(
        url,
        data=path.read_bytes(),
        method="PUT",
        headers={"Content-Type": "application/pdf"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PUT upload failed with {exc.code}: {detail}") from exc


def _redact_upload_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key == "upload_url" else _redact_upload_urls(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_upload_urls(item) for item in value]
    return value


def _pdf_bytes(title: str, body: str) -> bytes:
    stream = (
        "BT\n"
        "/F1 12 Tf\n"
        "72 740 Td\n"
        f"({title}) Tj\n"
        "0 -24 Td\n"
        f"({body}) Tj\n"
        "ET\n"
    ).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Length "
        + str(len(stream)).encode("ascii")
        + b" >> stream\n"
        + stream
        + b"endstream\nendobj\n",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(content))
        content.extend(obj)
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(content)


def _build_dataset(directory: Path, count: int, scenario: str) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for index in range(count):
        filename = f"load-{scenario}-{index + 1:03d}.pdf"
        path = directory / filename
        text = (
            f"Load test document {index + 1}. "
            f"Scenario {scenario}. "
            "This document contains extractable text for ingestion benchmarking."
        )
        path.write_bytes(_pdf_bytes(filename, text))
        files.append(path)
    if scenario == "mixed" and files:
        invalid_path = directory / "load-mixed-invalid.txt"
        invalid_path.write_text("This is intentionally not a PDF.", encoding="utf-8")
        files[-1] = invalid_path
    return files


def _terminal(status_value: str) -> bool:
    return status_value in {"completed", "partial", "failed"}


def run_load_test(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token or os.getenv("RAG_BEARER_TOKEN")
    if not token:
        raise RuntimeError("Provide --token or set RAG_BEARER_TOKEN")

    api_base = args.api_base.rstrip("/")
    started_at = time.perf_counter()
    generated_at = datetime.now(timezone.utc).isoformat()
    dataset_dir = Path(args.dataset_dir)
    files = _build_dataset(dataset_dir, args.count, args.scenario)

    prepare_payload = {
        "name": f"load-{args.scenario}-{args.count}-{generated_at}",
        "files": [
            {
                "filename": path.name,
                "content_type": "application/pdf",
                "file_size_bytes": path.stat().st_size,
                "client_file_id": path.stem,
                "idempotency_key": f"{args.scenario}-{args.count}-{path.name}-{generated_at}",
            }
            for path in files
        ],
    }
    prepare_started = time.perf_counter()
    prepare = _json_request(
        method="POST",
        url=f"{api_base}/documents/upload-prepare-batch",
        token=token,
        request_timeout_seconds=args.request_timeout_seconds,
        payload=prepare_payload,
    )
    upload_items = []
    file_by_name = {path.name: path for path in files}
    for item in prepare.get("items", []):
        if item.get("status") not in {"prepared", "accepted"}:
            continue
        path = file_by_name[str(item["filename"])]
        _put_file(str(item["upload_url"]), path)
        upload_items.append(
            {
                "document_id": item["document_id"],
                "bucket": item["bucket"],
                "storage_path": item["storage_path"],
            }
        )

    complete = _json_request(
        method="POST",
        url=f"{api_base}/documents/upload-complete-batch",
        token=token,
        request_timeout_seconds=args.request_timeout_seconds,
        payload={"ingestion_run_id": prepare.get("ingestion_run_id"), "files": upload_items},
    )
    prepare_upload_complete_seconds = round(time.perf_counter() - prepare_started, 3)

    queue_snapshots: list[dict[str, Any]] = []
    run: dict[str, Any] = {}
    deadline = time.perf_counter() + args.timeout_seconds
    while time.perf_counter() < deadline:
        if prepare.get("ingestion_run_id"):
            run = _json_request(
                method="GET",
                url=f"{api_base}/documents/ingestion-runs/{prepare['ingestion_run_id']}",
                token=token,
                request_timeout_seconds=args.request_timeout_seconds,
            )
        queues = _json_request(
            method="GET",
            url=f"{api_base}/documents/ingestion-queues",
            token=token,
            request_timeout_seconds=args.request_timeout_seconds,
        )
        queue_snapshots.append(
            {
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                "queues": queues.get("queues", []),
                "run_status": run.get("status"),
                "document_statuses": run.get("document_statuses"),
            }
        )
        if run and _terminal(str(run.get("status"))):
            break
        time.sleep(args.poll_seconds)

    documents = _json_request(
        method="GET",
        url=f"{api_base}/documents?limit=100",
        token=token,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    total_seconds = round(time.perf_counter() - started_at, 3)
    result = {
        "generated_at": generated_at,
        "api_base": api_base,
        "scenario": args.scenario,
        "requested_count": args.count,
        "ingestion_run_id": prepare.get("ingestion_run_id"),
        "prepare": _redact_upload_urls(prepare),
        "complete": complete,
        "final_run": run,
        "documents": documents,
        "queue_snapshots": queue_snapshots,
        "durations_seconds": {
            "prepare_upload_complete": prepare_upload_complete_seconds,
            "total": total_seconds,
        },
    }

    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / (
        f"ingestion-load-{args.scenario}-{args.count}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    artifact_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    result["artifact_path"] = str(artifact_path)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a repeatable ingestion load test.")
    parser.add_argument("--api-base", default=os.getenv("RAG_API_BASE", "http://localhost:8000"))
    parser.add_argument("--token", default=None, help="Bearer token; defaults to RAG_BEARER_TOKEN")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--scenario", choices=["valid", "mixed"], default="valid")
    parser.add_argument("--dataset-dir", default="artifacts/ingestion-load/dataset")
    parser.add_argument("--artifact-dir", default="artifacts/ingestion-load")
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--request-timeout-seconds", type=float, default=180)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser.parse_args()


if __name__ == "__main__":
    summary = run_load_test(parse_args())
    print(json.dumps({"artifact_path": summary["artifact_path"], "final_run": summary["final_run"]}, indent=2))
