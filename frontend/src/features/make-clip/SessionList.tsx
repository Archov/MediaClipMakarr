import AvTimerRounded from "@mui/icons-material/AvTimerRounded";
import { Box, List, ListItemButton, ListItemIcon, ListItemText, Typography } from "@mui/material";

import type { PlexSession, PlexSessionSnapshot } from "../../types";
import { SessionDetail } from "./SessionDetail";

export function SessionList({
  snapshot,
  selectedSessionIdentity,
  onSelect,
}: {
  snapshot: PlexSessionSnapshot | undefined;
  selectedSessionIdentity: string | null;
  onSelect: (session: PlexSession) => void;
}) {
  if (!snapshot || snapshot.sessions.length === 0) {
    return <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 3 }}><Typography color="text.secondary">No active sessions.</Typography></Box>;
  }
  return <List disablePadding>{snapshot.sessions.map((session) => {
    const selected = session.session_identity === selectedSessionIdentity;
    return <ListItemButton key={session.session_identity} selected={selected} onClick={() => onSelect(session)} sx={{ borderRadius: 1, mb: 1, alignItems: "flex-start" }}>
      <ListItemIcon sx={{ minWidth: 42, pt: 0.5 }}><AvTimerRounded color={selected ? "primary" : "inherit"} /></ListItemIcon>
      <ListItemText primary={session.title} secondary={<SessionDetail session={session} />} slotProps={{ secondary: { component: "div" } }} />
    </ListItemButton>;
  })}</List>;
}

