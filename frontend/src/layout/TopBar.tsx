import React, { useEffect, useState } from "react";
import { AppBar, Toolbar, Typography, Box, Chip, IconButton, Tooltip } from "@mui/material";
import { Refresh as RefreshIcon, FiberManualRecord as StatusDot } from "@mui/icons-material";
import { getHealth } from "../api/client";
import { DRAWER_WIDTH } from "./Sidebar";

export const TopBar: React.FC = () => {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [checking, setChecking] = useState<boolean>(false);

  const checkStatus = async () => {
    setChecking(true);
    try {
      const res = await getHealth();
      setHealthy(res.status === "ok");
    } catch {
      setHealthy(false);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    checkStatus();
    const timer = setInterval(checkStatus, 15000);
    return () => clearInterval(timer);
  }, []);

  return (
    <AppBar
      position="fixed"
      sx={{
        width: `calc(100% - ${DRAWER_WIDTH}px)`,
        ml: `${DRAWER_WIDTH}px`,
        bgcolor: "background.paper",
        color: "text.primary",
      }}
    >
      <Toolbar sx={{ justifyContent: "space-between" }}>
        <Typography variant="h6" noWrap component="div" sx={{ fontSize: "1.1rem" }}>
          Engineering AI Workspace
        </Typography>

        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Chip
            size="small"
            icon={
              <StatusDot
                sx={{
                  fontSize: "0.8rem !important",
                  color:
                    healthy === null
                      ? "#d29922"
                      : healthy
                      ? "#3fb950"
                      : "#f85149",
                }}
              />
            }
            label={
              healthy === null
                ? "Connecting..."
                : healthy
                ? "Backend Connected"
                : "Backend Offline"
            }
            variant="outlined"
            sx={{
              borderColor:
                healthy === null
                  ? "warning.main"
                  : healthy
                  ? "success.main"
                  : "error.main",
              color: "text.primary",
              fontWeight: 500,
            }}
          />

          <Tooltip title="Refresh connection status">
            <IconButton
              size="small"
              onClick={checkStatus}
              disabled={checking}
              sx={{ color: "text.secondary" }}
            >
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      </Toolbar>
    </AppBar>
  );
};
