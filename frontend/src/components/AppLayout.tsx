import React from "react";
import { Layout } from "../layout/Layout";

// Re-export Layout as AppLayout for backwards compatibility
export const AppLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return <Layout>{children}</Layout>;
};

export default AppLayout;
