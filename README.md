# Ollama Package Manager

Back up, restore and manage your local Ollama models.

Ollama Package Manager is a lightweight Python desktop app for local Ollama users who want to archive installed models, free disk space, and restore them later without re-downloading large files. It works directly with Ollama's local model storage and creates portable `.tar` archives containing the manifest and blobs.

## Features

- Browse installed local Ollama models with size and date
- Archive selected models to a backup folder
- Optionally remove a model from Ollama after it has been archived
- Delete models through Ollama itself, so shared blobs are reference-counted
- Restore archives into the Ollama model store, verifying every blob's SHA-256
- Honors `OLLAMA_MODELS` and `OLLAMA_HOST`
- Standard library only (Tkinter for the window)

## Requirements

- Python 3.8 or newer (Tkinter is included with standard installs)
- Ollama installed locally

## Usage

```bash
git clone https://github.com/Merely-Human/OP-Manager.git
cd OP-Manager
python Ollama_Package_Manager.py
```

**Archive:** Refresh, tick one or more models, choose the archive folder, click Archive Checked. Optionally enable removal from Ollama after a successful archive.

**Restore:** click Restore Archive(s), choose the `.tar` files. Archives are validated, blobs are checksum-verified, and manifests are written last so a model never appears before its blobs exist.

## Archive format

- `ollama_archive.json`: model metadata
- `manifests/<host>/<namespace>/<model>/<tag>`: model manifests
- `blobs/sha256-<hex>`: model data

## Safety

- Archive members are validated: no absolute paths, no `..`, only `manifests/` and `blobs/`.
- Removing or deleting models always asks for confirmation first.
- Existing blobs are never overwritten on restore; corrupt blobs are rejected by checksum.
- Only the local Ollama API (127.0.0.1) is contacted.

## Troubleshooting

- **Model missing after restore:** check that the archive is complete and that `OLLAMA_MODELS` points to the expected folder.
- **Archive integrity warning:** the archive is incomplete or was altered. Recreate it and retry.

## License

MIT. See [LICENSE](LICENSE).
