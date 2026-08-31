import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import { Alert, Box, Button, Chip, LinearProgress, Stack } from "@mui/material";

import { formatTimestampMs } from "../../timestamps";
import type { JobSnapshot, JobState } from "../../types";

function severity(state: JobState): "success" | "info" | "warning" | "error" {
  if (state === "SUCCEEDED") return "success";
  if (state === "PARTIAL") return "warning";
  if (state === "FAILED") return "error";
  return "info";
}

export function JobStatus({ job }: { job: JobSnapshot | null }) {
  if (!job) return null;
  return <Stack spacing={2}>
    <Alert severity={severity(job.state)}>{job.message}{job.queue_position ? ` Queue position ${job.queue_position}.` : ""}{job.error ? ` ${job.error.message}` : ""}</Alert>
    {job.state !== "SUCCEEDED" && job.state !== "FAILED" && <LinearProgress variant="determinate" value={Math.round(job.progress * 100)} aria-label="Clip render progress" />}
    {job.state === "SUCCEEDED" && job.result && <Stack spacing={2}>
      <Box component="video" src={job.result.play_url} controls sx={{ width: "100%", borderRadius: 1, bgcolor: "black" }} />
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Chip label={job.result.title} color="success" variant="outlined" />
        <Chip label={formatTimestampMs(job.result.duration_ms) || "--:--"} variant="outlined" />
        <Button href={job.result.download_url} variant="outlined" startIcon={<ArrowDownwardRounded />}>Download</Button>
      </Stack>
    </Stack>}
  </Stack>;
}

