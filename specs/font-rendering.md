# MSYS font rendering baseline

This document defines a small visual baseline, not a new resident service,
role, HAL device, or application framework. Applications remain free to use
native C/C++, Tk, Qt, Electron, or another toolkit.

## Ownership boundary

- A display-output provider transports completed pixels and input events. It
  does not install, convert, select, or rasterize application fonts.
- Each GUI runtime owns its text layout and rasterization, while the system
  release owns the compatible shared runtime and its default font policy.
- `MSYS_UI_FONT_FAMILY` is the optional cross-toolkit family preference.
  Applications may choose another family for a deliberate design need. The
  legacy `MSYS_TK_FONT_FAMILY` name is a compatibility fallback, not a second
  policy.
- A font package or rendering helper is not HAL. No font daemon is required.

## X11 baseline

The normal lightweight X11 path is:

```text
Fontconfig -> FreeType -> Xft -> XRender -> X11 drawable
```

Fontconfig selects installed faces and language fallback. FreeType rasterizes
outline glyphs and applies hinting. Xft bridges those operations to X11 and
XRender composites the anti-aliased glyph masks.

Use explicit pixel sizes for small fixed-density panels. Grayscale
anti-aliasing with hinting is the portable default because an SPI panel may be
rotated and its RGB/BGR subpixel order may be unknown. Subpixel rendering may
be enabled only when the output provider exposes a stable physical orientation
and the result has been checked on the actual panel.

The 320x480 reference profile uses a 96-DPI logical scale converted once to
toolkit pixel sizes: 12 px minimum for secondary text, about 14 px for body and
controls, 16 px bold for section headings, and 20 px bold for page titles. Tk
expresses these as negative sizes; Qt uses `setPixelSize`. Do not use global
`tk scaling` to compensate for an X server with missing physical dimensions.
The rotated ST7796 profile uses grayscale AA (`rgba=none`) even though the
panel controller itself is configured BGR.

HarfBuzz plus Pango is an optional higher-level path for Arabic, Indic scripts,
advanced bidirectional text, rich line breaking, and other complex shaping. It
is not required merely to display Chinese and Latin UI labels. SDF/MSDF glyph
atlases are also not the default for small CJK interface text: their atlas and
shader complexity do not replace correct small-size hinting.

## Unsupported primary paths

X11 Core Fonts and BDF/PCF bitmap conversions are not an acceptable primary UI
backend. They lack the required scalable anti-aliasing and can make an outline
font name appear selectable while the toolkit still renders pixels or tofu.
They may remain an emergency ASCII diagnostic fallback only.

Renaming, extracting, or converting a TTC does not prove that a toolkit uses
the outline face. Enumerating a family is also insufficient. The selected
runtime must prove positive CJK glyph advances and a live outline backend.

## Framework guidance

- Native X11 components use Xft directly and keep Xlib Core Fonts as the final
  diagnostic fallback only.
- Tk applications require an Xft-enabled Tcl/Tk built and released together
  with the matching isolated Python and `_tkinter`. A standalone replacement
  `_tkinter` or Tk shared object is not an ABI-safe update.
- Qt and Electron use their normal Fontconfig/FreeType-backed Linux stacks and
  should honor `MSYS_UI_FONT_FAMILY` when selecting the application default.
  The SPI reference profile uses Qt `NoSubpixelAntialias` and Chromium's
  `disable-lcd-text` switch; HDMI profiles may opt into verified panel-specific
  subpixel rendering later.
- A short-lived Xft-to-image helper may reduce persistent memory for static or
  custom-drawn text. It does not repair native Entry/Text/Treeview caret,
  selection, hit-testing, scrolling, accessibility, or IME preedit behavior.

## Release gate

A GUI runtime candidate is acceptable only when all of the following pass on
the target display before the system release is activated:

1. The requested CJK family does not resolve to `fixed` and every sample glyph
   has a positive advance.
2. Label, Button, Entry, Text, Treeview, and the touch input method render real
   Chinese text; editable controls retain cursor, selection, and preedit
   behavior.
3. The live process proves Xft/Fontconfig/FreeType use, so a renamed BDF cannot
   pass by family name alone.
4. Screenshots are checked at native 320x480 resolution, including normal,
   disabled, selected, and focused text.
5. Cold start, steady PSS, and an application-plus-input-method case fit the
   target memory budget.
6. The complete Python/Tcl/Tk/font runtime is included in the system release
   hash and uses the existing health-gated atomic switch and rollback path.

The workstation command `msys-dev font-doctor --python <candidate-python>`
implements the non-interactive portion of this gate without writing bytecode
into the candidate release.
