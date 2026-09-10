import CropRounded from "@mui/icons-material/CropRounded";
import SaveRounded from "@mui/icons-material/SaveRounded";
import {
  Alert,
  Autocomplete,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useEffect, useRef, useState } from "react";

import { detectCrop, fetchAspectRatioOverride, saveAspectRatioOverride } from "../../api";
import type { CropBox } from "../../types";

const COMMON_RATIOS = [
  "4:3",
  "1.37:1",
  "16:9",
  "16:10",
  "1.85:1",
  "1.90:1",
  "21:9",
  "2.35:1",
  "2.39:1",
];

interface CropControlsProps {
  sessionIdentity: string;
  mediaIdentity: string;
  startMs: number | null;
  endMs: number | null;
  onCropChange: (crop: CropBox | null) => void;
}

export function CropControls({
  sessionIdentity,
  mediaIdentity,
  startMs,
  endMs,
  onCropChange,
}: CropControlsProps) {
  const [enabled, setEnabled] = useState(false);
  const [ratioChoice, setRatioChoice] = useState("auto");
  const [detected, setDetected] = useState<{
    fraction: string;
    decimal: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const requestIdRef = useRef(0);

  // A saved override for this show/movie defaults the controls on, pre-set
  // to that ratio — the whole point of persisting it is to skip re-detecting.
  useEffect(() => {
    let cancelled = false;
    setEnabled(false);
    setRatioChoice("auto");
    setDetected(null);
    setError(null);
    setSaveState("idle");
    fetchAspectRatioOverride(sessionIdentity)
      .then((response) => {
        if (cancelled || !response.aspect_ratio) return;
        setRatioChoice(response.aspect_ratio);
        setEnabled(true);
      })
      .catch(() => {
        // No saved override (or Plex/lookup unavailable) — Auto stays the default.
      });
    return () => {
      cancelled = true;
    };
    // Only reset when the underlying media changes, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionIdentity, mediaIdentity]);

  // Debounced: enabling crop, picking a ratio, and nudging Start/End all land
  // here, and each detection is a real network round trip (auto mode runs
  // two ffmpeg probes server-side). Firing on every micro-change — e.g. a
  // burst of 1-frame nudges — would queue up several requests whose
  // responses interleave out of order, each swapping the preview's crop box
  // (and therefore its image URL) before the previous one ever finished
  // loading: a permanently "loading" thumbnail even though every individual
  // request succeeds. Waiting for input to settle avoids that pile-up.
  useEffect(() => {
    const hasRange = startMs !== null && endMs !== null && endMs > startMs;
    // Auto mode samples near both Start and End, so it needs a real range.
    // A fixed ratio only needs the source's dimensions — not a range — so
    // it can (and should) still crop the Start preview before End is set.
    if (!enabled || startMs === null || (ratioChoice === "auto" && !hasRange)) {
      setLoading(false);
      setDetected(null);
      setError(null);
      onCropChange(null);
      return;
    }
    setLoading(true);
    setError(null);
    const timeout = window.setTimeout(() => {
      const requestId = ++requestIdRef.current;
      detectCrop(sessionIdentity, {
        start_ms: startMs,
        end_ms: hasRange && endMs !== null ? endMs : startMs + 1,
        aspect_ratio: ratioChoice,
      })
        .then((response) => {
          if (requestIdRef.current !== requestId) return;
          setLoading(false);
          if (!response.crop) {
            setDetected(null);
            onCropChange(null);
            setError(
              ratioChoice === "auto"
                ? "No letterboxing or pillarboxing was detected."
                : "The source already matches this aspect ratio — nothing to crop.",
            );
            return;
          }
          setDetected({
            fraction: response.aspect_ratio_fraction ?? "",
            decimal: response.aspect_ratio_decimal ?? "",
          });
          onCropChange(response.crop);
        })
        .catch((cropError: Error) => {
          if (requestIdRef.current !== requestId) return;
          setLoading(false);
          setDetected(null);
          onCropChange(null);
          setError(cropError.message || "Crop detection failed.");
        });
    }, 600);
    return () => window.clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, sessionIdentity, startMs, endMs, ratioChoice]);

  const handleSave = () => {
    const valueToSave = ratioChoice === "auto" ? detected?.fraction ?? null : ratioChoice;
    if (!valueToSave) return;
    setSaveState("saving");
    saveAspectRatioOverride(sessionIdentity, valueToSave)
      .then(() => setSaveState("saved"))
      .catch(() => setSaveState("error"));
  };

  const canSave =
    enabled && !loading && (ratioChoice !== "auto" || Boolean(detected)) && !error;

  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={2} alignItems="center" useFlexGap flexWrap="wrap">
        <FormControlLabel
          control={
            <Checkbox
              checked={enabled}
              onChange={(event) => {
                setEnabled(event.target.checked);
                setSaveState("idle");
              }}
              icon={<CropRounded sx={{ opacity: 0.4 }} />}
              checkedIcon={<CropRounded />}
            />
          }
          label="Detect letterbox/pillarbox crop"
        />
        {enabled && (
          <Autocomplete
            freeSolo
            size="small"
            options={["auto", ...COMMON_RATIOS]}
            value={ratioChoice}
            onChange={(_event, value) => {
              setRatioChoice(value || "auto");
              setSaveState("idle");
            }}
            onInputChange={(_event, value, reason) => {
              if (reason === "input") {
                setRatioChoice(value || "auto");
                setSaveState("idle");
              }
            }}
            renderOption={(props, option) => (
              <li {...props} key={option}>
                {option === "auto" ? "Auto (detect)" : option}
              </li>
            )}
            sx={{ width: 200 }}
            renderInput={(params) => <TextField {...params} label="Aspect ratio" />}
          />
        )}
        {loading && <CircularProgress size={20} aria-label="Detecting crop" />}
        {enabled && (
          <Tooltip
            title={
              ratioChoice === "auto"
                ? "Save the detected ratio for this show/movie so future clips skip re-detecting."
                : "Save this ratio for this show/movie."
            }
          >
            <span>
              <Button
                size="small"
                variant={saveState === "saved" ? "outlined" : "text"}
                color={saveState === "saved" ? "success" : "primary"}
                startIcon={<SaveRounded />}
                disabled={!canSave || saveState === "saving"}
                onClick={handleSave}
              >
                {saveState === "saved" ? "Saved" : saveState === "saving" ? "Saving…" : "Save for this title"}
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>
      {enabled && ratioChoice === "auto" && detected && (
        <Typography variant="body2" color="text.secondary">
          Detected aspect ratio: <strong>{detected.fraction}</strong> ({detected.decimal})
        </Typography>
      )}
      {enabled && error && <Alert severity="info">{error}</Alert>}
      {saveState === "error" && <Alert severity="error">Could not save the aspect ratio.</Alert>}
    </Stack>
  );
}
