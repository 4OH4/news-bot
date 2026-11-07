import { render } from "preact";
import { App } from "./App";
import { RECONNECT_PATH } from "./auth/reconnect";
import { onSignOut } from "./auth/signout";
import { clearAll } from "./store/db";
import "./style.css";
import { startFeedback } from "./feedback/start";

startFeedback();

// A sign-out the user chooses wipes the stored feed and articles (#183).
onSignOut(clearAll);

// Reconnect lands here after Google's sign-in; show the normal address without navigating.
if (location.pathname === RECONNECT_PATH) history.replaceState(null, "", "/");

const root = document.getElementById("app");
if (root) render(<App />, root);
