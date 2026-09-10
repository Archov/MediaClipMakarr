import { Box, LinearProgress, Stack, Typography } from "@mui/material";

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
    <Stack spacing={0.75} sx={{ width: "100%", textAlign: "center" }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
        {session.title}
      </Typography>
      <Box sx={{ width: "100%" }}>
        <LinearProgress variant="determinate" value={progress} aria-label="Playback progress" />
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 0.5, fontVariantNumeric: "tabular-nums" }}
        >
          {formatMilliseconds(position)} / {formatMilliseconds(session.duration_ms)}
        </Typography>
      </Box>
    </Stack>
  );
}
