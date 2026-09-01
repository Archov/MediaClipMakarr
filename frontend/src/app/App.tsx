import ContentCutRounded from "@mui/icons-material/ContentCutRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import VideoLibraryRounded from "@mui/icons-material/VideoLibraryRounded";
import { AppBar, Box, Button, Chip, Container, CssBaseline, Stack, ThemeProvider, Toolbar, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { fetchHealth } from "../api";
import { MakeClipScreen } from "../features/make-clip/MakeClipScreen";
import { LibraryScreen } from "../features/library/LibraryScreen";
import { SettingsScreen } from "../features/settings/SettingsScreen";
import { theme } from "./theme";

type AppPage = "make-clip" | "library" | "settings";

export function App() {
  const [page, setPageState] = useState<AppPage>(() => window.location.pathname === "/library" ? "library" : window.location.pathname === "/settings" ? "settings" : "make-clip");
  const setPage = (next: AppPage) => {
    setPageState(next);
    window.history.pushState(null, "", next === "make-clip" ? "/" : `/${next}`);
  };
  useEffect(() => {
    const navigate = () => setPageState(window.location.pathname === "/library" ? "library" : window.location.pathname === "/settings" ? "settings" : "make-clip");
    window.addEventListener("popstate", navigate);
    return () => window.removeEventListener("popstate", navigate);
  }, []);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });
  const healthColor = health.data?.status === "ok"
    ? "success"
    : health.data?.status === "degraded"
      ? "warning"
      : "error";
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="static" color="transparent" elevation={0}>
        <Toolbar sx={{ gap: 1, flexWrap: "wrap", py: 1 }}>
          <ContentCutRounded sx={{ mr: 1 }} />
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700, minWidth: 180 }}>
            MediaClipMakarr
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mr: { sm: 2 } }}>
            <Button color={page === "make-clip" ? "primary" : "inherit"} startIcon={<ContentCutRounded />} variant={page === "make-clip" ? "outlined" : "text"} onClick={() => setPage("make-clip")}>Clip</Button>
            <Button color={page === "library" ? "primary" : "inherit"} startIcon={<VideoLibraryRounded />} variant={page === "library" ? "outlined" : "text"} onClick={() => setPage("library")}>Library</Button>
            <Button color={page === "settings" ? "primary" : "inherit"} startIcon={<SettingsRounded />} variant={page === "settings" ? "outlined" : "text"} onClick={() => setPage("settings")}>Settings</Button>
          </Stack>
          {health.data && <Chip label={health.data.status.toUpperCase()} color={healthColor} size="small" />}
        </Toolbar>
      </AppBar>
      <Container maxWidth={page === "library" ? "xl" : "md"} sx={{ py: page === "library" ? { xs: 1.5, md: 2.5 } : { xs: 4, md: 7 } }}>
        <Box>{page === "make-clip" ? <MakeClipScreen /> : page === "library" ? <LibraryScreen /> : <SettingsScreen />}</Box>
      </Container>
    </ThemeProvider>
  );
}
