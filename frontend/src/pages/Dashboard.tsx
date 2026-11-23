import React, { useEffect, useState } from "react";
import {
  Box,
  Typography,
  Grid,
  Paper,
  Chip,
  Button,
  CircularProgress,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import {
  Add as AddIcon,
  Refresh as RefreshIcon,
  Engineering as EngineeringIcon,
  CheckCircle as OkIcon,
  Error as ErrorIcon,
} from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getHealth, listTasks, getSkills } from "../api/client";
import { TaskInfo, HealthCheck, SkillsResponse } from "../api/types";

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [health, setHealth] = useState<HealthCheck | null>(null);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [skillsData, setSkillsData] = useState<SkillsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, t, s] = await Promise.all([
        getHealth().catch(() => ({ status: "unreachable" })),
        listTasks().catch(() => []),
        getSkills().catch(() => null),
      ]);
      setHealth(h);
      setTasks(Array.isArray(t) ? t : []);
      setSkillsData(s);
    } catch (err: any) {
      setError(err?.message || "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const totalTasks = tasks.length;
  const pendingTasks = tasks.filter(
    (t) =>
      t.status === "pending" ||
      t.status === "SUBMITTED" ||
      t.status === "PLANNING" ||
      t.status === "RUNNING" ||
      t.status === "WAITING_FOR_APPROVAL"
  ).length;
  const completedTasks = tasks.filter(
    (t) => t.status === "completed" || t.status === "COMPLETED"
  ).length;
  const failedTasks = tasks.filter(
    (t) => t.status === "failed" || t.status === "FAILED"
  ).length;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 3 }}>
        <div>
          <Typography variant="h5" fontWeight={700}>
            System Dashboard
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Local AI OS V1 status and operational overview
          </Typography>
        </div>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshIcon />}
            onClick={loadData}
            disabled={loading}
          >
            Refresh
          </Button>
          <Button
            variant="contained"
            size="small"
            startIcon={<AddIcon />}
            onClick={() => navigate("/workspace")}
          >
            New Task
          </Button>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
          <CircularProgress size={36} />
        </Box>
      ) : (
        <>
          <Grid container spacing={2} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={6} md={3}>
              <Paper sx={{ p: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  BACKEND HEALTH
                </Typography>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 1 }}>
                  {health?.status === "ok" ? (
                    <OkIcon color="success" />
                  ) : (
                    <ErrorIcon color="error" />
                  )}
                  <Typography variant="h6" fontWeight={700}>
                    {health?.status === "ok" ? "Operational" : "Unavailable"}
                  </Typography>
                </Box>
                <Typography variant="caption" color="text.secondary">
                  API status: {health?.status || "offline"}
                </Typography>
              </Paper>
            </Grid>

            <Grid item xs={12} sm={6} md={3}>
              <Paper sx={{ p: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  TOTAL TASKS
                </Typography>
                <Typography variant="h4" fontWeight={700} sx={{ mt: 0.5 }}>
                  {totalTasks}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Recorded in TaskRunner
                </Typography>
              </Paper>
            </Grid>

            <Grid item xs={12} sm={6} md={3}>
              <Paper sx={{ p: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  ACTIVE / PENDING
                </Typography>
                <Typography variant="h4" fontWeight={700} color="warning.main" sx={{ mt: 0.5 }}>
                  {pendingTasks}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {completedTasks} completed, {failedTasks} failed
                </Typography>
              </Paper>
            </Grid>

            <Grid item xs={12} sm={6} md={3}>
              <Paper sx={{ p: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  REGISTERED SKILLS
                </Typography>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 0.5 }}>
                  <EngineeringIcon color="primary" />
                  <Typography variant="h4" fontWeight={700}>
                    {skillsData?.skills ? skillsData.skills.length : 0}
                  </Typography>
                </Box>
                <Typography variant="caption" color="text.secondary">
                  {skillsData?.agent || "MATLAB Expert Agent"}
                </Typography>
              </Paper>
            </Grid>
          </Grid>

          {/* Recent tasks list */}
          <Paper sx={{ p: 2, mb: 3 }}>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
              <Typography variant="h6">Recent Tasks</Typography>
              <Button size="small" onClick={() => navigate("/tasks")}>
                View All Tasks
              </Button>
            </Box>

            {tasks.length === 0 ? (
              <Box sx={{ py: 4, textAlign: "center" }}>
                <Typography color="text.secondary">No tasks submitted yet.</Typography>
                <Button
                  variant="outlined"
                  size="small"
                  sx={{ mt: 2 }}
                  startIcon={<AddIcon />}
                  onClick={() => navigate("/workspace")}
                >
                  Submit First Task
                </Button>
              </Box>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Task ID</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>History / Details</TableCell>
                      <TableCell align="right">Action</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {tasks.slice(0, 5).map((t) => (
                      <TableRow key={t.submission_id}>
                        <TableCell sx={{ fontFamily: "monospace", fontSize: "0.85rem" }}>
                          {t.submission_id}
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={t.status}
                            size="small"
                            color={
                              t.status === "completed" || t.status === "COMPLETED"
                                ? "success"
                                : t.status === "failed" || t.status === "FAILED"
                                ? "error"
                                : "warning"
                            }
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 350, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {t.history && t.history.length > 0
                            ? t.history[t.history.length - 1]
                            : "No history recorded"}
                        </TableCell>
                        <TableCell align="right">
                          <Button
                            size="small"
                            onClick={() => navigate(`/workspace?id=${t.submission_id}`)}
                          >
                            Inspect
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Paper>
        </>
      )}
    </Box>
  );
};
