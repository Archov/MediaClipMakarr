import ImageRounded from "@mui/icons-material/ImageRounded";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";

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
  useEffect(() => setStatus("loading"), [source]);

  return (
    <Box
      sx={{
        position: "relative",
        width,
        maxWidth: "100%",
        aspectRatio: "16 / 9",
        bgcolor: "black",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      {status !== "error" && (
        <Box
          component="img"
          src={source}
          alt={alt}
          onLoad={() => setStatus("loaded")}
          onError={() => setStatus("error")}
          sx={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
        />
      )}
      {status === "loading" && (
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
          sx={{ position: "absolute", inset: 0, color: "grey.400" }}
        >
          <ImageRounded />
          <Typography variant="caption" color="inherit">Frame unavailable</Typography>
        </Stack>
      )}
    </Box>
  );
}
