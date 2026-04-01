/**
 * Update Video — marks a video as complete in video_queue.json via GitHub API.
 *
 * POST /api/update-video
 * Body: { video_id, youtube_url, password }
 *
 * Requires GITHUB_TOKEN env var (fine-grained PAT with Contents: Read+Write
 * on realestateinvestorceo/highlevel-ai).
 */

const HASH = "20779ca4e9489d7b0a80076f0f79aa5ddbb2f3b322eb43355687d12c9f53c19c";
const REPO = "realestateinvestorceo/highlevel-ai";
const FILE_PATH = "site/video_queue.json";

async function sha256(text) {
  const encoded = new TextEncoder().encode(text);
  const buf = await crypto.subtle.digest("SHA-256", encoded);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function extractVideoId(url) {
  try {
    const u = new URL(url);
    if (u.hostname.includes("youtu.be")) {
      return u.pathname.slice(1);
    }
    return u.searchParams.get("v") || "";
  } catch {
    return "";
  }
}

module.exports = async function handler(req, res) {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "https://www.highlevel.ai");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  const { video_id, youtube_url, password } = req.body || {};

  if (!video_id || !youtube_url || !password) {
    return res.status(400).json({ error: "Missing required fields: video_id, youtube_url, password" });
  }

  // Verify password
  const pwHash = await sha256(password);
  if (pwHash !== HASH) {
    return res.status(403).json({ error: "Invalid password" });
  }

  const youtubeVideoId = extractVideoId(youtube_url);
  if (!youtubeVideoId) {
    return res.status(400).json({ error: "Could not extract YouTube video ID from URL" });
  }

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return res.status(500).json({ error: "GITHUB_TOKEN not configured" });
  }

  try {
    // Fetch current file from GitHub
    const getResp = await fetch(
      `https://api.github.com/repos/${REPO}/contents/${FILE_PATH}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github.v3+json",
        },
      }
    );

    if (!getResp.ok) {
      const errText = await getResp.text();
      return res.status(500).json({ error: "Failed to fetch video_queue.json: " + errText });
    }

    const fileData = await getResp.json();
    const content = Buffer.from(fileData.content, "base64").toString("utf-8");
    const queue = JSON.parse(content);

    // Find and update the video entry
    const video = queue.videos.find((v) => v.id === video_id);
    if (!video) {
      return res.status(404).json({ error: "Video not found: " + video_id });
    }

    video.youtube_url = youtube_url;
    video.youtube_video_id = youtubeVideoId;
    video.status = "complete";
    video.completed_date = new Date().toISOString().slice(0, 10);
    queue.last_updated = new Date().toISOString();

    // Commit updated file back to GitHub
    const updatedContent = Buffer.from(
      JSON.stringify(queue, null, 2) + "\n"
    ).toString("base64");

    const putResp = await fetch(
      `https://api.github.com/repos/${REPO}/contents/${FILE_PATH}`,
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github.v3+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: `Mark video complete: ${video_id}`,
          content: updatedContent,
          sha: fileData.sha,
        }),
      }
    );

    if (!putResp.ok) {
      const errText = await putResp.text();
      return res.status(500).json({ error: "Failed to update video_queue.json: " + errText });
    }

    return res.status(200).json({ success: true, video_id, youtube_video_id: youtubeVideoId });
  } catch (err) {
    console.error("Update video error:", err.message);
    return res.status(500).json({ error: "Internal error: " + err.message });
  }
};
