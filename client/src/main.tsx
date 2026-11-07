import { render } from "preact";
import { App } from "./App";
import { RECONNECT_PATH } from "./auth/reconnect";
import "./style.css";

// Reconnect lands here after Google's sign-in; show the normal address without navigating.
if (location.pathname === RECONNECT_PATH) history.replaceState(null, "", "/");

const root = document.getElementById("app");
if (root) render(<App />, root);
