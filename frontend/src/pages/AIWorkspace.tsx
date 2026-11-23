import React, { useEffect, useState } from "react";
import {
  Box,
  Typography,
  Paper,
  TextField,
  Button,
  Chip,
  Alert,
  CircularProgress,
  Divider,
  Grid,
} from "@mui/material";
import {
  Send as SendIcon,
  Refresh as RefreshIcon,
  CheckCircle as OkIcon,
  Error as ErrorIcon,
  HourglassEmpty as PendingIcon,
} from "@mui/icons-material";
import { useSearchParams } from "react-router-dom";
import { submitTask, getTaskStatus, getTaskResult, executeTask } from "../api/client";
import { TaskInfo } from "../api/types";

export const AIWorkspace: React.FC = () => {
  const [searchParams] = useSearchParams();
  const initialId = searchParams.get("id");

  const [description, setDescription] = useState("");
  const [submissionId, setSubmissionId] = useState<string | null>(initialId);
  const [taskStatus, setTaskStatus] = useState<TaskInfo | null>(null);
  const [taskResult, setTaskResult] = useState<any | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [executing, setExecuting] = useState(false);

  const handleExecute = async (id: string) => {
    setExecuting(true);
    setError(null);
    try {
      const updated = await executeTask(id);
      setTaskStatus(updated);
      await refreshTask(id);
    } catch (err: any) {
      setError(err?.message || "Execution trigger failed");
    } finally {
      setExecuting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!description.trim()) return;

    setSubmitting(true);
    setError(null);
    setTaskResult(null);

    try {
      const info = await submitTask(description.trim(), {
        source: "web_ui",
        submitted_at: new Date().toISOString(),
      });
      setSubmissionId(info.submission_id);
      setTaskStatus(info);
      setDescription("");
    } catch (err: any) {
      setError(err?.message || "Task submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  const refreshTask = async (id: string) => {
    setPolling(true);
    try {
      const status = await getTaskStatus(id);
      setTaskStatus(status);

      const statusUpper = (status.status || "").toUpperCase();
      if (
        statusUpper === "COMPLETED" ||
        statusUpper === "FAILED" ||
        statusUpper === "PARTIALLY_COMPLETED"
      ) {
        try {
          const res = await getTaskResult(id);
          setTaskResult(res);
        } catch {
          // Result might not be ready yet
        }
      }
    } catch (err: any) {
      setError(err?.message || "Failed to fetch task status");
    } finally {
      setPolling(false);
    }
  };

  useEffect(() => {
    if (initialId) {
      setSubmissionId(initialId);
      refreshTask(initialId);
    }
  }, [initialId]);

  useEffect(() => {
    if (!submissionId) return;

    let active = true;
    const interval = setInterval(() => {
      if (active) {
        const s = (taskStatus?.status || "").toUpperCase();
        if (s !== "COMPLETED" && s !== "FAILED") {
          refreshTask(submissionId);
        }
      }
    }, 4000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [submissionId, taskStatus?.status]);

  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" fontWeight={700}>
          AI Workspace
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Submit and orchestrate natural-language engineering tasks through Local AI OS
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* Task submission form */}
        <Grid item xs={12} md={7}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 1.5 }}>
              Task Submission
            </Typography>
            <form onSubmit={handleSubmit}>
              <TextField
                fullWidth
                multiline
                rows={4}
                placeholder="Enter engineering task description (e.g., 'Verify PID controller stability margin in MATLAB', 'disp(2+2)')"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={submitting}
                sx={{ mb: 2 }}
              />
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <Typography variant="caption" color="text.secondary">
                  Submissions execute via LocalAIOSControlPlane & TaskRunner
                </Typography>
                <Button
                  type="submit"
                  variant="contained"
                  startIcon={submitting ? <CircularProgress size={16} /> : <SendIcon />}
                  disabled={submitting || !description.trim()}
                >
                  {submitting ? "Submitting..." : "Submit Task"}
                </Button>
              </Box>
            </form>
          </Paper>

          {/* Active task details */}
          {submissionId && (
            <Paper sx={{ p: 3 }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
                <Typography variant="h6">Task Inspector</Typography>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<RefreshIcon />}
                  onClick={() => refreshTask(submissionId)}
                  disabled={polling}
                >
                  {polling ? "Refreshing..." : "Refresh"}
                </Button>
              </Box>

              <Box sx={{ mb: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  SUBMISSION ID
                </Typography>
                <Typography variant="body1" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
                  {submissionId}
                </Typography>
              </Box>

              <Box sx={{ mb: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  STATUS
                </Typography>
                <Box sx={{ mt: 0.5 }}>
                  <Chip
                    label={taskStatus?.status || "PENDING"}
                    color={
                      (taskStatus?.status || "").toUpperCase() === "COMPLETED"
                        ? "success"
                        : (taskStatus?.status || "").toUpperCase() === "FAILED"
                        ? "error"
                        : "warning"
                    }
                    icon={
                      (taskStatus?.status || "").toUpperCase() === "COMPLETED" ? (
                        <OkIcon />
                      ) : (taskStatus?.status || "").toUpperCase() === "FAILED" ? (
                        <ErrorIcon />
                      ) : (
                        <PendingIcon />
                      )
                    }
                  />
                </Box>
              </Box>

              {taskStatus?.status === "WAITING_FOR_APPROVAL" && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" fontWeight={700}>
                    Awaiting Out-of-Band Operator Approval
                  </Typography>
                  <Typography variant="body2" sx={{ mt: 0.5 }}>
                    This execution requires explicit human authorization. Direct web approval is disabled.
                    Run the following command in your terminal:
                  </Typography>
                  <Box
                    component="pre"
                    sx={{
                      bgcolor: "#0d1117",
                      p: 1.5,
                      borderRadius: 1,
                      fontFamily: "monospace",
                      fontSize: "0.85rem",
                      my: 1,
                      border: "1px solid #30363d",
                      userSelect: "all",
                    }}
                  >
                    {taskStatus.metadata?.cli_command ||
                      `python -m matlab_os.integration.approval_cli approve ${taskStatus.metadata?.approval_token_id} --as "Operator"`}
                  </Box>
                  <Typography variant="caption" color="text.secondary">
                    Token: {taskStatus.metadata?.approval_token_id} | Digest:{" "}
                    {taskStatus.metadata?.payload_sha256?.substring(0, 16)}...
                  </Typography>

                  {taskStatus.metadata?.approval_status === "approved" && (
                    <Box sx={{ mt: 2 }}>
                      <Alert severity="success" sx={{ mb: 1.5 }}>
                        Ticket approved via CLI. Click below to initiate execution.
                      </Alert>
                      <Button
                        variant="contained"
                        color="success"
                        startIcon={executing ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
                        disabled={executing}
                        onClick={() => submissionId && handleExecute(submissionId)}
                      >
                        {executing ? "Starting Execution..." : "Execute Approved Task"}
                      </Button>
                    </Box>
                  )}
                </Alert>
              )}

              {(taskResult || taskStatus?.result) && (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="caption" color="text.secondary">
                    EXECUTION RESULT (MATLAB OUTPUT)
                  </Typography>
                  <Paper
                    sx={{
                      p: 2,
                      mt: 1,
                      bgcolor: "#0d1117",
                      fontFamily: "monospace",
                      fontSize: "1rem",
                      fontWeight: 600,
                      color: "#58a6ff",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                      border: "1px solid #30363d",
                    }}
                  >
                    {taskStatus?.result !== undefined && taskStatus?.result !== null
                      ? String(taskStatus.result)
                      : typeof taskResult === "string"
                      ? taskResult
                      : taskResult?.result !== undefined
                      ? String(taskResult.result)
                      : JSON.stringify(taskResult, null, 2)}
                  </Paper>
                </Box>
              )}

              {taskStatus?.history && taskStatus.history.length > 0 && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="caption" color="text.secondary">
                    EXECUTION HISTORY
                  </Typography>
                  <Box sx={{ mt: 1, pl: 1, borderLeft: "2px solid #30363d" }}>
                    {taskStatus.history.map((step, idx) => (
                      <Typography
                        key={idx}
                        variant="body2"
                        sx={{ fontFamily: "monospace", fontSize: "0.85rem", py: 0.5 }}
                      >
                        {step}
                      </Typography>
                    ))}
                  </Box>
                </Box>
              )}
            </Paper>
          )}
        </Grid>

        {/* Guidance and capability notes */}
        <Grid item xs={12} md={5}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 1.5 }}>
              Execution Lifecycle
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Tasks submitted through the AI Workspace follow the strict Local AI OS V1 architecture:
            </Typography>
            <Box component="ol" sx={{ pl: 2.5, m: 0, fontSize: "0.85rem", color: "text.secondary" }}>
              <li>Task request is submitted to FastAPI <code>POST /api/v1/tasks</code>.</li>
              <li>Handled by <code>TaskRunner</code> &amp; registered in persistent Control Plane.</li>
              <li>Operations requiring approval enter <code>WAITING_FOR_APPROVAL</code> state.</li>
              <li>Execution runs through <code>MATLABAgent</code> &amp; MATLAB MCP client.</li>
              <li>Output and structured results are persisted and returned.</li>
            </Box>
          </Paper>

          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" sx={{ mb: 1.5 }}>
              Sample Quick Tasks
            </Typography>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
              {[
                "Calculate aerodynamic drag matrix",
                "Evaluate transfer function step response: G(s) = 1/(s^2 + 2s + 1)",
                "disp(2+2)",
                "Analyze power system harmonics on 3-phase bus",
              ].map((sample, idx) => (
                <Button
                  key={idx}
                  size="small"
                  variant="outlined"
                  sx={{ justifyContent: "flex-start", textAlign: "left", textTransform: "none" }}
                  onClick={() => setDescription(sample)}
                >
                  {sample}
                </Button>
              ))}
            </Box>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};
