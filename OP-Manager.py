"""Ollama Local Model Manager.

Archive installed Ollama models to a backup folder (optionally removing them
from Ollama afterwards) and restore them from those archives later.

Archive layout (plain .tar, mirrors the Ollama models directory):
    ollama_archive.json                      metadata (model name, digests)
    manifests/<host>/<namespace>/<model>/<tag>
    blobs/sha256-<hex>
"""

import hashlib
import json
import os
import queue
import shutil
import subprocess
import tarfile
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

DEFAULT_HOST = "registry.ollama.ai"
DEFAULT_NAMESPACE = "library"
METADATA_NAME = "ollama_archive.json"
ARCHIVE_VERSION = 1
CHUNK = 4 * 1024 * 1024


def human_size(num):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024


def ollama_api_base():
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return "http://127.0.0.1:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    host = host.rstrip("/").replace("0.0.0.0", "127.0.0.1")
    if host.count(":") < 2:  # no port given
        host += ":11434"
    return host


def get_models_dir():
    if os.environ.get("OLLAMA_MODELS"):
        return Path(os.environ["OLLAMA_MODELS"])
    return Path.home() / ".ollama" / "models"


def parse_model_name(name):
    """Split an Ollama model name into (host, namespace, model, tag)."""
    base, tag = name, "latest"
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        base, tag = name.rsplit(":", 1)
    parts = base.split("/")
    if len(parts) == 1:
        return DEFAULT_HOST, DEFAULT_NAMESPACE, parts[0], tag
    if len(parts) == 2:
        return DEFAULT_HOST, parts[0], parts[1], tag
    return parts[0], "/".join(parts[1:-1]), parts[-1], tag


def display_name(host, namespace, model, tag):
    if host == DEFAULT_HOST and namespace == DEFAULT_NAMESPACE:
        return f"{model}:{tag}"
    if host == DEFAULT_HOST:
        return f"{namespace}/{model}:{tag}"
    return f"{host}/{namespace}/{model}:{tag}"


def safe_member_path(name):
    """Return a validated relative path for an archive member, or None."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    if p.parts[0] not in ("manifests", "blobs"):
        return None
    if p.parts[0] == "blobs" and (len(p.parts) != 2 or not p.parts[1].startswith("sha256-")):
        return None
    return p


class OllamaStore:
    """Read/write access to the local Ollama models directory and API."""

    def __init__(self):
        self.models_dir = get_models_dir()
        self.api = ollama_api_base()

    def api_request(self, path, method="GET", body=None, timeout=5):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

    def server_running(self):
        try:
            self.api_request("/api/version", timeout=2)
            return True
        except Exception:
            return False

    def manifest_path(self, name):
        host, ns, model, tag = parse_model_name(name)
        return self.models_dir.joinpath("manifests", host, *ns.split("/"), model, tag)

    def blob_path(self, digest):
        return self.models_dir / "blobs" / digest.replace(":", "-")

    def read_manifest(self, name):
        path = self.manifest_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"Manifest not found:\n{path}")
        with open(path, "r", encoding="utf-8") as f:
            return path, json.load(f)

    @staticmethod
    def manifest_digests(manifest):
        layers = []
        if manifest.get("config", {}).get("digest"):
            layers.append(manifest["config"])
        layers.extend(l for l in manifest.get("layers", []) if l.get("digest"))
        return layers

    def list_models(self):
        """Return list of dicts: name, size, modified. Disk is the source of truth."""
        models = {}
        base = self.models_dir / "manifests"
        if base.is_dir():
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                parts = path.relative_to(base).parts
                if len(parts) < 4:
                    continue
                name = display_name(parts[0], "/".join(parts[1:-2]), parts[-2], parts[-1])
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    size = sum(l.get("size", 0) for l in self.manifest_digests(manifest))
                except Exception:
                    size = 0
                modified = datetime.fromtimestamp(path.stat().st_mtime)
                models[name] = {"name": name, "size": size, "modified": modified}
        return sorted(models.values(), key=lambda m: m["name"].lower())

    def remove_model(self, name):
        """Remove a model via Ollama itself so shared blobs are reference-counted."""
        if self.server_running():
            try:
                self.api_request("/api/delete", method="DELETE", body={"model": name}, timeout=60)
                return
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Ollama refused to delete {name}: HTTP {e.code} {e.read().decode(errors='ignore')}")
        exe = shutil.which("ollama")
        if not exe:
            raise RuntimeError("Ollama server is not running and the 'ollama' command was not found, "
                               "so the model could not be removed. Start Ollama and try again.")
        result = subprocess.run([exe, "rm", name], capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise RuntimeError(f"'ollama rm {name}' failed:\n{result.stderr or result.stdout}")

    def archive_model(self, name, output_dir, progress):
        manifest_path, manifest = self.read_manifest(name)
        layers = self.manifest_digests(manifest)

        missing = [l["digest"] for l in layers if not self.blob_path(l["digest"]).is_file()]
        if missing:
            raise FileNotFoundError(f"{name} is incomplete; missing blobs:\n" + "\n".join(missing))

        total = sum(self.blob_path(l["digest"]).stat().st_size for l in layers) or 1
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        final = output_dir / f"{safe}.tar"
        partial = final.with_suffix(".tar.partial")

        metadata = {
            "format": "ollama-model-archive",
            "version": ARCHIVE_VERSION,
            "model": name,
            "created": datetime.now().isoformat(timespec="seconds"),
            "manifest": manifest_path.relative_to(self.models_dir).as_posix(),
            "blobs": [l["digest"] for l in layers],
            "size": total,
        }

        done = 0
        try:
            with tarfile.open(partial, "w", format=tarfile.PAX_FORMAT) as tar:
                meta_bytes = json.dumps(metadata, indent=2).encode("utf-8")
                info = tarfile.TarInfo(METADATA_NAME)
                info.size = len(meta_bytes)
                info.mtime = int(datetime.now().timestamp())
                tar.addfile(info, _BytesReader(meta_bytes))

                tar.add(manifest_path, arcname=metadata["manifest"])
                for layer in layers:
                    blob = self.blob_path(layer["digest"])
                    info = tar.gettarinfo(blob, arcname=f"blobs/{blob.name}")
                    with open(blob, "rb") as f:
                        tar.addfile(info, _ProgressReader(f, lambda n: progress(min((done + n) / total, 1.0))))
                    done += info.size
            os.replace(partial, final)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return final

    def restore_archive(self, archive_path, overwrite, progress):
        """Extract an archive into the models dir, verifying every blob's sha256."""
        restored_name = None
        with tarfile.open(archive_path, "r:*") as tar:
            members = tar.getmembers()
            total = sum(m.size for m in members if m.isfile()) or 1
            done = 0

            meta_member = next((m for m in members if m.name == METADATA_NAME), None)
            if meta_member:
                restored_name = json.load(tar.extractfile(meta_member)).get("model")

            manifests = []
            for m in members:
                if m.name == METADATA_NAME or m.isdir():
                    continue
                rel = safe_member_path(m.name)
                if rel is None or not m.isfile():
                    raise ValueError(f"Archive contains an unexpected entry: {m.name}")
                target = self.models_dir.joinpath(*rel.parts)

                if rel.parts[0] == "manifests":
                    manifests.append((m, rel, target))
                    continue

                if target.is_file() and not overwrite:
                    done += m.size  # shared layer already present
                    progress(done / total)
                    continue

                expected = rel.parts[1].split("-", 1)[1]
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".partial")
                h = hashlib.sha256()
                try:
                    src = tar.extractfile(m)
                    with open(tmp, "wb") as out:
                        while chunk := src.read(CHUNK):
                            h.update(chunk)
                            out.write(chunk)
                            done += len(chunk)
                            progress(done / total)
                    if h.hexdigest() != expected:
                        raise ValueError(f"Checksum mismatch for {rel.parts[1]} - archive is corrupt.")
                    os.replace(tmp, target)
                finally:
                    if tmp.exists():
                        tmp.unlink()

            if not manifests:
                raise ValueError("Archive contains no model manifest.")

            # Manifests last, so a model never appears before its blobs exist.
            for m, rel, target in manifests:
                data = tar.extractfile(m).read()
                manifest = json.loads(data)
                for layer in self.manifest_digests(manifest):
                    if not self.blob_path(layer["digest"]).is_file():
                        raise FileNotFoundError(f"Archive is missing blob {layer['digest']}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                if not restored_name:
                    p = rel.parts[1:]
                    restored_name = display_name(p[0], "/".join(p[1:-2]), p[-2], p[-1])
        progress(1.0)
        return restored_name


class _BytesReader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n=-1):
        if n < 0:
            n = len(self.data) - self.pos
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk


class _ProgressReader:
    def __init__(self, f, callback):
        self.f, self.callback, self.read_total = f, callback, 0

    def read(self, n=-1):
        chunk = self.f.read(n)
        self.read_total += len(chunk)
        self.callback(self.read_total)
        return chunk


class OllamaManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Ollama Local Model Manager")
        self.root.geometry("820x580")
        self.root.minsize(640, 420)

        self.store = OllamaStore()
        self.events = queue.Queue()
        self.busy = False
        self.buttons = []

        self.create_widgets()
        self.root.after(100, self.process_events)
        self.refresh_models()

    # ---------- UI ----------
    def create_widgets(self):
        top = ttk.LabelFrame(self.root, text="Archive Folder", padding=8)
        top.pack(fill="x", padx=10, pady=(10, 5))
        self.dir_var = tk.StringVar(value=str(Path.home() / "Documents" / "ollama_archives"))
        ttk.Entry(top, textvariable=self.dir_var).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(top, text="Open", command=self.open_output_dir).pack(side="right")
        ttk.Button(top, text="Browse...", command=self.select_output_dir).pack(side="right", padx=5)

        mid = ttk.LabelFrame(self.root, text=f"Installed Models  ({self.store.models_dir})", padding=8)
        mid.pack(fill="both", expand=True, padx=10, pady=5)
        cols = ("size", "modified")
        self.tree = ttk.Treeview(mid, columns=cols, selectmode="none")
        self.tree.heading("#0", text="Model", command=lambda: self.sort_by("#0"))
        self.tree.heading("size", text="Size", command=lambda: self.sort_by("size"))
        self.tree.heading("modified", text="Modified", command=lambda: self.sort_by("modified"))
        self.tree.column("#0", width=420, stretch=True)
        self.tree.column("size", width=110, anchor="e", stretch=False)
        self.tree.column("modified", width=160, anchor="center", stretch=False)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<space>", lambda e: self.toggle(self.tree.focus()))
        self.sort_state = ("#0", False)
        self.model_info = {}
        self.checked = set()

        opts = ttk.Frame(self.root, padding=(10, 0))
        opts.pack(fill="x")
        self.remove_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Remove from Ollama after successful archive",
                        variable=self.remove_var).pack(side="left")
        self.selection_label = ttk.Label(opts, text="")
        self.selection_label.pack(side="right")

        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill="x")
        for text, cmd, side in (
            ("Refresh", self.refresh_models, "left"),
            ("Check / Uncheck All", self.select_all, "left"),
            ("Archive Checked", self.archive_selected, "left"),
            ("Delete Checked", self.delete_selected, "left"),
            ("Restore Archive(s)...", self.restore_archives, "right"),
        ):
            b = ttk.Button(bottom, text=text, command=cmd)
            b.pack(side=side, padx=(0, 6))
            self.buttons.append(b)

        status = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        status.pack(fill="x")
        self.progress = ttk.Progressbar(status, maximum=1.0)
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w", pady=(4, 0))

    def set_busy(self, busy):
        self.busy = busy
        for b in self.buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        self.root.config(cursor="watch" if busy else "")

    def update_selection_label(self):
        sel = self.checked
        size = sum(self.model_info[i]["size"] for i in sel if i in self.model_info)
        self.selection_label.config(text=f"{len(sel)} checked ({human_size(size)})" if sel else "")

    def draw_check(self, name):
        box = "☑" if name in self.checked else "☐"
        self.tree.item(name, text=f"{box}  {name}")

    def toggle(self, name):
        if not name or self.busy:
            return
        self.checked.symmetric_difference_update({name})
        self.draw_check(name)
        self.update_selection_label()

    def on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) in ("tree", "cell"):
            row = self.tree.identify_row(event.y)
            self.tree.focus(row)
            self.toggle(row)
            return "break"

    def select_all(self):
        names = set(self.model_info)
        self.checked = set() if self.checked >= names else names
        for name in names:
            self.draw_check(name)
        self.update_selection_label()

    def sort_by(self, col):
        reverse = not self.sort_state[1] if self.sort_state[0] == col else False
        self.sort_state = (col, reverse)
        key = {"#0": lambda m: m["name"].lower(), "size": lambda m: m["size"],
               "modified": lambda m: m["modified"]}[col]
        items = sorted(self.model_info.values(), key=key, reverse=reverse)
        for idx, m in enumerate(items):
            self.tree.move(m["name"], "", idx)

    def select_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get())
        if chosen:
            self.dir_var.set(chosen)

    def open_output_dir(self):
        path = Path(self.dir_var.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path) if hasattr(os, "startfile") else subprocess.Popen(["xdg-open", str(path)])

    def refresh_models(self):
        try:
            models = self.store.list_models()
        except Exception as e:
            messagebox.showerror("Error", f"Could not read models:\n{e}")
            models = []
        self.tree.delete(*self.tree.get_children())
        self.model_info = {m["name"]: m for m in models}
        self.checked &= set(self.model_info)
        for m in models:
            self.tree.insert("", "end", iid=m["name"], text=m["name"],
                             values=(human_size(m["size"]), m["modified"].strftime("%Y-%m-%d %H:%M")))
            self.draw_check(m["name"])
        server = "Ollama server running" if self.store.server_running() else "Ollama server NOT running"
        self.status_var.set(f"{len(models)} model(s) installed. {server}.")
        self.update_selection_label()

    # ---------- background work ----------
    def run_task(self, title, func):
        self.set_busy(True)
        self.progress["value"] = 0
        self.status_var.set(title)

        def worker():
            try:
                result = func()
                self.events.put(("done", result))
            except Exception as e:
                self.events.put(("error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def post(self, kind, value):
        self.events.put((kind, value))

    def process_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.progress["value"] = value
                elif kind == "status":
                    self.status_var.set(value)
                elif kind in ("done", "error"):
                    self.set_busy(False)
                    self.refresh_models()
                    if kind == "error":
                        self.status_var.set("Failed.")
                        messagebox.showerror("Error", value)
                    else:
                        self.status_var.set(value.splitlines()[0])
                        messagebox.showinfo("Finished", value)
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    # ---------- actions ----------
    def selected_models(self):
        sel = sorted(self.checked)
        if not sel:
            messagebox.showwarning("Nothing checked", "Tick the box next to one or more models first.")
        return sel

    def archive_selected(self):
        models = self.selected_models()
        if not models:
            return
        output_dir = Path(self.dir_var.get())
        remove = self.remove_var.get()
        if remove and not messagebox.askyesno(
                "Confirm", f"Archive {len(models)} model(s) to:\n{output_dir}\n\n"
                           "and REMOVE them from Ollama after each archive succeeds?"):
            return

        def task():
            output_dir.mkdir(parents=True, exist_ok=True)
            ok, failed = [], []
            for i, name in enumerate(models, 1):
                prefix = f"[{i}/{len(models)}] {name}"
                self.post("status", f"{prefix}: archiving...")
                try:
                    path = self.store.archive_model(
                        name, output_dir, lambda f: self.post("progress", f))
                    if remove:
                        self.post("status", f"{prefix}: removing from Ollama...")
                        self.store.remove_model(name)
                    ok.append(f"{name} -> {path.name}")
                except Exception as e:
                    failed.append(f"{name}: {e}")
            lines = [f"Archived {len(ok)} of {len(models)} model(s) to {output_dir}"]
            lines += ok
            if failed:
                lines += ["", "Failures:"] + failed
                raise RuntimeError("\n".join(lines))
            return "\n".join(lines)

        self.run_task("Archiving...", task)

    def delete_selected(self):
        models = self.selected_models()
        if not models:
            return
        if not messagebox.askyesno("Confirm delete",
                                   "Permanently remove these models from Ollama (no archive)?\n\n"
                                   + "\n".join(models), icon="warning"):
            return

        def task():
            for name in models:
                self.post("status", f"Removing {name}...")
                self.store.remove_model(name)
            return f"Removed {len(models)} model(s)."

        self.run_task("Removing...", task)

    def restore_archives(self):
        paths = filedialog.askopenfilenames(
            title="Select Ollama model archive(s)",
            initialdir=self.dir_var.get(),
            filetypes=[("Ollama archives", "*.tar *.tar.gz *.tgz"), ("All files", "*.*")])
        if not paths:
            return

        def task():
            restored, failed = [], []
            for i, p in enumerate(paths, 1):
                self.post("status", f"[{i}/{len(paths)}] Restoring {Path(p).name}...")
                try:
                    name = self.store.restore_archive(Path(p), overwrite=False,
                                                      progress=lambda f: self.post("progress", f))
                    restored.append(name or Path(p).name)
                except Exception as e:
                    failed.append(f"{Path(p).name}: {e}")
            lines = [f"Restored {len(restored)} of {len(paths)} archive(s) into {self.store.models_dir}"]
            lines += restored
            if failed:
                lines += ["", "Failures:"] + failed
                raise RuntimeError("\n".join(lines))
            return "\n".join(lines)

        self.run_task("Restoring...", task)


if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    app = OllamaManagerApp(root)
    root.mainloop()
