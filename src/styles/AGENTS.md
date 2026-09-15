# Application styles

`globals.css` sets Tailwind's automatic class discovery root to `src` with
`source("../")`, resolved relative to this stylesheet. Application pages,
components, map markup, and shared class helpers live under that root. Keep
repository artifacts and service workspaces outside discovery so unrelated
files or inaccessible temporary directories cannot break CSS compilation.

When introducing a template or Tailwind component library outside `src`, add
its specific path with `@source`, relative to this stylesheet. The current
`public` directory contains a service worker and image assets, and does not
need a class source declaration. Imported dependency styles such as MapLibre
CSS and Next's generated font styles retain their own compilation paths.

See [Tailwind source detection](https://tailwindcss.com/docs/detecting-classes-in-source-files#setting-your-base-path).
