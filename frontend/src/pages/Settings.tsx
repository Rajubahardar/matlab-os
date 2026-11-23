import React, { useState } from "react";
import {
  Box,
  Typography,
  Paper,
  Grid,
  Button,
  Chip,
  Alert,
  Divider,
  List,
  ListItem,
  ListItemText,
} from "@mui/material";
import {
  Settings as SettingsIcon,
  CheckCircle as OkIcon,
  Storage as DbIcon,
  Language as ApiIcon,
} from "@mui/icons-material";
import { getHealth } from "../api/client";
import { HealthCheck } from "../api/types";

export const Settings: React.FC = () => {
  const [healthResult, setHealthResult] = useState<HealthCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const testConnection = async () => {
    setChecking(true);
    setError(null);
    try {
      const res = await getHealth();
      setHealthResult(res);
    } catch (err: any) {
      setError(err?.message || "Health check failed");
    } finally {
      setChecking(false);
    }
  };

  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" fontWeight={700}>
          System Settings &amp; Configuration
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Environment configuration, persistence paths, and service health diagnostics
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* API & Backend Diagnostics */}
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 2 }}>
              <ApiIcon color="primary" />
              <Typography variant="h6">FastAPI Backend Connection</Typography>
            </Box>

            <List dense disablePadding>
              <ListItem disableGutters>
                <ListItemText
                  primary="API Base URL"
                  secondary="/api/v1 (Served by Vite proxy or root Uvicorn)"
                />
              </ListItem>
              <ListItem disableGutters>
                <ListItemText
                  primary="Health Endpoint"
                  secondary="GET /health"
                />
              </ListItem>
              <ListItem disableGutters>
                <ListItemText
                  primary="Protocol"
                  secondary="HTTP REST / JSON"
                />
              </ListItem>
            </List>

            <Divider sx={{ my: 2 }} />

            <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <Button
                variant="outlined"
                size="small"
                onClick={testConnection}
                disabled={checking}
              >
                {checking ? "Checking..." : "Test Backend Liveness"}
              </Button>

              {healthResult && (
                <Chip
                  icon={<OkIcon />}
                  label={`Status: ${healthResult.status}`}
                  color="success"
                  size="small"
                />
              )}
            </Box>
          </Paper>
        </Grid>

        {/* Database & Persistence Info */}
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 2 }}>
              <DbIcon color="primary" />
              <Typography variant="h6">Persistence &amp; Storage</Typography>
            </Box>

            <List dense disablePadding>
              <ListItem disableGutters>
                <ListItemText
                  primary="Primary Database"
                  secondary="SQLite (data/matlab_agent.db)"
                />
              </ListItem>
              <ListItem disableGutters>
                <ListItemText
                  primary="Control Plane Workspace"
                  secondary="workspace/ (Derived relative to project root)"
                />
              </ListItem>
              <ListItem disableGutters>
                <ListItemText
                  primary="Task Runner"
                  secondary="matlab_os.integrations.runner:TaskRunner"
                />
              </ListItem>
            </List>
          </Paper>
        </Grid>

        {/* Environment Security */}
        <Grid item xs={12}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" sx={{ mb: 1.5 }}>
              Security &amp; Privacy Policy
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Local AI OS V1 runs strictly local-first. Credentials, environment secrets, and sensitive tokens
              are never displayed or transmitted to third-party endpoints. All filesystem operations remain
              confined to designated workspace directories.
            </Typography>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};
