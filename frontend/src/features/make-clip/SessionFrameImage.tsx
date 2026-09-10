import ImageRounded from "@mui/icons-material/ImageRounded";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";

/** A captured frame, sized purely by the decoded image's own pixels — never
 * a guessed aspect ratio. Guessing (even briefly, e.g. assuming 16:9 while a
 * separate "what shape is this?" request is still in flight) shows fake
 * letterbox/pillarbox bars over content that has none, then reflows once
 * the truth arrives. The plain `<img>` here needs no such guess: the
 * browser sizes it from the file itself, so the box is always correct,
 * immediately, with no separate lookup and no correction step. */
export function SessionFrameImage({
  source,
  alt,
  width,
}: {
  source: string;
  alt: string;
  width: number | string;
}) {
  const [status, setStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  useEffect(() => setStatus("loading"), [source]);

  return (
    <Box sx={{ position: "relative", width, maxWidth: "100%" }}>
      <Box
        component="img"
        src={source}
        alt={alt}
        onLoad={() => {
          setStatus("loaded");
          setHasLoadedOnce(true);
        }}
        onError={() => setStatus("error")}
        sx={{
          width: "100%",
          height: "auto",
          display: hasLoadedOnce ? "block" : "none",
          borderRadius: 1,
          bgcolor: "black",
        }}
      />
      {!hasLoadedOnce && status !== "error" && (
        // Nothing has ever loaded for this slot yet — a neutral loading
        // skeleton, not a claim about the video's shape.
        <Box
          sx={{
            width: "100%",
            aspectRatio: "16 / 9",
            bgcolor: "black",
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
          }}
        >
          <CircularProgress size={24} aria-label="Loading captured frame" sx={{ color: "common.white" }} />
        </Box>
      )}
      {hasLoadedOnce && status === "loading" && (
        // Reloading (a nudge, a new crop, …): keep showing the last correctly
        // sized frame underneath rather than resetting to a guessed shape.
        <CircularProgress
          size={24}
          aria-label="Loading captured frame"
          sx={{ position: "absolute", inset: 0, m: "auto", color: "common.white" }}
        />
      )}
      {status === "error" && (
        <Stack
          spacing={0.5}
          alignItems="center"
          justifyContent="center"
          sx={
            hasLoadedOnce
              ? { position: "absolute", inset: 0, color: "grey.400" }
              : { width: "100%", aspectRatio: "16 / 9", color: "grey.400" }
          }
        >
          <ImageRounded />
          <Typography variant="caption" color="inherit">Frame unavailable</Typography>
        </Stack>
      )}
    </Box>
  );
}
