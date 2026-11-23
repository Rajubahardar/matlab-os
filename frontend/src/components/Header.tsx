/* Header component displaying app title and user info */
import React from "react";
import { Box, AppBar, Toolbar, Typography, Avatar } from "@mui/material";

export const Header: React.FC = () => {
  return (
    <Box sx={{ flexGrow: 1 }}>
      <AppBar position="fixed" sx={{ bgcolor: "#1a1a2e", color: "#e0e0f0" }}>
        <Toolbar>
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" noWrap>
              Local AI OS V1
            </Typography>
          </Box>

          {/* User avatar and name */}
          <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
            <Avatar sx={{ width: 32, height: 32 }}>
              AI
            </Avatar>

            <Typography variant="body2" color="inherit">
              Operator
            </Typography>
          </Box>
        </Toolbar>
      </AppBar>
    </Box>
  );
};
