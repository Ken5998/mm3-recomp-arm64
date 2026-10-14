# Game files

This project contains no part of Midtown Madness 3, and its releases contain
none either. The launcher builds the game on the player's own handheld, from a
disc image of the game the player owns; it reads that image, copies the game's
files into the player's data folder (`~/.local/share/MidtownMadness3`) and
translates the game's code there.

Never commit or publish:

- disc images (`.iso`, `.xiso`) or the files copied from them (`game_files/`,
  `default.xbe` and the game's data),
- the translated code (`src/recomp/gen/`) or anything compiled from it, such
  as `mm3_recomp`,
- artwork drawn from the game: the launcher draws the Steam artwork on the
  handheld from the player's own disc.

The continuous integration builds the runtime, its tests and the launcher
without any game files.
