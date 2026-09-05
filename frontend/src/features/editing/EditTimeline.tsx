import { Box, Typography } from "@mui/material";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  canShiftTimelineBoundary,
  clampTimelineSelection,
  createTimelineTicks,
  effectiveEditableRange,
  formatTimelineRulerTime,
  pointerPositionToTime,
  shiftTimelineBoundary,
  timeToTimelinePercent,
  visibleTimelineIntersection,
  type TimelineRange,
} from "./timelineMath";

export interface EditTimelineProps {
  viewportRange: TimelineRange;
  editableRange?: TimelineRange;
  selectionRange: TimelineRange;
  referenceRange?: TimelineRange;
  playheadMs: number;
  stepMs?: number;
  activeBoundary?: "start" | "end" | null;
  formatRulerTime?: (valueMs: number) => string;
  onSelectionChange: (range: TimelineRange, activeBoundary: "start" | "end") => void;
  onPlayheadChange: (valueMs: number) => void;
  onActiveBoundaryChange?: (boundary: "start" | "end" | null) => void;
  onInteractionStart?: () => void;
}

interface BoundaryHandleProps {
  boundary: "start" | "end";
  viewportRange: TimelineRange;
  editableRange: TimelineRange;
  selectionRange: TimelineRange;
  stepMs?: number;
  active: boolean;
  formatTime: (valueMs: number) => string;
  trackRef: React.RefObject<HTMLDivElement | null>;
  onSelectionChange: EditTimelineProps["onSelectionChange"];
  onPlayheadChange: EditTimelineProps["onPlayheadChange"];
  onActivate: () => void;
  onInteractionStart?: EditTimelineProps["onInteractionStart"];
}

function BoundaryHandle({
  boundary,
  viewportRange,
  editableRange,
  selectionRange,
  stepMs,
  active,
  formatTime,
  trackRef,
  onSelectionChange,
  onPlayheadChange,
  onActivate,
  onInteractionStart,
}: BoundaryHandleProps) {
  const dragOffsetRef = useRef(0);
  const valueMs = boundary === "start" ? selectionRange.startMs : selectionRange.endMs;
  const isVisible = valueMs >= viewportRange.startMs && valueMs <= viewportRange.endMs;
  if (!isVisible) return null;

  const updateFromPointer = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return;
    const bounds = track.getBoundingClientRect();
    const pointerMs = pointerPositionToTime(clientX, bounds.left, bounds.width, viewportRange);
    const next = clampTimelineSelection(
      boundary === "start"
        ? { ...selectionRange, startMs: pointerMs }
        : { ...selectionRange, endMs: pointerMs },
      editableRange,
      boundary,
    );
    onSelectionChange(next, boundary);
    onPlayheadChange(boundary === "start" ? next.startMs : next.endMs);
  };

  return (
    <Box
      component="button"
      type="button"
      role="slider"
      aria-label={`${boundary === "start" ? "Start" : "End"} boundary${active ? ", selected" : ""}`}
      aria-valuemin={boundary === "start" ? editableRange.startMs : selectionRange.startMs + 1}
      aria-valuemax={boundary === "start" ? selectionRange.endMs - 1 : editableRange.endMs}
      aria-valuenow={valueMs}
      aria-valuetext={formatTime(valueMs)}
      onFocus={onActivate}
      onPointerDown={(event) => {
        event.stopPropagation();
        const handleBounds = event.currentTarget.getBoundingClientRect();
        dragOffsetRef.current = event.clientX - (handleBounds.left + handleBounds.width / 2);
        event.currentTarget.setPointerCapture(event.pointerId);
        onActivate();
        onInteractionStart?.();
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          updateFromPointer(event.clientX - dragOffsetRef.current);
        }
      }}
      onPointerUp={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onKeyDown={(event) => {
        if (!stepMs || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
        event.preventDefault();
        const deltaMs = event.key === "ArrowLeft" ? -stepMs : stepMs;
        if (!canShiftTimelineBoundary(selectionRange, editableRange, boundary, deltaMs)) return;
        onInteractionStart?.();
        const next = shiftTimelineBoundary(selectionRange, editableRange, boundary, deltaMs);
        onSelectionChange(next, boundary);
        onPlayheadChange(boundary === "start" ? next.startMs : next.endMs);
      }}
      sx={{
        appearance: "none",
        position: "absolute",
        zIndex: 5,
        left: `${timeToTimelinePercent(valueMs, viewportRange)}%`,
        top: 0,
        bottom: 0,
        width: 44,
        height: "auto",
        p: 0,
        transform: "translateX(-50%)",
        border: 0,
        bgcolor: "transparent",
        color: "common.white",
        cursor: "ew-resize",
        touchAction: "none",
        "&::before": {
          content: '""',
          position: "absolute",
          left: "50%",
          top: 2,
          bottom: 0,
          width: 4,
          transform: "translateX(-50%)",
          borderRadius: 1,
          bgcolor: "primary.light",
          boxShadow: active
            ? "0 0 0 1px #fff, 0 0 0 2px rgba(0,0,0,.75)"
            : "0 0 0 1px rgba(0,0,0,.7)",
        },
        "&:focus-visible": {
          outline: "none",
        },
      }}
    >
      <Box
        sx={{
          position: "absolute",
          bottom: 0,
          left: "50%",
          width: 14,
          height: 14,
          transform: "translate(-50%, 50%) rotate(45deg)",
          bgcolor: "primary.light",
          borderRadius: "2px 0 2px 0",
          boxShadow: active
            ? "0 0 0 1px #fff, 0 0 0 2px rgba(0,0,0,.75)"
            : "0 0 0 1px rgba(0,0,0,.7)",
        }}
      />
    </Box>
  );
}

export function EditTimeline({
  viewportRange,
  editableRange: suppliedEditableRange,
  selectionRange,
  referenceRange,
  playheadMs,
  stepMs,
  activeBoundary,
  formatRulerTime = formatTimelineRulerTime,
  onSelectionChange,
  onPlayheadChange,
  onActiveBoundaryChange,
  onInteractionStart,
}: EditTimelineProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [trackWidth, setTrackWidth] = useState(800);
  const editableRange = effectiveEditableRange(viewportRange, suppliedEditableRange);
  const visibleSelection = visibleTimelineIntersection(selectionRange, viewportRange);
  const visibleReference = visibleTimelineIntersection(referenceRange, viewportRange);
  const showReference = Boolean(
    visibleReference
    && referenceRange
    && (referenceRange.startMs !== viewportRange.startMs || referenceRange.endMs !== viewportRange.endMs),
  );
  const ticks = useMemo(
    () => createTimelineTicks(viewportRange, trackWidth),
    [trackWidth, viewportRange],
  );
  const playheadVisible = playheadMs >= viewportRange.startMs && playheadMs <= viewportRange.endMs;

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const updateWidth = () => setTrackWidth(track.getBoundingClientRect().width);
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(track);
    return () => observer.disconnect();
  }, []);

  const updatePlayheadFromPointer = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return;
    const bounds = track.getBoundingClientRect();
    onPlayheadChange(pointerPositionToTime(clientX, bounds.left, bounds.width, viewportRange));
  };

  return (
    <Box
      aria-label="Edit timeline"
      sx={{ minWidth: 0, userSelect: "none", WebkitUserSelect: "none" }}
    >
      <Box
        sx={{
          position: "relative",
          height: 22,
          mx: 0.5,
          borderBottom: 1,
          borderColor: "divider",
          overflow: "hidden",
        }}
      >
        {ticks.map((tick) => {
          const left = timeToTimelinePercent(tick.valueMs, viewportRange);
          return (
            <Box key={`${tick.valueMs}-${tick.major}`} sx={{ position: "absolute", left: `${left}%`, bottom: 0 }}>
              {tick.major && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    position: "absolute",
                    bottom: 7,
                    transform: "translateX(-50%)",
                    whiteSpace: "nowrap",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: "0.68rem",
                  }}
                >
                  {formatRulerTime(tick.valueMs)}
                </Typography>
              )}
              <Box sx={{ width: 1, height: tick.major ? 7 : 4, bgcolor: tick.major ? "text.secondary" : "divider" }} />
            </Box>
          );
        })}
      </Box>

      <Box
        ref={trackRef}
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          onActiveBoundaryChange?.(null);
          onInteractionStart?.();
          updatePlayheadFromPointer(event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) updatePlayheadFromPointer(event.clientX);
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        sx={{
          position: "relative",
          height: 14,
          bgcolor: "#090d14",
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          overflow: "visible",
          cursor: "crosshair",
          touchAction: "none",
          userSelect: "none",
          WebkitUserSelect: "none",
          backgroundImage: "linear-gradient(to right, rgba(255,255,255,.045) 1px, transparent 1px)",
          backgroundSize: "12.5% 100%",
        }}
      >
        {showReference && visibleReference && (
          <Box
            aria-label="Existing clip range"
            sx={{
              position: "absolute",
              left: `${timeToTimelinePercent(visibleReference.startMs, viewportRange)}%`,
              width: `${timeToTimelinePercent(visibleReference.endMs, viewportRange) - timeToTimelinePercent(visibleReference.startMs, viewportRange)}%`,
              top: 1,
              height: 2,
              bgcolor: "rgba(255,255,255,.2)",
              borderRadius: 1,
              pointerEvents: "none",
            }}
          />
        )}

        <Box sx={{ position: "absolute", inset: "2px 0 2px", bgcolor: "rgba(0,0,0,.38)" }} />
        {visibleSelection && (
          <Box
            aria-label="Selected clip range"
            sx={{
              position: "absolute",
              left: `${timeToTimelinePercent(visibleSelection.startMs, viewportRange)}%`,
              width: `${timeToTimelinePercent(visibleSelection.endMs, viewportRange) - timeToTimelinePercent(visibleSelection.startMs, viewportRange)}%`,
              top: 2,
              bottom: 2,
              bgcolor: "rgba(96,165,250,.34)",
              borderTop: 1,
              borderBottom: 1,
              borderColor: "primary.light",
              overflow: "hidden",
              pointerEvents: "none",
            }}
          />
        )}

        <BoundaryHandle
          boundary="start"
          viewportRange={viewportRange}
          editableRange={editableRange}
          selectionRange={selectionRange}
          stepMs={stepMs}
          active={activeBoundary === "start"}
          formatTime={formatRulerTime}
          trackRef={trackRef}
          onSelectionChange={onSelectionChange}
          onPlayheadChange={onPlayheadChange}
          onActivate={() => onActiveBoundaryChange?.("start")}
          onInteractionStart={onInteractionStart}
        />
        <BoundaryHandle
          boundary="end"
          viewportRange={viewportRange}
          editableRange={editableRange}
          selectionRange={selectionRange}
          stepMs={stepMs}
          active={activeBoundary === "end"}
          formatTime={formatRulerTime}
          trackRef={trackRef}
          onSelectionChange={onSelectionChange}
          onPlayheadChange={onPlayheadChange}
          onActivate={() => onActiveBoundaryChange?.("end")}
          onInteractionStart={onInteractionStart}
        />

        {playheadVisible && (
          <Box
            component="button"
            type="button"
            aria-label="Playhead"
            onPointerDown={(event) => {
              event.stopPropagation();
              event.currentTarget.setPointerCapture(event.pointerId);
              onActiveBoundaryChange?.(null);
              onInteractionStart?.();
              updatePlayheadFromPointer(event.clientX);
            }}
            onPointerMove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) updatePlayheadFromPointer(event.clientX);
            }}
            onPointerUp={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
            }}
            onPointerCancel={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
            }}
            sx={{
              appearance: "none",
              position: "absolute",
              zIndex: 6,
              left: `${timeToTimelinePercent(playheadMs, viewportRange)}%`,
              top: -6,
              bottom: 0,
              width: 20,
              p: 0,
              transform: "translateX(-50%)",
              border: 0,
              bgcolor: "transparent",
              cursor: "ew-resize",
              touchAction: "none",
              "&::before": {
                content: '""',
                position: "absolute",
                left: "50%",
                top: 0,
                bottom: 0,
                width: 2,
                transform: "translateX(-50%)",
                bgcolor: "warning.main",
                boxShadow: "0 0 0 1px rgba(0,0,0,.45)",
              },
              "&::after": {
                content: '""',
                position: "absolute",
                top: 0,
                left: "50%",
                width: 12,
                height: 12,
                transform: "translate(-50%, -50%) rotate(45deg)",
                bgcolor: "warning.main",
                borderRadius: "2px 0 2px 0",
                boxShadow: "0 0 0 1px rgba(0,0,0,.45)",
              },
              "&:focus-visible": {
                outline: "none",
              },
            }}
          />
        )}
      </Box>
    </Box>
  );
}
