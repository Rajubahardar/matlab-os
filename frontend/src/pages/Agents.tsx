import React, { useEffect, useState } from "react";
import {
  Box,
  Typography,
  Paper,
  Grid,
  Chip,
  Button,
  CircularProgress,
  Alert,
  Divider,
} from "@mui/material";
import {
  Hub as HubIcon,
  CheckCircle as ActiveIcon,
  Security as SecurityIcon,
  Memory as MemoryIcon,
  Storage as StorageIcon,
} from "@mui/icons-material";
import { getSkills } from "../api/client";
import { SkillsResponse } from "../api/types";

export const Agents: React.FC = () => {
  const [skillsData, setSkillsData] = useState<SkillsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAgentInfo = async () => {
      try {
        const s = await getSkills();
        setSkillsData(s);
      } catch (err: any) {
        setError(err?.message || "Failed to load agent architecture data");
      } finally {
        setLoading(false);
      }
    };
    fetchAgentInfo();
  }, []);

  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" fontWeight={700}>
          Agent Architecture
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Autonomous engineering agent ecosystem under Local AI OS control plane
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
          <CircularProgress size={32} />
        </Box>
      ) : (
        <Grid container spacing={3}>
          {/* Main Agent Card */}
          <Grid item xs={12} md={8}>
            <Paper sx={{ p: 3, mb: 3 }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", mb: 2 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
                  <HubIcon color="primary" sx={{ fontSize: 36 }} />
                  <Box>
                    <Typography variant="h6" fontWeight={700}>
                      {skillsData?.agent || "MATLAB Expert Agent"}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Core Specialized Agent · Version 0.2.0 · Local AI OS Architecture
                    </Typography>
                  </Box>
                </Box>
                <Chip
                  icon={<ActiveIcon sx={{ fontSize: "14px !important" }} />}
                  label="Registered"
                  size="small"
                  color="success"
                  variant="outlined"
                />
              </Box>

              <Typography variant="body2" color="text.secondary" paragraph>
                The primary autonomous engineering agent in Local AI OS. Rather than fragmenting
                capabilities into fragile, disconnected sub-agents, MATLABAgent acts as a unified
                expert possessing 16 specialized engineering skills, coordinated through
                LocalAIOSControlPlane and bounded by strict approval security gates.
              </Typography>

              <Divider sx={{ my: 2 }} />

              <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 1.5 }}>
                Registered Skills ({skillsData?.skills?.length || 0})
              </Typography>

              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
                {(skillsData?.skills || []).map((skill, idx) => (
                  <Chip
                    key={idx}
                    label={skill.name}
                    size="small"
                    variant="outlined"
                    sx={{ borderColor: "#30363d" }}
                  />
                ))}
              </Box>
            </Paper>
          </Grid>

          {/* Architecture info sidebar */}
          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 3, mb: 3 }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5 }}>
                <SecurityIcon color="warning" />
                <Typography variant="subtitle1" fontWeight={600}>
                  Security Boundaries
                </Typography>
              </Box>
              <Typography variant="body2" color="text.secondary" paragraph sx={{ fontSize: "0.85rem" }}>
                All MATLAB execution requests are intercepted by the MCP approval gate.
                Requester/approver separation, SHA-256 payload binding, and expiration
                rules are strictly enforced.
              </Typography>

              <Divider sx={{ my: 2 }} />

              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5 }}>
                <StorageIcon color="info" />
                <Typography variant="subtitle1" fontWeight={600}>
                  Persistence &amp; Memory
                </Typography>
              </Box>
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.85rem" }}>
                Episode, fact, and task records are persisted in SQLite under 
                <code> data/matlab_agent.db</code> and associated with project audit logs.
              </Typography>
            </Paper>
          </Grid>
        </Grid>
      )}
    </Box>
  );
};
