import AddRounded from "@mui/icons-material/AddRounded";
import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import ArrowUpwardRounded from "@mui/icons-material/ArrowUpwardRounded";
import CancelRounded from "@mui/icons-material/CancelRounded";
import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import LockRounded from "@mui/icons-material/LockRounded";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputAdornment,
  InputLabel,
  Link,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { testImmichConnection, testPlexConnection, updateSettings } from "../../api";
import type {
  ApplicationSettingField,
  ApplicationSettings,
  ApplicationSettingsUpdate,
  ImmichConnectionRequest,
  ImmichConnectionResult,
  PlexConnectionRequest,
  PlexConnectionResult,
  SourcePathMapping,
} from "../../types";

const x264Presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];
const SECRET_MASK = "●●●●●●●●";
const AUTO_SAVE_DEBOUNCE_MS = 800;
interface ImmichPermission {
  scope: string;
  conditional: boolean;
}

const IMMICH_REQUIRED_PERMISSIONS: ImmichPermission[] = [
  { scope: "asset.upload", conditional: false },
  { scope: "asset.update", conditional: false },
  { scope: "asset.read", conditional: false },
  { scope: "tag.read", conditional: false },
  { scope: "tag.create", conditional: false },
  { scope: "tag.asset", conditional: false },
  { scope: "asset.delete", conditional: true },
];

function secretDraft(configured: boolean): string { return configured ? SECRET_MASK : ""; }
function enteredSecret(value: string): string { const secret = value.trim(); return secret === SECRET_MASK ? "" : secret; }
function ManagedLabel({ managed }: { managed: boolean }) { return managed ? <Chip label="Environment managed" size="small" variant="outlined" /> : null; }

function ConnectionStatusPill({
  connection,
  pending,
  notConfiguredCode,
  missingPermissions,
}: {
  connection: { connected: boolean; code: string } | null;
  pending: boolean;
  notConfiguredCode: string;
  missingPermissions?: boolean;
}) {
  if (!connection && pending) {
    return <Chip size="small" variant="outlined" icon={<CircularProgress size={12} />} label="Checking…" />;
  }
  if (!connection) return null;
  if (connection.connected) {
    if (missingPermissions) return <Chip size="small" color="warning" label="Missing Permissions" />;
    return <Chip size="small" color="success" label="Connected" />;
  }
  if (connection.code === notConfiguredCode) {
    return <Chip size="small" variant="outlined" label="Not configured" />;
  }
  return <Chip size="small" color="error" label="Disconnected" />;
}

function initialTimezone(settings: ApplicationSettings): string {
  if (settings.timezone_configured) return settings.timezone;
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return detected && settings.available_timezones.includes(detected)
    ? detected
    : settings.timezone;
}

export function SettingsForm({
  settings,
  readiness,
}: {
  settings: ApplicationSettings;
  readiness?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [plexUrl, setPlexUrl] = useState(settings.plex_url);
  const [plexToken, setPlexToken] = useState(() => secretDraft(settings.plex_token_configured));
  const [plexConnection, setPlexConnection] = useState<PlexConnectionResult | null>(null);

  const [immichUrl, setImmichUrl] = useState(settings.immich_url);
  const [immichApiKey, setImmichApiKey] = useState(() => secretDraft(settings.immich_api_key_configured));
  const [immichDefaultTag, setImmichDefaultTag] = useState(settings.immich_default_tag);
  const [immichAutoUpload, setImmichAutoUpload] = useState(settings.immich_auto_upload);
  const [immichManageRemote, setImmichManageRemote] = useState(settings.immich_manage_remote);
  const [immichTagLibrary, setImmichTagLibrary] = useState(settings.immich_tag_library);
  const [immichTagShow, setImmichTagShow] = useState(settings.immich_tag_show);
  const [immichTagEpisode, setImmichTagEpisode] = useState(settings.immich_tag_episode);
  const [immichConnection, setImmichConnection] = useState<ImmichConnectionResult | null>(null);
  const [immichPermissionsOpen, setImmichPermissionsOpen] = useState(false);

  const [timezone, setTimezone] = useState(() => initialTimezone(settings));
  // On a fresh install the initial timezone above is only a browser-detected guess,
  // not a real edit — it must not auto-save on its own. Only a genuine selection
  // from the control (or an already-configured value differing from it) counts.
  const [timezoneTouched, setTimezoneTouched] = useState(false);
  const [x264Preset, setX264Preset] = useState(settings.x264_preset);
  const [mappings, setMappings] = useState<SourcePathMapping[]>(settings.source_path_mappings);

  // A saved secret locks its service's URL field: retargeting the URL while the old
  // secret stays configured would let it silently follow to wherever the URL now
  // points (the Plex session poller and the Immich auto-connection-check both send the
  // saved credential on their own, with no chance to verify the new destination first).
  // Unlocking requires an explicit confirmation that clears the secret.
  const [plexUrlUnlocked, setPlexUrlUnlocked] = useState(false);
  const [immichUrlUnlocked, setImmichUrlUnlocked] = useState(false);
  const [unlockConfirmTarget, setUnlockConfirmTarget] = useState<"plex" | "immich" | null>(null);
  const plexUrlLocked = settings.plex_token_configured && !plexUrlUnlocked;
  const immichUrlLocked = settings.immich_api_key_configured && !immichUrlUnlocked;

  // Re-lock once a secret is (re)configured, e.g. after a successful Test connection.
  useEffect(() => {
    if (settings.plex_token_configured) setPlexUrlUnlocked(false);
  }, [settings.plex_token_configured]);

  useEffect(() => {
    if (settings.immich_api_key_configured) setImmichUrlUnlocked(false);
  }, [settings.immich_api_key_configured]);

  // For each field the auto-save effect below manages, the value we believe the
  // server currently holds: seeded from `settings` on load, optimistically advanced
  // the moment we dispatch a save for it (before the response arrives), and adopted
  // as the new baseline whenever a refresh confirms the local draft still matches it.
  // A refresh only ever overwrites a field's draft when the draft equals this
  // baseline — i.e. nothing has changed locally since we last told the server (or
  // last learned) what the field holds. Unlike comparing against a single frozen
  // "previous settings" snapshot, this correctly survives an A→B→A revert made while
  // the A→B save is still in flight: the reverted draft no longer matches the
  // optimistic B baseline, so the in-flight response landing later leaves it alone,
  // and the diff-based auto-save effect notices the mismatch against server truth and
  // resends the revert on its own.
  const knownValuesRef = useRef({
    plexUrl: settings.plex_url,
    mappings: settings.source_path_mappings,
    timezone: initialTimezone(settings),
    x264Preset: settings.x264_preset,
    immichUrl: settings.immich_url,
    immichDefaultTag: settings.immich_default_tag,
    immichAutoUpload: settings.immich_auto_upload,
    immichManageRemote: settings.immich_manage_remote,
    immichTagLibrary: settings.immich_tag_library,
    immichTagShow: settings.immich_tag_show,
    immichTagEpisode: settings.immich_tag_episode,
  });

  // The Plex token / Immich API key masks are driven purely by their "configured"
  // booleans (set only via Test connection or an explicit clear), not by the general
  // auto-save flow, so they still just compare against the previous settings snapshot.
  const previousSettingsRef = useRef(settings);
  useEffect(() => {
    const previous = previousSettingsRef.current;
    previousSettingsRef.current = settings;
    const known = knownValuesRef.current;

    setPlexToken((current) => {
      const previousDraft = secretDraft(previous.plex_token_configured);
      return current === previousDraft ? secretDraft(settings.plex_token_configured) : current;
    });
    setImmichApiKey((current) => {
      const previousDraft = secretDraft(previous.immich_api_key_configured);
      return current === previousDraft ? secretDraft(settings.immich_api_key_configured) : current;
    });

    setPlexUrl((current) => {
      if (current !== known.plexUrl) return current;
      known.plexUrl = settings.plex_url;
      return settings.plex_url;
    });
    setMappings((current) => {
      if (JSON.stringify(current) !== JSON.stringify(known.mappings)) return current;
      known.mappings = settings.source_path_mappings;
      return settings.source_path_mappings;
    });
    setTimezone((current) => {
      if (current !== known.timezone) return current;
      known.timezone = initialTimezone(settings);
      return known.timezone;
    });
    setX264Preset((current) => {
      if (current !== known.x264Preset) return current;
      known.x264Preset = settings.x264_preset;
      return settings.x264_preset;
    });
    setImmichUrl((current) => {
      if (current !== known.immichUrl) return current;
      known.immichUrl = settings.immich_url;
      return settings.immich_url;
    });
    setImmichDefaultTag((current) => {
      if (current !== known.immichDefaultTag) return current;
      known.immichDefaultTag = settings.immich_default_tag;
      return settings.immich_default_tag;
    });
    setImmichAutoUpload((current) => {
      if (current !== known.immichAutoUpload) return current;
      known.immichAutoUpload = settings.immich_auto_upload;
      return settings.immich_auto_upload;
    });
    setImmichManageRemote((current) => {
      if (current !== known.immichManageRemote) return current;
      known.immichManageRemote = settings.immich_manage_remote;
      return settings.immich_manage_remote;
    });
    setImmichTagLibrary((current) => {
      if (current !== known.immichTagLibrary) return current;
      known.immichTagLibrary = settings.immich_tag_library;
      return settings.immich_tag_library;
    });
    setImmichTagShow((current) => {
      if (current !== known.immichTagShow) return current;
      known.immichTagShow = settings.immich_tag_show;
      return settings.immich_tag_show;
    });
    setImmichTagEpisode((current) => {
      if (current !== known.immichTagEpisode) return current;
      known.immichTagEpisode = settings.immich_tag_episode;
      return settings.immich_tag_episode;
    });
  }, [settings]);

  const managed = (field: ApplicationSettingField) => settings.environment_managed[field];

  const autoSave = useMutation({
    mutationFn: (update: ApplicationSettingsUpdate) => updateSettings(update),
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings"], updated);
    },
  });

  // Everything here is plain configuration, not a secret — it saves itself shortly
  // after the user stops changing it. Only the Plex token and Immich API key are
  // withheld from this and saved solely via a successful "Test connection". The
  // payload only ever includes fields that currently differ from the last-known
  // server state (recomputed on every run, including whenever `settings` itself
  // refreshes), so a save racing in from elsewhere just reschedules this debounce
  // instead of silently dropping or redundantly resending anything.
  useEffect(() => {
    const update: ApplicationSettingsUpdate = {};
    if (!managed("plex_url") && plexUrl !== settings.plex_url) update.plex_url = plexUrl;
    if (
      !managed("source_path_mappings") &&
      JSON.stringify(mappings) !== JSON.stringify(settings.source_path_mappings)
    ) {
      update.source_path_mappings = mappings;
    }
    if (
      !managed("timezone") &&
      (settings.timezone_configured || timezoneTouched) &&
      timezone !== settings.timezone
    ) {
      update.timezone = timezone;
    }
    if (!managed("x264_preset") && x264Preset !== settings.x264_preset) {
      update.x264_preset = x264Preset;
    }
    if (!managed("immich_url") && immichUrl !== settings.immich_url) update.immich_url = immichUrl;
    if (!managed("immich_default_tag") && immichDefaultTag !== settings.immich_default_tag) {
      update.immich_default_tag = immichDefaultTag;
    }
    if (!managed("immich_auto_upload") && immichAutoUpload !== settings.immich_auto_upload) {
      update.immich_auto_upload = immichAutoUpload;
    }
    if (!managed("immich_manage_remote") && immichManageRemote !== settings.immich_manage_remote) {
      update.immich_manage_remote = immichManageRemote;
    }
    if (!managed("immich_tag_library") && immichTagLibrary !== settings.immich_tag_library) {
      update.immich_tag_library = immichTagLibrary;
    }
    if (!managed("immich_tag_show") && immichTagShow !== settings.immich_tag_show) {
      update.immich_tag_show = immichTagShow;
    }
    if (!managed("immich_tag_episode") && immichTagEpisode !== settings.immich_tag_episode) {
      update.immich_tag_episode = immichTagEpisode;
    }
    if (Object.keys(update).length === 0) return;

    const timer = setTimeout(() => {
      // Advance the optimistic baseline for exactly the fields we're about to send,
      // so the resync effect can tell a value confirmed by this save apart from one
      // edited again in the meantime (see knownValuesRef above).
      const known = knownValuesRef.current;
      if (update.plex_url !== undefined) known.plexUrl = update.plex_url;
      if (update.source_path_mappings !== undefined) known.mappings = update.source_path_mappings;
      if (update.timezone !== undefined) known.timezone = update.timezone;
      if (update.x264_preset !== undefined) known.x264Preset = update.x264_preset;
      if (update.immich_url !== undefined) known.immichUrl = update.immich_url;
      if (update.immich_default_tag !== undefined) known.immichDefaultTag = update.immich_default_tag;
      if (update.immich_auto_upload !== undefined) known.immichAutoUpload = update.immich_auto_upload;
      if (update.immich_manage_remote !== undefined) known.immichManageRemote = update.immich_manage_remote;
      if (update.immich_tag_library !== undefined) known.immichTagLibrary = update.immich_tag_library;
      if (update.immich_tag_show !== undefined) known.immichTagShow = update.immich_tag_show;
      if (update.immich_tag_episode !== undefined) known.immichTagEpisode = update.immich_tag_episode;
      autoSave.mutate(update);
    }, AUTO_SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    settings,
    plexUrl,
    mappings,
    timezone,
    timezoneTouched,
    x264Preset,
    immichUrl,
    immichDefaultTag,
    immichAutoUpload,
    immichManageRemote,
    immichTagLibrary,
    immichTagShow,
    immichTagEpisode,
  ]);

  const confirmUnlockUrl = () => {
    if (unlockConfirmTarget === "plex") {
      setPlexUrlUnlocked(true);
      setPlexToken("");
      setPlexConnection(null);
      autoSave.mutate({ clear_plex_token: true });
    } else if (unlockConfirmTarget === "immich") {
      setImmichUrlUnlocked(true);
      setImmichApiKey("");
      setImmichConnection(null);
      autoSave.mutate({ clear_immich_api_key: true });
    }
    setUnlockConfirmTarget(null);
  };

  interface ConnectionCandidate<TRequest> {
    test: TRequest;
    save: ApplicationSettingsUpdate;
  }

  const testPlex = useMutation({
    mutationFn: async (candidate: ConnectionCandidate<PlexConnectionRequest>) => {
      const result = await testPlexConnection(candidate.test);
      // Only ever report settings that were actually just written — falling back to
      // the settings captured when this mutation started would let a slow, read-only
      // connection check (e.g. the automatic check on page load) overwrite the cache
      // with a stale snapshot if a real save elsewhere had already moved it forward.
      const updated =
        result.connected && Object.keys(candidate.save).length
          ? await updateSettings(candidate.save)
          : null;
      return { settings: updated, connection: result };
    },
    onSuccess: (result) => {
      setPlexConnection(result.connection);
      if (result.settings) {
        queryClient.setQueryData(["settings"], result.settings);
        setPlexToken(secretDraft(result.settings.plex_token_configured));
      }
    },
  });

  const runPlexTest = () => {
    const submittedToken = enteredSecret(plexToken);
    if (!submittedToken && plexUrl !== settings.plex_url) {
      testPlex.reset();
      setPlexConnection({
        connected: false,
        code: "PLEX_CREDENTIALS_REQUIRED",
        message: "Enter the Plex token when testing a different server URL.",
        server_name: null,
        server_version: null,
      });
      return;
    }
    testPlex.mutate({
      test: submittedToken ? { plex_url: plexUrl, plex_token: submittedToken } : {},
      save: {
        ...(!managed("plex_url") && submittedToken && { plex_url: plexUrl }),
        ...(!managed("plex_token") && submittedToken && { plex_token: submittedToken }),
      },
    });
  };

  const testImmich = useMutation({
    mutationFn: async (candidate: ConnectionCandidate<ImmichConnectionRequest>) => {
      const result = await testImmichConnection(candidate.test);
      // See the equivalent comment on testPlex above — don't fall back to a stale
      // settings snapshot when nothing was actually saved.
      const updated =
        result.connected && Object.keys(candidate.save).length
          ? await updateSettings(candidate.save)
          : null;
      return { settings: updated, connection: result };
    },
    onSuccess: (result) => {
      setImmichConnection(result.connection);
      if (result.settings) {
        queryClient.setQueryData(["settings"], result.settings);
        setImmichApiKey(secretDraft(result.settings.immich_api_key_configured));
      }
    },
  });

  const runImmichTest = () => {
    const submittedKey = enteredSecret(immichApiKey);
    if (!submittedKey && immichUrl !== settings.immich_url) {
      testImmich.reset();
      setImmichConnection({
        connected: false,
        code: "IMMICH_CREDENTIALS_REQUIRED",
        message: "Enter the Immich API key when testing a different server URL.",
        server_version: null,
        api_key_permissions: null,
      });
      return;
    }
    // Only the credential pair is saved here, gated on a freshly-submitted key, same
    // as Plex. The plain-config fields (default tag, auto-upload, tag toggles) are
    // already kept in sync by the general auto-save effect above; duplicating them
    // here meant the automatic mount-time connection check (which runs with no
    // submitted key) always had a non-empty save payload built from its stale
    // mount-time closure, letting it clobber a since-auto-saved edit once it finally
    // resolved.
    testImmich.mutate({
      test: submittedKey ? { immich_url: immichUrl, immich_api_key: submittedKey } : {},
      save: {
        ...(!managed("immich_url") && submittedKey && { immich_url: immichUrl }),
        ...(!managed("immich_api_key") && submittedKey && { immich_api_key: submittedKey }),
      },
    });
  };

  useEffect(() => {
    // Check current connectivity as soon as the settings page loads, in addition to
    // whenever the user explicitly clicks "Test connection".
    runPlexTest();
    runImmichTest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeMapping = (index: number, field: keyof SourcePathMapping, value: string) => {
    setMappings((current) =>
      current.map((mapping, mappingIndex) =>
        mappingIndex === index ? { ...mapping, [field]: value } : mapping,
      ),
    );
  };
  const moveMapping = (index: number, offset: -1 | 1) => {
    setMappings((current) => {
      const destination = index + offset;
      if (destination < 0 || destination >= current.length) return current;
      const reordered = [...current];
      [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
      return reordered;
    });
  };

  const grantedImmichPermissions = immichConnection?.connected
    ? (immichConnection.api_key_permissions ?? [])
    : null;
  const hasAllImmichPermissions = grantedImmichPermissions?.includes("all") ?? false;
  const extraImmichPermissions =
    grantedImmichPermissions && !hasAllImmichPermissions
      ? grantedImmichPermissions.filter(
          (scope) => !IMMICH_REQUIRED_PERMISSIONS.some((permission) => permission.scope === scope),
        )
      : [];
  const immichMissingRequiredPermissions =
    grantedImmichPermissions !== null &&
    !hasAllImmichPermissions &&
    IMMICH_REQUIRED_PERMISSIONS.some((permission) => {
      const isNeeded = !permission.conditional || immichManageRemote;
      return isNeeded && !grantedImmichPermissions.includes(permission.scope);
    });

  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 3, alignItems: "start" }}>
      <Stack spacing={3}>
      <Card variant="outlined">
        <CardContent>
          <Box component="form" onSubmit={(event) => { event.preventDefault(); runPlexTest(); }}>
            <Stack spacing={3}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="h5">Plex</Typography>
                <ConnectionStatusPill
                  connection={plexConnection}
                  pending={testPlex.isPending}
                  notConfiguredCode="PLEX_NOT_CONFIGURED"
                />
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                <TextField
                  fullWidth
                  label="Plex server URL"
                  placeholder="http://192.168.1.20:32400"
                  value={plexUrl}
                  disabled={managed("plex_url") || plexUrlLocked}
                  onChange={(event) => {
                    setPlexUrl(event.target.value);
                    setPlexConnection(null);
                  }}
                  slotProps={
                    plexUrlLocked && !managed("plex_url")
                      ? {
                          input: {
                            endAdornment: (
                              <InputAdornment position="end">
                                <IconButton
                                  size="small"
                                  aria-label="Unlock to change the Plex server URL"
                                  onClick={() => setUnlockConfirmTarget("plex")}
                                >
                                  <LockRounded fontSize="small" />
                                </IconButton>
                              </InputAdornment>
                            ),
                          },
                        }
                      : undefined
                  }
                />
                <ManagedLabel managed={managed("plex_url")} />
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Stack direction="row" justifyContent="flex-end" mb={0.5}>
                    <Link
                      href="https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/"
                      target="_blank"
                      rel="noreferrer"
                      variant="body2"
                    >
                      How do I find this?
                    </Link>
                  </Stack>
                  <TextField
                    fullWidth
                    type="password"
                    name="plex_token"
                    label="Plex token"
                    placeholder="Enter token"
                    value={plexToken}
                    slotProps={{ inputLabel: { shrink: true } }}
                    disabled={managed("plex_token")}
                    onFocus={() => {
                      if (plexToken === SECRET_MASK) setPlexToken("");
                    }}
                    onBlur={() => {
                      if (!plexToken && settings.plex_token_configured) setPlexToken(SECRET_MASK);
                    }}
                    onChange={(event) => {
                      setPlexToken(event.target.value);
                      setPlexConnection(null);
                    }}
                    helperText="The saved token is replaced only when you enter a new one."
                  />
                </Box>
                <ManagedLabel managed={managed("plex_token")} />
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
                <Button
                  type="submit"
                  variant="outlined"
                  disabled={!plexUrl.trim() || testPlex.isPending}
                  sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 165 }}
                >
                  {testPlex.isPending ? "Testing…" : "Test connection"}
                </Button>
                <Typography color="text.secondary" variant="body2">
                  Tests the current URL/token and saves them only when the connection succeeds.
                </Typography>
              </Stack>
              {testPlex.error && <Alert severity="error">{testPlex.error.message}</Alert>}
              {plexConnection && !plexConnection.connected && plexConnection.code !== "PLEX_NOT_CONFIGURED" && (
                <Alert severity="error">{plexConnection.message}</Alert>
              )}
            </Stack>
          </Box>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Box component="form" onSubmit={(event) => { event.preventDefault(); runImmichTest(); }}>
            <Stack spacing={3}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="h5">Immich</Typography>
                <ConnectionStatusPill
                  connection={immichConnection}
                  pending={testImmich.isPending}
                  notConfiguredCode="IMMICH_NOT_CONFIGURED"
                  missingPermissions={immichMissingRequiredPermissions}
                />
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                <TextField
                  fullWidth
                  label="Immich URL"
                  placeholder="https://photos.example.com"
                  value={immichUrl}
                  disabled={managed("immich_url") || immichUrlLocked}
                  onChange={(event) => {
                    setImmichUrl(event.target.value);
                    setImmichConnection(null);
                  }}
                  slotProps={
                    immichUrlLocked && !managed("immich_url")
                      ? {
                          input: {
                            endAdornment: (
                              <InputAdornment position="end">
                                <IconButton
                                  size="small"
                                  aria-label="Unlock to change the Immich URL"
                                  onClick={() => setUnlockConfirmTarget("immich")}
                                >
                                  <LockRounded fontSize="small" />
                                </IconButton>
                              </InputAdornment>
                            ),
                          },
                        }
                      : undefined
                  }
                />
                <ManagedLabel managed={managed("immich_url")} />
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <TextField
                    fullWidth
                    type="password"
                    name="immich_api_key"
                    label="Immich API key"
                    placeholder={settings.immich_api_key_configured ? "Saved — leave blank to keep it" : "Enter API key"}
                    value={immichApiKey}
                    slotProps={{ inputLabel: { shrink: true } }}
                    disabled={managed("immich_api_key")}
                    onFocus={() => {
                      if (immichApiKey === SECRET_MASK) setImmichApiKey("");
                    }}
                    onBlur={() => {
                      if (!immichApiKey && settings.immich_api_key_configured) setImmichApiKey(SECRET_MASK);
                    }}
                    onChange={(event) => {
                      setImmichApiKey(event.target.value);
                      setImmichConnection(null);
                    }}
                  />
                  <Stack direction="row" spacing={2} mt={0.5}>
                    <Link
                      href={
                        immichUrl.trim()
                          ? `${immichUrl.trim().replace(/\/+$/, "")}/user-settings?isOpen=api-keys`
                          : "https://immich.app/docs/features/command-line-interface#obtain-the-api-key"
                      }
                      target="_blank"
                      rel="noreferrer"
                      variant="body2"
                    >
                      Create an API key
                    </Link>
                    <Link
                      component="button"
                      type="button"
                      variant="body2"
                      onClick={() => setImmichPermissionsOpen(true)}
                    >
                      Required permissions
                    </Link>
                  </Stack>

                  <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }} mt={2}>
                    <Button
                      type="submit"
                      variant="outlined"
                      disabled={!immichUrl.trim() || testImmich.isPending}
                      sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 165 }}
                    >
                      {testImmich.isPending ? "Testing…" : "Test connection"}
                    </Button>
                    <Typography color="text.secondary" variant="body2">
                      Tests the current URL/API key and saves the Immich settings only when the connection succeeds.
                    </Typography>
                  </Stack>
                </Box>
                <ManagedLabel managed={managed("immich_api_key")} />
              </Stack>

              {testImmich.error && <Alert severity="error">{testImmich.error.message}</Alert>}
              {immichConnection && !immichConnection.connected && immichConnection.code !== "IMMICH_NOT_CONFIGURED" && (
                <Alert severity="error">{immichConnection.message}</Alert>
              )}

              <Divider />

              <Stack spacing={1}>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={immichAutoUpload}
                      disabled={managed("immich_auto_upload")}
                      onChange={(event) => setImmichAutoUpload(event.target.checked)}
                    />
                  }
                  label="Auto-upload new clips"
                />
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={immichManageRemote}
                      disabled={managed("immich_manage_remote")}
                      onChange={(event) => setImmichManageRemote(event.target.checked)}
                    />
                  }
                  label="Manage Immich clips after upload"
                />
              </Stack>

              <Box sx={{ p: 2, borderRadius: 1, border: 1, borderColor: "divider" }}>
                <Typography variant="overline" color="text.secondary">Auto-tag with</Typography>
                <Stack spacing={0.5} mt={0.5}>
                  <FormControlLabel
                    control={
                      <Checkbox
                        size="small"
                        checked={immichTagLibrary}
                        disabled={managed("immich_tag_library")}
                        onChange={(event) => setImmichTagLibrary(event.target.checked)}
                      />
                    }
                    label="Media library name"
                  />
                  <FormControlLabel
                    control={
                      <Checkbox
                        size="small"
                        checked={immichTagShow}
                        disabled={managed("immich_tag_show")}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setImmichTagShow(checked);
                          // Episode nests under Show — turning Show off leaves no
                          // parent for a stale "Episode was on" setting to nest
                          // under, so cascade it off too rather than leaving it
                          // silently armed for whenever Show gets re-enabled.
                          // Skip this when Episode is environment-managed: autosave
                          // never persists a managed field, so a local-only "false"
                          // here would just diverge from the real (managed) value
                          // until a resync happens to overwrite it back.
                          if (!checked && !managed("immich_tag_episode")) setImmichTagEpisode(false);
                        }}
                      />
                    }
                    label="Show/movie name"
                  />
                  <FormControlLabel
                    control={
                      <Checkbox
                        size="small"
                        checked={immichTagEpisode}
                        disabled={managed("immich_tag_episode") || !immichTagShow}
                        onChange={(event) => setImmichTagEpisode(event.target.checked)}
                      />
                    }
                    label="Episode title (S##E##)"
                  />
                </Stack>
              </Box>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                <TextField
                  label="Default Immich tag"
                  value={immichDefaultTag}
                  disabled={managed("immich_default_tag")}
                  onChange={(event) => setImmichDefaultTag(event.target.value)}
                  sx={{ maxWidth: 320, width: "100%" }}
                />
                <ManagedLabel managed={managed("immich_default_tag")} />
              </Stack>

              <Tooltip title="Uploading existing clips is coming in a future release.">
                <span style={{ alignSelf: "flex-start" }}>
                  <Button variant="outlined" disabled sx={{ alignSelf: "flex-start", whiteSpace: "nowrap" }}>
                    Upload all non-uploaded clips
                  </Button>
                </span>
              </Tooltip>
            </Stack>
          </Box>
        </CardContent>
      </Card>

      <Dialog open={immichPermissionsOpen} onClose={() => setImmichPermissionsOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Required Immich API key permissions</DialogTitle>
        <DialogContent>
          {!grantedImmichPermissions && (
            <Typography variant="body2" color="text.secondary" mb={2}>
              Test the connection to see which of these permissions your key actually has.
            </Typography>
          )}
          <List dense disablePadding>
            {IMMICH_REQUIRED_PERMISSIONS.map((permission) => {
              const isGranted = grantedImmichPermissions
                ? hasAllImmichPermissions || grantedImmichPermissions.includes(permission.scope)
                : null;
              return (
                <ListItem key={permission.scope} disableGutters alignItems="center">
                  <ListItemIcon sx={{ minWidth: 32 }}>
                    {isGranted === true && <CheckCircleRounded color="success" fontSize="small" />}
                    {isGranted === false && <CancelRounded color="error" fontSize="small" />}
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography component="code" sx={{ fontFamily: "monospace", fontSize: 13.5 }}>
                          {permission.scope}
                        </Typography>
                        {permission.conditional && (
                          <Tooltip title="Only required when “Manage Immich clips after upload” is enabled.">
                            <Chip
                              size="small"
                              variant="outlined"
                              color={immichManageRemote ? "primary" : "default"}
                              label={immichManageRemote ? "required now" : "conditional"}
                            />
                          </Tooltip>
                        )}
                      </Stack>
                    }
                  />
                </ListItem>
              );
            })}
          </List>
          {grantedImmichPermissions && (
            <>
              <Divider sx={{ my: 1.5 }} />
              {hasAllImmichPermissions ? (
                <Typography variant="body2" color="text.secondary">
                  This key has the <code>all</code> permission, so every scope above — and
                  everything else — is granted.
                </Typography>
              ) : extraImmichPermissions.length > 0 ? (
                <>
                  <Typography variant="overline" color="text.secondary">Also granted (not required)</Typography>
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap mt={0.5}>
                    {extraImmichPermissions.map((scope) => (
                      <Chip key={scope} size="small" variant="outlined" label={scope} sx={{ fontFamily: "monospace" }} />
                    ))}
                  </Stack>
                </>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  This key has exactly the permissions above — nothing extra.
                </Typography>
              )}
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setImmichPermissionsOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={unlockConfirmTarget !== null} onClose={() => setUnlockConfirmTarget(null)}>
        <DialogTitle>
          Change the {unlockConfirmTarget === "plex" ? "Plex server URL" : "Immich URL"}?
        </DialogTitle>
        <DialogContent>
          <Typography>
            Changing the URL will delete the saved{" "}
            {unlockConfirmTarget === "plex" ? "Plex token" : "Immich API key"}. You&rsquo;ll
            need to enter it again and test the connection before it&rsquo;s saved.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUnlockConfirmTarget(null)}>Nevermind</Button>
          <Button color="error" onClick={confirmUnlockUrl}>Change Anyway</Button>
        </DialogActions>
      </Dialog>
      </Stack>

      <Stack spacing={3}>
      <Box>
        <Stack spacing={3}>
        <Card variant="outlined">
          <CardContent>
            <Stack spacing={3}>
              <Box>
                <Stack direction="row" spacing={1} alignItems="center" mb={1}>
                  <Typography variant="h5">Source-path mappings</Typography>
                  <ManagedLabel managed={managed("source_path_mappings")} />
                </Stack>
                <Typography color="text.secondary">
                  Mappings are checked from top to bottom. Example: Plex prefix <strong>D:\Media\Movies</strong>{" "}
                  maps to local/container prefix <strong>/media/movies</strong>.
                </Typography>
              </Box>

              {mappings.map((mapping, index) => (
                <Stack
                  key={index}
                  direction={{ xs: "column", sm: "row" }}
                  spacing={1}
                  alignItems={{ sm: "center" }}
                >
                  <TextField
                    fullWidth
                    label={`Plex prefix ${index + 1}`}
                    placeholder="D:\Media or /srv/plex/media"
                    value={mapping.plex_prefix}
                    disabled={managed("source_path_mappings")}
                    onChange={(event) => changeMapping(index, "plex_prefix", event.target.value)}
                  />
                  <TextField
                    fullWidth
                    label={`Local/container prefix ${index + 1}`}
                    placeholder="/media"
                    value={mapping.local_prefix}
                    disabled={managed("source_path_mappings")}
                    onChange={(event) => changeMapping(index, "local_prefix", event.target.value)}
                  />
                  <Stack direction="row" spacing={0.5} justifyContent={{ xs: "flex-end", sm: "flex-start" }} flexShrink={0}>
                    <IconButton
                      aria-label={`Move mapping ${index + 1} up`}
                      disabled={managed("source_path_mappings") || index === 0}
                      onClick={() => moveMapping(index, -1)}
                    >
                      <ArrowUpwardRounded />
                    </IconButton>
                    <IconButton
                      aria-label={`Move mapping ${index + 1} down`}
                      disabled={managed("source_path_mappings") || index === mappings.length - 1}
                      onClick={() => moveMapping(index, 1)}
                    >
                      <ArrowDownwardRounded />
                    </IconButton>
                    <IconButton
                      aria-label={`Remove mapping ${index + 1}`}
                      disabled={managed("source_path_mappings")}
                      onClick={() => setMappings((current) => current.filter((_, item) => item !== index))}
                    >
                      <DeleteOutlineRounded />
                    </IconButton>
                  </Stack>
                </Stack>
              ))}
              <Button
                startIcon={<AddRounded />}
                disabled={managed("source_path_mappings")}
                onClick={() => setMappings((current) => [...current, { plex_prefix: "", local_prefix: "" }])}
                sx={{ alignSelf: "flex-start" }}
              >
                Add mapping
              </Button>
            </Stack>
          </CardContent>
        </Card>

        <Card variant="outlined">
          <CardContent>
            <Stack spacing={3}>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <Stack direction="row" spacing={1} alignItems="center" flex={1}>
                  <Autocomplete
                    fullWidth
                    disableClearable
                    options={settings.available_timezones}
                    value={timezone}
                    disabled={managed("timezone")}
                    onChange={(_event, value) => {
                      setTimezone(value);
                      setTimezoneTouched(true);
                    }}
                    renderInput={(parameters) => (
                      <TextField
                        {...parameters}
                        label="Timezone"
                        helperText={
                          settings.timezone_configured
                            ? undefined
                            : "Detected from this browser."
                        }
                      />
                    )}
                  />
                  <ManagedLabel managed={managed("timezone")} />
                </Stack>
                <Stack direction="row" spacing={1} alignItems="center" flex={1}>
                  <FormControl fullWidth disabled={managed("x264_preset")}>
                    <InputLabel id="x264-preset-label">x264 preset</InputLabel>
                    <Select
                      labelId="x264-preset-label"
                      label="x264 preset"
                      value={x264Preset}
                      onChange={(event) => setX264Preset(event.target.value)}
                    >
                      {x264Presets.map((preset) => <MenuItem key={preset} value={preset}>{preset}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <ManagedLabel managed={managed("x264_preset")} />
                </Stack>
              </Stack>

              {autoSave.error && <Alert severity="error">{autoSave.error.message}</Alert>}
              <Typography variant="body2" color="text.secondary">
                {autoSave.isPending
                  ? "Saving…"
                  : autoSave.isSuccess
                    ? "All changes saved"
                    : "Changes save automatically."}
              </Typography>
            </Stack>
          </CardContent>
        </Card>
        </Stack>
      </Box>
      {readiness}
      </Stack>
    </Box>
  );
}
