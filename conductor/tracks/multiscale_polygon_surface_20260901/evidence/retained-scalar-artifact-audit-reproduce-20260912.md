---
type: reproduction-recipe
status: retained-artifact-audit-only
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
---

# Recheck the retained scalar PNG evidence

This recipe reads the previously committed 26 PNGs and report; it does not run
React, MapLibre, a browser, a data service or a new capture. Node built-ins decode
8-bit, non-interlaced RGB/RGBA PNG scanlines and recompute the same RGB thresholds
and pixel regions as the original `pixels.mjs`. SHA-256 records bind the exact
input files. The decoder rejects unsupported image formats; it does not validate
PNG chunk CRCs. The two interior seam rows retain their original limited scope.

Save the fenced script as `.omc/research/renderer-receipt-audit.mjs` from the
repository root, then run `node .omc/research/renderer-receipt-audit.mjs` against
the source commit above or an explicitly reconciled successor. It rewrites only
`evidence/retained-scalar-artifact-audit-20260912.json` in this track, updating its
audit timestamp. Review the resulting diff before retaining another receipt.
No dependencies, network, display server or package installation are needed.
The source commit/tree literals bind this original audit; update both explicitly
before using the script to describe a different source revision.

```javascript
import { readFileSync, writeFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';
import { createHash } from 'node:crypto';

const directory = 'conductor/tracks/multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912/';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const reportBytes = readFileSync(directory + 'canvas-report.json');
const report = JSON.parse(reportBytes);
const paeth = (a, b, c) => {
  const p = a + b - c;
  const distances = [Math.abs(p-a), Math.abs(p-b), Math.abs(p-c)];
  return distances[0] <= distances[1] && distances[0] <= distances[2] ? a : distances[1] <= distances[2] ? b : c;
};
function decode(bytes) {
  if (!bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) throw new Error('Bad PNG signature');
  let width, height, channels;
  const parts = [];
  for (let offset = 8; offset < bytes.length;) {
    const length = bytes.readUInt32BE(offset);
    const name = bytes.toString('ascii', offset+4, offset+8);
    const chunk = bytes.subarray(offset+8, offset+8+length);
    if (name === 'IHDR') {
      width = chunk.readUInt32BE(0); height = chunk.readUInt32BE(4);
      if (chunk[8] !== 8 || ![2,6].includes(chunk[9]) || chunk[10] !== 0 || chunk[11] !== 0 || chunk[12] !== 0) throw new Error('Unsupported PNG encoding');
      channels = chunk[9] === 2 ? 3 : 4;
    }
    if (name === 'IDAT') parts.push(chunk);
    offset += length + 12;
  }
  const raw = inflateSync(Buffer.concat(parts));
  const stride = width * channels;
  if (raw.length !== (stride+1)*height) throw new Error('Unexpected image length');
  const pixels = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y*(stride+1)];
    if (filter > 4) throw new Error('Invalid filter');
    for (let x = 0; x < stride; x++) {
      const at = y*stride+x;
      const left = x >= channels ? pixels[at-channels] : 0;
      const up = y > 0 ? pixels[at-stride] : 0;
      const upperLeft = y > 0 && x >= channels ? pixels[at-stride-channels] : 0;
      const prediction = [0, left, up, Math.floor((left+up)/2), paeth(left,up,upperLeft)][filter];
      pixels[at] = (raw[y*(stride+1)+x+1]+prediction) & 255;
    }
  }
  return {width,height,channels,pixels};
}
const screenshots = report.map(row => {
  const bytes = readFileSync(directory + row.screenshot);
  const {width,height,channels,pixels} = decode(bytes);
  if (width !== row.viewport.width || height !== row.viewport.height) throw new Error('Viewport mismatch');
  let bright = 0, background = 0, seamBackground = 0;
  const isBackground = at => pixels[at] === 21 && pixels[at+1] === 34 && pixels[at+2] === 43;
  for (let at = 60 * width * channels; at < pixels.length; at += channels) {
    if (pixels[at] > 220 && pixels[at+1] > 220 && pixels[at+2] > 220) bright++;
    if (isBackground(at)) background++;
  }
  for (let x = row.seamProbe.start; x <= row.seamProbe.end; x++) {
    if (isBackground((row.seamProbe.row*width+x)*channels)) seamBackground++;
  }
  const matches = bright === row.screenshotBrightPixels && background === row.screenshotBackgroundPixels && (height-60)*width === row.screenshotSampledPixels && seamBackground === row.seamProbe.backgroundPixels;
  return {file:row.screenshot,sha256:digest(bytes),width,height,brightPixels:bright,backgroundPixels:background,sampledPixels:(height-60)*width,seamProbe:{...row.seamProbe,backgroundPixels:seamBackground},matchesRetainedReport:matches};
});
const sourcePaths = ['src/lib/map/measured-value-label.ts','src/components/map/layers/ClimateFieldLayer.tsx','src/components/map/layers/SoilFieldLayer.tsx'];
const output = {
  type:'retained-artifact-audit',auditedAt:new Date().toISOString(),sourceCommit:'843b4b313e03447594b23a67f75c3062b2b1a024',sourceTree:'9533bb9e5423240630935df0cd012cd8ead15504',
  method:'Offline PNG inflate/filter reversal with Node built-ins; RGB thresholds and regions match retained pixels.mjs. No browser, service or renderer execution. PNG CRCs are not independently validated.',
  report:{path:directory+'canvas-report.json',sha256:digest(reportBytes),scenarioCount:report.length,recordedMapErrors:report.reduce((n,r)=>n+r.errors.length,0),recordedPageErrors:report.reduce((n,r)=>n+r.pageErrors.length,0),recordedSettleMs:[Math.min(...report.map(r=>r.renderAndSettleMs)),Math.max(...report.map(r=>r.renderAndSettleMs))]},
  recipe:{path:directory+'reproduce.md',sha256:digest(readFileSync(directory+'reproduce.md'))},
  capturedSourceHashes:sourcePaths.map(path=>({path,sha256:digest(readFileSync(path))})),
  screenshots,allPixelFieldsMatch:screenshots.every(s=>s.matchesRetainedReport)
};
writeFileSync('conductor/tracks/multiscale_polygon_surface_20260901/evidence/retained-scalar-artifact-audit-20260912.json',JSON.stringify(output,null,2)+'\n');
console.log(JSON.stringify({scenarioCount:report.length,allPixelFieldsMatch:output.allPixelFieldsMatch,reportSha256:output.report.sha256,recipeSha256:output.recipe.sha256,recordedSettleMs:output.report.recordedSettleMs}));
if (!output.allPixelFieldsMatch) process.exitCode = 1;

```
