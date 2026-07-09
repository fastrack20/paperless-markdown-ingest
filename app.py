import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from markitdown import MarkItDown


CONSUME_DIR = Path(os.getenv("CONSUME_DIR", "/consume"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/archive"))
FAILED_DIR = Path(os.getenv("FAILED_DIR", "/failed"))
OBSIDIAN_OUTPUT_DIR = Path(os.getenv("OBSIDIAN_OUTPUT_DIR", "/obsidian-output"))
PAPERLESS_ATTACHMENT_DIR = Path(os.getenv("PAPERLESS_ATTACHMENT_DIR", "/paperless-attachments"))

PAPERLESS_URL = os.getenv("PAPERLESS_URL", "").rstrip("/")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
STABILITY_SECONDS = int(os.getenv("STABILITY_SECONDS", "10"))
UPLOAD_TO_PAPERLESS = os.getenv("UPLOAD_TO_PAPERLESS", "true").lower() in {"1", "true", "yes"}
WRITE_MARKDOWN = os.getenv("WRITE_MARKDOWN", "true").lower() in {"1", "true", "yes"}
CREATE_OBSIDIAN_PAPERLESS_LINK = os.getenv("CREATE_OBSIDIAN_PAPERLESS_LINK", "true").lower() in {"1", "true", "yes"}
DELETE_AFTER_SUCCESS = os.getenv("DELETE_AFTER_SUCCESS", "false").lower() in {"1", "true", "yes"}
EXTENSIONS = {
    ext.strip().lower()
    for ext in os.getenv("EXTENSIONS", ".pdf").split(",")
    if ext.strip()
}


def log(message):
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


def require_config():
    if UPLOAD_TO_PAPERLESS and (not PAPERLESS_URL or not PAPERLESS_TOKEN):
        raise SystemExit("PAPERLESS_URL and PAPERLESS_TOKEN are required when UPLOAD_TO_PAPERLESS=true")
    for path in (CONSUME_DIR, ARCHIVE_DIR, FAILED_DIR, OBSIDIAN_OUTPUT_DIR, PAPERLESS_ATTACHMENT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def slugify(value):
    value = re.sub(r"[^\w\s.-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:140] or "document"


def unique_path(directory, filename):
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 10000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find unique path for {filename}")


def stable_file(path):
    first = path.stat()
    time.sleep(STABILITY_SECONDS)
    second = path.stat()
    return first.st_size == second.st_size and first.st_mtime == second.st_mtime


def write_markdown(path, paperless=None):
    result = MarkItDown().convert(str(path))
    now = datetime.now(timezone.utc).isoformat()
    title = path.stem
    markdown_name = f"{datetime.now().strftime('%Y-%m-%d')} - {slugify(title)}.md"
    markdown_path = unique_path(OBSIDIAN_OUTPUT_DIR, markdown_name)
    body = result.text_content or ""
    paperless = paperless or {}
    paperless_frontmatter = ""
    paperless_embed = ""
    if paperless.get("document_id"):
        paperless_frontmatter += f"paperless_document_id: {paperless['document_id']}\n"
    if paperless.get("share_url"):
        paperless_frontmatter += f'paperless_share_url: "{paperless["share_url"]}"\n'
    if paperless.get("embed"):
        paperless_frontmatter += f'paperless_embed: "{paperless["embed"]}"\n'
        paperless_embed = f"{paperless['embed']}\n\n"
    markdown_path.write_text(
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f'source_file: "{path.name.replace(chr(34), chr(39))}"\n'
        f"parsed_at: {now}\n"
        f"{paperless_frontmatter}"
        "source: paperless-api-consumer\n"
        "tags:\n"
        "  - parsed-document\n"
        "---\n\n"
        f"{paperless_embed}"
        f"# {title}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return markdown_path


def upload_to_paperless(path):
    endpoint = f"{PAPERLESS_URL}/api/documents/post_document/"
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}
    with path.open("rb") as handle:
        response = requests.post(
            endpoint,
            headers=headers,
            files={"document": (path.name, handle, "application/pdf")},
            data={"title": path.stem},
            timeout=120,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Paperless upload failed: HTTP {response.status_code} {response.text[:500]}")
    return response.text.strip().strip('"')


def paperless_headers():
    return {"Authorization": f"Token {PAPERLESS_TOKEN}"}


def get_json(url, **params):
    response = requests.get(url, headers=paperless_headers(), params=params, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(f"Paperless GET failed: HTTP {response.status_code} {response.text[:500]}")
    return response.json()


def find_paperless_document_id(title, filename, attempts=24, delay=5):
    search_url = f"{PAPERLESS_URL}/api/documents/"
    for attempt in range(attempts):
        data = get_json(search_url, format="json", title_content=title)
        candidates = data.get("results") or []
        if not candidates and data.get("all"):
            for document_id in sorted(data["all"], reverse=True)[:10]:
                candidates.append(get_json(f"{PAPERLESS_URL}/api/documents/{document_id}/"))

        best = None
        for document in candidates:
            document_id = document.get("id")
            if not document_id:
                continue
            doc_title = str(document.get("title") or "")
            original = str(document.get("original_file_name") or document.get("filename") or "")
            archive = str(document.get("archive_filename") or "")
            if doc_title == title or filename in {original, Path(archive).name}:
                return document_id
            best = document_id if best is None else max(best, document_id)

        if best is not None:
            return best
        if attempt < attempts - 1:
            time.sleep(delay)
    return None


def get_existing_share_url(document_id):
    data = get_json(f"{PAPERLESS_URL}/api/documents/{document_id}/share_links/", format="json")
    for share in data:
        if share.get("expiration") is None and share.get("slug"):
            return f"{PAPERLESS_URL}/share/{share['slug']}"
    return None


def create_share_link(document_id):
    response = requests.post(
        f"{PAPERLESS_URL}/api/share_links/",
        headers={**paperless_headers(), "Content-Type": "application/json"},
        json={"document": document_id, "file_version": "original"},
        timeout=60,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Paperless share link failed: HTTP {response.status_code} {response.text[:500]}")


def ensure_share_url(document_id):
    share_url = get_existing_share_url(document_id)
    if not share_url:
        create_share_link(document_id)
        for _ in range(5):
            share_url = get_existing_share_url(document_id)
            if share_url:
                break
            time.sleep(1)
    return share_url


def create_obsidian_paperless_stub(document_id, share_url):
    filename = f"paperless-{document_id}.pdf"
    target = PAPERLESS_ATTACHMENT_DIR / filename
    if not target.exists():
        target.write_text(share_url, encoding="utf-8")
    return f"![[{filename}]]"


def move_finished(path, destination_dir):
    if DELETE_AFTER_SUCCESS and destination_dir == ARCHIVE_DIR:
        path.unlink()
        return None
    destination = unique_path(destination_dir, path.name)
    shutil.move(str(path), str(destination))
    return destination


def process(path):
    log(f"Processing {path.name}")
    markdown_path = None
    upload_result = None
    paperless = {}

    if UPLOAD_TO_PAPERLESS:
        upload_result = upload_to_paperless(path)
        log(f"Uploaded to Paperless: {upload_result or 'accepted'}")
        if CREATE_OBSIDIAN_PAPERLESS_LINK:
            document_id = find_paperless_document_id(path.stem, path.name)
            if document_id:
                share_url = ensure_share_url(document_id)
                if share_url:
                    embed = create_obsidian_paperless_stub(document_id, share_url)
                    paperless = {"document_id": document_id, "share_url": share_url, "embed": embed}
                    log(f"Created Obsidian Paperless link: {embed}")
                else:
                    log(f"Paperless document {document_id} has no share URL")
            else:
                log("Could not find uploaded Paperless document ID")

    if WRITE_MARKDOWN:
        markdown_path = write_markdown(path, paperless)
        log(f"Wrote Markdown: {markdown_path}")

    finished = move_finished(path, ARCHIVE_DIR)
    if finished:
        log(f"Archived original: {finished}")
    else:
        log("Deleted original after successful processing")


def scan_once():
    for path in sorted(CONSUME_DIR.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            if not stable_file(path):
                log(f"Skipping unstable file for now: {path.name}")
                continue
            process(path)
        except Exception as exc:
            log(f"Failed {path.name}: {exc}")
            try:
                failed = move_finished(path, FAILED_DIR)
                log(f"Moved failed file: {failed}")
            except Exception as move_exc:
                log(f"Could not move failed file {path}: {move_exc}")


def main():
    require_config()
    log(f"Watching {CONSUME_DIR} for: {', '.join(sorted(EXTENSIONS))}")
    while True:
        scan_once()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
