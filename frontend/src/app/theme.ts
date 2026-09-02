import { createTheme } from "@mui/material";

export const theme = createTheme({
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        html: { scrollbarGutter: "stable" },
      },
    },
  },
  palette: {
    mode: "dark",
    primary: { main: "#60a5fa" },
    background: { default: "#0b1120", paper: "#111827" },
  },
  shape: { borderRadius: 8 },
});
