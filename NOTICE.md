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

Backend files adapted from `odysseus/routes/gallery/gallery_routes.py` and
`odysseus/src/url_safety.py` carry an "Adapted from Odysseus" header:
`server/app/imaging.py`, `server/app/url_safety.py`,
`server/app/routers/image_local.py`, `server/app/routers/image_ai.py`,
`server/app/services/local_tools.py`, `server/app/services/image_vendors.py`.

New to this project: `web/app/`, the shims `web/js/ui.js` and
`web/js/modalManager.js`, `tools/`, and the rest of `server/` (config,
middleware, the multi-vendor LLM client `llm.py`, the Quick Edit planner).

Fira Code (`web/fonts/`) is by Nikita Prokopov, SIL Open Font License 1.1
([licenses/FiraCode-OFL-1.1.txt](licenses/FiraCode-OFL-1.1.txt)).

## License

Odysseus-Edit is distributed under the GNU Affero General Public License,
version 3 or later, the same license as upstream. See [LICENSE](LICENSE).
If you run a modified version as a network service, you must offer its
complete source code to the users of that service (AGPL section 13).
