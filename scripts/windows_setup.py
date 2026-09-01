"""Windows configuration wizard and verified Jellyfin FFmpeg installer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import StringVar, Tk, filedialog, messagebox, ttk

from mediaclipmakarr.config import Settings, validate_path_layout
from mediaclipmakarr.source_paths import SourcePathMapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PROFILE_PATH = PROJECT_ROOT / ".env.windows"
TOOLS_ROOT = PROJECT_ROOT / "data" / "tools"
JELLYFIN_FFMPEG_VERSION = "7.1.4-3"
EXPECTED_FFMPEG_IDENTITY = "7.1.4-Jellyfin"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DownloadAsset:
    architecture: str
    url: str
    sha256: str
    size: int


DOWNLOAD_ASSETS = {
    "AMD64": DownloadAsset(
        architecture="win64",
        url=(
            "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/"
            "v7.1.4-3/jellyfin-ffmpeg_7.1.4-3_portable_win64-clang-gpl.zip"
        ),
        sha256="113adeb702683c38be40a65d859f8ef7ffb07bae9df16dfb6c3df5ac3d95ef3c",
        size=60_257_737,
    ),
    "ARM64": DownloadAsset(
        architecture="winarm64",
        url=(
            "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/"
            "v7.1.4-3/jellyfin-ffmpeg_7.1.4-3_portable_winarm64-clang-gpl.zip"
        ),
        sha256="fcab60b6892ffa10c09a87570e53b88d8eda2344d58bf32e89ee8b2c2ababbf1",
        size=46_642_620,
    ),
}


@dataclass(frozen=True, slots=True)
class PathMapping:
    plex_prefix: str
    local_prefix: Path


@dataclass(slots=True)
class WindowsProfile:
    data_root: Path = PROJECT_ROOT / "data" / "windows"
    source_dirs: list[Path] = field(default_factory=list)
    mappings: list[PathMapping] = field(default_factory=list)
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    api_port: int = 8000
    web_port: int = 5173


@dataclass(frozen=True, slots=True)
class MediaToolStatus:
    available: bool
    jellyfin_build: bool
    message: str


def _portable_path(path: Path) -> str:
    return path.expanduser().resolve(strict=False).as_posix()


def validate_windows_profile(profile: WindowsProfile) -> None:
    if not profile.source_dirs:
        raise ValueError("Add at least one folder containing Plex source media.")
    if not profile.mappings:
        raise ValueError("Add at least one Plex path mapping.")

    sources = [path.expanduser().resolve(strict=False) for path in profile.source_dirs]
    for source in sources:
        if not source.is_dir():
            raise ValueError(f"Source folder does not exist: {source}")

    data_root = profile.data_root.expanduser().resolve(strict=False)
    settings = Settings(
        _env_file=None,
        private_data_dir=data_root / "private",
        work_dir=data_root / "work",
        clip_dir=data_root / "clips",
        source_dirs=sources,
    )
    validate_path_layout(settings)

    for mapping in profile.mappings:
        validated = SourcePathMapping(
            plex_prefix=mapping.plex_prefix,
            local_prefix=str(mapping.local_prefix),
        )
        local_prefix = Path(validated.local_prefix).expanduser().resolve(strict=False)
        if not any(local_prefix.is_relative_to(source) for source in sources):
            raise ValueError(
                f"Mapping destination must be inside an approved source folder: {local_prefix}"
            )

    for label, path in (("FFmpeg", profile.ffmpeg_path), ("FFprobe", profile.ffprobe_path)):
        if path is None or not path.expanduser().is_file():
            raise ValueError(f"{label} executable was not found. Detect it or download it first.")

    for label, port in (("API", profile.api_port), ("web", profile.web_port)):
        if not 1 <= port <= 65535:
            raise ValueError(f"The {label} port must be between 1 and 65535.")
    if profile.api_port == profile.web_port:
        raise ValueError("The API and web ports must be different.")


def render_windows_profile(profile: WindowsProfile) -> str:
    data_root = profile.data_root.expanduser().resolve(strict=False)
    source_dirs = [_portable_path(path) for path in profile.source_dirs]
    mappings = [
        {
            "plex_prefix": mapping.plex_prefix.strip(),
            "local_prefix": _portable_path(mapping.local_prefix),
        }
        for mapping in profile.mappings
    ]
    values = {
        "MCM_WINDOWS_DATA_ROOT": _portable_path(data_root),
        "MCM_PRIVATE_DATA_DIR": _portable_path(data_root / "private"),
        "MCM_WORK_DIR": _portable_path(data_root / "work"),
        "MCM_CLIP_DIR": _portable_path(data_root / "clips"),
        "MCM_SOURCE_DIRS": json.dumps(source_dirs, separators=(",", ":")),
        "MCM_SOURCE_PATH_MAPPINGS": json.dumps(mappings, separators=(",", ":")),
        "MCM_FFMPEG_PATH": _portable_path(profile.ffmpeg_path or Path("ffmpeg.exe")),
        "MCM_FFPROBE_PATH": _portable_path(profile.ffprobe_path or Path("ffprobe.exe")),
        "MCM_EXPECTED_FFMPEG_IDENTITY": EXPECTED_FFMPEG_IDENTITY,
        "MCM_DEV_API_PORT": str(profile.api_port),
        "MCM_DEV_WEB_PORT": str(profile.web_port),
    }
    lines = [
        "# Generated by setup_windows.py.",
        "# Re-run the Windows setup wizard to edit this profile safely.",
    ]
    lines.extend(f"{key}={value}" for key, value in values.items())
    return "\n".join(lines) + "\n"


def write_windows_profile(profile: WindowsProfile, path: Path = WINDOWS_PROFILE_PATH) -> None:
    validate_windows_profile(profile)
    data_root = profile.data_root.expanduser().resolve(strict=False)
    for directory in (data_root / "private", data_root / "work", data_root / "clips"):
        directory.mkdir(parents=True, exist_ok=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(f"{path.name}.bak"))
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(render_windows_profile(profile), encoding="utf-8")
    os.replace(temporary_path, path)


def _read_profile_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_windows_profile(path: Path = WINDOWS_PROFILE_PATH) -> WindowsProfile:
    values = _read_profile_values(path)
    profile = WindowsProfile()
    if data_root := values.get("MCM_WINDOWS_DATA_ROOT"):
        profile.data_root = Path(data_root)
    elif private_dir := values.get("MCM_PRIVATE_DATA_DIR"):
        profile.data_root = Path(private_dir).parent

    if source_dirs := values.get("MCM_SOURCE_DIRS"):
        profile.source_dirs = [Path(item) for item in json.loads(source_dirs)]
    if raw_mappings := values.get("MCM_SOURCE_PATH_MAPPINGS"):
        profile.mappings = [
            PathMapping(item["plex_prefix"], Path(item["local_prefix"]))
            for item in json.loads(raw_mappings)
        ]
    if ffmpeg := values.get("MCM_FFMPEG_PATH"):
        profile.ffmpeg_path = Path(ffmpeg)
    if ffprobe := values.get("MCM_FFPROBE_PATH"):
        profile.ffprobe_path = Path(ffprobe)
    if api_port := values.get("MCM_DEV_API_PORT"):
        profile.api_port = int(api_port)
    if web_port := values.get("MCM_DEV_WEB_PORT"):
        profile.web_port = int(web_port)
    return profile


def detect_media_tools() -> tuple[Path | None, Path | None]:
    ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
    return (Path(ffmpeg) if ffmpeg else None, Path(ffprobe) if ffprobe else None)


def inspect_media_tools(ffmpeg: Path | None, ffprobe: Path | None) -> MediaToolStatus:
    if ffmpeg is None or ffprobe is None or not ffmpeg.is_file() or not ffprobe.is_file():
        return MediaToolStatus(False, False, "FFmpeg and FFprobe were not both found.")
    try:
        results = [
            subprocess.run(
                [str(path), "-version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
            for path in (ffmpeg, ffprobe)
        ]
    except (OSError, subprocess.TimeoutExpired) as error:
        return MediaToolStatus(False, False, f"Media tools could not be started: {error}")
    if any(result.returncode != 0 for result in results):
        return MediaToolStatus(False, False, "FFmpeg or FFprobe failed its version check.")
    identities = [
        result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
        for result in results
    ]
    jellyfin = all(
        EXPECTED_FFMPEG_IDENTITY in identity and "jellyfin" in identity.casefold()
        for identity in identities
    )
    if jellyfin:
        return MediaToolStatus(True, True, "Pinned Jellyfin FFmpeg and FFprobe are ready.")
    return MediaToolStatus(
        True,
        False,
        "FFmpeg and FFprobe work, but they are not the pinned Jellyfin build.",
    )


def select_download_asset(machine: str | None = None) -> DownloadAsset:
    normalized = (machine or platform.machine()).upper()
    aliases = {"X86_64": "AMD64", "AARCH64": "ARM64"}
    normalized = aliases.get(normalized, normalized)
    try:
        return DOWNLOAD_ASSETS[normalized]
    except KeyError as error:
        raise ValueError(
            f"No supported Jellyfin FFmpeg download for architecture: {normalized}"
        ) from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve(strict=False)
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve(strict=False)
            if not target.is_relative_to(destination):
                raise ValueError("The downloaded archive contains an unsafe path.")
            unix_mode = member.external_attr >> 16
            if unix_mode & 0o170000 == 0o120000:
                raise ValueError("The downloaded archive contains an unsupported symbolic link.")
        package.extractall(destination)


def download_jellyfin_ffmpeg(
    destination: Path,
    *,
    asset: DownloadAsset | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Path, Path]:
    selected = asset or select_download_asset()
    destination = destination.expanduser().resolve(strict=False)
    existing_ffmpeg = destination / "ffmpeg.exe"
    existing_ffprobe = destination / "ffprobe.exe"
    if existing_ffmpeg.is_file() and existing_ffprobe.is_file():
        status = inspect_media_tools(existing_ffmpeg, existing_ffprobe)
        if status.jellyfin_build:
            return existing_ffmpeg, existing_ffprobe
        raise ValueError(
            f"The existing install is not the pinned Jellyfin FFmpeg build: {destination}"
        )
    if destination.exists():
        raise ValueError(
            f"The install folder already exists but is incomplete: {destination}. "
            "Remove or rename it, then try again."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mcm-ffmpeg-", dir=destination.parent
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        archive_path = temporary_root / "jellyfin-ffmpeg.zip"
        request = urllib.request.Request(selected.url, headers={"User-Agent": "MediaClipMakarr"})
        received = 0
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            archive_path.open("wb") as output,
        ):
            total = int(response.headers.get("Content-Length") or selected.size)
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        if sha256_file(archive_path) != selected.sha256:
            raise ValueError("The Jellyfin FFmpeg download failed SHA-256 verification.")

        extraction_root = temporary_root / "extracted"
        extraction_root.mkdir()
        _safe_extract_zip(archive_path, extraction_root)
        ffmpeg_candidates = list(extraction_root.rglob("ffmpeg.exe"))
        ffprobe_candidates = list(extraction_root.rglob("ffprobe.exe"))
        if len(ffmpeg_candidates) != 1 or len(ffprobe_candidates) != 1:
            raise ValueError("The verified archive does not contain one FFmpeg/FFprobe pair.")
        if ffmpeg_candidates[0].parent != ffprobe_candidates[0].parent:
            raise ValueError("FFmpeg and FFprobe were found in different archive folders.")
        shutil.move(str(ffmpeg_candidates[0].parent), str(destination))

    return destination / "ffmpeg.exe", destination / "ffprobe.exe"


class WindowsSetupWizard:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("MediaClipMakarr Windows Setup")
        self.root.geometry("900x790")
        self.root.minsize(780, 700)
        try:
            self.profile = load_windows_profile()
            profile_error = None
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.profile = WindowsProfile()
            profile_error = str(error)
        self.data_root = StringVar(value=str(self.profile.data_root))
        self.ffmpeg_path = StringVar(value=str(self.profile.ffmpeg_path or ""))
        self.ffprobe_path = StringVar(value=str(self.profile.ffprobe_path or ""))
        self.api_port = StringVar(value=str(self.profile.api_port))
        self.web_port = StringVar(value=str(self.profile.web_port))
        self.mapping_plex = StringVar()
        self.mapping_local = StringVar()
        self.status = StringVar(value="Ready.")
        self._build()
        self._populate()
        if profile_error:
            self.status.set(f"Existing .env.windows could not be loaded: {profile_error}")
        elif not self.profile.ffmpeg_path or not self.profile.ffprobe_path:
            self.detect_tools()

    def _build(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        ttk.Label(
            container,
            text="Windows setup",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            container,
            text=(
                "This creates .env.windows for local launches."
                "Source folders are treated as read-only."
            ),
            wraplength=820,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 14))

        ttk.Label(container, text="Application data folder").grid(row=2, column=0, sticky="w")
        ttk.Entry(container, textvariable=self.data_root).grid(
            row=2, column=1, sticky="ew", padx=8
        )
        ttk.Button(container, text="Browse…", command=self.browse_data_root).grid(
            row=2, column=2
        )

        source_frame = ttk.LabelFrame(container, text="Read-only Plex media folders", padding=8)
        source_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        source_frame.columnconfigure(0, weight=1)
        self.sources = ttk.Treeview(source_frame, columns=("path",), show="headings", height=4)
        self.sources.heading("path", text="Windows folder available to MediaClipMakarr")
        self.sources.column("path", width=690)
        self.sources.grid(row=0, column=0, rowspan=2, sticky="nsew")
        ttk.Button(source_frame, text="Add folder…", command=self.add_source).grid(
            row=0, column=1, padx=(8, 0), sticky="ew"
        )
        ttk.Button(source_frame, text="Remove", command=self.remove_source).grid(
            row=1, column=1, padx=(8, 0), sticky="new"
        )

        mapping_frame = ttk.LabelFrame(
            container,
            text="Plex path mappings (first match wins)",
            padding=8,
        )
        mapping_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        mapping_frame.columnconfigure(0, weight=1)
        self.mappings = ttk.Treeview(
            mapping_frame,
            columns=("plex", "local"),
            show="headings",
            height=5,
        )
        self.mappings.heading("plex", text="Path prefix reported by Plex")
        self.mappings.heading("local", text="Matching Windows source folder")
        self.mappings.column("plex", width=340)
        self.mappings.column("local", width=390)
        self.mappings.grid(row=0, column=0, columnspan=4, sticky="nsew")
        self.mappings.bind("<<TreeviewSelect>>", self.select_mapping)
        ttk.Entry(mapping_frame, textvariable=self.mapping_plex).grid(
            row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 4)
        )
        ttk.Entry(mapping_frame, textvariable=self.mapping_local).grid(
            row=1, column=1, sticky="ew", pady=(8, 0), padx=4
        )
        ttk.Button(mapping_frame, text="Browse…", command=self.browse_mapping_local).grid(
            row=1, column=2, pady=(8, 0), padx=4
        )
        ttk.Button(mapping_frame, text="Add / update", command=self.add_or_update_mapping).grid(
            row=1, column=3, pady=(8, 0), padx=(4, 0)
        )
        mapping_buttons = ttk.Frame(mapping_frame)
        mapping_buttons.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(mapping_buttons, text="Remove", command=self.remove_mapping).pack(side="left")
        ttk.Button(mapping_buttons, text="Move up", command=lambda: self.move_mapping(-1)).pack(
            side="left", padx=6
        )
        ttk.Button(mapping_buttons, text="Move down", command=lambda: self.move_mapping(1)).pack(
            side="left"
        )

        tools_frame = ttk.LabelFrame(container, text="Media tools", padding=8)
        tools_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        tools_frame.columnconfigure(1, weight=1)
        ttk.Label(tools_frame, text="FFmpeg").grid(row=0, column=0, sticky="w")
        ttk.Entry(tools_frame, textvariable=self.ffmpeg_path).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(
            tools_frame,
            text="Browse…",
            command=lambda: self.browse_tool(self.ffmpeg_path),
        ).grid(
            row=0, column=2
        )
        ttk.Label(tools_frame, text="FFprobe").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(tools_frame, textvariable=self.ffprobe_path).grid(
            row=1, column=1, sticky="ew", padx=8, pady=(6, 0)
        )
        ttk.Button(
            tools_frame,
            text="Browse…",
            command=lambda: self.browse_tool(self.ffprobe_path),
        ).grid(row=1, column=2, pady=(6, 0))
        tool_buttons = ttk.Frame(tools_frame)
        tool_buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(tool_buttons, text="Detect on PATH", command=self.detect_tools).pack(side="left")
        self.download_button = ttk.Button(
            tool_buttons,
            text=f"Download Jellyfin FFmpeg {JELLYFIN_FFMPEG_VERSION}",
            command=self.download_tools,
        )
        self.download_button.pack(side="left", padx=8)

        ports = ttk.Frame(container)
        ports.grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(ports, text="API port").pack(side="left")
        ttk.Entry(ports, textvariable=self.api_port, width=8).pack(side="left", padx=(6, 18))
        ttk.Label(ports, text="Web port").pack(side="left")
        ttk.Entry(ports, textvariable=self.web_port, width=8).pack(side="left", padx=6)

        ttk.Separator(container).grid(row=7, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(container, textvariable=self.status, wraplength=700).grid(
            row=8, column=0, columnspan=2, sticky="w"
        )
        ttk.Button(container, text="Save Windows profile", command=self.save).grid(
            row=8, column=2, sticky="e"
        )
        container.rowconfigure(4, weight=1)

    def _populate(self) -> None:
        for source in self.profile.source_dirs:
            self.sources.insert("", "end", values=(str(source),))
        for mapping in self.profile.mappings:
            self.mappings.insert(
                "", "end", values=(mapping.plex_prefix, str(mapping.local_prefix))
            )

    def browse_data_root(self) -> None:
        if selected := filedialog.askdirectory(initialdir=self.data_root.get() or PROJECT_ROOT):
            self.data_root.set(selected)

    def add_source(self) -> None:
        selected = filedialog.askdirectory(title="Choose a read-only Plex media folder")
        if not selected:
            return
        existing = {self.sources.item(item, "values")[0] for item in self.sources.get_children()}
        if selected not in existing:
            self.sources.insert("", "end", values=(selected,))
            self.mappings.insert("", "end", values=(selected, selected))

    def remove_source(self) -> None:
        for item in self.sources.selection():
            self.sources.delete(item)

    def browse_mapping_local(self) -> None:
        if selected := filedialog.askdirectory(title="Choose the matching Windows source folder"):
            self.mapping_local.set(selected)

    def select_mapping(self, _event: object = None) -> None:
        selection = self.mappings.selection()
        if selection:
            plex, local = self.mappings.item(selection[0], "values")
            self.mapping_plex.set(plex)
            self.mapping_local.set(local)

    def add_or_update_mapping(self) -> None:
        plex = self.mapping_plex.get().strip()
        local = self.mapping_local.get().strip()
        if not plex or not local:
            messagebox.showerror("Incomplete mapping", "Enter both Plex and Windows prefixes.")
            return
        selection = self.mappings.selection()
        if selection:
            self.mappings.item(selection[0], values=(plex, local))
        else:
            self.mappings.insert("", "end", values=(plex, local))
        self.mapping_plex.set("")
        self.mapping_local.set("")

    def remove_mapping(self) -> None:
        for item in self.mappings.selection():
            self.mappings.delete(item)

    def move_mapping(self, offset: int) -> None:
        selection = self.mappings.selection()
        if not selection:
            return
        item = selection[0]
        index = self.mappings.index(item)
        destination = max(0, min(len(self.mappings.get_children()) - 1, index + offset))
        self.mappings.move(item, "", destination)

    def browse_tool(self, variable: StringVar) -> None:
        if selected := filedialog.askopenfilename(filetypes=[("Windows executable", "*.exe")]):
            variable.set(selected)

    def detect_tools(self) -> None:
        ffmpeg, ffprobe = detect_media_tools()
        if ffmpeg:
            self.ffmpeg_path.set(str(ffmpeg))
        if ffprobe:
            self.ffprobe_path.set(str(ffprobe))
        tool_status = inspect_media_tools(ffmpeg, ffprobe)
        self.status.set(tool_status.message)

    def download_tools(self) -> None:
        self.download_button.state(["disabled"])
        self.status.set("Downloading and verifying Jellyfin FFmpeg…")

        def report(received: int, total: int) -> None:
            percent = int(received * 100 / total) if total else 0
            self.root.after(0, self.status.set, f"Downloading Jellyfin FFmpeg… {percent}%")

        def worker() -> None:
            try:
                asset = select_download_asset()
                destination = TOOLS_ROOT / (
                    f"jellyfin-ffmpeg-{JELLYFIN_FFMPEG_VERSION}-{asset.architecture}"
                )
                ffmpeg, ffprobe = download_jellyfin_ffmpeg(
                    destination, asset=asset, progress=report
                )
            except Exception as error:
                self.root.after(0, self._download_failed, str(error))
            else:
                self.root.after(0, self._download_finished, ffmpeg, ffprobe)

        threading.Thread(target=worker, name="jellyfin-ffmpeg-download", daemon=True).start()

    def _download_failed(self, message: str) -> None:
        self.download_button.state(["!disabled"])
        self.status.set("Jellyfin FFmpeg download failed.")
        messagebox.showerror("Download failed", message)

    def _download_finished(self, ffmpeg: Path, ffprobe: Path) -> None:
        self.download_button.state(["!disabled"])
        self.ffmpeg_path.set(str(ffmpeg))
        self.ffprobe_path.set(str(ffprobe))
        self.status.set("Pinned Jellyfin FFmpeg downloaded and SHA-256 verified.")

    def _profile_from_form(self) -> WindowsProfile:
        return WindowsProfile(
            data_root=Path(self.data_root.get().strip()),
            source_dirs=[
                Path(self.sources.item(item, "values")[0])
                for item in self.sources.get_children()
            ],
            mappings=[
                PathMapping(
                    self.mappings.item(item, "values")[0],
                    Path(self.mappings.item(item, "values")[1]),
                )
                for item in self.mappings.get_children()
            ],
            ffmpeg_path=Path(self.ffmpeg_path.get().strip()),
            ffprobe_path=Path(self.ffprobe_path.get().strip()),
            api_port=int(self.api_port.get()),
            web_port=int(self.web_port.get()),
        )

    def save(self) -> None:
        try:
            profile = self._profile_from_form()
            validate_windows_profile(profile)
            tool_status = inspect_media_tools(profile.ffmpeg_path, profile.ffprobe_path)
            if not tool_status.available:
                raise ValueError(tool_status.message)
            if not tool_status.jellyfin_build and not messagebox.askyesno(
                "Non-Jellyfin FFmpeg",
                f"{tool_status.message}\n\nSave this profile anyway?",
            ):
                return
            write_windows_profile(profile)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            messagebox.showerror("Configuration error", str(error))
            return
        self.status.set(f"Saved {WINDOWS_PROFILE_PATH}. Run: python launch.py")
        messagebox.showinfo(
            "Windows profile saved",
            "Start the app with: python launch.py",
        )


def main() -> None:
    if os.name != "nt":
        raise SystemExit("The Windows setup wizard can only run on Windows.")
    root = Tk()
    WindowsSetupWizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
