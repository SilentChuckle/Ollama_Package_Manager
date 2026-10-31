"""
Ollama Local Model Manager
==========================

WHAT THIS PROGRAM DOES
    Archives installed Ollama models to a backup folder (optionally removing them from Ollama afterwards)
    and restores them from those archives later.

HOW IT WORKS (step by step)
    1. LIST: reads the Ollama models folder (manifests + blobs) and shows each model with size and date.
    2. ARCHIVE: for each ticked model, writes a plain .tar (metadata, manifest, blobs) to a temporary
       name and renames it when complete; optionally removes the model from Ollama afterwards.
    3. DELETE: removes ticked models via Ollama itself so shared blobs are reference-counted.
    4. RESTORE: extracts archives back into the models folder, verifying every blob's sha256 and
       writing manifests last so a model never appears before its blobs exist.
    Archive layout: ollama_archive.json, manifests/<host>/<namespace>/<model>/<tag>, blobs/sha256-<hex>.

SAFETY
    * Archive members are validated (no absolute paths, no "..", only manifests/ and blobs/).
    * Removing or deleting models always asks for confirmation first.
    * Existing blobs are never overwritten on restore; corrupt blobs are rejected by checksum.
    * Only the local Ollama API (127.0.0.1) is contacted.

REQUIREMENTS
    Only Python's built-in library (and a local Ollama install).

Author : SilentChuckle (with Claude support)
Created: 2026-10-24
Updated: 2026-10-27  (modern chassis: hidden console, opt-in logging, theme, banner, sections)

License: PolyForm Noncommercial License 1.0.0  (SPDX: PolyForm-Noncommercial-1.0.0)
    https://polyformproject.org/licenses/noncommercial/1.0.0
    Required Notice: Copyright (c) 2026 SilentChuckle (https://github.com/Merely-Human)
    Free for any non-commercial purpose. Commercial use needs written permission from the author.
"""

# @@BEGIN BLOCK_A@@
# =====================================================================================
# STEP 1 - HIDE THE BLACK CONSOLE WINDOW, AND LEAVE NO FILES BEHIND WITHOUT PERMISSION
# =====================================================================================
# Windows opens a black "console" window when a script is started with python.exe. This program has
# its own window, so the console is just clutter. If we were started with python.exe we immediately
# start a second copy with pythonw.exe (the "windowless" Python) and quit this copy. The environment
# variable _GUI_RELAUNCHED stops the new copy from doing the same thing again. This only happens when the
# script is RUN (not when another program imports it), and - for programs that also work from the command
# line - only when no command-line arguments were given.
import os as _os, sys as _sys
# Nothing is written to disk just by running this program unless you ask for it. Python normally caches
# compiled imports in a '__pycache__' folder next to the script; we turn that off so none appears. (If a
# '__pycache__' is ever left behind by something else, the cleanup below removes it when the program closes.)
_sys.dont_write_bytecode = True
import atexit as _atexit
@_atexit.register
def _remove_pycache():
    import shutil as _sh
    _pc = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "__pycache__")
    _sh.rmtree(_pc, ignore_errors=True)
if (_sys.platform == "win32" and __name__ == "__main__" and _sys.executable.lower().endswith("python.exe")
        and _os.environ.get("_GUI_RELAUNCHED") != "1"
        and not (globals().get("RELAUNCH_ONLY_WITHOUT_ARGUMENTS") and len(_sys.argv) > 1)):
    _pyw = _sys.executable[:-len("python.exe")] + "pythonw.exe"          # path of the windowless Python
    if _os.path.exists(_pyw):
        import subprocess as _sp
        _sp.Popen([_pyw, _os.path.abspath(__file__)] + _sys.argv[1:], env=dict(_os.environ, _GUI_RELAUNCHED="1"))
        _sys.exit(0)                                                      # this console copy is finished
# Without a console, print() has nowhere to go. Anything printed (and any crash message) is normally lost,
# which makes problems hard to report - but we never save that to a ".log" file on disk unless you have
# opted in, either with the environment variable SILENTCHUCKLE_SAVE_LOGS=1 or by placing a file named
# "<this script>.logs_enabled" next to it. Without opt-in, the text is only kept in memory and shown once
# in a pop-up if the program exits with something to report - nothing touches the disk either way unless
# you asked for it. (Programs must never print passwords or other private data.)
_LOG_OPT_IN = (_os.environ.get("SILENTCHUCKLE_SAVE_LOGS") == "1"
               or _os.path.exists(_os.path.splitext(_os.path.abspath(__file__))[0] + ".logs_enabled"))
if _sys.stdout is None or _sys.stderr is None:
    class _LazyLog:
        def __init__(self):
            self._buffer = []                                            # used only when not opted in
        def write(self, text):
            if _LOG_OPT_IN:
                with open(_os.path.splitext(_os.path.abspath(__file__))[0] + ".log", "a", encoding="utf-8") as f:
                    f.write(text)
            else:
                self._buffer.append(text)
        def flush(self):
            pass
    _log = _LazyLog()
    _sys.stdout = _sys.stdout or _log
    _sys.stderr = _sys.stderr or _log
    if not _LOG_OPT_IN:
        @_atexit.register
        def _show_unsaved_output():
            text = "".join(_log._buffer).strip()
            if not text:
                return
            try:
                import tkinter as _tk
                from tkinter import messagebox as _mb
                _r = _tk.Tk(); _r.withdraw(); _r.attributes("-topmost", True)
                _mb.showerror("Something was reported (not saved to disk)",
                              text[-1500:] + "\n\nTo save this to a .log file next time, create a file "
                              "named '" + _os.path.basename(_os.path.splitext(__file__)[0]) + ".logs_enabled' "
                              "next to this script.", parent=_r)
                _r.destroy()
            except Exception:
                pass
# @@END BLOCK_A@@

# =====================================================================================
# STEP 2 - SAY WHAT THIS PROGRAM NEEDS
# =====================================================================================
REQUIRED_PACKAGES = {}
REQUIRED_TOOLS = {}

# @@BEGIN BLOCK_B@@
# =====================================================================================
# STEP 3 - CHECK THAT EVERYTHING THIS PROGRAM NEEDS IS INSTALLED (before any real work starts)
# =====================================================================================
# A "dependency" is an add-on package (written by other people) or an outside program that this
# program uses. If one is missing the program would crash halfway through its job, so we look FIRST.
# If something is missing we ask permission to install it - nothing is ever installed without
# your OK. The names to look for are listed in REQUIRED_PACKAGES / REQUIRED_TOOLS in STEP 2.
import shutil as _shutil, subprocess as _subprocess, importlib as _importlib, importlib.util as _importlib_util
import os as _os, sys as _sys


def find_missing_dependencies(packages, tools=None):
    """Return (missing_pip_names, missing_tools). Nothing is installed here - this only looks.
    packages: {"name used in `import`": "name pip knows it by"}, e.g. {"PIL": "pillow"}
    tools:    {"program name": "winget package id"}, e.g. {"ffmpeg": "Gyan.FFmpeg"}"""
    missing_packages = [pip_name for module, pip_name in packages.items()
                        if _importlib_util.find_spec(module) is None]       # None = Python cannot find it
    missing_tools = {exe: winget_id for exe, winget_id in (tools or {}).items()
                     if _shutil.which(exe) is None}                           # not found on the PATH
    return missing_packages, missing_tools


def _ask_yes_no(title, text):
    """Ask a Yes/No question: in the terminal if one is attached, otherwise in a pop-up window."""
    if _sys.stdin is not None and _sys.stdin.isatty():
        try:
            return input(text + "\n[y/N] ").strip().lower().startswith("y")
        except (EOFError, OSError):                      # nobody is there to answer -> safest answer is "no"
            return False
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()                                  # we only want the pop-up, not an empty window
        root.attributes("-topmost", True)
        answer = messagebox.askyesno(title, text, parent=root)
        root.destroy()
        return answer
    except Exception:
        return False                                     # no way to ask -> safest answer is "do not install"


def _tell(title, text, error=False):
    """Show a message in the terminal or in a pop-up window."""
    if _sys.stdin is not None and _sys.stdin.isatty():
        print(text)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        (messagebox.showerror if error else messagebox.showinfo)(title, text, parent=root)
        root.destroy()
    except Exception:
        print(text)


def _run_with_wait_window(command, message):
    """Run a slow command (like pip) while showing a small 'please wait' window so the program does
    not look frozen. Returns the finished process (return code + captured output)."""
    import threading
    result = {}
    flags = getattr(_subprocess, "CREATE_NO_WINDOW", 0)                  # do not flash a console window
    worker = threading.Thread(target=lambda: result.update(
        done=_subprocess.run(command, capture_output=True, text=True, creationflags=flags)))
    worker.start()
    try:
        import tkinter as tk
        window = tk.Tk()
        window.title("Please wait")
        tk.Label(window, text=message, padx=30, pady=25).pack()
        window.attributes("-topmost", True)
        while worker.is_alive():                                          # keep the window alive while pip works
            window.update()
            worker.join(0.05)
        window.destroy()
    except Exception:
        worker.join()                                                     # no window possible: just wait
    return result["done"]


def ensure_dependencies(packages, tools=None):
    """Check dependencies; if any are missing, offer to install them. Exits the program if the user
    says no or the install fails; returns normally when everything is present."""
    missing_packages, missing_tools = find_missing_dependencies(packages, tools)
    if not missing_packages and not missing_tools:
        return                                          # everything is here -> carry on with the real work

    if missing_packages:                                # ---- missing Python packages: install with pip
        names = "\n  - ".join(missing_packages)
        if not _ask_yes_no("Missing components",
                           f"This program needs these Python packages:\n  - {names}\n\n"
                           "They will be downloaded from PyPI (the official Python package site).\n"
                           "Install them now?"):
            _sys.exit(0)
        python = _sys.executable                                          # pythonw.exe cannot run pip well,
        if python.lower().endswith("pythonw.exe") and _os.path.exists(python[:-11] + "python.exe"):
            python = python[:-11] + "python.exe"                          # so use its console twin
        # The package names come from the list written above - never from user input - so nothing
        # untrusted can reach this command, and no shell is involved.
        done = _run_with_wait_window([python, "-m", "pip", "install", "--disable-pip-version-check", *missing_packages],
                                     "Installing: " + ", ".join(missing_packages) + "\nThis can take a few minutes...")
        _importlib.invalidate_caches()                                    # let Python notice the new packages
        if done.returncode != 0 or find_missing_dependencies(packages)[0]:
            _tell("Install failed", "Could not install the required packages.\n\n" + (done.stderr or "")[-600:], True)
            _sys.exit(1)

    for exe, winget_id in missing_tools.items():        # ---- missing outside programs (e.g. ffmpeg)
        if winget_id and _shutil.which("winget") and _ask_yes_no(
                "Missing program", f"This program needs '{exe}', which is not installed.\n"
                                   f"Install it with the Windows Package Manager (winget id: {winget_id})?"):
            done = _run_with_wait_window(["winget", "install", "--id", winget_id, "-e", "--silent",
                                          "--accept-source-agreements", "--accept-package-agreements"],
                                         f"Installing {exe}...")
            _tell("Restart needed", f"{exe} was installed. Please close and re-open this program so Windows "
                                    "can find it." if done.returncode == 0 else f"Could not install {exe}.", done.returncode != 0)
        else:
            _tell("Missing program", f"This program needs '{exe}'. Please install it and start the program again.", True)
        _sys.exit(1)


ensure_dependencies(REQUIRED_PACKAGES, REQUIRED_TOOLS)         # <- the actual check happens here
# @@END BLOCK_B@@


import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

# =====================================================================================
# THE WINDOW CHASSIS (shared by every SilentChuckle tool)
# =====================================================================================
SIGNATURE = "SilentChuckle"

MIT_LICENSE_TEXT = (
    "MIT License\n\n"
    "Copyright (c) 2026 SilentChuckle\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and "
    "associated documentation files (the \"Software\"), to deal in the Software without restriction, including "
    "without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
    "copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the "
    "following conditions:\n\n"
    "The above copyright notice and this permission notice shall be included in all copies or substantial "
    "portions of the Software.\n\n"
    "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT "
    "LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO "
    "EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER "
    "IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE "
    "USE OR OTHER DEALINGS IN THE SOFTWARE."
)

THEME = {
    "bg": "#f3f5f9", "card": "#ffffff", "head": "#e4e9f4", "head_hover": "#d5ddef", "text": "#1d2433",
    "muted": "#6b7488", "accent": "#3563e9", "accent_hover": "#294fc0", "accent_off": "#a9b6e8",
    "border": "#d0d7e6", "log_bg": "#10151f", "log_fg": "#d7e0f2",
    "info": "#eef1fb", "info_text": "#2c3a66", "ok": "#e7f6ec", "ok_text": "#1f6f3d",
    "warn": "#fdecea", "warn_text": "#9c2b1f",
}


def _enable_dpi_awareness():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def flash_taskbar(window):
    """Flash this window's taskbar button to draw attention, but only if it is not the one in focus
    right now (no point flashing something you're already looking at). Windows stops the flashing on
    its own as soon as the window is brought to the front - nothing else has to call it off."""
    try:
        import ctypes
        if window.focus_displayof() is not None:          # some widget in this window already has focus
            return
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                        ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]
        FLASHW_TRAY, FLASHW_TIMERNOFG = 0x00000002, 0x0000000C
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_TRAY | FLASHW_TIMERNOFG, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass                                               # not on Windows, or no window handle yet - skip quietly


class Banner(ttk.Frame):
    """A message bar built into the window itself, in place of a separate pop-up dialog. show() displays
    text in the right color for its kind and an optional Yes/No pair of buttons for a question that would
    otherwise have been a messagebox.askyesno; ok() is the plain case with just a Dismiss button."""
    KIND_STYLE = {"info": ("info", "info_text"), "ok": ("ok", "ok_text"), "warn": ("warn", "warn_text")}

    def __init__(self, parent):
        super().__init__(parent)
        self._bg_key = None
        self._anchor = None                                  # set via attach(): keeps the banner pinned at top
        self.label = tk.Label(self, text="", anchor="w", justify="left", wraplength=1000, padx=12, pady=8)
        self.label.pack(side="left", fill="x", expand=True)
        self.btns = ttk.Frame(self)
        self.btns.pack(side="right", padx=8)
        self.pack_forget()                                  # hidden until there is something to say

    def attach(self, before_widget):
        """Remember which widget the banner should stay above, so repeated show()/ask() calls always
        land in the same place (the top of the window) instead of drifting to wherever was packed last."""
        self._anchor = before_widget

    def _set_kind(self, kind):
        bg_key, fg_key = self.KIND_STYLE.get(kind, self.KIND_STYLE["info"])
        self.label.configure(background=THEME[bg_key], foreground=THEME[fg_key])
        self.configure(style="TFrame")

    def _clear_buttons(self):
        for w in self.btns.winfo_children():
            w.destroy()

    def show(self, text, kind="info"):
        """A plain message with just a Dismiss button (replaces messagebox.showinfo/showerror)."""
        self._set_kind("warn" if kind == "error" else kind)
        self.label.configure(text=text)
        self._clear_buttons()
        ttk.Button(self.btns, text="Dismiss", command=self.hide).pack(side="left")
        self.pack_forget()
        self.pack(fill="x", side="top", before=self._anchor)
        flash_taskbar(self.winfo_toplevel())

    def ask(self, text, on_yes, on_no=None, yes_text="Continue", no_text="Cancel"):
        """A question with its own Yes/No buttons, built into the window (replaces messagebox.askyesno)."""
        self._set_kind("info")
        self.label.configure(text=text)
        self._clear_buttons()

        def _choose(fn):
            self.hide()
            if fn:
                fn()
        ttk.Button(self.btns, text=no_text, command=lambda: _choose(on_no)).pack(side="right", padx=(6, 0))
        ttk.Button(self.btns, text=yes_text, style="Accent.TButton",
                   command=lambda: _choose(on_yes)).pack(side="right")
        self.pack_forget()
        self.pack(fill="x", side="top", before=self._anchor)
        flash_taskbar(self.winfo_toplevel())

    def hide(self):
        self.pack_forget()


def _apply_theme(root):
    T = THEME
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    base = ("Segoe UI", 10)
    root.configure(bg=T["bg"])
    root.option_add("*Font", base)
    style.configure(".", background=T["bg"], foreground=T["text"], font=base, bordercolor=T["border"],
                    lightcolor=T["bg"], darkcolor=T["bg"], focuscolor=T["accent"])
    style.configure("TFrame", background=T["bg"])
    style.configure("Card.TFrame", background=T["card"])
    style.configure("TLabel", background=T["bg"], foreground=T["text"])
    style.configure("Card.TLabel", background=T["card"], foreground=T["text"])
    style.configure("Muted.TLabel", background=T["bg"], foreground=T["muted"])
    style.configure("CardMuted.TLabel", background=T["card"], foreground=T["muted"], font=("Segoe UI", 9))
    style.configure("Title.TLabel", background=T["bg"], font=("Segoe UI", 17, "bold"))
    style.configure("Head.TLabel", background=T["head"], font=("Segoe UI", 10, "bold"), padding=(12, 8))
    style.configure("TButton", background=T["card"], padding=(14, 6), relief="flat", borderwidth=1)
    style.map("TButton", background=[("active", T["head_hover"]), ("disabled", T["bg"])],
              foreground=[("disabled", T["muted"])])
    style.configure("Accent.TButton", background=T["accent"], foreground="#ffffff", borderwidth=0)
    style.map("Accent.TButton", background=[("active", T["accent_hover"]), ("disabled", T["accent_off"])],
              foreground=[("disabled", "#ffffff")])
    style.configure("TCheckbutton", background=T["bg"])
    style.configure("Card.TCheckbutton", background=T["card"])
    style.configure("TEntry", fieldbackground="#ffffff", padding=4)
    style.configure("TCombobox", fieldbackground="#ffffff", padding=4)
    style.configure("Horizontal.TProgressbar", troughcolor=T["head"], background=T["accent"], thickness=10,
                    borderwidth=0)


class ScrollFrame(ttk.Frame):
    def __init__(self, parent, on_resize=None):
        super().__init__(parent)
        self._on_resize = on_resize
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=THEME["bg"], borderwidth=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.inner = ttk.Frame(self.canvas, padding=(16, 12, 16, 12))
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.refresh())
        self.canvas.bind("<Configure>", lambda _e: self.refresh())
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Shift-MouseWheel>", self._wheel_sideways)

    def refresh(self):
        need_w, need_h = self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()
        view_w, view_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        width = max(view_w, need_w)
        self.canvas.itemconfigure(self._window, width=width)
        self.canvas.configure(scrollregion=(0, 0, width, max(view_h, need_h)))
        if need_w > view_w + 1:
            self.hbar.grid()
        else:
            self.hbar.grid_remove()
        if self._on_resize:
            self._on_resize(view_w)

    def _wheel(self, event):
        w = event.widget
        if isinstance(w, (tk.Text, tk.Listbox, ttk.Treeview)) or not str(w).startswith(str(self)):
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _wheel_sideways(self, event):
        if str(event.widget).startswith(str(self)):
            self.canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")


class Section(ttk.Frame):
    def __init__(self, parent, title, opened=True, on_toggle=None):
        super().__init__(parent)
        self.title, self.opened, self._on_toggle = title, opened, on_toggle
        self.head = ttk.Label(self, style="Head.TLabel", cursor="hand2", takefocus=True)
        self.head.pack(fill="x")
        self.body = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        self.head.bind("<Button-1>", lambda _e: self.toggle())
        self.head.bind("<Return>", lambda _e: self.toggle())
        self.head.bind("<space>", lambda _e: self.toggle())
        self.head.bind("<Enter>", lambda _e: self.head.configure(background=THEME["head_hover"]))
        self.head.bind("<Leave>", lambda _e: self.head.configure(background=THEME["head"]))
        self._apply()

    def toggle(self):
        self.opened = not self.opened
        self._apply()
        if self._on_toggle:
            self._on_toggle()

    def _apply(self):
        self.head.configure(text=("v   " if self.opened else ">   ") + self.title)
        if self.opened:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()



# =====================================================================================
# APPLICATION LOGIC
# =====================================================================================
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tarfile
import threading
import traceback
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from tkinter import filedialog, ttk

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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Ollama Local Model Manager  -  {SIGNATURE}")
        self.minsize(720, 520)
        _apply_theme(self)
        self.store = OllamaStore()
        self.events = queue.Queue()
        self.busy = False
        self.buttons = []
        self.banner = Banner(self)
        self.sort_state = ("#0", False)
        self.model_info = {}
        self.checked = set()
        self._build()
        self.after(10, self._fit_window)
        self.after(100, self.process_events)
        self.refresh_models()

    # --- window size -----------------------------------------------------------------------------
    def _fit_window(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(max(self.scroller.inner.winfo_reqwidth() + 40, 900), int(sw * 0.94))
        h = min(max(self.scroller.inner.winfo_reqheight() + 20, 720), int(sh * 0.90))
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 3, 0)}")
        self.minsize(min(420, sw), min(320, sh))

    def _grow_if_needed(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        need = self.scroller.inner.winfo_reqwidth() + 40
        if need > self.winfo_width() and need <= int(sw * 0.96):
            self.geometry(f"{need}x{self.winfo_height()}")

    # ---------- UI ----------
    def _build(self):
        ttk.Label(self, text="Ollama Local Model Manager", style="Title.TLabel").pack(anchor="w", padx=12, pady=6)
        self.banner.attach(self.winfo_children()[-1])
        self.scroller = ScrollFrame(self)
        self.scroller.pack(fill="both", expand=True, padx=8)
        root = self.scroller.inner

        sec_dir = Section(root, "Archive Folder", opened=True, on_toggle=self._grow_if_needed)
        sec_dir.pack(fill="x", pady=(0, 10))
        self.dir_var = tk.StringVar(value=str(Path.home() / "Documents" / "ollama_archives"))
        ttk.Entry(sec_dir.body, textvariable=self.dir_var).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(sec_dir.body, text="Browse...", command=self.select_output_dir).pack(side="left", padx=5)
        ttk.Button(sec_dir.body, text="Open", command=self.open_output_dir).pack(side="left")

        sec_models = Section(root, f"Installed Models  ({self.store.models_dir})", opened=True, on_toggle=self._grow_if_needed)
        sec_models.pack(fill="x", pady=(0, 10))
        mid = sec_models.body
        self.tree = ttk.Treeview(mid, columns=("size", "modified"), selectmode="none", height=10)
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

        sec_act = Section(root, "Actions", opened=True, on_toggle=self._grow_if_needed)
        sec_act.pack(fill="x", pady=(0, 10))
        opts = ttk.Frame(sec_act.body, style="Card.TFrame")
        opts.pack(fill="x")
        self.remove_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Remove from Ollama after successful archive", style="Card.TCheckbutton",
                        variable=self.remove_var).pack(side="left")
        self.selection_label = ttk.Label(opts, text="", style="Card.TLabel")
        self.selection_label.pack(side="right")
        bottom = ttk.Frame(sec_act.body, style="Card.TFrame")
        bottom.pack(fill="x", pady=(8, 0))
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

        sec_prog = Section(root, "Progress", opened=True, on_toggle=self._grow_if_needed)
        sec_prog.pack(fill="x", pady=(0, 10))
        self.progress = ttk.Progressbar(sec_prog.body, maximum=1.0)
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(sec_prog.body, textvariable=self.status_var, style="Card.TLabel").pack(anchor="w", pady=(4, 0))

        about = Section(root, "About & License", False, self._grow_if_needed)
        about.pack(fill="x", pady=(0, 10))
        ttk.Label(about.body, text=f"Written by {SIGNATURE}", style="Card.TLabel",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(about.body, text=MIT_LICENSE_TEXT, style="CardMuted.TLabel", justify="left",
                  wraplength=760).pack(anchor="w", pady=(6, 0))
        ttk.Label(root, text=f"{SIGNATURE}  |  MIT License", style="Muted.TLabel").pack(anchor="e", pady=(4, 0))

    def set_busy(self, busy):
        self.busy = busy
        for b in self.buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        self.config(cursor="watch" if busy else "")

    def update_selection_label(self):
        sel = self.checked
        size = sum(self.model_info[i]["size"] for i in sel if i in self.model_info)
        self.selection_label.config(text=f"{len(sel)} checked ({human_size(size)})" if sel else "")

    def draw_check(self, name):
        box = "[x]" if name in self.checked else "[ ]"
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
        for idx, m in enumerate(sorted(self.model_info.values(), key=key, reverse=reverse)):
            self.tree.move(m["name"], "", idx)

    def select_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get())
        if chosen:
            self.dir_var.set(chosen)

    def open_output_dir(self):
        path = Path(self.dir_var.get())
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path) if hasattr(os, "startfile") else subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            self.banner.show(f"Cannot open folder: {e}", "error")

    def refresh_models(self):
        try:
            models = self.store.list_models()
        except Exception as e:  # noqa: BLE001 - keep the window alive
            self.banner.show(f"Could not read models:\n{e}", "error")
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
                self.events.put(("done", func()))
            except Exception as e:  # noqa: BLE001 - reported in the banner
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
                        self.banner.show(value, "error")
                    else:
                        self.status_var.set(value.splitlines()[0])
                        self.banner.show(value, "ok")
                    flash_taskbar(self)
        except queue.Empty:
            pass
        self.after(100, self.process_events)

    # ---------- actions ----------
    def selected_models(self):
        sel = sorted(self.checked)
        if not sel:
            self.banner.show("Tick the box next to one or more models first.", "info")
        return sel

    def archive_selected(self):
        models = self.selected_models()
        if not models:
            return
        output_dir = Path(self.dir_var.get())
        remove = self.remove_var.get()
        if remove:
            self.banner.ask(f"Archive {len(models)} model(s) to:\n{output_dir}\n\n"
                            "and REMOVE them from Ollama after each archive succeeds?",
                            on_yes=lambda: self._do_archive(models, output_dir, remove))
        else:
            self._do_archive(models, output_dir, remove)

    def _do_archive(self, models, output_dir, remove):
        def task():
            output_dir.mkdir(parents=True, exist_ok=True)
            ok, failed = [], []
            for i, name in enumerate(models, 1):
                prefix = f"[{i}/{len(models)}] {name}"
                self.post("status", f"{prefix}: archiving...")
                try:
                    path = self.store.archive_model(name, output_dir, lambda f: self.post("progress", f))
                    if remove:
                        self.post("status", f"{prefix}: removing from Ollama...")
                        self.store.remove_model(name)
                    ok.append(f"{name} -> {path.name}")
                except Exception as e:  # noqa: BLE001 - one bad model must not stop the batch
                    failed.append(f"{name}: {e}")
            lines = [f"Archived {len(ok)} of {len(models)} model(s) to {output_dir}"] + ok
            if failed:
                lines += ["", "Failures:"] + failed
                raise RuntimeError("\n".join(lines))
            return "\n".join(lines)
        self.run_task("Archiving...", task)

    def delete_selected(self):
        models = self.selected_models()
        if not models:
            return
        self.banner.ask("Permanently remove these models from Ollama (no archive)?\n\n" + "\n".join(models),
                        on_yes=lambda: self._do_delete(models), yes_text="Delete")

    def _do_delete(self, models):
        def task():
            for name in models:
                self.post("status", f"Removing {name}...")
                self.store.remove_model(name)
            return f"Removed {len(models)} model(s)."
        self.run_task("Removing...", task)

    def restore_archives(self):
        paths = filedialog.askopenfilenames(
            title="Select Ollama model archive(s)", initialdir=self.dir_var.get(),
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
                except Exception as e:  # noqa: BLE001 - one bad archive must not stop the batch
                    failed.append(f"{Path(p).name}: {e}")
            lines = [f"Restored {len(restored)} of {len(paths)} archive(s) into {self.store.models_dir}"] + restored
            if failed:
                lines += ["", "Failures:"] + failed
                raise RuntimeError("\n".join(lines))
            return "\n".join(lines)
        self.run_task("Restoring...", task)


def _main():
    try:
        _enable_dpi_awareness()
        App().mainloop()
    except Exception:
        Path(__file__).with_suffix(".crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    _main()
