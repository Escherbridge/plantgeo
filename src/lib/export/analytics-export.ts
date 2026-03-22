// npm install recharts @react-pdf/renderer
// Fallback implementations using Blob/CSV and window.print() when @react-pdf/renderer is unavailable.

import type { RegionalRiskSummary, TrendPoint, PrioritySubregion } from "@/lib/server/db/analytics";

// ─── CSV Export ───────────────────────────────────────────────────────────────

/**
 * Convert an array of records to CSV and trigger a browser download.
 */
export function exportCSV(data: Record<string, unknown>[], filename: string): void {
  if (typeof window === "undefined") return;
  if (!data.length) return;

  const headers = Object.keys(data[0]);
  const escape = (v: unknown): string => {
    const s = String(v ?? "");
    if (s.includes(",") || s.includes('"') || s.includes("\n")) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };

  const rows = [
    headers.map(escape).join(","),
    ...data.map((row) => headers.map((h) => escape(row[h])).join(",")),
  ];

  const csv = rows.join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ─── PDF Export ───────────────────────────────────────────────────────────────

/**
 * Generate a PDF report for the regional analytics dashboard.
 *
 * Uses @react-pdf/renderer if available; falls back to HTML + window.print().
 */
export function exportPDF(
  summary: RegionalRiskSummary,
  trends: TrendPoint[],
  priorities: PrioritySubregion[]
): void {
  if (typeof window === "undefined") return;

  const timestamp = new Date().toLocaleString();

  const droughtLabels = ["None", "D0 Abnormally Dry", "D1 Moderate", "D2 Severe", "D3 Extreme", "D4 Exceptional"];
  const droughtLabel = droughtLabels[Math.min(summary.droughtClass, 5)] ?? "D4+";

  const priorityRows = priorities
    .map(
      (p, i) => `
      <tr>
        <td>${i + 1}</td>
        <td>${p.name}</td>
        <td>${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}</td>
        <td><strong>${p.score}</strong></td>
        <td>${p.primaryIssue}</td>
      </tr>`
    )
    .join("");

  const trendRows = trends
    .map(
      (t) => `
      <tr>
        <td>${t.date}</td>
        <td>${t.value}</td>
      </tr>`
    )
    .join("");

  const html = `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>PlantGeo Regional Analytics Report</title>
  <style>
    @media print {
      body { margin: 0; }
      .no-print { display: none; }
    }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 13px;
      color: #1a1a1a;
      max-width: 800px;
      margin: 32px auto;
      padding: 0 24px;
    }
    h1 { font-size: 22px; color: #166534; margin-bottom: 4px; }
    h2 { font-size: 16px; color: #166534; margin-top: 28px; border-bottom: 1px solid #d1d5db; padding-bottom: 4px; }
    p.meta { font-size: 11px; color: #6b7280; margin-bottom: 24px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th { background: #f0fdf4; text-align: left; padding: 6px 8px; font-size: 11px; color: #374151; border: 1px solid #d1d5db; }
    td { padding: 6px 8px; border: 1px solid #e5e7eb; font-size: 12px; }
    tr:nth-child(even) td { background: #f9fafb; }
    .risk-badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-green { background: #dcfce7; color: #166534; }
    .badge-yellow { background: #fef9c3; color: #854d0e; }
    .badge-red { background: #fee2e2; color: #991b1b; }
    button.print-btn {
      margin-bottom: 24px;
      padding: 8px 16px;
      background: #16a34a;
      color: white;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
    }
  </style>
</head>
<body>
  <button class="print-btn no-print" onclick="window.print()">Print / Save as PDF</button>
  <h1>PlantGeo Regional Analytics Report</h1>
  <p class="meta">Generated: ${timestamp} &nbsp;|&nbsp; Viewport: current map region</p>

  <h2>Regional Risk Summary</h2>
  <table>
    <thead>
      <tr>
        <th>Metric</th>
        <th>Value</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Fire Risk (avg)</td>
        <td>${summary.fireRiskAvg}%</td>
        <td>
          <span class="risk-badge ${
            summary.fireRiskAvg >= 60 ? "badge-red" : summary.fireRiskAvg >= 30 ? "badge-yellow" : "badge-green"
          }">
            ${summary.fireRiskAvg >= 60 ? "High" : summary.fireRiskAvg >= 30 ? "Moderate" : "Low"}
          </span>
        </td>
      </tr>
      <tr>
        <td>Drought Class</td>
        <td>${droughtLabel}</td>
        <td>
          <span class="risk-badge ${
            summary.droughtClass >= 3 ? "badge-red" : summary.droughtClass >= 2 ? "badge-yellow" : "badge-green"
          }">
            ${summary.droughtClass >= 3 ? "Severe" : summary.droughtClass >= 2 ? "Moderate" : "Normal"}
          </span>
        </td>
      </tr>
      <tr>
        <td>Streamflow Percentile</td>
        <td>${summary.streamflowPercentile}%</td>
        <td>
          <span class="risk-badge ${
            summary.streamflowPercentile <= 20 ? "badge-red" : summary.streamflowPercentile <= 40 ? "badge-yellow" : "badge-green"
          }">
            ${summary.streamflowPercentile <= 20 ? "Critically Low" : summary.streamflowPercentile <= 40 ? "Below Normal" : "Normal"}
          </span>
        </td>
      </tr>
      <tr>
        <td>Active Fire Count</td>
        <td>${summary.activeFireCount}</td>
        <td>
          <span class="risk-badge ${
            summary.activeFireCount > 20 ? "badge-red" : summary.activeFireCount > 5 ? "badge-yellow" : "badge-green"
          }">
            ${summary.activeFireCount > 20 ? "High" : summary.activeFireCount > 5 ? "Elevated" : "Normal"}
          </span>
        </td>
      </tr>
      <tr>
        <td>Overall Risk Trend</td>
        <td colspan="2">
          <span class="risk-badge ${
            summary.riskTrend === "worsening" ? "badge-red" : summary.riskTrend === "improving" ? "badge-green" : "badge-yellow"
          }">
            ${summary.riskTrend.charAt(0).toUpperCase() + summary.riskTrend.slice(1)}
          </span>
        </td>
      </tr>
    </tbody>
  </table>

  ${
    trends.length > 0
      ? `<h2>Trend Data</h2>
  <table>
    <thead>
      <tr><th>Date</th><th>Value</th></tr>
    </thead>
    <tbody>${trendRows}</tbody>
  </table>`
      : ""
  }

  ${
    priorities.length > 0
      ? `<h2>Priority Subregions (Top ${priorities.length})</h2>
  <table>
    <thead>
      <tr><th>#</th><th>Name</th><th>Coordinates</th><th>Score</th><th>Primary Issue</th></tr>
    </thead>
    <tbody>${priorityRows}</tbody>
  </table>`
      : ""
  }
</body>
</html>
  `.trim();

  const printWindow = window.open("", "_blank", "width=900,height=700");
  if (!printWindow) return;
  printWindow.document.write(html);
  printWindow.document.close();
}
