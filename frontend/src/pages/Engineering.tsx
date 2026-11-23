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
} from "@mui/material";
import {
  Engineering as EngineeringIcon,
  PlayArrow as ExecuteIcon,
  Refresh as RefreshIcon,
  CheckCircle as ActiveIcon,
} from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getSkills } from "../api/client";
import { SkillEntry, SkillsResponse } from "../api/types";

export const Engineering: React.FC = () => {
  const navigate = useNavigate();
  const [skillsData, setSkillsData] = useState<SkillsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSkills = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSkills();
      setSkillsData(data);
    } catch (err: any) {
      setError(err?.message || "Failed to load skills registry");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSkills();
  }, []);

  const handleUseSkill = (skill: SkillEntry) => {
    navigate(`/workspace?task=${encodeURIComponent(`Execute ${skill.name}`)}`);
  };

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 3 }}>
        <div>
          <Typography variant="h5" fontWeight={700}>
            Engineering Skills Registry
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Canonical engineering capabilities registered with {skillsData?.agent || "MATLAB Expert Agent"}
          </Typography>
        </div>
        <Button
          variant="outlined"
          size="small"
          startIcon={<RefreshIcon />}
          onClick={loadSkills}
          disabled={loading}
        >
          Refresh
        </Button>
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
        <>
          <Paper sx={{ p: 2, mb: 3, display: "flex", alignItems: "center", gap: 2 }}>
            <EngineeringIcon color="primary" sx={{ fontSize: 32 }} />
            <Box>
              <Typography variant="subtitle1" fontWeight={600}>
                {skillsData?.agent || "MATLAB Expert Agent"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {skillsData?.skills?.length || 0} registered engineering skills governed by MATLAB MCP approval policy
              </Typography>
            </Box>
          </Paper>

          <Grid container spacing={2}>
            {(skillsData?.skills || []).map((skill, index) => (
              <Grid item xs={12} sm={6} md={4} key={index}>
                <Paper
                  sx={{
                    p: 2.5,
                    height: "100%",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    transition: "border-color 0.2s",
                    "&:hover": {
                      borderColor: "primary.main",
                    },
                  }}
                >
                  <Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", mb: 1 }}>
                      <Typography variant="subtitle2" fontWeight={600}>
                        {skill.name}
                      </Typography>
                      <Chip
                        icon={<ActiveIcon sx={{ fontSize: "14px !important" }} />}
                        label="Active"
                        size="small"
                        color="success"
                        variant="outlined"
                      />
                    </Box>
                    <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.85rem", mb: 2 }}>
                      {skill.description || `Registered domain capability for ${skill.name}.`}
                    </Typography>
                  </Box>

                  <Box sx={{ display: "flex", justifyContent: "flex-end", pt: 1, borderTop: "1px solid #30363d" }}>
                    <Button
                      size="small"
                      startIcon={<ExecuteIcon />}
                      onClick={() => handleUseSkill(skill)}
                    >
                      Use Skill
                    </Button>
                  </Box>
                </Paper>
              </Grid>
            ))}
          </Grid>
        </>
      )}
    </Box>
  );
};
