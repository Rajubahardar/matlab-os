import React from "react";
import {
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Typography,
  Box,
  Divider,
} from "@mui/material";
import {
  Dashboard as DashboardIcon,
  Psychology as AiIcon,
  Assignment as TasksIcon,
  Engineering as EngineeringIcon,
  Hub as AgentsIcon,
  Gavel as ApprovalsIcon,
  Settings as SettingsIcon,
} from "@mui/icons-material";
import { useNavigate, useLocation } from "react-router-dom";

export const DRAWER_WIDTH = 240;

interface NavItem {
  label: string;
  path: string;
  icon: React.ReactElement;
}

const navItems: NavItem[] = [
  { label: "Dashboard", path: "/", icon: <DashboardIcon /> },
  { label: "AI Workspace", path: "/workspace", icon: <AiIcon /> },
  { label: "Tasks", path: "/tasks", icon: <TasksIcon /> },
  { label: "Engineering Skills", path: "/engineering", icon: <EngineeringIcon /> },
  { label: "Agents", path: "/agents", icon: <AgentsIcon /> },
  { label: "Approvals", path: "/approvals", icon: <ApprovalsIcon /> },
  { label: "Settings", path: "/settings", icon: <SettingsIcon /> },
];

export const Sidebar: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Drawer
      variant="permanent"
      sx={{
        width: DRAWER_WIDTH,
        flexShrink: 0,
        [`& .MuiDrawer-paper`]: {
          width: DRAWER_WIDTH,
          boxSizing: "border-box",
        },
      }}
    >
      <Toolbar sx={{ px: 2, display: "flex", alignItems: "center", gap: 1 }}>
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: 1,
            bgcolor: "primary.main",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: "bold",
            color: "#0d1117",
            fontSize: 16,
          }}
        >
          AI
        </Box>
        <Typography variant="subtitle1" fontWeight={700} color="text.primary">
          Local AI OS V1
        </Typography>
      </Toolbar>
      <Divider />
      <Box sx={{ overflow: "auto", mt: 1 }}>
        <List>
          {navItems.map((item) => {
            const isSelected =
              item.path === "/"
                ? location.pathname === "/"
                : location.pathname.startsWith(item.path);

            return (
              <ListItem key={item.path} disablePadding sx={{ display: "block", px: 1 }}>
                <ListItemButton
                  selected={isSelected}
                  onClick={() => navigate(item.path)}
                  sx={{
                    borderRadius: 1,
                    mb: 0.5,
                    "&.Mui-selected": {
                      backgroundColor: "rgba(88, 166, 255, 0.15)",
                      color: "primary.light",
                      "& .MuiListItemIcon-root": {
                        color: "primary.light",
                      },
                      "&:hover": {
                        backgroundColor: "rgba(88, 166, 255, 0.25)",
                      },
                    },
                  }}
                >
                  <ListItemIcon
                    sx={{
                      minWidth: 40,
                      color: isSelected ? "primary.light" : "text.secondary",
                    }}
                  >
                    {item.icon}
                  </ListItemIcon>
                  <ListItemText
                    primary={item.label}
                    primaryTypographyProps={{
                      fontSize: "0.9rem",
                      fontWeight: isSelected ? 600 : 400,
                    }}
                  />
                </ListItemButton>
              </ListItem>
            );
          })}
        </List>
      </Box>
    </Drawer>
  );
};
