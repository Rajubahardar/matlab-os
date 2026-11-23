/* Sidebar navigation component for the Local AI OS V1 frontend */
import React from "react";
import { Box, List, ListItemButton, ListItemIcon, ListItemText } from "@mui/material";
import HomeIcon from "@mui/icons-material/Home";
import AssignmentIcon from "@mui/icons-material/Assignment";
import BookIcon from "@mui/icons-material/Book";
import BuildIcon from "@mui/icons-material/Build";
import PeopleIcon from "@mui/icons-material/Person";
import SettingsIcon from "@mui/icons-material/Settings";
import { useLocation, useNavigate } from "react-router-dom";

interface NavItem {
  path: string;
  label: string;
  icon: React.ReactElement;
}

const navItems: NavItem[] = [
  { path: "/", label: "Dashboard", icon: <HomeIcon /> },
  { path: "/workspace", label: "AI Workspace", icon: <AssignmentIcon /> },
  { path: "/tasks", label: "Tasks", icon: <BookIcon /> },
  { path: "/engineering", label: "Engineering", icon: <BuildIcon /> },
  { path: "/agents", label: "Agents", icon: <PeopleIcon /> },
  { path: "/settings", label: "Settings", icon: <SettingsIcon /> },
];

export const Sidebar: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Box
      sx={{
        width: 240,
        flexShrink: 0,
        bgcolor: "#161625",
        color: "#e0e0f0",
        minHeight: "100vh",
        pt: 8,
      }}
    >
      <List>
        {navItems.map((item) => (
          <ListItemButton
            key={item.path}
            selected={location.pathname === item.path}
            onClick={() => navigate(item.path)}
            sx={{
              "&.Mui-selected": {
                bgcolor: "#24243e",
                color: "#6c63ff",
                "& .MuiListItemIcon-root": {
                  color: "#6c63ff",
                },
              },
              "&:hover": {
                bgcolor: "#1f1f35",
              },
            }}
          >
            <ListItemIcon sx={{ color: "#a0a0b0" }}>{item.icon}</ListItemIcon>
            <ListItemText primary={item.label} />
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
};
