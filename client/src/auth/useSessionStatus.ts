import { useEffect, useState } from "preact/hooks";
import { onStatusChange, sessionStatus, type SessionStatus } from "./status";

/** The current session status, re-rendering the caller on change. */
export function useSessionStatus(): SessionStatus {
  const [status, setStatus] = useState<SessionStatus>(sessionStatus);
  useEffect(() => {
    setStatus(sessionStatus);
    return onStatusChange(setStatus);
  }, []);
  return status;
}
