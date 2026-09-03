import AddRounded from "@mui/icons-material/AddRounded";
import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import ArrowUpwardRounded from "@mui/icons-material/ArrowUpwardRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
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
  InputLabel,
  Link,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";

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
  { scope: "album.read", conditional: false },
  { scope: "album.create", conditional: false },
  { scope: "albumAsset.create", conditional: false },
  { scope: "asset.delete", conditional: true },
];

function secretDraft(configured: boolean): string { return configured ? SECRET_MASK : ""; }
function enteredSecret(value: string): string { const secret = value.trim(); return secret === SECRET_MASK ? "" : secret; }
function ManagedLabel({ managed }: { managed: boolean }) { return managed ? <Chip label="Environment managed" size="small" variant="outlined" /> : null; }

function ConnectionStatusPill({
  connection,
  pending,
  notConfiguredCode,
}: {
  connection: { connected: boolean; code: string } | null;
  pending: boolean;
  notConfiguredCode: string;
}) {
  if (!connection && pending) {
    return <Chip size="small" variant="outlined" icon={<CircularProgress size={12} />} label="Checking…" />;
  }
  if (!connection) return null;
  if (connection.connected) return <Chip size="small" color="success" label="Connected" />;
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
  const [x264Preset, setX264Preset] = useState(settings.x264_preset);
  const [mappings, setMappings] = useState<SourcePathMapping[]>(settings.source_path_mappings);

  useEffect(() => {
    setPlexUrl(settings.plex_url);
  }, [settings.plex_url]);

  useEffect(() => {
    setImmichUrl(settings.immich_url);
  }, [settings.immich_url]);

  useEffect(() => {
    setPlexToken(secretDraft(settings.plex_token_configured));
    setImmichApiKey(secretDraft(settings.immich_api_key_configured));
    setImmichDefaultTag(settings.immich_default_tag);
    setImmichAutoUpload(settings.immich_auto_upload);
    setImmichManageRemote(settings.immich_manage_remote);
    setImmichTagLibrary(settings.immich_tag_library);
    setImmichTagShow(settings.immich_tag_show);
    setImmichTagEpisode(settings.immich_tag_episode);
    setTimezone(initialTimezone(settings));
    setX264Preset(settings.x264_preset);
    setMappings(settings.source_path_mappings);
  }, [settings]);

  const managed = (field: ApplicationSettingField) => settings.environment_managed[field];

  const saveGeneral = useMutation({
    mutationKey: ["settingsGeneralSave"],
    mutationFn: (update: ApplicationSettingsUpdate) => updateSettings(update),
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings"], updated);
    },
  });

  const submitGeneral = (event: FormEvent) => {
    event.preventDefault();

    // Changing the Plex URL requires re-verifying the token via that card's own "Test
    // connection" action (the token may not be valid on a different server) — silently
    // dropping the typed URL here would report success while leaving it unsaved, so
    // block the whole save and explain why. The Immich API key has no such coupling:
    // its URL is a plain setting and saves like any other field below.
    const plexUrlChangedWithoutToken = plexUrl !== settings.plex_url && !enteredSecret(plexToken);
    if (plexUrlChangedWithoutToken) {
      testPlex.reset();
      setPlexConnection({
        connected: false,
        code: "PLEX_CREDENTIALS_REQUIRED",
        message: "Enter the Plex token to save the changed Plex server URL, then use Test connection.",
        server_name: null,
        server_version: null,
      });
      return;
    }

    saveGeneral.mutate({
      ...(!managed("source_path_mappings") && { source_path_mappings: mappings }),
      ...(!managed("timezone") && { timezone }),
      ...(!managed("x264_preset") && { x264_preset: x264Preset }),
      ...(!managed("immich_url") && { immich_url: immichUrl }),
      ...(!managed("immich_default_tag") && { immich_default_tag: immichDefaultTag }),
      ...(!managed("immich_auto_upload") && { immich_auto_upload: immichAutoUpload }),
      ...(!managed("immich_manage_remote") && { immich_manage_remote: immichManageRemote }),
      ...(!managed("immich_tag_library") && { immich_tag_library: immichTagLibrary }),
      ...(!managed("immich_tag_show") && { immich_tag_show: immichTagShow }),
      ...(!managed("immich_tag_episode") && { immich_tag_episode: immichTagEpisode }),
    });
  };

  interface ConnectionCandidate<TRequest> {
    test: TRequest;
    save: ApplicationSettingsUpdate;
  }

  const testPlex = useMutation({
    mutationFn: async (candidate: ConnectionCandidate<PlexConnectionRequest>) => {
      const result = await testPlexConnection(candidate.test);
      const updated =
        result.connected && Object.keys(candidate.save).length
          ? await updateSettings(candidate.save)
          : settings;
      return { settings: updated, connection: result };
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["settings"], result.settings);
      setPlexConnection(result.connection);
      if (result.connection.connected) setPlexToken(secretDraft(result.settings.plex_token_configured));
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
      const updated =
        result.connected && Object.keys(candidate.save).length
          ? await updateSettings(candidate.save)
          : settings;
      return { settings: updated, connection: result };
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["settings"], result.settings);
      setImmichConnection(result.connection);
      if (result.connection.connected) setImmichApiKey(secretDraft(result.settings.immich_api_key_configured));
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
      });
      return;
    }
    testImmich.mutate({
      test: submittedKey ? { immich_url: immichUrl, immich_api_key: submittedKey } : {},
      save: {
        ...(!managed("immich_url") && submittedKey && { immich_url: immichUrl }),
        ...(!managed("immich_api_key") && submittedKey && { immich_api_key: submittedKey }),
        ...(!managed("immich_default_tag") && { immich_default_tag: immichDefaultTag }),
        ...(!managed("immich_auto_upload") && { immich_auto_upload: immichAutoUpload }),
        ...(!managed("immich_manage_remote") && { immich_manage_remote: immichManageRemote }),
        ...(!managed("immich_tag_library") && { immich_tag_library: immichTagLibrary }),
        ...(!managed("immich_tag_show") && { immich_tag_show: immichTagShow }),
        ...(!managed("immich_tag_episode") && { immich_tag_episode: immichTagEpisode }),
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
                  disabled={managed("plex_url")}
                  onChange={(event) => {
                    setPlexUrl(event.target.value);
                    setPlexConnection(null);
                  }}
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
                />
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                <TextField
                  fullWidth
                  label="Immich URL"
                  placeholder="https://photos.example.com"
                  value={immichUrl}
                  disabled={managed("immich_url")}
                  onChange={(event) => {
                    setImmichUrl(event.target.value);
                    setImmichConnection(null);
                  }}
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
                        onChange={(event) => setImmichTagShow(event.target.checked)}
                      />
                    }
                    label="Show/movie name"
                  />
                  <FormControlLabel
                    control={
                      <Checkbox
                        size="small"
                        checked={immichTagEpisode}
                        disabled={managed("immich_tag_episode")}
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
          <List dense disablePadding>
            {IMMICH_REQUIRED_PERMISSIONS.map((permission) => (
              <ListItem key={permission.scope} disableGutters alignItems="flex-start">
                <ListItemText
                  primary={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography component="code" sx={{ fontFamily: "monospace", fontSize: 13.5 }}>
                        {permission.scope}
                      </Typography>
                      {permission.conditional && (
                        <Chip
                          size="small"
                          variant="outlined"
                          color={immichManageRemote ? "primary" : "default"}
                          label={immichManageRemote ? "required now" : "conditional"}
                        />
                      )}
                    </Stack>
                  }
                  secondary={
                    permission.conditional
                      ? "Only required when “Manage Immich clips after upload” is enabled."
                      : undefined
                  }
                />
              </ListItem>
            ))}
          </List>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setImmichPermissionsOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
      </Stack>

      <Stack spacing={3}>
      <Box component="form" id="settings-general-form" onSubmit={submitGeneral}>
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
                    onChange={(_event, value) => setTimezone(value)}
                    renderInput={(parameters) => (
                      <TextField
                        {...parameters}
                        label="Timezone"
                        helperText={
                          settings.timezone_configured
                            ? undefined
                            : "Detected from this browser. Save settings to keep it."
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

              {saveGeneral.error && <Alert severity="error">{saveGeneral.error.message}</Alert>}
              {saveGeneral.data && <Alert severity="success">Settings saved.</Alert>}
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
