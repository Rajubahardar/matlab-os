import React, { useEffect, useState } from "react";
import {
  Box,
  Typography,
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
  ToggleButtonGroup,
  ToggleButton,
} from "@mui/material";
import {
  Refresh as RefreshIcon,
  Add as AddIcon,
  Search as SearchIcon,
} from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { listTasks } from "../api/client";
import { TaskInfo } from "../api/types";

export const Tasks: React.FC = () => {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  const loadTasks = async () => {
    setLoading(true);
    setError(null);
    try {
      const filter = statusFilter === "ALL" ? undefined : statusFilter;
      const data = await listTasks(filter);
      setTasks(Array.isArray(data) ? data : []);
    } catch (err: any) {
      setError(err?.message || "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTasks();
  }, [statusFilter]);

  const handleFilterChange = (
    _event: React.MouseEvent<HTMLElement>,
    newFilter: string | null
  ) => {
    if (newFilter !== null) {
      setStatusFilter(newFilter);
    }
  };

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 3 }}>
        <div>
          <Typography variant="h5" fontWeight={700}>
            Task Management
          </Typography>
          <Typography variant="body2" color="text.secondary">
            View, filter, and inspect persisted engineering tasks
          </Typography>
        </div>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshIcon />}
            onClick={loadTasks}
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

      {/* Filter bar */}
      <Paper sx={{ p: 1.5, mb: 3, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <ToggleButtonGroup
          value={statusFilter}
          exclusive
          onChange={handleFilterChange}
          size="small"
        >
          <ToggleButton value="ALL">All ({tasks.length})</ToggleButton>
          <ToggleButton value="SUBMITTED">Submitted</ToggleButton>
          <ToggleButton value="RUNNING">Running</ToggleButton>
          <ToggleButton value="COMPLETED">Completed</ToggleButton>
          <ToggleButton value="FAILED">Failed</ToggleButton>
        </ToggleButtonGroup>

        <Typography variant="caption" color="text.secondary">
          Data source: LocalAIOSControlPlane TaskRunner
        </Typography>
      </Paper>

      {/* Tasks table */}
      <Paper sx={{ p: 2 }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
            <CircularProgress size={32} />
          </Box>
        ) : tasks.length === 0 ? (
          <Box sx={{ py: 6, textAlign: "center" }}>
            <Typography variant="body1" color="text.secondary">
              No tasks match the selected filter.
            </Typography>
            <Button
              variant="outlined"
              size="small"
              sx={{ mt: 2 }}
              startIcon={<AddIcon />}
              onClick={() => navigate("/workspace")}
            >
              Submit Task
            </Button>
          </Box>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Task ID</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Latest Activity</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {tasks.map((task) => (
                  <TableRow key={task.submission_id} hover>
                    <TableCell sx={{ fontFamily: "monospace", fontSize: "0.85rem", fontWeight: 600 }}>
                      {task.submission_id}
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={task.status}
                        size="small"
                        color={
                          task.status === "COMPLETED" || task.status === "completed"
                            ? "success"
                            : task.status === "FAILED" || task.status === "failed"
                            ? "error"
                            : "warning"
                        }
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell sx={{ maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {task.history && task.history.length > 0
                        ? task.history[task.history.length - 1]
                        : "No history"}
                    </TableCell>
                    <TableCell align="right">
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => navigate(`/workspace?id=${task.submission_id}`)}
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
    </Box>
  );
};
