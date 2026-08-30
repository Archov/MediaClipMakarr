import MovieRounded from "@mui/icons-material/MovieRounded";
import PersonRounded from "@mui/icons-material/PersonRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import SmartDisplayRounded from "@mui/icons-material/SmartDisplayRounded";
import { Box, Chip, LinearProgress, Stack, Typography } from "@mui/material";

import type { PlexSession } from "../../types";
import { formatTimestampMs } from "../../timestamps";
import { displayedPosition, useClock } from "./hooks";

function formatMilliseconds(value: number | null): string {
  return formatTimestampMs(value) || "--:--";
}

export function SessionDetail({ session }: { session: PlexSession }) {
  const now = useClock(session.state.toLowerCase() === "playing");
  const position = displayedPosition(session, now);
  const progress =
    session.duration_ms && session.duration_ms > 0
      ? Math.min(100, Math.max(0, (position / session.duration_ms) * 100))
      : 0;
  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        <Chip
          icon={<PlayArrowRounded />}
          label={session.state}
          color={session.state.toLowerCase() === "playing" ? "success" : "default"}
          sx={{ alignSelf: "flex-start", textTransform: "capitalize" }}
        />
        <Chip
          icon={<MovieRounded />}
          label={session.media_type}
          variant="outlined"
          sx={{ alignSelf: "flex-start", textTransform: "capitalize" }}
        />
        {session.plex_user && (
          <Chip icon={<PersonRounded />} label={session.plex_user} variant="outlined" />
        )}
        {session.player && (
          <Chip icon={<SmartDisplayRounded />} label={session.player} variant="outlined" />
        )}
      </Stack>
      <Box>
        <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
          <Typography variant="body2" color="text.secondary">Playback position</Typography>
          <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {formatMilliseconds(position)} / {formatMilliseconds(session.duration_ms)}
          </Typography>
        </Stack>
        <LinearProgress variant="determinate" value={progress} aria-label="Playback progress" />
      </Box>
    </Stack>
  );
}
