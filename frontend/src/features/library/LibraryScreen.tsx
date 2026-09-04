import CloseRounded from "@mui/icons-material/CloseRounded";
import CollectionsRounded from "@mui/icons-material/CollectionsRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import FilterListRounded from "@mui/icons-material/FilterListRounded";
import FilterAltOffRounded from "@mui/icons-material/FilterAltOffRounded";
import GridViewRounded from "@mui/icons-material/GridViewRounded";
import ListRounded from "@mui/icons-material/ListRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import {
  Alert,
  Autocomplete,
  Badge,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  FormControl,
  IconButton,
  InputLabel,
  Menu,
  MenuItem,
  Pagination,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  deleteClip,
  fetchClipFilterOptions,
  fetchClips,
  fetchJob,
  fetchSettings,
  updateClipMetadata,
  uploadClipToImmich,
} from "../../api";
import type { ClipMetadataUpdate, ClipRecord, ImmichUploadJobResult, ImmichUploadJobSummary, JobSnapshot } from "../../types";
import { DeleteClipDialog, MetadataDialog } from "./ClipDialogs";

const NONTERMINAL_JOB_STATES = new Set(["QUEUED", "RUNNING", "FINALIZING"]);

type ViewMode = "grid" | "list";
type ThumbnailSize = "small" | "medium" | "large";
type GroupMode = "none" | "library" | "library-media" | "library-episode";
type PageSize = 25 | 50 | 100 | "all";
type MediaFilterOption = { kind: "movie" | "show"; name: string };
type ClipGroup = { key: string; label: string; items: ClipRecord[] };
type EpisodeFilterOption = {
  show_name: string;
  title: string;
  season_number: number | null;
  episode_number: number | null;
};

const widths: Record<ThumbnailSize, number> = { small: 160, medium: 220, large: 320 };

function currentParams() {
  return new URLSearchParams(window.location.search);
}

function writeParams(changes: Record<string, string | string[] | null>) {
  const params = currentParams();
  Object.entries(changes).forEach(([key, value]) => {
    params.delete(key);
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
    else if (value) params.set(key, value);
  });
  window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
}

function storedValue<T extends string>(key: string, fallback: T): T {
  const value = window.localStorage.getItem(key);
  return (value || fallback) as T;
}

function ClipThumbnail({ clip, expanded, onActivate }: { clip: ClipRecord; expanded: boolean; onActivate: () => void }) {
  const [attempt, setAttempt] = useState(0);
  return (
    <Box
      component="button"
      type="button"
      aria-label={expanded ? `Play ${clip.title}` : `Show details for ${clip.title}`}
      onClick={(event) => { event.stopPropagation(); onActivate(); }}
      sx={{ border: 0, p: 0, m: 0, width: "100%", display: "block", position: "relative", overflow: "hidden", bgcolor: "grey.900", color: "common.white", cursor: "pointer" }}
    >
      {attempt >= 10 ? (
        <Box sx={{ width: "100%", aspectRatio: "16 / 9", display: "grid", placeItems: "center" }}><Typography variant="caption" color="text.secondary">Thumbnail unavailable</Typography></Box>
      ) : (
        <Box component="img" src={`${clip.thumbnail_url}?revision=${clip.revision}&attempt=${attempt}`} alt="" onError={() => window.setTimeout(() => setAttempt((value) => value + 1), 2_000)} sx={{ width: "100%", aspectRatio: "16 / 9", objectFit: "cover", display: "block" }} />
      )}
      <Box sx={{ position: "absolute", right: 5, bottom: 5, px: 0.6, py: 0.15, borderRadius: 0.75, bgcolor: "rgba(0, 0, 0, 0.78)", fontSize: "0.7rem", fontWeight: 700, lineHeight: 1.35 }}>
        {formatDuration(clip.duration_ms)}
      </Box>
      {expanded && (
        <Box sx={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", bgcolor: "rgba(0, 0, 0, 0.12)" }}>
          <Box sx={{ display: "grid", placeItems: "center", borderRadius: "50%", bgcolor: "rgba(0, 0, 0, 0.66)", width: 44, height: 44 }}><PlayArrowRounded fontSize="large" /></Box>
        </Box>
      )}
    </Box>
  );
}

function ClipAction({ label, icon, large, href, color = "primary", disabled, onClick }: { label: string; icon: ReactNode; large: boolean; href?: string; color?: "primary" | "error"; disabled?: boolean; onClick?: () => void }) {
  const stopAndRun = (event: React.MouseEvent) => { event.stopPropagation(); onClick?.(); };
  if (large) return <Button size="small" color={color} disabled={disabled} startIcon={icon} href={href} onClick={stopAndRun}>{label}</Button>;
  if (href) return <Tooltip title={label}><IconButton size="small" color={color} disabled={disabled} component="a" href={href} aria-label={label} onClick={stopAndRun}>{icon}</IconButton></Tooltip>;
  return <Tooltip title={label}><span><IconButton size="small" color={color} disabled={disabled} aria-label={label} onClick={stopAndRun}>{icon}</IconButton></span></Tooltip>;
}

// The theme-matched blue from src/assets/immich.svg — inlined (rather than loaded
// via <img>) so its fill can switch to the theme's error red before upload.
const IMMICH_ICON_BLUE = "#61a6fa";
const IMMICH_ICON_PATH =
  "M238.8 155.5c33.5 29.7 60.5 61.5 77.9 91.5 29.9-53.4 49.8-116.9 50.1-157.3v-.8c0-59.8-59.7-83.1-111.1-83.1S144.6 29 144.6 88.8V92c28.7 12.8 62.6 35.6 94.2 63.5M55.9 318.6c21-23.3 53.1-48.6 89.4-69.9 38.6-22.7 77.2-38.6 111.1-45.8-41.6-44.9-95.8-83.5-134.1-96.2-.3-.1-.5-.2-.7-.2-57-18.7-97.6 30.9-113.5 79.8S-4.1 299.1 52.8 317.6c.8.2 1.8.6 3.1 1m448-133.2C488 136.6 447.4 87 390.5 105.5c-.8.3-1.8.6-3.1 1-3.3 31.2-14.4 70.5-31.2 109.1-17.9 41.1-39.8 76.6-62.9 102.4 60 11.9 126.5 11.3 165.1-1 .3-.1.5-.2.7-.2 57-18.6 60.6-82.5 44.8-131.4M205 366.3c-9.7-43.7-12.8-85.3-9.3-119.8-55.5 25.7-109 65.3-133 97.8-.2.2-.3.4-.5.6-35.2 48.4-.6 102.3 41 132.5s103.5 46.4 138.7-1.9c.5-.7 1.1-1.5 1.9-2.6-15.6-27.1-29.7-65.5-38.8-106.6m243.8-24.4c-30.7 6.5-71.5 8.1-113.4 4-44.6-4.3-85.1-14.2-116.8-28.2 7.2 60.8 28.4 123.8 51.9 156.7.2.2.3.4.5.6 35.2 48.4 97.1 32.2 138.7 1.9 41.6-30.2 76.2-84.1 41-132.5-.5-.6-1.1-1.4-1.9-2.5";

function ImmichIcon({ uploaded }: { uploaded: boolean }) {
  const theme = useTheme();
  return (
    <Box component="svg" viewBox="0 0 512 512" sx={{ width: 20, height: 20, display: "block" }}>
      <path d={IMMICH_ICON_PATH} fill={uploaded ? IMMICH_ICON_BLUE : theme.palette.error.main} />
    </Box>
  );
}

function ImmichStatusChip({ clip }: { clip: ClipRecord }) {
  const job = clip.immich_upload_job;
  if (job && NONTERMINAL_JOB_STATES.has(job.state)) {
    return <Chip size="small" variant="outlined" icon={<CircularProgress size={12} />} label="Uploading…" />;
  }
  if (job?.state === "PARTIAL" || job?.state === "FAILED") {
    const label = job.state === "PARTIAL" ? "Partial" : "Failed";
    const detail = job.error ? `${job.error.code}: ${job.error.message}` : label;
    return <Tooltip title={detail}><Chip size="small" color={job.state === "PARTIAL" ? "warning" : "error"} label={label} /></Tooltip>;
  }
  if (job?.state === "SUCCEEDED" || clip.immich_asset_id) {
    return <Chip size="small" color="success" label="Uploaded" />;
  }
  return null;
}

function ClipCard({ clip, mode, size, listThumbnailWidth, expanded, onToggle, onPlay, onEdit, onDelete, onUploadImmich, immichConfigured }: { clip: ClipRecord; mode: ViewMode; size: ThumbnailSize; listThumbnailWidth: number; expanded: boolean; onToggle: () => void; onPlay: (clip: ClipRecord) => void; onEdit: (clip: ClipRecord) => void; onDelete: (clip: ClipRecord) => void; onUploadImmich: (clip: ClipRecord) => void; immichConfigured: boolean }) {
  const metadata = clipMetadata(clip);
  const uploadJob = clip.immich_upload_job;
  const uploading = Boolean(uploadJob && NONTERMINAL_JOB_STATES.has(uploadJob.state));
  const uploadLabel = uploading
    ? "Uploading to Immich…"
    : uploadJob?.state === "PARTIAL" || uploadJob?.state === "FAILED"
      ? "Retry Immich upload"
      : clip.immich_asset_id
        ? "Re-upload to Immich"
        : "Upload to Immich";
  const isList = mode === "list";
  const detailsVisible = isList || expanded;
  const [playArmed, setPlayArmed] = useState(false);
  useEffect(() => {
    setPlayArmed(false);
    if (!expanded) return;
    const timeout = window.setTimeout(() => setPlayArmed(true), 350);
    return () => window.clearTimeout(timeout);
  }, [expanded]);
  return (
    <Card
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={onToggle}
      onKeyDown={(event) => {
        if ((event.key === "Enter" || event.key === " ") && event.target === event.currentTarget) { event.preventDefault(); onToggle(); }
      }}
      sx={{ cursor: "pointer", display: isList ? "grid" : "block", gridTemplateColumns: isList ? `${listThumbnailWidth}px minmax(0, 1fr)` : undefined, alignItems: "start", alignSelf: "start", width: "100%", minWidth: 0, maxWidth: "100%", height: "fit-content", overflow: "hidden", transition: "box-shadow 120ms ease", "&:hover": { boxShadow: 5 } }}
    >
      <ClipThumbnail clip={clip} expanded={expanded} onActivate={() => {
        if (!expanded) onToggle();
        else if (playArmed) onPlay(clip);
      }} />
      <Box sx={{ minWidth: 0 }}>
        <CardContent sx={{ px: 1, py: 0.75, "&:last-child": { pb: detailsVisible ? 0.5 : 0.75 } }}>
          <Typography variant={size === "large" ? "subtitle1" : "body2"} fontWeight={700} lineHeight={1.25} noWrap={!detailsVisible} title={clip.title}>{clip.title}</Typography>
          <Collapse in={detailsVisible} unmountOnExit>
            <Stack spacing={0.15} sx={{ pt: 0.75 }}>
              {metadata.map((line) => <Typography key={line} variant="caption" color="text.secondary" lineHeight={1.35}>{line}</Typography>)}
            </Stack>
          </Collapse>
        </CardContent>
        <Collapse in={detailsVisible} unmountOnExit>
          <CardActions sx={{ px: 0.5, pt: 0, pb: 0.5, gap: size === "large" ? 0 : 0.25, flexWrap: "wrap", alignItems: "center" }}>
            <ClipAction label="Play" icon={<PlayArrowRounded />} large={size === "large"} onClick={() => onPlay(clip)} />
            <ClipAction label="Edit" icon={<EditRounded />} large={size === "large"} onClick={() => onEdit(clip)} />
            <ClipAction label="Download" icon={<DownloadRounded />} large={size === "large"} href={clip.download_url} />
            {immichConfigured && (
              <ClipAction
                label={uploadLabel}
                icon={<ImmichIcon uploaded={Boolean(clip.immich_asset_id)} />}
                large={size === "large"}
                disabled={uploading}
                onClick={() => onUploadImmich(clip)}
              />
            )}
            <ClipAction label="Delete" icon={<DeleteOutlineRounded />} large={size === "large"} color="error" onClick={() => onDelete(clip)} />
            <ImmichStatusChip clip={clip} />
          </CardActions>
        </Collapse>
      </Box>
    </Card>
  );
}

export function LibraryScreen() {
  const theme = useTheme();
  const compactLayout = useMediaQuery(theme.breakpoints.down("sm"));
  const initial = useMemo(currentParams, []);
  const [search, setSearch] = useState(initial.get("search") ?? "");
  const [library, setLibrary] = useState(initial.get("library") ?? "");
  const [mediaType, setMediaType] = useState(initial.get("media_type") ?? "");
  const [movies, setMovies] = useState<string[]>(initial.getAll("movie"));
  const [shows, setShows] = useState<string[]>(initial.getAll("show"));
  const [episodes, setEpisodes] = useState<string[]>(initial.getAll("episode"));
  const [sort, setSort] = useState(initial.get("sort") ?? "newest");
  const [page, setPage] = useState(Number(initial.get("page") ?? 1));
  const [pageSize, setPageSize] = useState<PageSize>(() => parsePageSize(initial.get("page_size")));
  const [mode, setMode] = useState<ViewMode>(() => storedValue("mcm.library.mode", "grid"));
  const [size, setSize] = useState<ThumbnailSize>(() => storedValue("mcm.library.size", "medium"));
  const [groupMode, setGroupMode] = useState<GroupMode>("none");
  const [groupAnchor, setGroupAnchor] = useState<HTMLElement | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [expandedClipId, setExpandedClipId] = useState<string | null>(null);
  const [playing, setPlaying] = useState<ClipRecord | null>(null);
  const [editing, setEditing] = useState<ClipRecord | null>(null);
  const [deleting, setDeleting] = useState<ClipRecord | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const [editJobId, setEditJobId] = useState<string | null>(null);
  const layoutRef = useRef<HTMLDivElement>(null);
  const [layoutWidth, setLayoutWidth] = useState(0);
  const queryClient = useQueryClient();

  useLayoutEffect(() => {
    const element = layoutRef.current;
    if (!element) return;
    const updateWidth = () => setLayoutWidth(element.getBoundingClientRect().width);
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const navigate = () => {
      setPage(Number(currentParams().get("page") ?? 1));
      setPlaying(null);
    };
    window.addEventListener("popstate", navigate);
    return () => window.removeEventListener("popstate", navigate);
  }, []);
  useEffect(() => { localStorage.setItem("mcm.library.mode", mode); }, [mode]);
  useEffect(() => { localStorage.setItem("mcm.library.size", size); }, [size]);
  useEffect(() => {
    const timeout = window.setTimeout(() => { setPage(1); writeParams({ search: search || null, page: null }); }, 250);
    return () => window.clearTimeout(timeout);
  }, [search]);

  const clipsQueryKey = ["clips", page, pageSize, search, library, mediaType, movies, shows, episodes, sort];
  const clips = useQuery({
    queryKey: clipsQueryKey,
    queryFn: () => fetchClips({ page, pageSize, search, library, mediaType, media: [...movies, ...shows], episode: episodes, sort }),
    // Only keep polling while at least one visible clip has an Immich upload still in
    // flight — avoids per-card queries while still recovering state across a reload.
    refetchInterval: (query) => {
      const hasPendingUpload = query.state.data?.items.some(
        (item) => item.immich_upload_job && NONTERMINAL_JOB_STATES.has(item.immich_upload_job.state)
      );
      return hasPendingUpload ? 1_000 : false;
    },
  });
  const filterOptions = useQuery({ queryKey: ["clip-filter-options"], queryFn: fetchClipFilterOptions });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const immichConfigured = Boolean(settings.data?.immich_url && settings.data?.immich_api_key_configured);
  const editJob = useQuery({
    queryKey: ["job", editJobId],
    queryFn: () => fetchJob(editJobId!),
    enabled: Boolean(editJobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["SUCCEEDED", "FAILED", "PARTIAL"].includes(state) ? false : 1_000;
    },
  });
  useEffect(() => {
    if (editJob.data?.state === "SUCCEEDED") {
      void queryClient.invalidateQueries({ queryKey: ["clips"] });
      void queryClient.invalidateQueries({ queryKey: ["clip-libraries"] });
      setEditing(null);
      setEditJobId(null);
    }
  }, [editJob.data?.state, queryClient]);

  const editMutation = useMutation({ mutationFn: ({ clip, update }: { clip: ClipRecord; update: ClipMetadataUpdate }) => updateClipMetadata(clip.id, update), onSuccess: (job) => setEditJobId(job.id) });
  const uploadMutation = useMutation({
    mutationFn: (clip: ClipRecord) => uploadClipToImmich(clip.id),
    onSuccess: (job: JobSnapshot, clip: ClipRecord) => {
      // Seed the shared clips cache with the fresh job snapshot so the conditional
      // refetchInterval above picks it up on the next tick without waiting on a
      // full round trip.
      const summary: ImmichUploadJobSummary = {
        id: job.id,
        state: job.state,
        stage: job.stage,
        progress: job.progress,
        message: job.message,
        result: job.result as ImmichUploadJobResult | null,
        error: job.error,
      };
      queryClient.setQueryData(clipsQueryKey, (page: typeof clips.data) =>
        page && {
          ...page,
          items: page.items.map((item) =>
            item.id === clip.id ? { ...item, immich_upload_job: summary } : item
          ),
        }
      );
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (clip: ClipRecord) => deleteClip(clip.id, clip.revision),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["clips"] });
      void queryClient.invalidateQueries({ queryKey: ["clip-libraries"] });
      setExpandedClipId(null);
      setDeleting(null);
      setDeleteNotice(result.cleanup_warnings.length ? result.cleanup_warnings.join(" ") : null);
    },
  });
  const visibleClips = clips.data?.items ?? [];
  const plexLibraryCase = useMemo(() => new Map(
    (filterOptions.data?.libraries ?? []).map((name) => [name.toLocaleLowerCase(), name])
  ), [filterOptions.data?.libraries]);
  const libraryGroups = useMemo(
    () => groupClips(visibleClips, (clip) => {
      const name = plexLibraryCase.get(clip.library.toLocaleLowerCase()) ?? clip.library;
      return { key: name.toLocaleLowerCase(), label: name };
    }),
    [visibleClips, plexLibraryCase]
  );
  const mediaOptions = useMemo<MediaFilterOption[]>(() => [
    ...(filterOptions.data?.movies ?? []).map((name) => ({ kind: "movie" as const, name })),
    ...(filterOptions.data?.shows ?? []).map((name) => ({ kind: "show" as const, name })),
  ], [filterOptions.data?.movies, filterOptions.data?.shows]);
  const selectedMedia = useMemo<MediaFilterOption[]>(() => [
    ...movies.map((name) => ({ kind: "movie" as const, name })),
    ...shows.map((name) => ({ kind: "show" as const, name })),
  ], [movies, shows]);
  const episodeOptions = useMemo<EpisodeFilterOption[]>(() => {
    const selectedShows = new Set(shows);
    return (filterOptions.data?.episodes ?? []).filter((item) => selectedShows.has(item.show_name));
  }, [filterOptions.data?.episodes, shows]);
  const selectedEpisodeOptions = useMemo<EpisodeFilterOption[]>(() => episodes.map(
    (title) => episodeOptions.find((option) => option.title === title)
      ?? { show_name: "", title, season_number: null, episode_number: null }
  ), [episodeOptions, episodes]);
  const activeFilterCount = [search, library, mediaType].filter(Boolean).length
    + movies.length + shows.length + episodes.length
    + (sort === "newest" ? 0 : 1);
  const gridColumnCount = cardColumnCount(size, layoutWidth);
  const gridGap = compactLayout ? 6 : 10;
  const listThumbnailWidth = layoutWidth
    ? (layoutWidth - gridGap * (gridColumnCount - 1)) / gridColumnCount
    : widths[size];
  const listRowWidth = Math.min(layoutWidth || listThumbnailWidth * 3, listThumbnailWidth * 3);

  const updateFilter = (key: string, value: string, setter: (value: string) => void) => {
    setter(value);
    setPage(1);
    writeParams({ [key]: value || null, page: null });
  };
  const updateMediaFilter = (values: MediaFilterOption[]) => {
    const nextMovies = values.filter((item) => item.kind === "movie").map((item) => item.name);
    const nextShows = values.filter((item) => item.kind === "show").map((item) => item.name);
    const selectedShowNames = new Set(nextShows);
    const allowedEpisodes = new Set(
      (filterOptions.data?.episodes ?? [])
        .filter((item) => selectedShowNames.has(item.show_name))
        .map((item) => item.title)
    );
    const nextEpisodes = episodes.filter((title) => allowedEpisodes.has(title));
    setMovies(nextMovies);
    setShows(nextShows);
    setEpisodes(nextEpisodes);
    setPage(1);
    writeParams({ movie: nextMovies, show: nextShows, episode: nextEpisodes, page: null });
  };
  const updateEpisodeFilter = (values: EpisodeFilterOption[]) => {
    const titles = [...new Set(values.map((item) => item.title))];
    setEpisodes(titles);
    setPage(1);
    writeParams({ episode: titles, page: null });
  };
  const clearFilters = () => {
    setSearch("");
    setLibrary("");
    setMediaType("");
    setMovies([]);
    setShows([]);
    setEpisodes([]);
    setSort("newest");
    setPage(1);
    writeParams({ search: null, library: null, media_type: null, media: null, movie: null, show: null, episode: null, sort: null, page: null });
  };
  const updatePageSize = (value: string) => {
    const next = parsePageSize(value);
    setPageSize(next);
    setPage(1);
    writeParams({ page_size: next === 25 ? null : String(next), page: null });
  };
  const selectGroupMode = (next: GroupMode) => {
    setGroupMode(next);
    setGroupAnchor(null);
  };
  const toggleGroup = (key: string) => setCollapsedGroups((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
  const openPlayer = (clip: ClipRecord) => {
    window.history.pushState({ ...window.history.state, mediaClipMakarrPlayer: true }, "", window.location.href);
    setPlaying(clip);
  };
  const closePlayer = () => {
    if (window.history.state?.mediaClipMakarrPlayer) window.history.back();
    else setPlaying(null);
  };
  const renderClipCards = (items: ClipRecord[]) => mode === "grid" ? (
    <Box sx={{ display: "grid", gridTemplateColumns: `repeat(${gridColumnCount}, minmax(0, 1fr))`, alignItems: "start", gap: { xs: 0.75, sm: 1.25 } }}>
      {columnize(items, gridColumnCount).map((column, columnIndex) => (
        <Stack key={columnIndex} spacing={{ xs: 0.75, sm: 1.25 }} sx={{ minWidth: 0, maxWidth: "100%" }}>
          {column.map((clip) => (
            <ClipCard key={clip.id} clip={clip} mode={mode} size={size} listThumbnailWidth={listThumbnailWidth} expanded={expandedClipId === clip.id} onToggle={() => setExpandedClipId((current) => current === clip.id ? null : clip.id)} onPlay={openPlayer} onEdit={setEditing} onDelete={(target) => { deleteMutation.reset(); setDeleting(target); }} onUploadImmich={(target) => uploadMutation.mutate(target)} immichConfigured={immichConfigured} />
          ))}
        </Stack>
      ))}
    </Box>
  ) : (
    <Box sx={{ display: "grid", alignItems: "start", alignSelf: "center", gap: 1, width: "100%", maxWidth: listRowWidth, mx: "auto" }}>
      {items.map((clip) => (
        <ClipCard key={clip.id} clip={clip} mode={mode} size={size} listThumbnailWidth={listThumbnailWidth} expanded={expandedClipId === clip.id} onToggle={() => setExpandedClipId((current) => current === clip.id ? null : clip.id)} onPlay={openPlayer} onEdit={setEditing} onDelete={(target) => { deleteMutation.reset(); setDeleting(target); }} onUploadImmich={(target) => uploadMutation.mutate(target)} immichConfigured={immichConfigured} />
      ))}
    </Box>
  );

  return (
    <Stack ref={layoutRef} spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} justifyContent="space-between" alignItems={{ xs: "stretch", sm: "center" }}>
        <Typography color="text.secondary">{clips.data?.total ?? 0} clips</Typography>
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" justifyContent={{ xs: "flex-start", sm: "flex-end" }}>
          <Tooltip title="Group by">
            <IconButton aria-label="Group by" color={groupMode === "none" ? "default" : "primary"} onClick={(event) => setGroupAnchor(event.currentTarget)}>
              <CollectionsRounded />
            </IconButton>
          </Tooltip>
          <Menu anchorEl={groupAnchor} open={Boolean(groupAnchor)} onClose={() => setGroupAnchor(null)}>
            <MenuItem selected={groupMode === "none"} onClick={() => selectGroupMode("none")}>None</MenuItem>
            <MenuItem selected={groupMode === "library"} onClick={() => selectGroupMode("library")}>Library</MenuItem>
            <MenuItem selected={groupMode === "library-media"} onClick={() => selectGroupMode("library-media")}>{"Library\\Movie or Series"}</MenuItem>
            <MenuItem selected={groupMode === "library-episode"} onClick={() => selectGroupMode("library-episode")}>{"Library\\Movie or Series\\Episode"}</MenuItem>
          </Menu>
          <Tooltip title={filtersOpen ? "Hide filters" : "Show filters"}>
            <IconButton aria-label={filtersOpen ? "Hide filters" : "Show filters"} color={filtersOpen || activeFilterCount ? "primary" : "default"} onClick={() => setFiltersOpen((value) => !value)}>
              <Badge badgeContent={activeFilterCount} color="primary"><FilterListRounded /></Badge>
            </IconButton>
          </Tooltip>
          <Tooltip title="Clear filters">
            <span><IconButton aria-label="Clear filters" disabled={activeFilterCount === 0} onClick={clearFilters}><FilterAltOffRounded /></IconButton></span>
          </Tooltip>
          <ToggleButtonGroup exclusive size="small" value={size} onChange={(_, value) => value && setSize(value)}><ToggleButton value="small">S</ToggleButton><ToggleButton value="medium">M</ToggleButton><ToggleButton value="large">L</ToggleButton></ToggleButtonGroup>
          <FormControl size="small" sx={{ minWidth: 92 }}>
            <InputLabel>Show</InputLabel>
            <Select label="Show" value={String(pageSize)} onChange={(event) => updatePageSize(event.target.value)}>
              <MenuItem value="25">25</MenuItem>
              <MenuItem value="50">50</MenuItem>
              <MenuItem value="100">100</MenuItem>
              <MenuItem value="all">All</MenuItem>
            </Select>
          </FormControl>
          <ToggleButtonGroup exclusive size="small" value={mode} onChange={(_, value) => value && setMode(value)}><ToggleButton value="grid" aria-label="Grid view"><GridViewRounded /></ToggleButton><ToggleButton value="list" aria-label="List view"><ListRounded /></ToggleButton></ToggleButtonGroup>
        </Stack>
      </Stack>
      <Collapse in={filtersOpen} unmountOnExit>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
            <TextField fullWidth label="Search metadata" value={search} onChange={(event) => setSearch(event.target.value)} />
            <FormControl sx={{ minWidth: 180 }}><InputLabel>Library</InputLabel><Select label="Library" value={library} onChange={(event) => updateFilter("library", event.target.value, setLibrary)}><MenuItem value="">All libraries</MenuItem>{filterOptions.data?.libraries.map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}</Select></FormControl>
            <FormControl sx={{ minWidth: 160 }}><InputLabel>Type</InputLabel><Select label="Type" value={mediaType} onChange={(event) => updateFilter("media_type", event.target.value, setMediaType)}><MenuItem value="">All types</MenuItem><MenuItem value="movie">Movies</MenuItem><MenuItem value="episode">Episodes</MenuItem><MenuItem value="video">Videos</MenuItem></Select></FormControl>
            <FormControl sx={{ minWidth: 180 }}><InputLabel>Sort</InputLabel><Select label="Sort" value={sort} onChange={(event) => updateFilter("sort", event.target.value, setSort)}><MenuItem value="newest">Newest</MenuItem><MenuItem value="oldest">Oldest</MenuItem><MenuItem value="title_asc">Title A–Z</MenuItem><MenuItem value="title_desc">Title Z–A</MenuItem><MenuItem value="duration_asc">Shortest</MenuItem><MenuItem value="duration_desc">Longest</MenuItem></Select></FormControl>
          </Stack>
          <Autocomplete
            multiple
            options={mediaOptions}
            value={selectedMedia}
            getOptionLabel={(option) => `${option.kind === "movie" ? "🎞️" : "📺"} ${option.name}`}
            isOptionEqualToValue={(option, value) => option.kind === value.kind && option.name === value.name}
            onChange={(_, values) => updateMediaFilter(values)}
            renderInput={(params) => <TextField {...params} label="Movies and shows" placeholder="Type to filter movies and shows" />}
            sx={{ width: "100%", minWidth: 0 }}
          />
          {shows.length > 0 && (
            <Autocomplete
              multiple
              options={episodeOptions}
              value={selectedEpisodeOptions}
              getOptionLabel={(option) => `${episodeCode(option)}${episodeCode(option) ? " · " : ""}${option.title}`}
              isOptionEqualToValue={(option, value) => option.show_name === value.show_name && option.title === value.title && option.season_number === value.season_number && option.episode_number === value.episode_number}
              onChange={(_, values) => updateEpisodeFilter(values)}
              renderInput={(params) => <TextField {...params} label="Episode titles" placeholder="Type to filter episodes" />}
              sx={{ width: "100%", minWidth: 0 }}
            />
          )}
        </Stack>
      </Collapse>
      {deleteNotice && <Alert severity="warning" onClose={() => setDeleteNotice(null)}>{deleteNotice}</Alert>}
      {clips.error && <Alert severity="error">{clips.error.message}</Alert>}
      {!clips.isLoading && visibleClips.length === 0 && <Alert severity="info">No clips match the current filters.</Alert>}
      {visibleClips.length > 0 && groupMode === "none" && renderClipCards(visibleClips)}
      {groupMode !== "none" && libraryGroups.map((group) => {
        const key = `library:${group.key}`;
        const collapsed = collapsedGroups.has(key);
        return (
          <Stack key={key} spacing={1}>
            <Button color="inherit" aria-expanded={!collapsed} onClick={() => toggleGroup(key)} sx={{ alignSelf: "flex-start", px: 0 }}>
              <Typography variant="h6">{collapsed ? "▸" : "▾"} {group.label} ({group.items.length})</Typography>
            </Button>
            <Collapse in={!collapsed}>
              {groupMode === "library" ? renderClipCards(group.items) : (
                <Stack spacing={1.5} sx={{ pl: { xs: 1, sm: 2 } }}>
                  {groupClips(group.items, mediaGroupIdentity).map((mediaGroup) => {
                    const mediaKey = `${key}:media:${mediaGroup.key}`;
                    const mediaCollapsed = collapsedGroups.has(mediaKey);
                    return (
                      <Stack key={mediaKey} spacing={0.75}>
                        <Button color="inherit" aria-expanded={!mediaCollapsed} onClick={() => toggleGroup(mediaKey)} sx={{ alignSelf: "flex-start", px: 0 }}>
                          <Typography variant="subtitle1" color="text.secondary">{mediaCollapsed ? "▸" : "▾"} {mediaGroup.label} ({mediaGroup.items.length})</Typography>
                        </Button>
                        <Collapse in={!mediaCollapsed}>
                          {groupMode === "library-episode" && mediaGroup.items[0]?.media_type === "episode" ? (
                            <Stack spacing={1.25} sx={{ pl: { xs: 1, sm: 2 } }}>
                              {groupClips(mediaGroup.items, episodeGroupIdentity).map((episodeGroup) => {
                                const episodeKey = `${mediaKey}:episode:${episodeGroup.key}`;
                                const episodeCollapsed = collapsedGroups.has(episodeKey);
                                return (
                                  <Stack key={episodeKey} spacing={0.75}>
                                    <Button color="inherit" aria-expanded={!episodeCollapsed} onClick={() => toggleGroup(episodeKey)} sx={{ alignSelf: "flex-start", px: 0 }}>
                                      <Typography variant="body1" color="text.secondary">{episodeCollapsed ? "▸" : "▾"} {episodeGroup.label} ({episodeGroup.items.length})</Typography>
                                    </Button>
                                    <Collapse in={!episodeCollapsed}>{renderClipCards(episodeGroup.items)}</Collapse>
                                  </Stack>
                                );
                              })}
                            </Stack>
                          ) : renderClipCards(mediaGroup.items)}
                        </Collapse>
                      </Stack>
                    );
                  })}
                </Stack>
              )}
            </Collapse>
          </Stack>
        );
      })}
      {(clips.data?.pages ?? 1) > 1 && <Pagination page={page} count={clips.data?.pages ?? 1} onChange={(_, value) => { setPage(value); writeParams({ page: value === 1 ? null : String(value) }); }} />}
      {playing && <PlaybackDialog clip={playing} onClose={closePlayer} />}
      {editing && <MetadataDialog clip={editing} busy={editMutation.isPending || Boolean(editJobId)} error={editMutation.error?.message ?? editJob.data?.error?.message ?? null} onClose={() => setEditing(null)} onSave={(update) => editMutation.mutate({ clip: editing, update })} />}
      {deleting && <DeleteClipDialog clip={deleting} busy={deleteMutation.isPending} error={deleteMutation.error?.message ?? null} onClose={() => { deleteMutation.reset(); setDeleting(null); }} onConfirm={() => deleteMutation.mutate(deleting)} />}
    </Stack>
  );
}

function PlaybackDialog({ clip, onClose }: { clip: ClipRecord; onClose: () => void }) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  const closeOnBackground = (event: React.MouseEvent) => { if (event.target === event.currentTarget) onClose(); };
  return (
    <Dialog open onClose={onClose} fullScreen={fullScreen} fullWidth maxWidth="md" aria-label={clip.title} slotProps={{ paper: { sx: { bgcolor: "black", overflow: "hidden", m: fullScreen ? 0 : 2 } } }}>
      <Box onClick={closeOnBackground} sx={{ display: "flex", flexDirection: "column", minHeight: fullScreen ? "100dvh" : 0, cursor: "pointer" }}>
        {!fullScreen && (
          <Box onClick={closeOnBackground} sx={{ height: 48, flex: "0 0 48px", display: "flex", alignItems: "center", justifyContent: "flex-end", px: 0.75 }}>
            <IconButton aria-label="Close player" onClick={(event) => { event.stopPropagation(); onClose(); }} sx={{ color: "common.white", border: "1px solid rgba(255, 255, 255, 0.38)", bgcolor: "rgba(255, 255, 255, 0.08)", "&:hover": { bgcolor: "rgba(255, 255, 255, 0.16)" } }}><CloseRounded /></IconButton>
          </Box>
        )}
        <Box onClick={closeOnBackground} sx={{ position: "relative", flex: 1, minHeight: 0, display: "grid", placeItems: "center" }}>
          <Box component="video" src={clip.play_url} poster={`${clip.thumbnail_url}?revision=${clip.revision}`} controls autoPlay playsInline onClick={(event) => event.stopPropagation()} sx={{ display: "block", width: "100%", maxHeight: fullScreen ? "100dvh" : "calc(100dvh - 80px)", objectFit: "contain", cursor: "default" }} />
          {fullScreen && <IconButton aria-label="Close player" onClick={onClose} sx={{ position: "absolute", zIndex: 2, top: "max(8px, env(safe-area-inset-top))", right: "max(8px, env(safe-area-inset-right))", color: "common.white", border: "1px solid rgba(255, 255, 255, 0.38)", bgcolor: "rgba(0, 0, 0, 0.78)", "&:hover": { bgcolor: "rgba(0, 0, 0, 0.9)" } }}><CloseRounded /></IconButton>}
        </Box>
      </Box>
    </Dialog>
  );
}

function clipMetadata(clip: ClipRecord): string[] {
  if (clip.media_type === "movie") return clip.movie_title ? [`${clip.movie_title}${clip.movie_year ? ` (${clip.movie_year})` : ""}`] : [];
  if (clip.media_type === "episode") {
    const position = [clip.season_number === null ? null : `Season ${clip.season_number}`, clip.episode_number === null ? null : `Episode ${clip.episode_number}`].filter(Boolean).join(" · ");
    return [clip.show_name, position || null, clip.episode_title].filter((value): value is string => Boolean(value));
  }
  return [];
}

function formatDuration(durationMs: number): string {
  const seconds = Math.round(durationMs / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function episodeCode(option: EpisodeFilterOption): string {
  if (option.season_number === null || option.episode_number === null) return "";
  return `S${String(option.season_number).padStart(2, "0")}E${String(option.episode_number).padStart(2, "0")}`;
}

function parsePageSize(value: string | null): PageSize {
  if (value === "50" || value === "100" || value === "all") return value === "all" ? "all" : Number(value) as 50 | 100;
  return 25;
}

function groupClips(
  clips: ClipRecord[],
  identify: (clip: ClipRecord) => { key: string; label: string }
): ClipGroup[] {
  const groups = new Map<string, ClipGroup>();
  clips.forEach((clip) => {
    const identity = identify(clip);
    const existing = groups.get(identity.key);
    if (existing) existing.items.push(clip);
    else groups.set(identity.key, { ...identity, items: [clip] });
  });
  return [...groups.values()];
}

function mediaGroupIdentity(clip: ClipRecord): { key: string; label: string } {
  if (clip.media_type === "movie") {
    const name = clip.movie_title?.trim() || clip.title;
    return { key: `movie:${name.toLocaleLowerCase()}`, label: `🎞️ ${name}` };
  }
  if (clip.media_type === "episode") {
    const name = clip.show_name?.trim() || "Unknown series";
    return { key: `series:${name.toLocaleLowerCase()}`, label: `📺 ${name}` };
  }
  return { key: "video", label: "Videos" };
}

function episodeGroupIdentity(clip: ClipRecord): { key: string; label: string } {
  const code = clip.season_number === null || clip.episode_number === null
    ? ""
    : `S${String(clip.season_number).padStart(2, "0")}E${String(clip.episode_number).padStart(2, "0")}`;
  const title = clip.episode_title?.trim() || "Unknown episode";
  return {
    key: `${clip.season_number ?? "x"}:${clip.episode_number ?? "x"}:${title.toLocaleLowerCase()}`,
    label: code ? `${code} · ${title}` : title,
  };
}

function cardColumnCount(size: ThumbnailSize, availableWidth: number): number {
  const minimum = size === "small" ? 3 : size === "medium" ? 2 : 1;
  if (!availableWidth) return minimum;
  return Math.max(minimum, Math.floor((availableWidth + 10) / (widths[size] + 10)));
}

function columnize<T>(items: T[], columnCount: number): T[][] {
  const columns = Array.from({ length: columnCount }, () => [] as T[]);
  items.forEach((item, index) => columns[index % columnCount].push(item));
  return columns;
}
