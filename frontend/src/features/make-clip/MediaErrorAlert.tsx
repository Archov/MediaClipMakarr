import { Alert, Button, Chip, Stack, Typography } from "@mui/material";

import { ApiRequestError } from "../../api";
import type { StructuredError } from "../../types";

type Alternative = Record<string, unknown>;

function alternativeLabel(alternative: Alternative): string {
  const language = typeof alternative.language === "string" ? alternative.language.toUpperCase() : null;
  const title = typeof alternative.title === "string" ? alternative.title : null;
  const codec = typeof alternative.codec_name === "string" ? alternative.codec_name : null;
  const stream = typeof alternative.stream_index === "number" ? `#${alternative.stream_index}` : null;
  return [language, title, codec, stream].filter(Boolean).join(" · ") || "Alternate track";
}

export function structuredErrorFrom(error: Error | null): StructuredError | null {
  return error instanceof ApiRequestError ? error.detail : null;
}

export function MediaErrorAlert({
  error,
  fallbackMessage,
  onSelectAlternative,
}: {
  error: StructuredError | null;
  fallbackMessage?: string;
  onSelectAlternative?: (alternative: Alternative) => void;
}) {
  if (!error && !fallbackMessage) return null;
  const contextEntries = Object.entries(error?.context ?? {});
  return <Alert severity="error">
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
        <Typography>{error?.message ?? fallbackMessage}</Typography>
        {error?.code && <Chip size="small" label={error.code} variant="outlined" />}
      </Stack>
      {contextEntries.length > 0 && (
        <Typography variant="caption" component="div" sx={{ fontFamily: "monospace" }}>
          {contextEntries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}
        </Typography>
      )}
      {(error?.alternatives ?? []).length > 0 && (
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
          {(error?.alternatives ?? []).map((alternative, index) => (
            <Button
              key={`${String(alternative.stream_index)}-${index}`}
              size="small"
              variant="outlined"
              onClick={() => onSelectAlternative?.(alternative)}
              disabled={!onSelectAlternative}
            >
              Use {alternativeLabel(alternative)}
            </Button>
          ))}
        </Stack>
      )}
    </Stack>
  </Alert>;
}
