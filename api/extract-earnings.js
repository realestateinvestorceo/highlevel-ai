const Anthropic = require("@anthropic-ai/sdk").default;

const client = new Anthropic(); // uses ANTHROPIC_API_KEY env var

module.exports = async function handler(req, res) {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "https://www.highlevel.ai");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  const { image } = req.body || {};
  if (!image) return res.status(400).json({ error: "Missing image field" });

  // Strip data URL prefix if present
  const base64 = image.replace(/^data:image\/\w+;base64,/, "");
  const mediaType = image.startsWith("data:image/png") ? "image/png" : "image/jpeg";

  try {
    const msg = await client.messages.create({
      model: "claude-haiku-4-5-20241022",
      max_tokens: 1024,
      messages: [
        {
          role: "user",
          content: [
            {
              type: "image",
              source: { type: "base64", media_type: mediaType, data: base64 },
            },
            {
              type: "text",
              text: `Extract the affiliate performance data from this screenshot into JSON. The table has columns: Day, Clicks, Signups, Customers, Earnings.

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "period": "Last 7 days",
  "totals": { "clicks": 0, "signups": 0, "customers": 0, "earnings": 0 },
  "rows": [
    { "day": "Today", "clicks": 0, "signups": 0, "customers": 0, "earnings": 0 }
  ]
}

Rules:
- "totals" should sum ALL rows in the table
- "earnings" values are numbers (no $ sign)
- If you see "$38.80" extract as 38.80
- Include every row visible in the table
- "period" should match what the screenshot shows (e.g. "Last 7 days", "Last 4 weeks", "Last 6 months")`,
            },
          ],
        },
      ],
    });

    const text = msg.content[0].text.trim();
    // Parse to validate it's real JSON
    const data = JSON.parse(text);
    return res.status(200).json(data);
  } catch (err) {
    console.error("Extraction error:", err.message);
    return res.status(500).json({ error: "Extraction failed: " + err.message });
  }
};
