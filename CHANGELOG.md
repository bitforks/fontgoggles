# Changelog for FontGoggles

## [0.9.4] - 2020-02-24

- Implement File -> Revert
- Discovered a bug in the BiDi algorithm we use, which triggered an assert.
  Disabled the assert so we can at least see the result of the bug.
  Workaround: disable BiDi processing. See [#35](https://github.com/justvanrossum/fontgoggles/issues/35).
- Hide the compile output panel by default, but show visual feedback
  in the font list when a compile warning or error was issued, and show
  the compile output pane automatically when a font item is selected
  that has a warning or error.
- Fixed issue with dragging multiple fonts: the selection was reset to
  a single item, making it impossible to drag multiple fonts.
- Fixed issue where you couldn't select glyphs by clicking outside the
  glyphs.
- Make app icon work better at small sizes.

## [0.9.3] - 2020-02-21

- Save all text settings and many UI settings to the project file.
- When performing undo/redo in the font list, also take the selection
  into account.

## [0.9.2] - 2020-02-19

- Fixed drag and drop bug on macOS 10.10

## [0.9.1] - 2020-02-19

- Reordering of fonts in the font list is now possible through drag and
  drop. One can also drag fonts to other FontGoggles windows, and to
  other applications.
- Implement copying selected data from the character list and the glyph
  list to the clipboard. Selecting a whole column is also implemented.
  The result is tab-separated text, so it can be pasted straight into a
  spreadsheet.
- Renamed “Size” slider to “Relative Size”, to make it clearer this is
  about the size relative to the font list item. Normal zooming is done
  with pinch gestures, command minus and plus, and option scroll.
- Misc copyright updates.

## [0.9.0] - 2020-02-17

First public release.
