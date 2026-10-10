import React from "react";
import ReactDOM from "react-dom/client";
import "./theme/tokens.css";
import "./components/ui/ui.css";
import App from "./App";
import { ThemeProvider } from "./theme/ThemeProvider";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
);