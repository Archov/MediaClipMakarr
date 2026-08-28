import AddRounded from "@mui/icons-material/AddRounded";
import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import ArrowUpwardRounded from "@mui/icons-material/ArrowUpwardRounded";
import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import ErrorRounded from "@mui/icons-material/ErrorRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import WarningRounded from "@mui/icons-material/WarningRounded";
import {
  Alert,
  AppBar,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  CssBaseline,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  TextField,
  ThemeProvider,
  Toolbar,
  Typography,
  createTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { fetchHealth, fetchSettings, testPlexConnection, updateSettings } from "./api";
import type {
  ApplicationSettingField,
  ApplicationSettings,
  ApplicationSettingsUpdate,
  HealthStatus,
  PlexConnectionRequest,
  PlexConnectionResult,
  SourcePathMapping,
} from "./types";

const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#60a5fa" },
    background: { default: "#0b1120", paper: "#111827" },
  },
  shape: { borderRadius: 14 },
});

const x264Presets = [
  "ultrafast",
  "superfast",
  "veryfast",
  "faster",
  "fast",
  "medium",
  "slow",
  "slower",
  "veryslow",
];

function StatusIcon({ status }: { status: HealthStatus }) {
  if (status === "ok") return <CheckCircleRounded color="success" />;
  if (status === "degraded") return <WarningRounded color="warning" />;
  return <ErrorRounded color="error" />;
}

function StatusChip({ status }: { status: HealthStatus }) {
  const color = status === "ok" ? "success" : status === "degraded" ? "warning" : "error";
  return <Chip label={status.toUpperCase()} color={color} size="small" />;
}

function ManagedLabel({ managed }: { managed: boolean }) {
  return managed ? <Chip label="Environment managed" size="small" variant="outlined" /> : null;
}

function initialTimezone(settings: ApplicationSettings): string {
  if (settings.timezone_configured) return settings.timezone;
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return detected && settings.available_timezones.includes(detected)
    ? detected
    : settings.timezone;
}

interface PlexCandidate {
  test: PlexConnectionRequest;
  save: ApplicationSettingsUpdate;
}

interface SettingsOperation {
  kind: "save" | "test";
  baseUpdate: ApplicationSettingsUpdate;
  plexCandidate?: PlexCandidate;
}

function SettingsForm({ settings }: { settings: ApplicationSettings }) {
  const queryClient = useQueryClient();
  const [plexUrl, setPlexUrl] = useState(settings.plex_url);
  const plexTokenInput = useRef<HTMLInputElement>(null);
  const [timezone, setTimezone] = useState(() => initialTimezone(settings));
  const [x264Preset, setX264Preset] = useState(settings.x264_preset);
  const [mappings, setMappings] = useState<SourcePathMapping[]>(settings.source_path_mappings);
  const [connection, setConnection] = useState<PlexConnectionResult | null>(null);

  useEffect(() => {
    setPlexUrl(settings.plex_url);
    if (plexTokenInput.current) plexTokenInput.current.value = "";
    setTimezone(initialTimezone(settings));
    setX264Preset(settings.x264_preset);
    setMappings(settings.source_path_mappings);
  }, [settings]);

  const managed = (field: ApplicationSettingField) => settings.environment_managed[field];
  const save = useMutation({
    mutationFn: async (operation: SettingsOperation) => {
      let updated = Object.keys(operation.baseUpdate).length
        ? await updateSettings(operation.baseUpdate)
        : settings;
      if (!operation.plexCandidate) {
        return {
          settings: updated,
          connection: null,
          notice: "Settings saved.",
        };
      }

      const result = await testPlexConnection(operation.plexCandidate.test);
      if (!result.connected) {
        return {
          settings: updated,
          connection: result,
          notice:
            operation.kind === "test"
              ? "Connection failed. Plex settings were not saved."
              : "Other settings were saved. The new Plex credentials were rejected.",
        };
      }

      if (Object.keys(operation.plexCandidate.save).length) {
        updated = await updateSettings(operation.plexCandidate.save);
      }
      return {
        settings: updated,
        connection: result,
        notice:
          operation.kind === "test"
            ? "Connection succeeded and Plex settings were saved."
            : "Settings and verified Plex credentials were saved.",
      };
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["settings"], result.settings);
      setConnection(result.connection);
      if (plexTokenInput.current) plexTokenInput.current.value = "";
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    const submittedToken = String(new FormData(form).get("plex_token") ?? "").trim();
    const baseUpdate: ApplicationSettingsUpdate = {
      ...(!submittedToken && !managed("plex_url") && { plex_url: plexUrl }),
      ...(!managed("source_path_mappings") && { source_path_mappings: mappings }),
      ...(!managed("timezone") && { timezone }),
      ...(!managed("x264_preset") && { x264_preset: x264Preset }),
    };
    save.mutate({
      kind: "save",
      baseUpdate,
      ...(!managed("plex_token") &&
        submittedToken && {
          plexCandidate: {
            test: { plex_url: plexUrl, plex_token: submittedToken },
            save: {
              ...(!managed("plex_url") && { plex_url: plexUrl }),
              plex_token: submittedToken,
            },
          },
        }),
    });
  };
  const testCurrentConnection = () => {
    const submittedToken = plexTokenInput.current?.value.trim() ?? "";
    save.mutate({
      kind: "test",
      baseUpdate: {},
      plexCandidate: {
        test: {
          plex_url: plexUrl,
          ...(submittedToken && { plex_token: submittedToken }),
        },
        save: {
          ...(!managed("plex_url") && { plex_url: plexUrl }),
          ...(!managed("plex_token") && submittedToken && { plex_token: submittedToken }),
        },
      },
    });
  };

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
    <Card variant="outlined">
      <CardContent>
        <Stack component="form" spacing={3} onSubmit={submit}>
          <Box>
            <Typography variant="h5" gutterBottom>Plex connection</Typography>
            <Typography color="text.secondary">
              Credentials stay on the server. The API only reports whether a token is configured.
            </Typography>
          </Box>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
            <TextField
              fullWidth
              label="Plex server URL"
              placeholder="http://192.168.1.20:32400"
              value={plexUrl}
              disabled={managed("plex_url")}
              onChange={(event) => {
                setPlexUrl(event.target.value);
                setConnection(null);
              }}
            />
            <ManagedLabel managed={managed("plex_url")} />
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
            <TextField
              fullWidth
              type="password"
              name="plex_token"
              label="Plex token"
              placeholder={settings.plex_token_configured ? "●●●●●●●●" : "Enter token"}
              slotProps={{ inputLabel: { shrink: true } }}
              inputRef={plexTokenInput}
              disabled={managed("plex_token")}
              onChange={() => setConnection(null)}
              helperText="The saved token is replaced only when you enter a new one."
            />
            <ManagedLabel managed={managed("plex_token")} />
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
            <Button
              variant="outlined"
              disabled={!plexUrl.trim() || save.isPending}
              onClick={testCurrentConnection}
            >
              {save.isPending && save.variables?.kind === "test" ? "Testing…" : "Test connection"}
            </Button>
            <Typography color="text.secondary" variant="body2">
              Tests the current URL/token and saves them only when the connection succeeds.
            </Typography>
          </Stack>
          {connection && (
            <Alert severity={connection.connected ? "success" : "error"}>
              {connection.message}{connection.server_name ? ` Server: ${connection.server_name}.` : ""}
            </Alert>
          )}

          <Divider />

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
              direction={{ xs: "column", md: "row" }}
              spacing={1}
              alignItems={{ md: "center" }}
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
          ))}
          <Button
            startIcon={<AddRounded />}
            disabled={managed("source_path_mappings")}
            onClick={() => setMappings((current) => [...current, { plex_prefix: "", local_prefix: "" }])}
            sx={{ alignSelf: "flex-start" }}
          >
            Add mapping
          </Button>

          <Divider />

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
                        ? "IANA timezone used for application timestamps."
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

          {save.error && <Alert severity="error">{save.error.message}</Alert>}
          {save.data && (
            <Alert severity={save.data.connection && !save.data.connection.connected ? "warning" : "success"}>
              {save.data.notice}
            </Alert>
          )}
          <Button type="submit" variant="contained" disabled={save.isPending} sx={{ alignSelf: "flex-start" }}>
            {save.isPending && save.variables?.kind === "save" ? "Saving…" : "Save settings"}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}

export function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="static" color="transparent" elevation={0}>
        <Toolbar>
          <SettingsRounded sx={{ mr: 1 }} />
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700 }}>MediaClipMakarr</Typography>
          {health.data && <StatusChip status={health.data.status} />}
        </Toolbar>
      </AppBar>
      <Container maxWidth="md" sx={{ py: { xs: 4, md: 7 } }}>
        <Stack spacing={3}>
          <Box>
            <Typography variant="h3" component="h1" gutterBottom sx={{ fontWeight: 800 }}>Settings</Typography>
            <Typography color="text.secondary">
              Connect Plex, map its media paths to read-only local sources, and choose encoding defaults.
            </Typography>
          </Box>

          {(settings.isLoading || health.isLoading) && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
              <CircularProgress aria-label="Loading settings" />
            </Box>
          )}
          {settings.error && <Alert severity="error">{settings.error.message}</Alert>}
          {settings.data && <SettingsForm settings={settings.data} />}

          {health.error && <Alert severity="error">{health.error.message}</Alert>}
          {health.data && (
            <Card variant="outlined">
              <CardContent>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Typography variant="h5">Runtime readiness</Typography>
                  <StatusChip status={health.data.status} />
                </Stack>
                <List>
                  {[
                    ["Application", health.data.application],
                    ["SQLite", health.data.database],
                    ["Jellyfin FFmpeg", health.data.media_tools],
                  ].map(([name, component]) => {
                    const item = component as typeof health.data.application;
                    return (
                      <ListItem key={name as string} disableGutters alignItems="flex-start">
                        <ListItemIcon sx={{ minWidth: 40, pt: 0.5 }}>
                          <StatusIcon status={item.status} />
                        </ListItemIcon>
                        <ListItemText primary={name as string} secondary={item.message} />
                      </ListItem>
                    );
                  })}
                </List>
              </CardContent>
            </Card>
          )}
        </Stack>
      </Container>
    </ThemeProvider>
  );
}
