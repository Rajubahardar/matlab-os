import React, { useEffect, useState } from "react";
import {
  Box,
  Typography,
  Paper,
  Alert,
  Chip,
  Button,
  Divider,
  CircularProgress,
} from "@mui/material";
import {
  Gavel as ApprovalsIcon,
  Security as SecurityIcon,
  Terminal as TerminalIcon,
  Refresh as RefreshIcon,
  HourglassEmpty as PendingIcon,
} from "@mui/icons-material";
import { getApprovals } from "../api/client";

export const Approvals: React.FC = () => {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchApprovals = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getApprovals();
      setApprovals(data);
    } catch (err: any) {
      setError(err?.message || "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchApprovals();
    const interval = setInterval(fetchApprovals, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 3 }}>
        <Box>
          <Typography variant="h5" fontWeight={700}>
            Approval &amp; Governance
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Out-of-band security gates for high-privilege operations and MATLAB executions
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={loading ? <CircularProgress size={16} /> : <RefreshIcon />}
          onClick={fetchApprovals}
          disabled={loading}
        >
          {loading ? "Checking..." : "Refresh"}
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      <Alert severity="info" sx={{ mb: 3 }}>
        <strong>Option B Security Policy Active:</strong> Web-based direct approval is strictly disabled.
        To authorize high-risk execution, a human operator must review the descriptor and authorize via the local CLI terminal.
      </Alert>

      <Paper sx={{ p: 3, mb: 3 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 2 }}>
          <SecurityIcon color="primary" />
          <Typography variant="h6">Approval Policy &amp; Security Boundary</Typography>
        </Box>

        <Typography variant="body2" color="text.secondary" paragraph>
          The Local AI OS V1 security engine enforces out-of-band human authorization:
        </Typography>

        <Box component="ul" sx={{ pl: 2.5, m: 0, color: "text.secondary", fontSize: "0.9rem" }}>
          <li><strong>Out-of-Band Human Authorization:</strong> Operators inspect the exact payload in the local shell before granting access.</li>
          <li><strong>HMAC Signed Capability Tickets:</strong> Authorizations are signed with an unforgeable local key.</li>
          <li><strong>SHA-256 Payload Binding:</strong> Tickets are cryptographically bound to the canonical command payload.</li>
          <li><strong>One-Time Use &amp; Replay Prevention:</strong> Tickets are consumed atomically on first redemption and cannot be replayed.</li>
          <li><strong>Fail-Closed Policy:</strong> Unapproved, modified, or expired execution requests are halted immediately.</li>
        </Box>
      </Paper>

      <Typography variant="h6" sx={{ mb: 2 }}>
        Pending Authorization Requests ({approvals.length})
      </Typography>

      {approvals.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: "center" }}>
          <ApprovalsIcon sx={{ fontSize: 48, color: "text.secondary", mb: 1.5 }} />
          <Typography variant="h6" fontWeight={600}>
            No Pending Approvals
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 450, mx: "auto", mt: 1 }}>
            When a task requires elevated privileges or MATLAB code execution,
            a request will appear here with instructions for out-of-band CLI confirmation.
          </Typography>
        </Paper>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {approvals.map((req) => (
            <Paper key={req.request_id} sx={{ p: 3 }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1.5 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="subtitle1" fontWeight={700} sx={{ fontFamily: "monospace" }}>
                    Token: {req.request_id}
                  </Typography>
                  <Chip
                    size="small"
                    label={req.operation || "code_execution"}
                    color="warning"
                    icon={<PendingIcon />}
                  />
                </Box>
                <Typography variant="caption" color="text.secondary">
                  Expires: {new Date(req.expires_at * 1000).toLocaleTimeString()}
                </Typography>
              </Box>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                <strong>Payload SHA-256 Digest:</strong>{" "}
                <span style={{ fontFamily: "monospace" }}>{req.payload_sha256}</span>
              </Typography>

              <Divider sx={{ my: 1.5 }} />

              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
                <TerminalIcon color="action" fontSize="small" />
                <Typography variant="body2" fontWeight={600}>
                  Authorize via Local CLI Terminal:
                </Typography>
              </Box>

              <Box
                component="pre"
                sx={{
                  bgcolor: "#0d1117",
                  p: 1.5,
                  borderRadius: 1,
                  fontFamily: "monospace",
                  fontSize: "0.85rem",
                  m: 0,
                  userSelect: "all",
                  border: "1px solid #30363d",
                }}
              >
                python -m matlab_os.integration.approval_cli approve {req.request_id} --as "Operator"
              </Box>
            </Paper>
          ))}
        </Box>
      )}
    </Box>
  );
};
