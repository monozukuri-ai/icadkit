# Native file viewer

The native viewer is available in the source checkout after 0.2.0. It renders
qualified native **boxes and cylinders**, using their saved global frames in
millimetres. It includes a part tree, stored properties, selection and visibility
controls. No preview extra, OCCT, iCAD, Wine, internet connection or schema catalog
is required. The browser needs WebGL for 3D display; without it, the part/property
inventory remains available.

From a source checkout:

```sh
uv run icadkit view model.icd
```

After installing the updated package, use `icadkit view model.icd`. The command
opens the default browser and serves only the generated viewer files on
`127.0.0.1` with an automatically selected port. Ctrl-C stops the server and
removes temporary files. Use `--no-open` to print the URL without opening a browser,
and `--port 8765` to choose a port.

To retain a viewer or export without starting a server:

```sh
icadkit view model.icd --output native-view
icadkit view model.icd --output native-view-export --write-only --json
```

The output directory must not exist. It contains `index.html`, `viewer.js`,
`viewer.css` and `scene.json`. Keep all four files together. Serve them over HTTP;
opening HTML with `file://` is not supported. There are no remote asset requests.
The exported JSON includes part names, stored text and source byte metadata;
share it only when you intend to share those attributes too.

## Controls and scope

- Drag to orbit; right-drag or Shift-drag to pan; scroll to zoom. Fit view (or F
  while the canvas has focus) fits currently visible shapes. Camera presets use
  orthographic isometric, top, front and right views with Z up.
- Select a part to inspect its stored text, external reference and part frame.
  Select an entity in the tree or click a shape to inspect dimensions, saved
  visibility, palette index, layer and source byte range. Selected shapes highlight.
- Entity checkboxes control individual shapes; part checkboxes affect their
  descendants. Show all reveals saved-hidden shapes; Saved visibility restores
  the initial flags. Unknown visibility is shown and reported as unknown.
- The viewport uses illustrative colors. Saved palette indices are shown as
  values, without interpreting them as RGB or inherited part colors. Layer and
  application view settings are not applied.

Native primitive frames are already in `3DGLOBAL`; part frames are **not applied
again**. Scene vertices are centered and uniformly normalized for WebGL float32
precision. `render_origin_mm` and `render_scale_mm` retain the reversible display
mapping; original primitive and part matrices stay in the metadata. Cylinders use
64 segments by default, adjustable with `--cylinder-segments` (8–256). This is an
approximate visualization, not a CAD measurement or B-Rep export.

Every view is labelled **partial model**. Indexed unsupported/invalid entities
remain selectable in the tree, with their original diagnostics and no invented
mesh. A model with no supported shapes opens an inventory-only view. The header
reports represented and omitted indexed entities; these counts do not account
for entities hidden inside unparsed ranges. Reader statuses, diagnostics and
unparsed ranges appear in the model properties.

Mirrors, spheres, cones, native CSG/booleans, drawings and automatic external-file
loading remain unsupported. Embedded Parasolid resources are counted but not
placed or displayed here, because their native ownership/placement is unresolved.
Use the separate [resource preview](preview.md) to view a selected supported
embedded resource. Unknown hierarchy links are retained; cycle-safe traversal
lists each part once. This does not repair or certify the hierarchy.

## Python

```python
import icadkit
from icadkit.viewer import serve_viewer, write_native_viewer

result = write_native_viewer(icadkit.read("model.icd"), "native-view")
print(result.rendered_entities, result.omitted_entities, result.scene_sha256)
with serve_viewer(result.directory) as server:
    print(server.url)
    server.open_browser()
    input("Press Enter to stop: ")
```

`ViewerResult` and `ViewerLimits` are frozen dataclasses. `write_native_viewer`
accepts `part_limits=PartLimits(...)`, `limits=ViewerLimits(...)` and
`cylinder_segments=64`. Input/index limits also remain available on `icadkit.read`.
The CLI exposes the read/part limits plus `--max-triangles` (default 1,000,000) and
`--max-output-bytes` (default 128 MiB). Limits do not cap total process/browser
memory or CPU time. Files are prepared and size-checked before the destination
is created. Publication reserves the destination and cleans up files it created
on failure; it is not a filesystem transaction.

Successful generation, including an explicitly partial or empty view, returns
CLI code 0. Invalid input/I/O returns 1, argument errors 2, unsupported input 3,
and exceeded limits 4. `--json` requires both `--write-only` and `--output`.
The result's `model_status` is always `partial`, independent of process success.
