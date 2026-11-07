/** Which stories have been seen, by `storyKey(item)` (made by the caller). */
import { openFeedbackDb } from "./db";

export async function markSeen(key: string): Promise<void> {
  const db = await openFeedbackDb();
  await db.put("seen", { key, at: new Date().toISOString() });
}

export async function isSeen(key: string): Promise<boolean> {
  const db = await openFeedbackDb();
  return (await db.count("seen", key)) > 0;
}
