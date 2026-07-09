# Paperless Markdown Ingest

A small Docker worker that treats a local folder like an ingest queue for Paperless-ngx and Markdown notes.

It watches a consume folder, converts supported documents to Markdown with Microsoft MarkItDown, uploads the original document to Paperless-ngx through the API, and moves the original to an archive or failed folder.

When enabled, it can also recreate the Obsidian Paperless plugin's link format by creating a Paperless share link, writing a `paperless-<document-id>.pdf` URL stub into an attachments folder, and embedding `![[paperless-<document-id>.pdf]]` in the generated Markdown.

## Build

```bash
docker build -t paperless-markdown-ingest:latest .
```

## Required Configuration

Create a Paperless-ngx API token for your user and provide it through environment variables:

```text
PAPERLESS_URL=https://paperless.example.com
PAPERLESS_TOKEN=your_api_token
```

## Volumes

Mount host folders into the container:

```text
/consume               Incoming documents to process
/archive               Originals after successful processing
/failed                Originals that failed conversion or upload
/obsidian-output       Generated Markdown notes
/paperless-attachments Optional Paperless share-link stubs for Obsidian
```

Example:

```bash
docker run -d \
  --name paperless-markdown-ingest \
  -e PAPERLESS_URL=https://paperless.example.com \
  -e PAPERLESS_TOKEN=your_api_token \
  -v /path/to/consume:/consume \
  -v /path/to/archive:/archive \
  -v /path/to/failed:/failed \
  -v /path/to/markdown-output:/obsidian-output \
  -v /path/to/attachments:/paperless-attachments \
  ghcr.io/fastrack20/paperless-markdown-ingest:latest
```

## Environment Variables

```text
PAPERLESS_URL                  Paperless-ngx base URL
PAPERLESS_TOKEN                Paperless-ngx API token
POLL_INTERVAL_SECONDS          Folder scan interval, default 60
STABILITY_SECONDS              Time a file must remain unchanged before processing, default 10
EXTENSIONS                     Comma-separated extensions to process, default .pdf
UPLOAD_TO_PAPERLESS            Upload originals to Paperless, default true
WRITE_MARKDOWN                 Write MarkItDown output, default true
CREATE_OBSIDIAN_PAPERLESS_LINK Create Paperless share-link stubs and embeds, default true
DELETE_AFTER_SUCCESS           Delete originals instead of archiving them, default false
```
