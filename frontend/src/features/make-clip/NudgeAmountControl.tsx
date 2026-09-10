import ChevronLeftRounded from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import SkipNextRounded from "@mui/icons-material/SkipNextRounded";
import SkipPreviousRounded from "@mui/icons-material/SkipPreviousRounded";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { useEffect, useRef } from "react";

import {
  NUDGE_UNIT_PREFIX,
  availableNudgeUnits,
  clampAdjustmentValue,
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

interface NudgeAmountControlProps {
  value: number;
  unit: NudgeUnit;
  framesAvailable: boolean;
  onValueChange: (value: number) => void;
  onUnitChange: (unit: NudgeUnit) => void;
}

/** The nudge amount spinner — how much time the flanking Start/End nudge
 * arrows apply per click. Fully controlled: the amount and unit live in the
 * parent so both the arrows and this spinner read the same current step. */
export function NudgeAmountControl({
  value,
  unit,
  framesAvailable,
  onValueChange,
  onUnitChange,
}: NudgeAmountControlProps) {
  const nudgeValueBoxRef = useRef<HTMLDivElement>(null);
  const availableUnits = availableNudgeUnits(framesAvailable);
  const unitIndex = availableUnits.indexOf(unit);
  const isCoarsestUnit = unitIndex <= 0;
  const isFinestUnit = unitIndex === availableUnits.length - 1;

  useEffect(() => {
    const box = nudgeValueBoxRef.current;
    if (!box) return;
    const wheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      onValueChange(clampAdjustmentValue(value + (event.deltaY < 0 ? 1 : -1)));
    };
    box.addEventListener("wheel", wheel, { passive: false });
    return () => box.removeEventListener("wheel", wheel);
  }, [value, onValueChange]);

  return (
    <Stack spacing={1} alignItems="center">
      <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary", letterSpacing: "0.04em" }}>
        NUDGE
      </Typography>
      <Stack direction="row" alignItems="center" sx={pillSx}>
        <Tooltip title="Coarser unit">
          <span>
            <IconButton
              aria-label="Switch to coarser nudge unit"
              disabled={isCoarsestUnit}
              onClick={() => onUnitChange(stepNudgeUnit(unit, -1, framesAvailable))}
              sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
            >
              <SkipPreviousRounded fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <IconButton
          aria-label="Decrease nudge amount"
          onClick={() => onValueChange(clampAdjustmentValue(value - 1))}
          sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
        >
          <ChevronLeftRounded fontSize="small" />
        </IconButton>
        <Tooltip title={unit === "frames" ? "Nominal frame durations to nudge" : "Unit count to nudge"}>
          <Box
            ref={nudgeValueBoxRef}
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: NUDGE_UNIT_PREFIX[unit] ? 0 : "5px",
              minWidth: 108,
              alignSelf: "stretch",
              px: 1.5,
              cursor: "default",
              userSelect: "none",
            }}
          >
            {NUDGE_UNIT_PREFIX[unit] && (
              <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600 }}>
                {NUDGE_UNIT_PREFIX[unit]}
              </Typography>
            )}
            <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600 }}>
              {value}
            </Typography>
            <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600, whiteSpace: "nowrap" }}>
              {nudgeUnitSuffix(unit, value)}
            </Typography>
          </Box>
        </Tooltip>
        <IconButton
          aria-label="Increase nudge amount"
          onClick={() => onValueChange(clampAdjustmentValue(value + 1))}
          sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
        >
          <ChevronRightRounded fontSize="small" />
        </IconButton>
        <Tooltip title="Finer unit">
          <span>
            <IconButton
              aria-label="Switch to finer nudge unit"
              disabled={isFinestUnit}
              onClick={() => onUnitChange(stepNudgeUnit(unit, 1, framesAvailable))}
              sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
            >
              <SkipNextRounded fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>
    </Stack>
  );
}
