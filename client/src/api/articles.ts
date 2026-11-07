import { apiFetch } from "./client";

/** The cleaned article HTML for an item, or null when the content has expired (404). */
export async function fetchArticle(id: string): Promise<string | null> {
  const response = await apiFetch(`/api/articles/${encodeURIComponent(id)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Article request failed with status ${response.status}`);
  return response.text();
}
