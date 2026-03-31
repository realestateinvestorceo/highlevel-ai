/**
 * Pipeline Webhook — receives step status updates from Make.com / HeyGen
 * and writes them to the PipelineLog tab in Google Sheets.
 *
 * POST /api/pipeline-webhook
 * Body: { step, status, details, topic, run_id }
 */

const { google } = require("googleapis");

const SHEET_ID = process.env.VIDEO_SHEET_ID;
const TAB_NAME = "PipelineLog";

async function getAuthClient() {
  const raw = process.env.GA4_SERVICE_ACCOUNT_JSON;
  if (!raw) throw new Error("Missing GA4_SERVICE_ACCOUNT_JSON env var");
  const key = JSON.parse(raw);
  const auth = new google.auth.GoogleAuth({
    credentials: key,
    scopes: ["https://www.googleapis.com/auth/spreadsheets"],
  });
  return auth.getClient();
}

module.exports = async function handler(req, res) {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "https://www.highlevel.ai");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  const { step, status, details, topic, run_id } = req.body || {};

  if (!step || !status) {
    return res.status(400).json({ error: "Missing required fields: step, status" });
  }

  const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  const row = [
    timestamp,
    run_id || new Date().toISOString().slice(0, 10),
    step,
    status,
    (details || "").slice(0, 500),
    topic || "",
    "",  // duration_seconds — external callers rarely know this
  ];

  try {
    const authClient = await getAuthClient();
    const sheets = google.sheets({ version: "v4", auth: authClient });

    await sheets.spreadsheets.values.append({
      spreadsheetId: SHEET_ID,
      range: `${TAB_NAME}!A:G`,
      valueInputOption: "USER_ENTERED",
      requestBody: { values: [row] },
    });

    return res.status(200).json({ ok: true, logged: { step, status } });
  } catch (err) {
    console.error("Pipeline webhook error:", err.message);
    return res.status(500).json({ error: "Failed to log: " + err.message });
  }
};
