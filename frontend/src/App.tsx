import React from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider, CssBaseline } from "@mui/material";
import { darkTheme } from "./theme/theme";
import { Layout } from "./layout/Layout";
import { Dashboard } from "./pages/Dashboard";
import { AIWorkspace } from "./pages/AIWorkspace";
import { Tasks } from "./pages/Tasks";
import { Engineering } from "./pages/Engineering";
import { Agents } from "./pages/Agents";
import { Approvals } from "./pages/Approvals";
import { Settings } from "./pages/Settings";

export const App: React.FC = () => {
  return (
    <ThemeProvider theme={darkTheme}>
      <CssBaseline />
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/workspace" element={<AIWorkspace />} />
          <Route path="/ai-workspace" element={<Navigate to="/workspace" replace />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/engineering" element={<Engineering />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </ThemeProvider>
  );
};

export default App;
