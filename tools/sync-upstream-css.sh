#!/usr/bin/env bash
# Regenerate web/css/upstream-pre.css and upstream-post.css from an Odysseus
# checkout: the rules from Odysseus' app-wide stylesheets that the editor's
# markup actually uses. 00-tokens.css and 13-image-editor.css are copied whole.
#
# usage: tools/sync-upstream-css.sh /path/to/odysseus
set -euo pipefail
UP="${1:?usage: $0 /path/to/odysseus}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CSS="$UP/static/css"
# Odysseus areas whose class names happen to appear in the editor's JS.
DENY=(sidebar msg research-overlay cookbook-modal tool-cookbook-btn rail-cookbook
      cookbook-tab message memory-cat-chip notes-pane-backdrop session-dropdown-menu
      serve-tags editing tour-hint tour-hint-dismiss section grow thumb toggle)
PRE=(01-agent-chat 02-compare 03-agent-chat 04-memory 05-documents 06-admin-settings
     07-documents 08-skills 09-gallery 10-cookbook 11-tasks 12-gallery)
# Odysseus' light theme greys every <button> with `:root.light button`
# (specificity 0,2,1), which beats the editor's own button colours: the Save
# and Quick Edit send buttons turn grey, icon buttons get grey boxes.
DROP=(":root.light button")
POST=(14-email 15-notes 16-calendar 17-research documents-gallery-editor
      email-calendar-notes-tasks cookbook-research-memory-settings)
run() {
  local out="$1"; shift
  local files=()
  for f in "$@"; do files+=("$CSS/$f.css"); done
  python3 -I "$ROOT/tools/extract_css.py" --js "$ROOT/web/js" --deny "${DENY[@]}" --drop-selector "${DROP[@]}" --out "$out" "${files[@]}"
}
run "$ROOT/web/css/upstream-pre.css" "${PRE[@]}"
run "$ROOT/web/css/upstream-post.css" "${POST[@]}"
echo "Regenerated web/css/upstream-pre.css and web/css/upstream-post.css"
