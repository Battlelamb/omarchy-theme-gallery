# Theme Gallery backend — v0.3.0

## Exact CLI

Run from the plugin checkout (the backend itself does not depend on cwd):

```sh
/usr/bin/python3 runtime/0.3.0/backend.py catalog
/usr/bin/python3 runtime/0.3.0/backend.py preferences
/usr/bin/python3 runtime/0.3.0/backend.py preferences --apply --payload '{"options":{"appearance":"dark","favoritesOnly":false}}'
/usr/bin/python3 runtime/0.3.0/backend.py action --id theme:INSTALLED-SLUG --verb favorite --apply
/usr/bin/python3 runtime/0.3.0/backend.py action --id theme:INSTALLED-SLUG --verb capture --apply --payload '{"name":"Reading","wallpaper":"/absolute/supported/local/image.png"}'
/usr/bin/python3 runtime/0.3.0/backend.py action --id ID-FROM-CATALOG --verb apply --apply
/usr/bin/python3 runtime/0.3.0/backend.py action --id restore:last --verb restore --apply
/usr/bin/python3 runtime/0.3.0/backend.py preferences --apply --payload '{"reset":true}'
```

`INSTALLED-SLUG`, the wallpaper path and `ID-FROM-CATALOG` are placeholders, not literal valid selections. `capture` accepts exactly `name` and optional `wallpaper`; omitting wallpaper uses the validated current-background link target. It requires an installed `theme:` row, not another combination. A name is nonempty/control-free, ≤64 characters, and cannot replace an existing named collection. Paths are ≤251 characters. Capture saves preferences only and never invokes setters. It inserts its named record using the preference store’s locked targeted merge with an expected-absent check inside the same lock. Other collections acknowledged concurrently through the preferences CLI survive; a concurrent same-name save wins admission and the stale Capture refuses without changing stored bytes. Other verbs accept **no payload**.

Only `catalog`, `preferences`, `action` are commands. Unknown/abbreviated options are refused. Catalog takes no options. Preference reads take no action options or `--apply`; writes require both `--apply` and `--payload`. Actions need explicit `--id`, `--verb`, `--apply`; omission never performs work. `--payload` must be one JSON object, ≤16384 UTF-8 bytes; duplicate keys, nonfinite constants, invalid types, unknown keys/options and excessive nesting fail closed. Each response is one JSON object; usable catalog/read or confirmed writes exit0, errors exit nonzero without a traceback. CLI output is capped at 1 MiB.

## Preferences and UI protocol

`PREFERENCE_SCHEMA`:

| Key | Type | Default | Allowed values |
| --- | --- | --- | --- |
| `appearance` | enum | `all` | `all`, `light`, `dark` |
| `favoritesOnly` | boolean | false | JSON boolean |

Preference responses are `{ok,preferences,settingsSchema,message}`. The preference document is `{version:1,favorites:[],recent:[],collections:{},options:{...}}`. Patches accept only `options`, `favorites`, `collections`; recent/version are backend-owned. `{"reset":true}` must be the sole key and resets that preference document, not effects/recovery state. The local sibling `preferences.py` supplies bounded validated reads, private storage, locking and atomic merge/replace. Absolute `XDG_CONFIG_HOME` is preferred, otherwise `$HOME/.config`; path suffix `omarchy/lego/theme-gallery/preferences.json`. Invalid existing data is never silently overwritten.

Combination records occupy `collections[name] = ["theme:<slug>", "wall:<absolute-path>", "sha256:<64-lowercase-hex>"]`. Other inert collection records do not become action authority. Catalog IDs are `theme:<slug>` and `pair:<first32-SHA256(name)>`; pair actions freshly revalidate the stored structure, installed theme, safe source and captured wallpaper digest. Edit/delete combinations via an explicit collections replacement; Reset also removes them.

Catalog retains baseline row fields and adds `favorite`, `category` (`light`/`dark`/`unknown`), `swatches` (up to12 validated hex colors); combination rows add `collection:"Saved combinations"` and local `preview`. Background relative luminance >0.179 is classified light; missing background stays unknown. Stock colors are overlaid by valid user colors; accent and preview behavior remains compatible. Filtering precedes the 256-row display cap. Favorites still require fresh installed/pair membership independent of filters/cap. Primary action is `apply`; secondary fixed verbs are `favorite`, `capture`, and conditional `restore`. Successful Apply updates recent only after confirmation; preference-storage failure becomes a truthful warning, not a fabricated setter failure.

New preference writes are preflighted under the store lock against an aggregate 128 KiB compact UTF-8 document budget before replacement. Individual list/text maxima do not imply their Cartesian maximum is supported. Legacy valid stores remain bounded to 16 MiB for reads and explicit Reset/recovery; a rejected write leaves the existing file unchanged. Replies use the common UTF-8 encoder below 1 MiB (including the newline, at most 1 MiB); the UI action collector/parser accepts up to and including 1 MiB of wire data. Oversized optional preference snapshots on successful commands are omitted with a warning, preserving `ok:true`; actual catalogue overflow still fails closed, without truncating saved data. A Recent-save failure likewise warns without reversing confirmed helper/export acceptance.

## Apply, snapshots and compensating Restore

Existing theme admission and source-signature guards are retained: installed stock/user slug membership, no symlink-shadowed user overlay, bounded directory recursion and total background hashing. `omarchy-theme-set` runs with the fixed installed Omarchy root, normal hooks/backgrounds, waiting enabled and no headless/offline/skip-background override. No config-file editing, template evaluation, installation or download is performed. Success requires a changed nonempty generation, exact target-name readback, stable source signatures, exit0 and no stderr warnings. Providers that changed state but warned still return failure.

A combination freezes the selected wallpaper and a safely admitted current wallpaper before running hooks. If the current wallpaper cannot be safely snapshotted, pair Apply refuses before the setter. Safe image roots are Pictures/Wallpapers and Omarchy user backgrounds (direct plus one collection level), installed-theme backgrounds, and staged current-theme backgrounds. The one deliberate symlink reader is Omarchy's known current-background link; its target is separately checked. File/ancestor symlinks and traversal/special files remain forbidden. An existing Theme Gallery-owned `image-<sha256>.<type>` snapshot is also admitted only after bounded no-follow hash validation; Capture rechecks the bytes it saves against that name. This permits name-only Capture after pair Apply, not arbitrary-path or cross-plugin snapshot admission. Images are ≤32 MiB. Snapshots are content-addressed in `$HOME/.local/state/omarchy-theme-gallery/`, ≤16 files / 256 MiB total, with no automatic pruning. Captured source changes refuse Apply; frozen bytes, not a mutable library pathname, reach the setter.

After normal theme confirmation, pair Apply requires shell ping, persistent `omarchy-theme-bg-set` readback, and acknowledged non-quiet `omarchy-shell background set`. It does not claim rendered pixels. Normal theme provider budget is22 seconds; combination theme budget20, followed by1/2/2-second wallpaper-helper budgets. Preference lock waits are bounded separately.

Restore accepts the original receipt selection or `restore:last`, even if the applied theme row disappeared. Another selected row is not Restore authority. Current theme name/generation must match the owned receipt. Pair Restore also requires matching owned snapshots and no unrelated wallpaper selection. A pending receipt preserves the post-hook observed wallpaper and intended target so an unacknowledged wallpaper dispatch can be compensated without accepting arbitrary divergence. On success it reapplies the prior installed theme through normal hooks, reapplies the previous verified wallpaper for a pair, and clears the one-level receipt. It is **not a full rollback** of hooks/app retints. Restore itself can fail partially; a changed generation prevents blindly repeating it. No independent automatic rollback daemon is claimed.

## Bounds and verification

Directory scans cap1024 entries (overfull directories skipped). Theme signatures cap2048 entries, depth5,32 MiB/file and128 MiB total direct-background hashing across stock/overlay. Native hard-link/unlink ctime-only snapshot bookkeeping is tolerated only for direct still backgrounds via content hashes; preserved-mtime edits, type/mode/owner changes and FD-read races remain checked. No global dropping of ctime guards.

All subprocesses use literal argv, bounded stdout/stderr (64 KiB each), hard deadlines and process-group cleanup, including pipe-holding descendants. State uses safe directory FDs, a private exclusive nonblocking regular-file lock and atomic/fsynced receipts. These are not isolation from malicious same-UID processes, escaped process sessions or arbitrary hook side effects.

Run only the shipped offline runner, with `TMPDIR` already naming an existing private scratch directory:

```sh
env -u PYTHONPATH /usr/bin/python3 scripts/run-tests.py --backend-only
```

It uses offline bwrap, cleared HOME/env, masked production setters, no live sockets and controlled fixture providers. Feature and regression RED/GREEN receipts are in ignored `release-output/v030-tdd/`; exact final commands/counts/hashes and outstanding integration criteria are in `release-output/v030-result.json`. These are local backend results, not hosted CI, native pixel verification, installation or publication. Provenance: NOTICE.backend.md and `runtime/0.3.0/data/source-ledger.json`.
