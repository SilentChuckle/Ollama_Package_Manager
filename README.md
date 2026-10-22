# OP-Manager

Backup, restore, and manage your local Ollama models with ease.

OP-Manager is a lightweight Python desktop tool for local Ollama users who want to archive installed models, free up disk space, and restore them later without re-downloading large model files. It works directly with Ollama's local model storage and creates portable archives that preserve the model manifest and required blob data.

Whether you are experimenting with multiple models, managing a limited machine, or keeping backups of your favorite local setups, OP-Manager makes the process simple and safe.

## Overview

Ollama stores local models in a structured directory containing manifests and binary blobs. OP-Manager reads that local storage, lets you select models to archive, and creates `.tar` backup files that can be restored later.

This project is designed for:
- local AI enthusiasts
- developers using Ollama on personal machines
- researchers and hobbyists managing multiple models
- anyone who wants a simple backup system for local LLM assets

## Features

- Browse installed local Ollama models
- Archive selected models to a custom backup folder
- Optionally remove models from Ollama after a successful archive
- Restore archived models back into the Ollama model store
- Validate archive integrity during restore
- Work with custom Ollama environment variables such as `OLLAMA_MODELS` and `OLLAMA_HOST`
- Built with Python and Tkinter for a simple desktop workflow
- Supports local model backup and recovery without depending on remote downloads

## Why Use This?

Local language models can consume a large amount of storage space. If you are working with several models or switching between them frequently, manually managing them becomes tedious and error-prone.

OP-Manager solves that by giving you a clean workflow to:
- back up important local models
- free disk space by removing archived models
- restore them later when you need them
- keep your local Ollama environment organized and recoverable

## Requirements

- Python 3.8 or newer
- Ollama installed locally
- Access to the local Ollama model directory
- Tkinter (included with most official Python builds)

Optional:
- `ollama` command available in PATH for fallback model removal
- custom `OLLAMA_MODELS` or `OLLAMA_HOST` environment settings

## Installation

1. Download or clone the project:
   ```bash
   git clone https://github.com/yourusername/op-manager.git
   cd op-manager
