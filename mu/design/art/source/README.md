# Wash artwork redesign

Created for the `orchestration` branch on 2026-10-20 using the built-in
image-generation tool. The exact generation prompts are in `prompts.json`.

The six scene originals replace the old procedural blobs with wet-on-wet
landscape washes: Dawn (apricot, rose and lilac), Lagoon (sky, teal and lilac),
and Meadow (sage, sun and lagoon). Paper and Dusk were painted independently;
the Dusk scenes are not filtered Paper images. The six transparent spot
originals use the shared pigment colours and work in both themes.

## Resolution and provenance

- Scene originals: **1672 × 941**, losslessly stored as WebP.
- Transparent spot originals: **1254 × 1254**, lossless RGBA WebP.
- Hero exports: 800 × 450, 1600 × 900 and 3840 × 2160.
- Ambient exports: 960 × 600, 1920 × 1200 and 3840 × 2400.
- Spot exports: 320 × 320 and 1024 × 1024.
- Android: existing filenames and dimensions, with additional `@2x` files.

The generator returned the dimensions above despite a higher-resolution
request. The 3840px exports are explicitly **Lanczos upscales**, not native
4K generations. Enlarging them does not add captured detail. The originals
are retained so a future higher-resolution generation can be substituted.

## Re-export

Run `python3 scripts/design/paint_washes.py`. It reads these committed source
paintings, crops without stretching, preserves spot alpha, and encodes the
web, Android and visual-reference files. Ambient exports flatten a 24% painting
over the theme's canvas token so text on glass keeps its contrast; hero art
retains the full painting. Legacy hero files stay below 60 KB;
legacy ambient files stay below 80 KB. Large exports use quality 88. Web
tokens select 3840px artwork on high-density screens; Android resolves the
`@2x` companions using its normal image-density mechanism.

The old `mu/design/art/*.json` recipes remain as historical design inputs;
they no longer regenerate the replacement paintings.
