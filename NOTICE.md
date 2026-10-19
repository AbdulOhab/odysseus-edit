# Notice

Odysseus-Edit is an **unofficial**, standalone image editor extracted from
[Odysseus](https://github.com/odysseus-dev/odysseus). It is not affiliated with
or endorsed by the Odysseus project.

## Upstream

| | |
|---|---|
| Project | Odysseus, a self-hosted AI workspace |
| Repository | https://github.com/odysseus-dev/odysseus |
| Branch | `dev` |
| Commit | `9712a5400175f8beacb215cefed1dad6935627df` (2026-10-16) |
| License | AGPL-3.0-or-later |

The first commit in this repository contains the upstream files unmodified, so
`git diff <first-commit> -- web/js` shows every change made here.

## Files taken from upstream

Mirrored from `odysseus/static/` at the same relative paths:

- `web/js/galleryEditor.js`
- `web/js/editor/` (the whole directory)
- `web/js/spinner.js`, `colorPicker.js`, `dragSort.js`, `storage.js`,
  `platform.js`, `modelSort.js`, `toolWindowZOrder.js`
- `web/css/00-tokens.css`, `web/css/13-image-editor.css`

Backend code under `server/` is adapted from
`odysseus/routes/gallery/gallery_routes.py`.

Files under `web/app/` and the shims `web/js/ui.js` and
`web/js/modalManager.js` are new to this project.

## License

Odysseus-Edit is distributed under the GNU Affero General Public License,
version 3 or later, the same license as upstream. See [LICENSE](LICENSE).
If you run a modified version as a network service, you must offer its
complete source code to the users of that service (AGPL section 13).
