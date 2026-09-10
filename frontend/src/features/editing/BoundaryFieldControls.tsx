import ArrowBackIosRounded from "@mui/icons-material/ArrowBackIosRounded";
import ArrowForwardIosRounded from "@mui/icons-material/ArrowForwardIosRounded";
import { IconButton, TextField, Tooltip } from "@mui/material";
import type { ReactNode } from "react";

// Matches the theme-matched accent blue used elsewhere in the app (see
// IMMICH_ICON_BLUE in LibraryScreen.tsx).
export const ACCENT_BLUE = "#61a6fa";

export interface TimestampFieldProps {
  id: string;
  label: string;
  value: string;
  error: string | null;
  onChange: (value: string) => void;
  onFocus: () => void;
  onBlur: () => void;
  startAdornment?: ReactNode;
  endAdornment?: ReactNode;
}

export function TimestampField({
  id,
  label,
  value,
  error,
  onChange,
  onFocus,
  onBlur,
  startAdornment,
  endAdornment,
}: TimestampFieldProps) {
  return (
    <TextField
      id={id}
      label={label}
      value={value}
      error={Boolean(error)}
      helperText={error}
      onChange={(event) => onChange(event.target.value)}
      onFocus={onFocus}
      onBlur={onBlur}
      size="small"
      slotProps={{
        input: { startAdornment, endAdornment },
        htmlInput: {
          style: { width: "12ch", textAlign: "center", fontVariantNumeric: "tabular-nums" },
        },
      }}
      sx={{
        width: "fit-content",
        // Centers the label over the input instead of MUI's default left-aligned
        // notch, by centering the real notch (the fieldset's `legend`, which
        // natively cuts the border gap) rather than hiding it behind a faked
        // background patch — the latter can't match this theme's dark-mode Paper
        // elevation overlay, which layers a translucent gradient over the base
        // background color rather than being one flat color.
        "& .MuiInputLabel-root": {
          right: 0,
          textAlign: "center",
        },
        "& .MuiInputLabel-shrink": {
          left: 0,
          right: 0,
          width: "fit-content",
          margin: "0 auto",
          whiteSpace: "nowrap",
          textAlign: "center",
          // MUI's default shrink transform is translate(14px, -9px) scale(0.75).
          // The -9px vertical offset is what correctly bisects the border line, so
          // it's kept as-is; the 14px horizontal offset is dropped since it fights
          // the left/right/margin centering above. transformOrigin must also move
          // to the element's center — MUI's default is the top-left corner, which
          // scales the box toward that corner instead of shrinking it in place,
          // silently un-centering it.
          transform: "translate(0, -9px) scale(0.75)",
          transformOrigin: "center",
        },
        "& .MuiOutlinedInput-root legend": {
          float: "none",
          margin: "0 auto",
          // MUI sets the notch width via an inline style, which needs !important
          // to override.
          width: "fit-content !important",
        },
      }}
    />
  );
}

export interface FrameNudgeButtonProps {
  boundary: "Start" | "End";
  direction: "backward" | "forward";
  disabled: boolean;
  tooltip?: string;
  ariaLabel?: string;
  onClick: () => void;
  onArrowNudge: (direction: -1 | 1) => void;
}

export function FrameNudgeButton({
  boundary,
  direction,
  disabled,
  tooltip,
  ariaLabel,
  onClick,
  onArrowNudge,
}: FrameNudgeButtonProps) {
  const backward = direction === "backward";
  return (
    <Tooltip title={tooltip ?? `Move ${boundary} ${direction}`}>
      <span>
        <IconButton
          aria-label={ariaLabel ?? `Move ${boundary} ${direction}`}
          disabled={disabled}
          onClick={onClick}
          onKeyDown={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            onArrowNudge(event.key === "ArrowLeft" ? -1 : 1);
          }}
          size="small"
          sx={{
            p: 0,
            color: disabled ? "text.disabled" : ACCENT_BLUE,
          }}
        >
          {backward ? (
            <ArrowBackIosRounded style={{ fontSize: 32 }} />
          ) : (
            <ArrowForwardIosRounded style={{ fontSize: 32 }} />
          )}
        </IconButton>
      </span>
    </Tooltip>
  );
}
