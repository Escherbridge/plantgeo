## Kubric meta-skill structure + deterministic primitive set
*2026-08-11 03:20:24* **Tags:** #kubric #video-production #tooling

Kubric is now the explicit meta-skill: viral-production and creative-studio-lead carry "Meta-skill inheritance" pointers deferring to kubric's field notes. New primitives in ~/.pi/agent/bin: genimage.mjs (Gemini image fallback), svgshot.mjs (SVG→PNG), vidpoll.mjs (durable authed video download + JSON sidecar), musicbed.mjs (procedural drone/chime/boom music bed via lavfi), vidgrade.mjs (grain/vignette/grade/mux finishing pass). textpass.mjs pattern (production/plantgeo-quest/tools/) is the deterministic glow-text renderer. Known-failures table now lives in kubric SKILL.md: generate_image 404s, poller silent 401, critique_video vision auth, mixed-fps concat collapse, glow blur.

---
## Kubric: concat normalization rule + glow blur fix
*2026-08-11 02:47:14* **Tags:** #kubric #video-production #tooling

More kubric pipeline fixes (2026-08): (1) NEVER concat mixed-fps clips with a filter — normalize each clip first (fps=30, scale/crop 1920:1080, libx264, -video_track_timescale 15360) then concat -c copy; direct concat collapsed an 8s clip to ~1.5s. (2) text-overlay glow bug: gblur was applied to a full opaque frame copy, blurring the entire video. Fixed in ~/.pi/agent/lib/text-overlay-filter.ts by zeroing the split copy's alpha (colorchannelmixer=aa=0) BEFORE drawtext+gblur. Validated pattern lives in production/plantgeo-quest/tools/textpass.mjs. Note: in-session extension tools use code loaded at startup — TS edits need a pi restart to take effect.

---
## Kubric deterministic pipeline — genimage/svgshot/zoompan fallbacks
*2026-08-11 01:55:16* **Tags:** #kubric #video-production #tooling

Video production pipeline learnings (2026-08): (1) image-gen tool models recraft-v4.1/flux-pro/sd3.5-large fail on the OpenRouter account — use `~/.pi/agent/bin/genimage.mjs` (Gemini image fallback chain; returns square images, crop to 16:9 in ffmpeg). (2) SVG/HTML cards rasterize via `~/.pi/agent/bin/svgshot.mjs` (headless Chrome, needs --user-data-dir). (3) Card clips via ffmpeg zoompan (scale 10% overscan first). (4) Submit all generate_video calls in one parallel block, poll list_video_tasks while prepping edits. All documented in kubric SKILL.md "Field notes: deterministic pipeline".

---
