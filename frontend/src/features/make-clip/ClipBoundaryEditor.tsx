import AddRounded from "@mui/icons-material/AddRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import { Button, IconButton, Stack, TextField } from "@mui/material";
import { useEffect, useRef } from "react";

const MAX_ADJUSTMENT_SECONDS = 99;
const clamp = (value: number) => Math.min(MAX_ADJUSTMENT_SECONDS, Math.max(-MAX_ADJUSTMENT_SECONDS, value));
const label = (value: number) => value > 0 ? `+${value}` : value.toString();

export function ClipBoundaryEditor({ adjustmentSeconds, startMs, endMs, onAdjustmentChange, onAdjustStart, onAdjustEnd }: {
  adjustmentSeconds: number; startMs: number | null; endMs: number | null;
  onAdjustmentChange: (value: number) => void; onAdjustStart: () => void; onAdjustEnd: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const wheel = (event: WheelEvent) => { if (event.deltaY !== 0) { event.preventDefault(); onAdjustmentChange(clamp(adjustmentSeconds + (event.deltaY < 0 ? 1 : -1))); } };
    input.addEventListener("wheel", wheel, { passive: false });
    return () => input.removeEventListener("wheel", wheel);
  }, [adjustmentSeconds, onAdjustmentChange]);
  return <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="flex-start">
    <TextField inputRef={inputRef} type="number" label="Seconds" value={adjustmentSeconds} onChange={(event) => { const value = Number(event.target.value); onAdjustmentChange(Number.isFinite(value) ? clamp(Math.trunc(value)) : 0); }} slotProps={{ htmlInput: { max: MAX_ADJUSTMENT_SECONDS, min: -MAX_ADJUSTMENT_SECONDS, step: 1 } }} sx={{ width: "12ch" }} />
    <Stack direction="row" spacing={0.5} sx={{ minHeight: 56 }}><IconButton aria-label="Decrease seconds" onClick={() => onAdjustmentChange(clamp(adjustmentSeconds - 1))} sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}><RemoveRounded /></IconButton><IconButton aria-label="Increase seconds" onClick={() => onAdjustmentChange(clamp(adjustmentSeconds + 1))} sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}><AddRounded /></IconButton></Stack>
    <Button variant="outlined" disabled={startMs === null} onClick={onAdjustStart} sx={{ minHeight: 56, textTransform: "none", whiteSpace: "nowrap", width: 116 }}>Start {label(adjustmentSeconds)}s</Button>
    <Button variant="outlined" disabled={endMs === null && startMs === null} onClick={onAdjustEnd} sx={{ minHeight: 56, textTransform: "none", whiteSpace: "nowrap", width: 116 }}>End {label(adjustmentSeconds)}s</Button>
  </Stack>;
}

