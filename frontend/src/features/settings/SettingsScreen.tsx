import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import ErrorRounded from "@mui/icons-material/ErrorRounded";
import WarningRounded from "@mui/icons-material/WarningRounded";
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";

import { fetchHealth, fetchSettings } from "../../api";
import type { HealthResponse, HealthStatus } from "../../types";
import { SettingsForm } from "./SettingsForm";

function StatusIcon({ status }: { status: HealthStatus }) {
  if (status === "ok") return <CheckCircleRounded color="success" />;
  if (status === "degraded") return <WarningRounded color="warning" />;
  return <ErrorRounded color="error" />;
}

function StatusChip({ status }: { status: HealthStatus }) {
  const color = status === "ok" ? "success" : status === "degraded" ? "warning" : "error";
  return <Chip label={status.toUpperCase()} color={color} size="small" />;
}

function RuntimeReadinessCard({ health }: { health: HealthResponse }) {
  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Typography variant="h5">Runtime readiness</Typography>
          <StatusChip status={health.status} />
        </Stack>
        <List>
          {[
            ["Application", health.application],
            ["SQLite", health.database],
            ["Jellyfin FFmpeg", health.media_tools],
          ].map(([name, component]) => {
            const item = component as typeof health.application;
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
  );
}

export function SettingsScreen() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });

  return (
    <Stack spacing={3}>
      {(settings.isLoading || health.isLoading) && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
          <CircularProgress aria-label="Loading settings" />
        </Box>
      )}
      {settings.error && <Alert severity="error">{settings.error.message}</Alert>}
      {health.error && <Alert severity="error">{health.error.message}</Alert>}
      {settings.data && (
        <SettingsForm
          settings={settings.data}
          readiness={health.data && <RuntimeReadinessCard health={health.data} />}
        />
      )}
    </Stack>
  );
}
