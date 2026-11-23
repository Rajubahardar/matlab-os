import React, { useEffect, useState } from "react";
import { Box, Typography, Chip } from "@mui/material";
import { getHealth } from "../api/client";

export const StatusIndicator: React.FC = () => {
  const [isConnected, setIsConnected] = useState<boolean | null>(null);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);

  useEffect(() => {
    let mounted = true;

    const checkHealth = async () => {
      try {
        const res = await getHealth();
        if (mounted) {
          setIsConnected(res.status === "ok");
          setLastCheck(new Date());
        }
      } catch {
        if (mounted) {
          setIsConnected(false);
        }
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <Box sx={{ mr: 2, ml: 2, display: "flex", alignItems: "center", gap: 1 }}>
      <Chip
        label={isConnected === null ? "Checking..." : isConnected ? "API Connected" : "API Disconnected"}
        size="small"
        color={isConnected === null ? "warning" : isConnected ? "success" : "error"}
        variant="outlined"
      />
      {lastCheck && (
        <Typography variant="caption" color="text.secondary">
          {lastCheck.toLocaleTimeString()}
        </Typography>
      )}
    </Box>
  );
};
