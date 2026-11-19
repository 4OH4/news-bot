// Made-up preference catalogue for the #332 prototype. Source ids match config/sources.yaml;
// regions and kinds are guesses for trying layouts, not data. No article text.

export type Kind = "UK and world news" | "Tech" | "Science" | "Long reads" | "Blogs";
export type Region = "UK" | "Europe" | "US" | "World";

export interface MockSource {
  id: string;
  name: string;
  kind: Kind;
  region?: Region;
  starter?: boolean;
}

export const KINDS: Kind[] = ["UK and world news", "Tech", "Science", "Long reads", "Blogs"];
export const REGIONS: Region[] = ["UK", "Europe", "US", "World"];

export const SOURCES: MockSource[] = [
  { id: "bbc-news", name: "BBC News", kind: "UK and world news", region: "UK", starter: true },
  { id: "guardian-world", name: "Guardian World", kind: "UK and world news", region: "UK", starter: true },
  { id: "independent-uk", name: "The Independent", kind: "UK and world news", region: "UK" },
  { id: "inews", name: "i news", kind: "UK and world news", region: "UK" },
  { id: "al-jazeera-english", name: "Al Jazeera English", kind: "UK and world news", region: "World", starter: true },
  { id: "dw-news", name: "DW News", kind: "UK and world news", region: "Europe" },
  { id: "business-insider", name: "Business Insider", kind: "UK and world news", region: "US" },
  { id: "ars-technica", name: "Ars Technica", kind: "Tech", region: "US", starter: true },
  { id: "the-verge", name: "The Verge", kind: "Tech", region: "US", starter: true },
  { id: "wired", name: "Wired", kind: "Tech", region: "US" },
  { id: "lwn", name: "LWN.net", kind: "Tech" },
  { id: "404media", name: "404 Media", kind: "Tech", region: "US" },
  { id: "mit-tech-review", name: "MIT Technology Review", kind: "Tech", region: "US" },
  { id: "nature-news", name: "Nature News", kind: "Science", region: "UK" },
  { id: "quanta", name: "Quanta Magazine", kind: "Science", region: "US", starter: true },
  { id: "phys-org", name: "Phys.org", kind: "Science" },
  { id: "carbon-brief", name: "Carbon Brief", kind: "Science", region: "UK", starter: true },
  { id: "bbc-future", name: "BBC Future", kind: "Long reads", region: "UK" },
  { id: "guardian-longread", name: "Guardian Long Read", kind: "Long reads", region: "UK" },
  { id: "aeon", name: "Aeon", kind: "Long reads" },
  { id: "new-yorker", name: "The New Yorker", kind: "Long reads", region: "US" },
  { id: "simon-willison", name: "Simon Willison", kind: "Blogs", starter: true },
  { id: "julia-evans", name: "Julia Evans", kind: "Blogs" },
  { id: "martin-fowler", name: "Martin Fowler", kind: "Blogs" },
  { id: "cloudflare-blog", name: "Cloudflare Blog", kind: "Blogs", region: "US" },
  { id: "noahpinion", name: "Noahpinion", kind: "Blogs", region: "US" },
];

export const STARTER_TOPICS = ["Politics", "Tech", "Science", "Climate and energy", "Business and economy", "Sport"];
export const MORE_TOPICS = [
  "AI",
  "Space",
  "Security and privacy",
  "Linux",
  "Health",
  "Culture",
  "Celebrity and royals",
  "Shopping",
];
