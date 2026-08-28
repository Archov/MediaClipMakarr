import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import ErrorRounded from "@mui/icons-material/ErrorRounded";
import WarningRounded from "@mui/icons-material/WarningRounded";
import {
  Alert,
  AppBar,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  CssBaseline,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Stack,
  ThemeProvider,
  Toolbar,
  Typography,
  createTheme,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";

import { fetchHealth } from "./api";
import type { HealthStatus } from "./types";

const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#60a5fa" },
    background: { default: "#0b1120", paper: "#111827" },
  },
  shape: { borderRadius: 14 },
});

function StatusIcon({ status }: { status: HealthStatus }) {
  if (status === "ok") return <CheckCircleRounded color="success" />;
  if (status === "degraded") return <WarningRounded color="warning" />;
  return <ErrorRounded color="error" />;
}

function StatusChip({ status }: { status: HealthStatus }) {
  const color = status === "ok" ? "success" : status === "degraded" ? "warning" : "error";
  return <Chip label={status.toUpperCase()} color={color} size="small" />;
}

export function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="static" color="transparent" elevation={0}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700 }}>
            MediaClipMakarr
          </Typography>
          {health.data && <StatusChip status={health.data.status} />}
        </Toolbar>
      </AppBar>
      <Container maxWidth="md" sx={{ py: { xs: 4, md: 8 } }}>
        <Stack spacing={3}>
          <Box>
            <Typography variant="h3" component="h1" gutterBottom sx={{ fontWeight: 800 }}>
              Runtime readiness
            </Typography>
            <Typography color="text.secondary">
              The capture workflow will arrive in the next milestones. This screen verifies the
              application foundation and its media dependencies.
            </Typography>
          </Box>

          {health.isLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
              <CircularProgress aria-label="Loading health status" />
            </Box>
          )}
          {health.error && <Alert severity="error">{health.error.message}</Alert>}
          {health.data && (
            <>
              <Card variant="outlined">
                <CardContent>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="h5">Core services</Typography>
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

              <Card variant="outlined">
                <CardContent>
                  <Typography variant="h5" gutterBottom>
                    Configured directories
                  </Typography>
                  <List>
                    {health.data.directories.map((directory) => (
                      <ListItem key={directory.name} disableGutters alignItems="flex-start">
                        <ListItemIcon sx={{ minWidth: 40, pt: 0.5 }}>
                          <StatusIcon status={directory.status} />
                        </ListItemIcon>
                        <ListItemText
                          primary={`${directory.name} · ${directory.mode}`}
                          secondary={directory.message}
                        />
                      </ListItem>
                    ))}
                  </List>
                </CardContent>
              </Card>
            </>
          )}
        </Stack>
      </Container>
    </ThemeProvider>
  );
}
