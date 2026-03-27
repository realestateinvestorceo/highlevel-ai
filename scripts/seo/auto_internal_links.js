#!/usr/bin/env node

/**
 * auto_internal_links.js
 *
 * Scans all HTML files in site/ and injects contextual internal links
 * based on a keyword-to-URL mapping defined in data/internal_links_map.json.
 *
 * Usage:
 *   node scripts/seo/auto_internal_links.js --dry-run          (default, preview changes)
 *   node scripts/seo/auto_internal_links.js --apply             (modify files with backups)
 *   node scripts/seo/auto_internal_links.js --apply --verbose   (modify + detailed logging)
 */

const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');

// ---------------------------------------------------------------------------
// Paths (relative to project root)
// ---------------------------------------------------------------------------
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const SITE_DIR = path.join(PROJECT_ROOT, 'site');
const LINKS_MAP_PATH = path.join(__dirname, 'data', 'internal_links_map.json');
const BACKUPS_DIR = path.join(__dirname, 'backups');
const REPORTS_DIR = path.join(__dirname, 'reports');

// ---------------------------------------------------------------------------
// CLI flags
// ---------------------------------------------------------------------------
const args = process.argv.slice(2);
const FLAG_APPLY = args.includes('--apply');
const FLAG_DRY_RUN = !FLAG_APPLY; // default
const FLAG_VERBOSE = args.includes('--verbose');

const MAX_LINKS_PER_PAGE = 5;

// Tags whose text nodes are eligible for link injection
const ELIGIBLE_TAGS = new Set(['p', 'li', 'td']);

// Tags that must never contain an injected link (anywhere in the ancestor chain)
const FORBIDDEN_ANCESTORS = new Set([
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'a', 'button', 'nav', 'header', 'footer',
  'script', 'style', 'code', 'pre',
]);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function log(msg) {
  if (FLAG_VERBOSE) {
    console.log(`  [verbose] ${msg}`);
  }
}

/**
 * Recursively collect all .html files under the given directories.
 */
function collectHtmlFiles(dirs) {
  const files = [];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isFile() && entry.name.endsWith('.html')) {
        files.push(fullPath);
      }
    }
  }
  return files;
}

/**
 * Determine the current page URL from <link rel="canonical"> or the file path.
 */
function getCurrentPageUrl($, filePath) {
  const canonical = $('link[rel="canonical"]').attr('href');
  if (canonical) {
    // Extract pathname from full URL or return as-is if already a path
    try {
      const url = new URL(canonical);
      return url.pathname;
    } catch {
      return canonical;
    }
  }
  // Derive from file path: site/foo/bar.html -> /foo/bar.html
  const rel = path.relative(SITE_DIR, filePath).replace(/\\/g, '/');
  return '/' + rel;
}

/**
 * Check whether any ancestor of an element (up to the root) is in the
 * forbidden set.
 */
function hasForbiddenAncestor($, el) {
  let current = el;
  while (current) {
    const tagName = current.tagName ? current.tagName.toLowerCase() : null;
    if (tagName && FORBIDDEN_ANCESTORS.has(tagName)) {
      return true;
    }
    current = current.parent;
  }
  return false;
}

/**
 * Check whether the element is inside the main content area — i.e. NOT inside
 * nav, header, footer, script, or style.
 */
function isInsideMainContent($, el) {
  const sectionExclusions = new Set(['nav', 'header', 'footer', 'script', 'style']);
  let current = el.parent;
  while (current) {
    const tagName = current.tagName ? current.tagName.toLowerCase() : null;
    if (tagName && sectionExclusions.has(tagName)) {
      return false;
    }
    current = current.parent;
  }
  return true;
}

/**
 * Find the closest element tag for a text node.
 */
function closestTag(el) {
  let current = el.parent;
  while (current) {
    if (current.tagName) return current.tagName.toLowerCase();
    current = current.parent;
  }
  return null;
}

/**
 * Walk all text nodes inside an element.
 * Returns an array of text-node objects from cheerio's internal DOM.
 */
function getTextNodes($, root) {
  const nodes = [];
  function walk(node) {
    if (node.type === 'text') {
      nodes.push(node);
    } else if (node.children) {
      for (const child of node.children) {
        walk(child);
      }
    }
  }
  // Walk all children of the root (the entire body)
  const body = $('body')[0];
  if (body && body.children) {
    for (const child of body.children) {
      walk(child);
    }
  }
  return nodes;
}

// ---------------------------------------------------------------------------
// Main processing
// ---------------------------------------------------------------------------

function processFile(filePath, keywordMap, sortedKeywords) {
  const html = fs.readFileSync(filePath, 'utf8');
  const $ = cheerio.load(html, { decodeEntities: false });

  const currentPageUrl = getCurrentPageUrl($, filePath);
  const relFilePath = path.relative(PROJECT_ROOT, filePath).replace(/\\/g, '/');

  log(`Processing: ${relFilePath}  (canonical: ${currentPageUrl})`);

  const changes = []; // { keyword, url, tag }
  const linkedKeywords = new Set(); // keywords already linked on this page
  let totalLinksInjected = 0;

  // Iterate keywords in length-descending order
  for (const keyword of sortedKeywords) {
    if (totalLinksInjected >= MAX_LINKS_PER_PAGE) break;
    if (linkedKeywords.has(keyword)) continue;

    const targetUrl = keywordMap[keyword];

    // Skip self-links
    if (targetUrl === currentPageUrl) {
      log(`  Skipping "${keyword}" — target is current page (${currentPageUrl})`);
      continue;
    }

    const regex = new RegExp('\\b(' + escapeRegex(keyword) + ')\\b', 'i');

    // Walk all text nodes in the document body
    const textNodes = getTextNodes($, $('body')[0]);

    let matched = false;

    for (const textNode of textNodes) {
      if (totalLinksInjected >= MAX_LINKS_PER_PAGE) break;
      if (matched) break;

      const text = textNode.data;
      if (!text || !regex.test(text)) continue;

      // Check the parent element tag
      const parentTag = closestTag(textNode);
      if (!parentTag || !ELIGIBLE_TAGS.has(parentTag)) {
        log(`  Skipping "${keyword}" in <${parentTag}> — not an eligible tag`);
        continue;
      }

      // Ensure not inside a forbidden ancestor
      if (hasForbiddenAncestor($, textNode)) {
        log(`  Skipping "${keyword}" — inside forbidden ancestor`);
        continue;
      }

      // Ensure inside main content area
      if (!isInsideMainContent($, textNode)) {
        log(`  Skipping "${keyword}" — not inside main content area`);
        continue;
      }

      // Perform replacement on the first match only
      const match = text.match(regex);
      if (!match) continue;

      const originalText = match[1]; // preserve original case
      const before = text.substring(0, match.index);
      const after = text.substring(match.index + originalText.length);

      // Build replacement nodes: textBefore + <a> + textAfter
      // We need to replace the text node with new nodes in the DOM
      const linkHtml = `<a href="${targetUrl}" data-auto-link="true">${originalText}</a>`;
      const newFragment = before + linkHtml + after;

      // Replace the text node with the new HTML fragment
      const parent = textNode.parent;
      const idx = parent.children.indexOf(textNode);

      // Parse the fragment and insert
      const frag = cheerio.load(newFragment, { decodeEntities: false });
      const fragNodes = frag('body')[0].children;

      // Remove the old text node and splice in new nodes
      parent.children.splice(idx, 1, ...fragNodes);
      // Re-parent the new nodes
      for (const n of fragNodes) {
        n.parent = parent;
      }

      linkedKeywords.add(keyword);
      totalLinksInjected++;
      matched = true;

      changes.push({
        keyword,
        url: targetUrl,
        tag: parentTag,
      });

      log(`  + "${keyword}" -> ${targetUrl} (in <${parentTag}>)`);
    }

    if (!matched) {
      log(`  No eligible match for "${keyword}" in ${relFilePath}`);
    }
  }

  return {
    filePath,
    relFilePath,
    changes,
    modifiedHtml: changes.length > 0 ? $.html() : null,
  };
}

function main() {
  // Load keyword map
  if (!fs.existsSync(LINKS_MAP_PATH)) {
    console.error(`Error: internal_links_map.json not found at ${LINKS_MAP_PATH}`);
    process.exit(1);
  }

  const keywordMap = JSON.parse(fs.readFileSync(LINKS_MAP_PATH, 'utf8'));
  const allKeywords = Object.keys(keywordMap);

  // Sort by length descending (longer phrases first to avoid partial matches)
  const sortedKeywords = allKeywords.sort((a, b) => b.length - a.length);

  console.log(`Loaded ${allKeywords.length} keyword mappings from internal_links_map.json`);

  // Collect HTML files
  const scanDirs = [
    SITE_DIR,
    path.join(SITE_DIR, 'blog'),
    path.join(SITE_DIR, 'tools'),
  ];
  const htmlFiles = collectHtmlFiles(scanDirs);

  if (htmlFiles.length === 0) {
    console.log('No HTML files found in site/, site/blog/, or site/tools/');
    process.exit(0);
  }

  console.log(`Found ${htmlFiles.length} HTML files to scan\n`);

  // Process all files
  const results = [];
  const allMatchedKeywords = new Set();

  for (const filePath of htmlFiles) {
    const result = processFile(filePath, keywordMap, sortedKeywords);
    results.push(result);
    for (const change of result.changes) {
      allMatchedKeywords.add(change.keyword);
    }
  }

  // Compute stats
  const modifiedResults = results.filter((r) => r.changes.length > 0);
  const totalLinks = modifiedResults.reduce((sum, r) => sum + r.changes.length, 0);
  const unmatchedKeywords = allKeywords.filter((kw) => !allMatchedKeywords.has(kw));

  // ---------------------------------------------------------------------------
  // Dry run output
  // ---------------------------------------------------------------------------
  if (FLAG_DRY_RUN) {
    console.log('DRY RUN -- No files modified\n');

    for (const result of modifiedResults) {
      console.log(`${result.relFilePath}:`);
      for (const change of result.changes) {
        console.log(`  + "${change.keyword}" -> ${change.url} (in <${change.tag}>)`);
      }
      console.log();
    }

    if (modifiedResults.length === 0) {
      console.log('No links would be injected.\n');
    }

    console.log(
      `Summary: ${totalLinks} links would be injected across ${modifiedResults.length} pages`
    );

    if (unmatchedKeywords.length > 0) {
      console.log(`\nKeywords with no matches (${unmatchedKeywords.length}):`);
      for (const kw of unmatchedKeywords) {
        console.log(`  - "${kw}"`);
      }
    }

    return;
  }

  // ---------------------------------------------------------------------------
  // Apply mode
  // ---------------------------------------------------------------------------
  console.log('APPLY MODE -- Modifying files\n');

  // Ensure backup and report dirs exist
  if (!fs.existsSync(BACKUPS_DIR)) {
    fs.mkdirSync(BACKUPS_DIR, { recursive: true });
  }
  if (!fs.existsSync(REPORTS_DIR)) {
    fs.mkdirSync(REPORTS_DIR, { recursive: true });
  }

  const timestamp = Date.now();

  for (const result of modifiedResults) {
    if (!result.modifiedHtml) continue;

    // Backup
    const baseName = path.basename(result.filePath, '.html');
    const backupName = `${baseName}.${timestamp}.html`;
    const backupPath = path.join(BACKUPS_DIR, backupName);
    fs.copyFileSync(result.filePath, backupPath);
    console.log(`  Backed up: ${result.relFilePath} -> ${path.relative(PROJECT_ROOT, backupPath)}`);

    // Write modified HTML
    fs.writeFileSync(result.filePath, result.modifiedHtml, 'utf8');
    console.log(`  Modified:  ${result.relFilePath} (${result.changes.length} links)`);
  }

  // ---------------------------------------------------------------------------
  // Generate report
  // ---------------------------------------------------------------------------
  const today = new Date().toISOString().slice(0, 10);
  const reportLines = [];

  reportLines.push(`# Internal Links Report -- ${today}`);
  reportLines.push('');
  reportLines.push('## Summary');
  reportLines.push(`- Pages scanned: ${results.length}`);
  reportLines.push(`- Pages modified: ${modifiedResults.length}`);
  reportLines.push(`- Links injected: ${totalLinks}`);
  reportLines.push('');

  if (modifiedResults.length > 0) {
    reportLines.push('## Changes by Page');
    for (const result of modifiedResults) {
      reportLines.push(`### ${result.relFilePath}`);
      for (const change of result.changes) {
        reportLines.push(`- "${change.keyword}" -> ${change.url}`);
      }
      reportLines.push('');
    }
  }

  if (unmatchedKeywords.length > 0) {
    reportLines.push('## Keywords Not Matched');
    for (const kw of unmatchedKeywords) {
      reportLines.push(`- "${kw}" -- no occurrences found in eligible elements`);
    }
    reportLines.push('');
  }

  const reportPath = path.join(REPORTS_DIR, 'internal-links-report.md');
  fs.writeFileSync(reportPath, reportLines.join('\n'), 'utf8');
  console.log(`\nReport written to: ${path.relative(PROJECT_ROOT, reportPath)}`);
  console.log(
    `\nDone: ${totalLinks} links injected across ${modifiedResults.length} pages`
  );
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
main();
