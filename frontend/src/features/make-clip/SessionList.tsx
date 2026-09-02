import DownloadRounded from "@mui/icons-material/DownloadRounded";
import {
  Box,
  Button,
  List,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";
import { useEffect, useState } from "react";

import { sessionFrameUrl } from "../../api";
import type { PlexSession, PlexSessionSnapshot } from "../../types";
import { SessionDetail } from "./SessionDetail";
import { SessionFrameImage } from "./SessionFrameImage";
import {
  reconcileSessionFrameCaptures,
  type SessionFrameCapture,
  type SessionFrameCaptures,
} from "./sessionFrames";

function SessionFramePreview({ session, capture }: {
  session: PlexSession;
  capture: SessionFrameCapture;
}) {
  const source = sessionFrameUrl(
    session.session_identity,
    capture.mediaIdentity,
    capture.positionMs,
    capture.captureVersion,
  );

  return (
    <Box sx={{ width: { xs: "100%", sm: 224 }, flex: { sm: "0 0 224px" } }}>
      <SessionFrameImage
        source={source}
        alt={`Captured frame from ${session.title}`}
        width="100%"
      />
    </Box>
  );
}

export function SessionList({
  snapshot,
  selectedSessionIdentity,
  onSelect,
}: {
  snapshot: PlexSessionSnapshot | undefined;
  selectedSessionIdentity: string | null;
  onSelect: (session: PlexSession) => void;
}) {
  const [captures, setCaptures] = useState<SessionFrameCaptures>({});
  useEffect(() => {
    setCaptures((previous) =>
      reconcileSessionFrameCaptures(previous, snapshot?.sessions ?? []),
    );
  }, [snapshot]);

  if (!snapshot || snapshot.sessions.length === 0) {
    return <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 3 }}><Typography color="text.secondary">No active sessions.</Typography></Box>;
  }
  return <List disablePadding>{snapshot.sessions.map((session) => {
    const selected = session.session_identity === selectedSessionIdentity;
    const capture = captures[session.session_identity];
    const exportUrl = capture
      ? sessionFrameUrl(
          session.session_identity,
          capture.mediaIdentity,
          capture.positionMs,
          capture.captureVersion,
          true,
        )
      : undefined;
    return <Box
      key={session.session_identity}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={() => onSelect(session)}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onSelect(session);
        }
      }}
      sx={{
        border: 1,
        borderColor: selected ? "primary.main" : "divider",
        borderRadius: 1,
        mb: 1,
        overflow: "hidden",
        cursor: "pointer",
        bgcolor: selected ? "action.selected" : "background.paper",
        transition: "background-color 120ms ease, border-color 120ms ease",
        "&:hover": { bgcolor: selected ? "action.selected" : "action.hover" },
        "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: 2 },
      }}
    >
      <Box sx={{ p: 1.5 }}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ width: "100%", minWidth: 0 }}>
          {capture && <SessionFramePreview session={session} capture={capture} />}
          <ListItemText primary={session.title} secondary={<SessionDetail session={session} />} slotProps={{ secondary: { component: "div" } }} sx={{ m: 0, minWidth: 0 }} />
        </Stack>
      </Box>
      <Stack direction="row" justifyContent="flex-end" sx={{ px: 2, py: 1, borderTop: 1, borderColor: "divider" }}>
        <Button
          component="a"
          href={exportUrl}
          download={`frame-${capture?.positionMs ?? 0}ms.png`}
          startIcon={<DownloadRounded />}
          size="small"
          disabled={!exportUrl}
          onClick={(event) => event.stopPropagation()}
        >
          Export frame
        </Button>
      </Stack>
    </Box>;
  })}</List>;
}

