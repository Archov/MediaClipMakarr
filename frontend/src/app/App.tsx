import ContentCutRounded from "@mui/icons-material/ContentCutRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import VideoLibraryRounded from "@mui/icons-material/VideoLibraryRounded";
import { AppBar, Box, Button, Container, CssBaseline, Stack, ThemeProvider, Toolbar, Typography } from "@mui/material";
import { useEffect, useState } from "react";

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
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar
        position="sticky"
        color="transparent"
        elevation={0}
        sx={{
          top: 0,
          bgcolor: "rgba(11, 17, 32, 0.92)",
          backdropFilter: "blur(10px)",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Toolbar
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr auto 1fr" },
            alignItems: "center",
            gap: 2,
            py: 1,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1} sx={{ justifySelf: { sm: "start" }, minWidth: 0 }}>
            <ContentCutRounded />
            <Typography variant="h6" sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>
              MediaClipMakarr
            </Typography>
          </Stack>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            useFlexGap
            flexWrap="wrap"
            sx={{ justifySelf: "center" }}
          >
            <Button color={page === "make-clip" ? "primary" : "inherit"} startIcon={<ContentCutRounded />} variant={page === "make-clip" ? "outlined" : "text"} onClick={() => setPage("make-clip")}>Clip</Button>
            <Button color={page === "library" ? "primary" : "inherit"} startIcon={<VideoLibraryRounded />} variant={page === "library" ? "outlined" : "text"} onClick={() => setPage("library")}>Library</Button>
            <Button color={page === "settings" ? "primary" : "inherit"} startIcon={<SettingsRounded />} variant={page === "settings" ? "outlined" : "text"} onClick={() => setPage("settings")}>Settings</Button>
          </Stack>
          <Stack direction="row" justifyContent={{ xs: "flex-start", sm: "flex-end" }} sx={{ justifySelf: { sm: "end" } }} />

        </Toolbar>
      </AppBar>
      <Container
        maxWidth={page === "library" || page === "settings" ? "xl" : "md"}
        sx={{ py: page === "library" ? { xs: 1.5, md: 2.5 } : { xs: 4, md: 7 } }}
      >
        <Box>{page === "make-clip" ? <MakeClipScreen /> : page === "library" ? <LibraryScreen /> : <SettingsScreen />}</Box>
      </Container>
    </ThemeProvider>
  );
}
