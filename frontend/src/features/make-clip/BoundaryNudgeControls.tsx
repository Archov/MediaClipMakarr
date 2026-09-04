import AddRounded from "@mui/icons-material/AddRounded";
import ChevronLeftRounded from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import SkipNextRounded from "@mui/icons-material/SkipNextRounded";
import SkipPreviousRounded from "@mui/icons-material/SkipPreviousRounded";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { type ReactNode, useEffect, useRef, useState } from "react";

import {
  NUDGE_UNIT_PREFIX,
  availableNudgeUnits,
  clampAdjustmentValue,
  clampBoundaryMs,
  nudgeStepMs,
  nudgeUnitSuffix,
  stepNudgeUnit,
  type NudgeUnit,
} from "./boundaryNudges";

const pillSx = {
  display: "flex",
  alignItems: "center",
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  overflow: "hidden",
} as const;

interface BoundaryNudgeControlsProps {
  startMs: number | null;
  endMs: number | null;
  maximumMs: number | null | undefined;
  frameRate: number | null;
  onStartChange: (value: number) => void;
  onEndChange: (value: number) => void;
  defaultUnit?: NudgeUnit;
  defaultValue?: number;
  extraAction?: ReactNode;
}

export function BoundaryNudgeControls({
  startMs,
  endMs,
  maximumMs,
  frameRate,
  onStartChange,
  onEndChange,
  defaultUnit = "seconds",
  defaultValue = 5,
  extraAction,
}: BoundaryNudgeControlsProps) {
  const nudgeValueBoxRef = useRef<HTMLDivElement>(null);
  const framesAvailable = Boolean(frameRate);
  const [adjustmentValue, setAdjustmentValue] = useState(() => clampAdjustmentValue(defaultValue));
  const [adjustmentUnit, setAdjustmentUnit] = useState<NudgeUnit>(() =>
    defaultUnit === "frames" && !framesAvailable ? "seconds" : defaultUnit,
  );
  const availableUnits = availableNudgeUnits(framesAvailable);
  const adjustmentUnitIndex = availableUnits.indexOf(adjustmentUnit);
  const isCoarsestUnit = adjustmentUnitIndex <= 0;
  const isFinestUnit = adjustmentUnitIndex === availableUnits.length - 1;

  useEffect(() => {
    const box = nudgeValueBoxRef.current;
    if (!box) return;
    const wheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      setAdjustmentValue((value) => clampAdjustmentValue(value + (event.deltaY < 0 ? 1 : -1)));
    };
    box.addEventListener("wheel", wheel, { passive: false });
    return () => box.removeEventListener("wheel", wheel);
  }, []);

  useEffect(() => {
    if (adjustmentUnit === "frames" && !framesAvailable) setAdjustmentUnit("seconds");
  }, [adjustmentUnit, framesAvailable]);

  const adjustStart = (direction: 1 | -1) => {
    if (startMs === null) return;
    const stepMs = nudgeStepMs(adjustmentUnit, adjustmentValue, frameRate);
    onStartChange(clampBoundaryMs(startMs + direction * stepMs, maximumMs));
  };

  const adjustEnd = (direction: 1 | -1) => {
    const baseMs = endMs ?? startMs;
    if (baseMs === null) return;
    const stepMs = nudgeStepMs(adjustmentUnit, adjustmentValue, frameRate);
    onEndChange(clampBoundaryMs(baseMs + direction * stepMs, maximumMs));
  };

  return (
    <Stack spacing={1} alignItems="center">
      <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary", letterSpacing: "0.04em" }}>
        NUDGE
      </Typography>
      <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="center" justifyContent="center">
        <Stack direction="row" alignItems="center" sx={pillSx}>
          <Tooltip title="Coarser unit">
            <span>
              <IconButton
                aria-label="Switch to coarser nudge unit"
                disabled={isCoarsestUnit}
                onClick={() => setAdjustmentUnit(stepNudgeUnit(adjustmentUnit, -1, framesAvailable))}
                sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
              >
                <SkipPreviousRounded fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <IconButton
            aria-label="Decrease nudge amount"
            onClick={() => setAdjustmentValue((value) => clampAdjustmentValue(value - 1))}
            sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
          >
            <ChevronLeftRounded fontSize="small" />
          </IconButton>
          <Tooltip title={adjustmentUnit === "frames" ? "Nominal frame durations to nudge" : "Unit count to nudge"}>
            <Box
              ref={nudgeValueBoxRef}
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: NUDGE_UNIT_PREFIX[adjustmentUnit] ? 0 : "5px",
                minWidth: 108,
                alignSelf: "stretch",
                px: 1.5,
                cursor: "default",
                userSelect: "none",
              }}
            >
              {NUDGE_UNIT_PREFIX[adjustmentUnit] && (
                <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600 }}>
                  {NUDGE_UNIT_PREFIX[adjustmentUnit]}
                </Typography>
              )}
              <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600 }}>
                {adjustmentValue}
              </Typography>
              <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600, whiteSpace: "nowrap" }}>
                {nudgeUnitSuffix(adjustmentUnit, adjustmentValue)}
              </Typography>
            </Box>
          </Tooltip>
          <IconButton
            aria-label="Increase nudge amount"
            onClick={() => setAdjustmentValue((value) => clampAdjustmentValue(value + 1))}
            sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
          >
            <ChevronRightRounded fontSize="small" />
          </IconButton>
          <Tooltip title="Finer unit">
            <span>
              <IconButton
                aria-label="Switch to finer nudge unit"
                disabled={isFinestUnit}
                onClick={() => setAdjustmentUnit(stepNudgeUnit(adjustmentUnit, 1, framesAvailable))}
                sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
              >
                <SkipNextRounded fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>

        <Stack direction="row" alignItems="center" sx={pillSx}>
          <IconButton
            aria-label="Nudge start earlier"
            disabled={startMs === null}
            onClick={() => adjustStart(-1)}
            sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
          >
            <RemoveRounded fontSize="small" />
          </IconButton>
          <Tooltip title="Add or subtract time from the current start timestamp">
            <Typography variant="body2" sx={{ px: 1.5, fontWeight: 600, whiteSpace: "nowrap", userSelect: "none" }}>
              Start
            </Typography>
          </Tooltip>
          <IconButton
            aria-label="Nudge start later"
            disabled={startMs === null}
            onClick={() => adjustStart(1)}
            sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
          >
            <AddRounded fontSize="small" />
          </IconButton>
        </Stack>

        <Stack direction="row" alignItems="center" sx={pillSx}>
          <IconButton
            aria-label="Nudge end earlier"
            disabled={endMs === null && startMs === null}
            onClick={() => adjustEnd(-1)}
            sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
          >
            <RemoveRounded fontSize="small" />
          </IconButton>
          <Tooltip title="Add or subtract time from the current end timestamp">
            <Typography variant="body2" sx={{ px: 1.5, fontWeight: 600, whiteSpace: "nowrap", userSelect: "none" }}>
              End
            </Typography>
          </Tooltip>
          <IconButton
            aria-label="Nudge end later"
            disabled={endMs === null && startMs === null}
            onClick={() => adjustEnd(1)}
            sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
          >
            <AddRounded fontSize="small" />
          </IconButton>
        </Stack>

        {extraAction}
      </Stack>
    </Stack>
  );
}
