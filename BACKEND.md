# Theme Gallery backend

## Operations and providers

Reads `/usr/share/omarchy/themes/<slug>` and `$HOME/.config/omarchy/themes/<slug>`; merges stock then user overlay by canonical slug, never by normalized display labels. Slugs are restricted to `[a-z0-9][a-z0-9-]{0,63}` because the stock setter normalizes names. A symlinked/non-directory user shadow blocks that stock slug too. Current state comes from `$HOME/.local/state/omarchy/current/{theme.name,theme.generation}`. Previews prefer user preview.png/jpg/jpeg/webp/bmp, then that layer's first safe background, with stock fallback; colors.toml accent is bounded TOML and a validated hex color. Invalid metadata does not crash browsing.

Primary `apply` calls existing `omarchy-theme-set <canonical-slug>` with OMARCHY_PATH fixed to the installed tree, OMARCHY_THEME_WAIT=1, offline/headless flags disabled and skip-background unset. This is the underlying existing `omarchy theme set` provider, not a new theming engine. The selected trees are checked for symlinks/special files and bounded to 2048 visible entries, depth 5 and 32 MiB/file. Their metadata signature is rechecked after application. Success requires zero exit, no stderr warnings, matching theme.name and a newly written theme.generation. A no-op exit0 is not confirmation.

**Provider deadline is 22 seconds, leaving headroom inside the parent's 30-second action watchdog.** Slow retints/hooks can therefore time out and report partial state rather than fake completion. A timeout/nonzero/warning reports possible partial state, not success. Stderr warnings remain visible as an actionable partial-retint warning without leaking arbitrary hook output.

## Restore and limits

One-level `restore` reapplies the previous still-installed canonical theme through the same provider. The durable receipt stores the original selected ID, old name, applied name and generation. Restore refuses external theme/generation changes and revalidates the old theme tree. After confirmed restore the receipt is removed. If no known previous theme exists, Restore is not advertised.

This is a **compensating theme selection, not generic rollback**: user theme-set hooks, generated configs, application retints, keyboard/browser policies and Omarchy's machine mirroring can have broad effects. Some installed hooks suppress their failures. Silent downstream failures and pixel presentation are not provable from name/generation alone. Source-tree races are detected before/after, not eliminated inside the existing setter; use only trusted installed themes and do not edit their trees while applying. No hooks/config files are executed directly by this backend and nothing is applied on hover/selection/catalog.

## Verified result

29 unittest cases passed in the final masked offline sandbox. The read-only real-stock probe found 24 theme rows, with 23 local previews and 23 validated accent colors (one installed theme lacked safe metadata). Tests include stock/user deduplication, local current/overlay previews, missing/deleted themes, unsafe trees, fresh full membership beyond display cap, argv, real CLI round-trip via fixture setter, partial warnings, generation confirmation and refusal of external-change restore.

## Contract and safety

System Python 3.11+ / standard library only; tested with installed Python 3.14.8. No cross-plugin imports, new packages, framework, daemon, network, installer, or autostart. Runtime paths do not depend on cwd. The UI calls:

```
/usr/bin/python3 runtime/0.1.0/backend.py catalog
/usr/bin/python3 runtime/0.1.0/backend.py action --id ID --verb VERB --apply
```

Catalog returns one JSON object with bounded unique rows, actions and actionLabel. Explicit actions return `{ok,message}` and optional `path`; CLI exits nonzero for any unconfirmed/partial result. No action is performed without `--apply`. Unknown/stale IDs are rejected against fresh bounded membership. `restore:last` is also accepted only when a matching durable receipt exists; the generic UI should restore with the original selected ID. A different selected row is not authorization to restore the last operation.

All selected source paths come from fixed local roots, not QML-supplied paths. Directory traversal uses directory FDs and O_NOFOLLOW for every component; symlinked roots, files and ancestors are rejected. The sole deliberate symlink reader is Omarchy's known current-background link; its target is separately allowlisted and opened safely before use. No arbitrary root/config/template input or remote thumbnails. Local previews are original installed/user files, not bundled art. Filenames are bounded/plain text; control characters are excluded. Image admission checks file type, byte limit and signatures; PNG dimensions are additionally capped. Catalog image previews are not a general-purpose hostile-media sanitizer; Qt/codec rendering is outside the backend suite.

Limits: 1024 entries per scanned directory (an overfull directory is skipped, not partially trusted), 512 wallpaper roots/files, 32 MiB per image, 64 KiB metadata reads, 8192-byte restore receipt, 256 displayed rows, <=1 MiB CLI output. Wallpaper identity is a hash of literal absolute path plus device/inode/size/mtime/ctime, not display text. Replacement invalidates the ID. No arbitrary recursive disk crawl. Content changes during bounded reads are rejected. Process argv is literal, shell=False; stdout and stderr each capped at 64 KiB, hard deadline and process-group SIGKILL on interruption/overflow/timeout, including pipe-holding descendants after their parent exits. Provider stderr is summarized rather than dumping paths/private hook output to the UI.

Plugin-owned state is under `$HOME/.local/state/omarchy-<slug>/`, private modes with an exclusive nonblocking lock and atomic/fsynced receipts. Existing state symlinks and special-file locks are refused. This is not an isolation boundary against malicious programs running as the same Unix user, nor protection from a provider that intentionally daemonizes into another session. Existing Omarchy setters can have their own broad side effects.

## Repeatable offline verification

From this project's root, with TMPDIR pointing at the approved scratch directory:

```
/usr/bin/python3 tests/run_backend_box.py new-receipt-label
```

The runner uses bwrap --unshare-all --clearenv, read-only source/usr/system ImageMagick policy, empty stock fixture, fake HOME, private /run and /tmp, and no host sockets, display or network. It also masks actual theme/background/shell executables with refusal stubs. Every successful setter in tests is a fixture shim inside the box. Tests execute the required `env -u PYTHONPATH /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v`. The runner refuses receipt overwrite. Do not run unittest on the live session.

Feature RED/GREEN receipts and extra boundary-control receipts are retained under ignored release-output. An initial missing-method discovery error is retained alongside its corrected assertion-based RED. Runtime feature slices were tested before implementation; additional passing characterization controls were added for existing guards. The first final portable-runner attempt failed before tests because a destination was a symlink; canonical target deduplication fixed it without changing any test threshold. That failed receipt is preserved.

No GUI, compositor setters, native renderer pixels, live theme/background changes, installation, enablement, package installation, publication or push was exercised. Headless fixture shims establish adapter logic, not successful live desktop integration. A separate read-only stock catalog probe uses real installed theme/image data in an offline fake-HOME box with setters masked. See release-output/backend-result.json for exact counts, source hashes and receipt names. Source provenance is in data/source-ledger.json and NOTICE.backend.md.
